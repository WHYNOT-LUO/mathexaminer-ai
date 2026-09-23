"""UI smoke tests: run the real Streamlit app against the in-memory demo backend."""

import importlib
import inspect
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "demo")]

from mathexaminer import db, deepseek  # noqa: E402
from mathexaminer.ui import auth_page  # noqa: E402

APP = str(ROOT / "app.py")
DEMO = str(ROOT / "demo" / "demo_app.py")


@pytest.fixture
def backend():
    saved = {n: getattr(db, n) for n in dir(db) if callable(getattr(db, n))}
    saved_grade, saved_render = deepseek.grade, auth_page.render
    import fake_backend

    fake_backend = importlib.reload(fake_backend)
    fake_backend.real_db = saved
    yield fake_backend
    for name, fn in saved.items():
        setattr(db, name, fn)
    deepseek.grade, auth_page.render = saved_grade, saved_render


def demo_app(backend, profile=None, **state):
    at = AppTest.from_file(DEMO, default_timeout=30)
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


def test_login_screen_renders(backend):
    at = demo_app(backend)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Log in", "Sign up"]


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
