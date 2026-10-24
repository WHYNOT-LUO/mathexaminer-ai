"""In-memory stand-in for Supabase + DeepSeek, so the whole UI runs with no accounts or keys.

It mirrors the public functions of ``mathexaminer.db`` (a test enforces matching signatures).
The homework in ``samples/`` is real: a question, a mark scheme and three handwritten answers,
each graded once by the real grader (``samples/recorded-gradings.json``). Instead of calling the
AI, ``fake_grade`` replays the recorded marking for whichever sample answer was submitted.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

from mathexaminer import db, deepseek, images
from mathexaminer.db import DBError, Profile
from mathexaminer.models import GradingResult
from mathexaminer.ui import auth_page, components, student_page

TEACHER = Profile("teacher-1", "teacher@demo.school", "Ms Carter", "teacher")
STUDENTS = {
    "student-1": Profile("student-1", "student@demo.school", "Alex Chen", "student"),
    "student-2": Profile("student-2", "sam@demo.school", "Sam Patel", "student"),
    "student-3": Profile("student-3", "mia@demo.school", "Mia Rossi", "student"),
}
LOGIN_HINT = "Demo mode: log in with any password as student@demo.school or teacher@demo.school."

_now = lambda: datetime.now(timezone.utc)  # noqa: E731
_iso = lambda dt: dt.isoformat()  # noqa: E731


SAMPLES = Path(__file__).with_name("samples")
RECORDINGS = json.loads((SAMPLES / "recorded-gradings.json").read_text())
ASSIGNMENT_TITLE = "Stationary points of a cubic"
SAMPLE_LABELS = {
    "answer-strong.png": "Strong answer",
    "answer-typical-mistake.png": "Typical mistake",
    "answer-hard-to-read.png": "Hard to read",
}
# Which demo student submitted which real answer, and how long ago.
SEEDED = [("student-2", "answer-strong.png", 50), ("student-3", "answer-hard-to-read.png", 26),
          ("student-1", "answer-typical-mistake.png", 3)]


def _page_key(page: bytes) -> str:
    return hashlib.sha256(page).hexdigest()


def _load_samples() -> tuple[dict[str, bytes], dict[str, GradingResult], dict[str, GradingResult]]:
    """Each answer's image bytes and its recorded marking, by file name and by its first JPEG page
    (the page the grader is given, so an uploaded copy of a sample is recognised too)."""
    answers, by_page, by_answer = {}, {}, {}
    for g in RECORDINGS["gradings"]:
        data = (SAMPLES / g["answer"]).read_bytes()
        result = GradingResult.from_model_json(g["reply"])
        answers[g["answer"]], by_answer[g["answer"]] = data, result
        by_page[_page_key(images.to_jpeg_pages(data)[0])] = result
    return answers, by_page, by_answer


ANSWERS, RECORDED, RECORDED_BY_ANSWER = _load_samples()


class _Store:
    def __init__(self) -> None:
        self.assignments: list[dict] = []
        self.submissions: list[dict] = []
        self.attempts: list[dict] = []  # grading_attempts: the 24-hour allowance
        self.files: dict[str, bytes] = {}
        self._seed()

    def _put(self, folder: str, name: str, data: bytes) -> str:
        path = f"{folder}/{uuid.uuid4()}{Path(name).suffix}"
        self.files[path] = data
        return path

    def _seed(self) -> None:
        assignment = {
            "id": str(uuid.uuid4()), "teacher_id": TEACHER.user_id, "title": ASSIGNMENT_TITLE, "status": "active",
            "mark_scheme_path": self._put(TEACHER.user_id, "mark-scheme.pdf", (SAMPLES / "mark-scheme.pdf").read_bytes()),
            "question_path": self._put(TEACHER.user_id, "question.png", (SAMPLES / "question.png").read_bytes()),
            "created_at": _iso(_now() - timedelta(days=5)),
        }
        self.assignments.append(assignment)
        for student_id, answer, hours_ago in SEEDED:
            path = self._put(student_id, answer, ANSWERS[answer])
            result = RECORDED_BY_ANSWER[answer]
            self.submissions.append(
                self._row(assignment, STUDENTS[student_id], result, [path], _now() - timedelta(hours=hours_ago))
            )

    def _row(self, assignment, student, result, paths, when, flag_reason=None) -> dict:
        return {
            "id": str(uuid.uuid4()), "assignment_id": assignment["id"], "student_id": student.user_id,
            "student_work_paths": paths, "ai_feedback": result.to_markdown(),
            "confidence_score": result.confidence, "score_awarded": result.score_awarded,
            "score_total": result.score_total, "topic_tag": result.topic, "key_takeaway": result.key_takeaway,
            "flagged_for_review": bool(flag_reason), "flag_source": "ai" if flag_reason else None,
            "flag_reason": flag_reason, "flagged_at": _iso(when) if flag_reason else None,
            "teacher_score_awarded": None, "teacher_comment": None, "resolved_at": None, "resolved_by": None,
            "created_at": _iso(when),
            "assignment_title": assignment["title"], "student_name": student.name,
        }


STORE = _Store()  # used outside a Streamlit session, e.g. by tests calling these functions directly


# Stores set aside when a visitor signs out (the app then clears the session), so what they did as the
# student is still there when they log in as the teacher. Bounded, so a busy public demo cannot grow forever.
_PARKED: OrderedDict[str, _Store] = OrderedDict()
MAX_PARKED = 200


def _store() -> _Store:
    """Each browser session gets its own copy, so visitors to a public demo never see each other's changes."""
    ctx = get_script_run_ctx()
    if ctx is None:
        return STORE
    if "demo_store" not in st.session_state:
        st.session_state.demo_store = _PARKED.pop(ctx.session_id, None) or _Store()
    return st.session_state.demo_store


DEMO_NOTE = (
    "Demo mode: the homework and its marking are real, recorded from the AI on 24 Oct 2026; the demo "
    "replays them instead of calling the AI. What you do stays when you switch between the student and "
    "teacher accounts. Refresh the page to start over."
)
_real_welcome_banner = components.welcome_banner


def _welcome_banner_with_note(name, email, role):
    _real_welcome_banner(name, email, role)
    st.info(DEMO_NOTE)

_real_auth_render = auth_page.render


def _auth_render_with_hint(client):
    _real_auth_render(client)
    st.info(LOGIN_HINT)

# ---- deepseek ---------------------------------------------------------------

NOT_A_SAMPLE = (
    "The demo only marks the three sample answers: it replays the marking the real AI gave them. "
    "Pick one of the sample answers, or run the real app to have your own work marked."
)


MARKING_DELAY_S = 2.5  # long enough to see the "marking" step; a real grading took 8-12 seconds


def fake_grade(settings, scheme_pages, student_pages, **_kwargs) -> GradingResult:
    time.sleep(MARKING_DELAY_S)
    result = RECORDED.get(_page_key(student_pages[0])) if student_pages else None
    if result is None:
        raise deepseek.GradingError(NOT_A_SAMPLE)
    return result


# ---- db (same names and signatures as mathexaminer.db) --------------------

def new_client(settings):
    return object()


def new_service_client(settings):
    return object()


def sign_in(client, email, password):
    for profile in (TEACHER, *STUDENTS.values()):
        if profile.email == email.strip().lower():
            return profile
    raise DBError("Incorrect email or password. " + LOGIN_HINT)


def sign_up(client, email, password, name, teacher_code=""):
    raise DBError("Sign-up is disabled in demo mode. " + LOGIN_HINT)


def sign_out(client):
    ctx = get_script_run_ctx()
    if ctx is not None and "demo_store" in st.session_state:
        _PARKED[ctx.session_id] = st.session_state.demo_store
        while len(_PARKED) > MAX_PARKED:
            _PARKED.popitem(last=False)


def download(client, bucket, path):
    return _store().files[path]


def create_assignment(client, teacher_id, title, filename, data, mime, question=None):
    store = _store()
    store.assignments.insert(
        0, {"id": str(uuid.uuid4()), "teacher_id": teacher_id, "title": title, "status": "active",
            "mark_scheme_path": store._put(teacher_id, filename, data),
            "question_path": store._put(teacher_id, question[0], question[1]) if question else None,
            "created_at": _iso(_now())}
    )


def list_teacher_assignments(client, teacher_id):
    return [dict(a) for a in _store().assignments if a["teacher_id"] == teacher_id]


def set_assignment_status(client, assignment_id, status):
    for a in _store().assignments:
        if a["id"] == assignment_id:
            a["status"] = status


def replace_mark_scheme(client, teacher_id, assignment_id, old_path, filename, data, mime):
    path = _store()._put(teacher_id, filename, data)
    for a in _store().assignments:
        if a["id"] == assignment_id:
            a["mark_scheme_path"] = path


def delete_assignment(client, service, assignment_id, mark_scheme_path, question_path=None):
    store = _store()
    gone = [s for s in store.submissions if s["assignment_id"] == assignment_id]
    store.assignments = [a for a in store.assignments if a["id"] != assignment_id]
    store.submissions = [s for s in store.submissions if s["assignment_id"] != assignment_id]
    for path in [mark_scheme_path, question_path, *(p for s in gone for p in s["student_work_paths"])]:
        store.files.pop(path, None)


def list_teacher_submissions(client, assignment_ids):
    hidden = ("ai_feedback", "key_takeaway")  # like db.TEACHER_LIST_COLUMNS
    rows = [{k: v for k, v in s.items() if k not in hidden}
            for s in _store().submissions if s["assignment_id"] in assignment_ids]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)  # newest first, like db.py


def submission_feedback(client, submission_ids):
    return {s["id"]: s["ai_feedback"] for s in _store().submissions if s["id"] in submission_ids}


def resolve_submission(client, submission_id, score, comment):
    for s in _store().submissions:
        if s["id"] == submission_id:
            s.update(teacher_score_awarded=score, teacher_comment=comment.strip() or None,
                     resolved_at=_iso(_now()), resolved_by=TEACHER.user_id)


def list_open_assignments(client):
    return [dict(a) for a in _store().assignments if a["status"] == "active"]


def count_gradings_today(service, student_id):
    since = _iso(_now() - timedelta(days=1))
    return sum(1 for a in _store().attempts if a["student_id"] == student_id and a["created_at"] >= since)


def reserve_grading(service, student_id, cap):
    attempt = {"student_id": student_id, "created_at": _iso(_now())}
    _store().attempts.append(attempt)
    if count_gradings_today(service, student_id) > cap:
        _store().attempts.remove(attempt)
        raise DBError(f"You have used all {cap} gradings for the last 24 hours. Please try again later.")


def upload_student_work(client, student_id, files):
    return [_store()._put(student_id, name, data) for name, data, _mime in files]


def save_submission(service, assignment_id, student_id, paths, result, auto_flag_reason=None):
    assignment = next((a for a in _store().assignments if a["id"] == assignment_id and a["status"] == "active"), None)
    if assignment is None:
        raise DBError("This assignment is no longer open for submissions.")
    row = _store()._row(assignment, STUDENTS[student_id], result, paths, _now(), auto_flag_reason)
    _store().submissions.insert(0, row)
    return row["id"]


def flag_submission(client, submission_id, reason):
    for s in _store().submissions:
        if s["id"] == submission_id:
            s.update(flagged_for_review=True, flag_source="student",
                     flag_reason=reason.strip() or "Student requested manual review.", flagged_at=_iso(_now()))


def list_student_submissions(client, student_id):
    rows = [dict(s) for s in _store().submissions if s["student_id"] == student_id]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)  # newest first, like db.py


DB_FUNCTIONS = (
    "new_client new_service_client sign_in sign_up sign_out download create_assignment "
    "list_teacher_assignments set_assignment_status replace_mark_scheme delete_assignment "
    "list_teacher_submissions submission_feedback resolve_submission list_open_assignments "
    "count_gradings_today reserve_grading upload_student_work save_submission flag_submission "
    "list_student_submissions"
).split()


def install() -> None:
    """Route the app's db/deepseek calls to this in-memory backend."""
    module = globals()
    for name in DB_FUNCTIONS:
        setattr(db, name, module[name])
    deepseek.grade = fake_grade
    student_page.SAMPLE_ANSWERS = [(SAMPLE_LABELS[name], name, data) for name, data in ANSWERS.items()]
    auth_page.render = _auth_render_with_hint
    components.welcome_banner = _welcome_banner_with_note
