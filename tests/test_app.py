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

from mathexaminer import db, deepseek  # noqa: E402
from mathexaminer.ui import auth_page, components  # noqa: E402

APP = str(ROOT / "app.py")
DEMO = str(ROOT / "demo" / "demo_app.py")


@pytest.fixture
def backend():
    saved = {n: getattr(db, n) for n in dir(db) if callable(getattr(db, n))}
    saved_grade, saved_render, saved_banner = deepseek.grade, auth_page.render, components.welcome_banner
    import fake_backend

    fake_backend = importlib.reload(fake_backend)
    fake_backend.real_db = saved
    yield fake_backend
    for name, fn in saved.items():
        setattr(db, name, fn)
    deepseek.grade, auth_page.render, components.welcome_banner = saved_grade, saved_render, saved_banner


def demo_app(backend, profile=None, **state):
    at = AppTest.from_file(DEMO, default_timeout=30)
    at.session_state["demo_store"] = backend.STORE  # share the test's store with the app session
    if profile is not None:
        at.session_state["profile"] = profile
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


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
    backend.STORE.assignments[0]["status"] = "closed"
    at = demo_app(backend, backend.STUDENTS["student-1"])
    assert not at.exception
    assert len(at.selectbox[0].options) == 1


def test_student_error_book_and_result_view(backend):
    result = backend.fake_grade(None, [], [])
    student = backend.STUDENTS["student-1"]
    sid = backend.save_submission(None, backend.STORE.assignments[0]["id"], student.user_id, [], result)
    last = {"submission_id": sid, "assignment_title": "Integration Test 1", "result": result,
            "flagged": False, "auto_flagged": False}
    at = demo_app(backend, student, last_result=last)
    assert not at.exception
    text = all_markdown(at)
    assert "4 / 5 marks" in text
    assert "Substitute the lower limit" in text


def test_key_takeaway_renders_math_and_stays_escaped(backend):
    # Streamlit only renders $...$ in plain markdown, so the takeaway must not sit inside raw HTML
    takeaway = "Solve the system for $M$. <img src=x onerror=alert(1)>"
    result = dataclasses.replace(backend.fake_grade(None, [], []), key_takeaway=takeaway)
    student = backend.STUDENTS["student-1"]
    sid = backend.save_submission(None, backend.STORE.assignments[0]["id"], student.user_id, [], result)
    last = {"submission_id": sid, "assignment_title": "Integration Test 1", "result": result,
            "flagged": False, "auto_flagged": False}
    at = demo_app(backend, student, last_result=last)
    assert not at.exception
    assert any(m.value == takeaway and not m.proto.allow_html for m in at.markdown)
    assert not any("<img src=x" in m.value for m in at.markdown if m.proto.allow_html)


def test_error_book_takeaways_render_math_and_stay_escaped(backend):
    takeaway = "Re-check $F(1)$ before subtracting. <img src=x onerror=alert(1)>"
    student = backend.STUDENTS["student-1"]
    mine = [s for s in backend.STORE.submissions if s["student_id"] == student.user_id]
    assert len(mine) > 1  # several Error Book entries, so the container keys must be unique
    for row in mine:
        row["key_takeaway"] = takeaway
    at = demo_app(backend, student)
    assert not at.exception
    assert sum(m.value == takeaway and not m.proto.allow_html for m in at.markdown) == len(mine)
    assert not any("<img src=x" in m.value for m in at.markdown if m.proto.allow_html)


def test_student_can_dispute_a_grade(backend):
    result = backend.fake_grade(None, [], [])
    student = backend.STUDENTS["student-1"]
    sid = backend.save_submission(None, backend.STORE.assignments[0]["id"], student.user_id, [], result)
    last = {"submission_id": sid, "assignment_title": "Integration Test 1", "result": result,
            "flagged": False, "auto_flagged": False}
    at = demo_app(backend, student, last_result=last)
    at.text_area[0].set_value("It misread my 2 as a 1.")
    next(b for b in at.button if b.label == "Send to teacher →").click()
    at.run()
    assert not at.exception
    row = next(s for s in backend.STORE.submissions if s["id"] == sid)
    assert row["flagged_for_review"] and row["flag_reason"] == "It misread my 2 as a 1."


def test_teacher_console_renders_queue_and_analytics(backend):
    pending = [s for s in backend.STORE.submissions if s["flagged_for_review"] and not s["resolved_at"]]
    at = demo_app(backend, backend.TEACHER)
    assert not at.exception
    assert [t.label for t in at.tabs][:3] == ["Assignments", f"Review queue ({len(pending)})", "Analytics"]
    assert len(at.metric) >= 4


def test_teacher_can_resolve_a_flagged_submission(backend):
    target = next(s for s in backend.STORE.submissions if s["flagged_for_review"])
    at = demo_app(backend, backend.TEACHER)
    at.number_input[0].set_value(target["score_total"])
    next(b for b in at.button if b.label == "Save review →").click()
    at.run()
    assert not at.exception
    resolved = [s for s in backend.STORE.submissions if s["resolved_at"]]
    assert len(resolved) == 1 and resolved[0]["teacher_score_awarded"] is not None


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
