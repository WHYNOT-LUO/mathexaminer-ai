"""UI smoke tests: run the real Streamlit app against the in-memory demo backend."""

import dataclasses
import importlib
import inspect
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "demo")]

import re  # noqa: E402

from mathexaminer import db, deepseek, images  # noqa: E402
from mathexaminer.models import GradingResult  # noqa: E402
from mathexaminer.ui import auth_page, components, student_page  # noqa: E402

APP = str(ROOT / "app.py")
DEMO = str(ROOT / "demo" / "demo_app.py")


@pytest.fixture
def backend():
    saved = {n: getattr(db, n) for n in dir(db) if callable(getattr(db, n))}
    saved_grade, saved_render, saved_banner = deepseek.grade, auth_page.render, components.welcome_banner
    saved_samples = student_page.SAMPLE_ANSWERS
    import fake_backend

    fake_backend = importlib.reload(fake_backend)
    fake_backend.real_db = saved
    fake_backend.MARKING_DELAY_S = 0
    yield fake_backend
    for name, fn in saved.items():
        setattr(db, name, fn)
    deepseek.grade, auth_page.render, components.welcome_banner = saved_grade, saved_render, saved_banner
    student_page.SAMPLE_ANSWERS = saved_samples


def recorded(backend, answer="answer-typical-mistake.png"):
    return backend.RECORDED_BY_ANSWER[answer]


def submit(backend, student, result, flag=None):
    return backend.save_submission(None, backend.STORE.assignments[0]["id"], student.user_id, [], result, flag)


def as_result_view(sid, result):
    return {"submission_id": sid, "assignment_title": "Stationary points of a cubic", "result": result,
            "flagged": False, "auto_flagged": False}


def demo_app(backend, profile=None, **state):
    at = AppTest.from_file(DEMO, default_timeout=30)
    at.session_state["demo_store"] = backend.STORE  # share the test's store with the app session
    if profile is not None:
        at.session_state["profile"] = profile
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def count_images(at):
    def walk(node):
        return (getattr(node, "type", None) == "image") + sum(walk(c) for c in getattr(node, "children", {}).values())

    return walk(at._tree)


def all_markdown(at):
    return "\n".join(m.value for m in at.markdown)


def test_unconfigured_app_shows_setup_help(monkeypatch):
    for name in ("SUPABASE_URL", "SUPABASE_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert "not configured yet" in all_markdown(at)


def test_fake_backend_matches_real_db_signatures(backend):
    def params(fn):
        return [(p.name, p.default) for p in inspect.signature(fn).parameters.values()]

    for name in backend.DB_FUNCTIONS:
        assert params(getattr(backend, name)) == params(backend.real_db[name]), name
    # ...and every public db function has a demo version, so the demo never reaches real Supabase
    public = {n for n, fn in backend.real_db.items()
              if inspect.isfunction(fn) and fn.__module__ == "mathexaminer.db" and not n.startswith("_")}
    assert public == set(backend.DB_FUNCTIONS)


def test_login_screen_renders(backend):
    at = demo_app(backend)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Log in", "Sign up"]
    assert "<h1>MathExaminer AI</h1>" in all_markdown(at)  # one line, no forced break before "AI"


def test_app_code_avoids_deprecated_use_container_width():
    # Streamlit deprecated use_container_width (removal announced for after 2025-12-31);
    # requirements.txt allows any newer Streamlit, so a release that drops it would break the app
    sources = [ROOT / "app.py", *(ROOT / "mathexaminer").rglob("*.py"), *(ROOT / "demo").rglob("*.py")]
    offenders = [str(p.relative_to(ROOT)) for p in sources if "use_container_width" in p.read_text()]
    assert offenders == []


def test_student_can_log_in_and_sign_out(backend):
    at = demo_app(backend)
    at.text_input[0].set_value("student@demo.school")
    at.text_input[1].set_value("whatever")
    at.button[0].click()
    at.run()
    assert not at.exception
    assert at.session_state["profile"].role == "student"
    sign_out = next(b for b in at.button if b.label == "Sign out")
    sign_out.click()
    at.run()
    assert "profile" not in at.session_state


def test_wrong_login_shows_error(backend):
    at = demo_app(backend)
    at.text_input[0].set_value("nobody@example.com")
    at.text_input[1].set_value("x")
    at.button[0].click()
    at.run()
    assert not at.exception
    assert any("Incorrect email" in m.value for m in at.markdown)


def test_student_portal_lists_only_open_assignments(backend):
    backend.create_assignment(None, backend.TEACHER.user_id, "Second", "ms.png", b"x", "image/png")
    closed = backend.STORE.assignments[-1]  # a seeded one
    closed["status"] = "closed"
    at = demo_app(backend, backend.STUDENTS["student-1"])
    assert not at.exception
    assert "Second" in at.selectbox[0].options and closed["title"] not in at.selectbox[0].options


def test_student_error_book_and_result_view(backend):
    result = recorded(backend)
    student = backend.STUDENTS["student-1"]
    sid = submit(backend, student, result)
    at = demo_app(backend, student, last_result=as_result_view(sid, result))
    assert not at.exception
    text = all_markdown(at)
    assert "6 / 7 marks" in text
    assert result.key_takeaway in text


def test_key_takeaway_renders_math_and_stays_escaped(backend):
    # Streamlit only renders $...$ in plain markdown, so the takeaway must not sit inside raw HTML
    takeaway = "Solve the system for $M$. <img src=x onerror=alert(1)>"
    result = dataclasses.replace(recorded(backend), key_takeaway=takeaway)
    student = backend.STUDENTS["student-1"]
    sid = submit(backend, student, result)
    at = demo_app(backend, student, last_result=as_result_view(sid, result))
    assert not at.exception
    assert any(m.value == takeaway and not m.proto.allow_html for m in at.markdown)
    assert not any("<img src=x" in m.value for m in at.markdown if m.proto.allow_html)


def test_error_book_takeaways_render_math_and_stay_escaped(backend):
    takeaway = "Re-check $F(1)$ before subtracting. <img src=x onerror=alert(1)>"
    student = backend.STUDENTS["student-1"]
    submit(backend, student, recorded(backend, "answer-strong.png"))
    mine = [s for s in backend.STORE.submissions if s["student_id"] == student.user_id]
    assert len(mine) > 1  # several Error Book entries, so the container keys must be unique
    for row in mine:
        row["key_takeaway"] = takeaway
    at = demo_app(backend, student)
    assert not at.exception
    assert sum(m.value == takeaway and not m.proto.allow_html for m in at.markdown) == len(mine)
    assert not any("<img src=x" in m.value for m in at.markdown if m.proto.allow_html)


def test_student_can_dispute_a_grade(backend):
    result = recorded(backend)
    student = backend.STUDENTS["student-1"]
    sid = submit(backend, student, result)
    at = demo_app(backend, student, last_result=as_result_view(sid, result))
    at.text_area[0].set_value("It misread my 2 as a 1.")
    next(b for b in at.button if b.label == "Send to teacher →").click()
    at.run()
    assert not at.exception
    row = next(s for s in backend.STORE.submissions if s["id"] == sid)
    assert row["flagged_for_review"] and row["flag_reason"] == "It misread my 2 as a 1."


def pending(backend):
    return sum(1 for s in backend.STORE.submissions if s["flagged_for_review"] and not s["resolved_at"])


def flag_one(backend):
    target = backend.STORE.submissions[0]
    backend.flag_submission(None, target["id"], "Please check the last mark.")
    return target


def test_teacher_console_renders_queue_and_analytics(backend):
    at = demo_app(backend, backend.TEACHER)
    assert not at.exception
    waiting = pending(backend)
    assert waiting >= 1  # low-confidence gradings start in the queue
    assert [t.label for t in at.tabs][:3] == ["Assignments", f"Review queue ({waiting})", "Analytics"]
    assert len(at.metric) >= 4
    flag_one(backend)
    at = demo_app(backend, backend.TEACHER)
    assert [t.label for t in at.tabs][1] == f"Review queue ({waiting + 1})"


def test_teacher_can_resolve_a_flagged_submission(backend):
    target = flag_one(backend)
    at = demo_app(backend, backend.TEACHER)
    form_input = next(n for n in at.number_input if n.label == "Final mark")
    form_input.set_value(target["score_total"])
    next(b for b in at.button if b.label == "Save review →").click()
    at.run()
    assert not at.exception
    resolved = [s for s in backend.STORE.submissions if s["resolved_at"]]
    assert len(resolved) == 1 and resolved[0]["teacher_score_awarded"] == target["score_total"]


def test_user_supplied_titles_cannot_inject_html(backend):
    evil = '<img src=x onerror=alert(1)>'
    backend.STORE.assignments[0]["title"] = evil
    for row in backend.STORE.submissions:
        row["assignment_title"] = evil
        row["student_name"] = evil
    for profile in (backend.TEACHER, backend.STUDENTS["student-1"]):
        at = demo_app(backend, profile)
        assert not at.exception
        assert "<img src=x" not in all_markdown(at)


def test_demo_sessions_are_isolated_and_labelled(backend):
    at = demo_app(backend, backend.STUDENTS["student-1"])
    assert not at.exception
    assert any(backend.DEMO_NOTE in i.value for i in at.info)
    fresh = AppTest.from_file(DEMO, default_timeout=30)  # a second visitor, no shared store
    fresh.session_state["profile"] = backend.TEACHER
    fresh.run()
    assert not fresh.exception
    assert fresh.session_state["demo_store"] is not backend.STORE


def test_teacher_queue_shows_the_ai_marking_of_pending_items(backend):
    flag_one(backend)
    at = demo_app(backend, backend.TEACHER)
    assert not at.exception
    assert "Step-by-Step Marking" in all_markdown(at)  # fetched separately from the lighter list query


def test_deleting_an_assignment_removes_its_student_files(backend):
    target = backend.STORE.assignments[0]
    work = [p for s in backend.STORE.submissions if s["assignment_id"] == target["id"] for p in s["student_work_paths"]]
    assert work and all(p in backend.STORE.files for p in work)
    at = demo_app(backend, backend.TEACHER)
    at.checkbox(key=f"confirm_del_{target['id']}").check().run()
    at.button(key=f"del_{target['id']}").click().run()
    assert not at.exception
    assert target["id"] not in [a["id"] for a in backend.STORE.assignments]
    assert not any(p in backend.STORE.files for p in work)


def test_allowance_counts_grading_attempts(backend):
    student = backend.STUDENTS["student-1"]
    for _ in range(3):
        backend.reserve_grading(None, student.user_id, cap=20)
    at = demo_app(backend, student)
    assert not at.exception
    assert any("Gradings left in the last 24 hours: 17 of 20" in c.value for c in at.caption)


# ---------------------------------------------------------------- the demo's real samples


MARK_LINE = re.compile(r"\((\d+)/(\d+)\)")


def test_every_seeded_marking_adds_up_to_its_score(backend):
    # the bug a visitor spotted: the mark-by-mark list must add up to the score shown
    assert len(backend.STORE.submissions) == 3 + 12  # the real samples and the example class
    for s in backend.STORE.submissions:
        marks = [(int(a), int(m)) for a, m in MARK_LINE.findall(s["ai_feedback"])]
        assert marks, s["student_name"]
        assert sum(a for a, _ in marks) == s["score_awarded"]
        out_of = 10 if s["assignment_title"] in backend.EXAMPLE_TITLES else 7  # the real scheme is out of 7
        assert sum(m for _, m in marks) == s["score_total"] == out_of
        assert f"**{s['score_awarded']} / {s['score_total']} marks**" in s["ai_feedback"]


def test_example_data_is_labelled_and_has_no_homework(backend):
    examples = [s for s in backend.STORE.submissions if s["assignment_title"] in backend.EXAMPLE_TITLES]
    assert len(examples) == 12
    for s in examples:
        assert not s["student_work_paths"]
        assert "Example data" in s["ai_feedback"]
    assert "example data" in backend.DEMO_NOTE
    at = demo_app(backend, backend.TEACHER)
    assert not at.exception
    assert "No pages were uploaded with this submission." in [c.value for c in at.caption]


def test_samples_are_not_marked_against_an_example_assignment(backend):
    example = next(a for a in backend.STORE.assignments if a["title"] in backend.EXAMPLE_TITLES)
    scheme = images.to_jpeg_pages(backend.STORE.files[example["mark_scheme_path"]])
    pages = images.files_to_jpeg_pages([backend.ANSWERS["answer-strong.png"]])
    with pytest.raises(deepseek.GradingError, match="example data"):
        backend.fake_grade(None, scheme, pages)
    real = backend.STORE.assignments[0]
    scheme = images.to_jpeg_pages(backend.STORE.files[real["mark_scheme_path"]], max_pages=6)
    assert backend.fake_grade(None, scheme, pages) is backend.RECORDED_BY_ANSWER["answer-strong.png"]


def test_recorded_gradings_are_the_real_model_replies(backend):
    for g in backend.RECORDINGS["gradings"]:
        assert g["model"] == "deepseek-flash"
        result = GradingResult.from_model_json(g["reply"])
        assert result.score_awarded == sum(m["awarded"] for m in g["reply"]["marks"])


def test_fake_grade_replays_samples_and_refuses_other_work(backend):
    for name, data in backend.ANSWERS.items():
        pages = images.files_to_jpeg_pages([data])
        assert backend.fake_grade(None, [], pages) is backend.RECORDED_BY_ANSWER[name]
    other = images.to_jpeg_pages(next(iter(backend.ANSWERS.values())))[0][:-10] + b"different"
    with pytest.raises(deepseek.GradingError, match="only marks the three sample answers"):
        backend.fake_grade(None, [], [other])


def test_hard_to_read_answer_was_read_with_low_confidence_and_waits_for_the_teacher(backend):
    result = recorded(backend, "answer-hard-to-read.png")
    assert result.confidence < student_page.LOW_CONFIDENCE_THRESHOLD
    seeded = [s for s in backend.STORE.submissions if s["confidence_score"] == result.confidence]
    assert len(seeded) == 1 and seeded[0]["flagged_for_review"] and seeded[0]["flag_source"] == "ai"
    assert seeded[0]["flag_reason"] == student_page.auto_flag_reason(result)
    at = demo_app(backend, backend.TEACHER)
    assert not at.exception
    assert [t.label for t in at.tabs][1] == f"Review queue ({pending(backend)})"
    assert any(e.label.startswith("Mia Rossi · Stationary points") and "confidence 52%" in e.label for e in at.expander)


def test_confidence_shows_in_the_submissions_list_and_error_book(backend):
    hard = next(s for s in backend.STORE.submissions if s["confidence_score"] == 52)
    at = demo_app(backend, backend.TEACHER)
    at.toggle(key=f"subs_{hard['assignment_id']}").set_value(True).run()
    assert not at.exception
    assert any(e.label.startswith("Mia Rossi · 7/7 · AI confidence 52%") for e in at.expander)
    at = demo_app(backend, backend.STUDENTS["student-3"])
    assert not at.exception
    assert any(e.label.startswith("Stationary points of a cubic · 7/7") and "AI confidence 52%" in e.label
               for e in at.expander)


def test_grading_the_hard_to_read_sample_flags_it_for_the_teacher(backend):
    at = demo_app(backend, backend.STUDENTS["student-1"])
    at.button(key="sample_2").click().run()  # "Hard to read"
    at.button(key="grade_btn").click().run()
    assert not at.exception
    assert at.session_state["last_result"]["auto_flagged"]
    assert "was not sure it read your handwriting" in all_markdown(at)


def test_student_grades_a_sample_answer_end_to_end(backend):
    at = demo_app(backend, backend.STUDENTS["student-1"])
    assert not at.exception
    at.button(key="sample_1").click().run()  # "Typical mistake"
    at.button(key="grade_btn").click().run()
    assert not at.exception
    assert at.session_state["last_result"]["result"] is backend.RECORDED_BY_ANSWER["answer-typical-mistake.png"]
    assert "6 / 7 marks" in all_markdown(at)


def test_student_sees_the_question_and_their_own_work(backend):
    at = demo_app(backend, backend.STUDENTS["student-1"])
    assert not at.exception
    assert any(e.label == "📄 The question" for e in at.expander)
    assert count_images(at) == 2  # the question sheet, and the student's own work in the Error Book


def test_teacher_sees_every_students_work_and_marking(backend):
    assignment = backend.STORE.assignments[0]
    at = demo_app(backend, backend.TEACHER)
    at.toggle(key=f"subs_{assignment['id']}").set_value(True).run()
    assert not at.exception
    labels = [e.label for e in at.expander]
    for name in ("Alex Chen", "Sam Patel", "Mia Rossi"):
        assert any(label.startswith(name) for label in labels)
    # each student's homework and marking, plus whatever waits in the review queue tab
    queued = [s for s in backend.STORE.submissions if s["flagged_for_review"] and not s["resolved_at"]]
    assert all_markdown(at).count("Step-by-Step Marking") == 3 + len(queued)
    assert count_images(at) == 3 + sum(len(s["student_work_paths"]) for s in queued)


def test_student_dispute_reaches_the_teacher_after_switching_accounts(backend):
    result = recorded(backend)
    student = backend.STUDENTS["student-1"]
    sid = submit(backend, student, result)
    waiting = pending(backend)
    at = demo_app(backend, student, last_result=as_result_view(sid, result))
    at.text_area[0].set_value("I think my last line deserves the A1.")
    next(b for b in at.button if b.label == "Send to teacher →").click()
    at.run()
    next(b for b in at.button if b.label == "Sign out").click()
    at.run()
    assert "profile" not in at.session_state
    at.text_input[0].set_value("teacher@demo.school")
    at.text_input[1].set_value("x")
    at.button[0].click()
    at.run()
    assert not at.exception
    assert [t.label for t in at.tabs][1] == f"Review queue ({waiting + 1})"
    assert "I think my last line deserves the A1." in all_markdown(at)
