"""In-memory stand-in for Supabase + DeepSeek, so the whole UI runs with no accounts or keys.

It mirrors the public functions of ``mathexaminer.db`` (a test enforces matching signatures)
and replaces ``deepseek.grade`` with a canned marking. State lives in this module, so it
survives Streamlit reruns but resets when the server restarts.
"""

from __future__ import annotations

import io
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import streamlit as st
from PIL import Image, ImageDraw

from mathexaminer import db, deepseek
from mathexaminer.db import DBError, Profile
from mathexaminer.models import GradingResult
from mathexaminer.ui import auth_page

TEACHER = Profile("teacher-1", "teacher@demo.school", "Ms Carter", "teacher")
STUDENTS = {
    "student-1": Profile("student-1", "student@demo.school", "Alex Chen", "student"),
    "student-2": Profile("student-2", "sam@demo.school", "Sam Patel", "student"),
    "student-3": Profile("student-3", "mia@demo.school", "Mia Rossi", "student"),
}
LOGIN_HINT = "Demo mode: log in with any password as student@demo.school or teacher@demo.school."

_now = lambda: datetime.now(timezone.utc)  # noqa: E731
_iso = lambda dt: dt.isoformat()  # noqa: E731


def _placeholder(text: str) -> bytes:
    img = Image.new("RGB", (900, 1200), (252, 251, 247))
    draw = ImageDraw.Draw(img)
    draw.text((40, 40), text, fill=(15, 27, 45))
    for i in range(1, 12):
        draw.line((40, 100 + i * 80, 860, 100 + i * 80), fill=(214, 208, 196))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class _Store:
    def __init__(self) -> None:
        self.assignments: list[dict] = []
        self.submissions: list[dict] = []
        self.files: dict[str, bytes] = {}
        self._seed()

    def _seed(self) -> None:
        for title, days in (("Integration Test 1", 9), ("Trigonometric Identities", 3)):
            path = f"teacher-1/{uuid.uuid4()}.png"
            self.files[path] = _placeholder(f"Mark scheme - {title} (demo)")
            self.assignments.append(
                {"id": str(uuid.uuid4()), "teacher_id": TEACHER.user_id, "title": title,
                 "status": "active", "mark_scheme_path": path,
                 "created_at": _iso(_now() - timedelta(days=days))}
            )
        rng = random.Random(7)
        topics = ["Calculus", "Algebra", "Trigonometry", "Series", "Vectors"]
        for i in range(16):
            a = self.assignments[i % 2]
            student = list(STUDENTS.values())[i % 3]
            total = 10
            awarded = rng.choice([3, 4, 5, 6, 7, 8, 9, 10])
            confidence = rng.choice([48, 55, 72, 81, 88, 93, 96])
            flagged = confidence < 60 or i == 4
            self.submissions.append(
                self._row(a, student, awarded, total, confidence, rng.choice(topics),
                          flagged, "ai" if confidence < 60 else "student",
                          "Low handwriting confidence." if confidence < 60 else "I think step 3 deserves the A1.",
                          _now() - timedelta(hours=6 * i))
            )

    def _row(self, assignment, student, awarded, total, confidence, topic, flagged, source, reason, when) -> dict:
        path = f"{student.user_id}/{uuid.uuid4()}.jpg"
        self.files[path] = _placeholder(f"{student.name} - handwritten work (demo)")
        return {
            "id": str(uuid.uuid4()), "assignment_id": assignment["id"], "student_id": student.user_id,
            "student_work_paths": [path],
            "ai_feedback": f"## Step-by-Step Marking\n\n- ✅ **1 M1** (1/1) — Sensible method.\n- ❌ **1 A1** (0/1) — Arithmetic slip.\n\n## Score\n\n**{awarded} / {total} marks**\n\n## Examiner's Summary\n\nDemo data.",
            "confidence_score": confidence, "score_awarded": awarded, "score_total": total,
            "topic_tag": topic, "key_takeaway": "Re-check arithmetic when substituting limits.",
            "flagged_for_review": flagged, "flag_source": source if flagged else None,
            "flag_reason": reason if flagged else None, "flagged_at": _iso(when) if flagged else None,
            "teacher_score_awarded": None, "teacher_comment": None, "resolved_at": None, "resolved_by": None,
            "created_at": _iso(when),
            "assignment_title": assignment["title"], "student_name": student.name,
        }


STORE = _Store()
_real_auth_render = auth_page.render


def _auth_render_with_hint(client):
    _real_auth_render(client)
    st.info(LOGIN_HINT)

CANNED_MARKING = {
    "marks": [
        {"label": "1(a)", "mark_type": "M1", "max_marks": 1, "awarded": 1,
         "comment": "Integrates each term, raising the power by one."},
        {"label": "1(a)", "mark_type": "A1", "max_marks": 1, "awarded": 1,
         "comment": "Correct antiderivative $F(x)=x^3+x^2$."},
        {"label": "1(b)", "mark_type": "M1", "max_marks": 1, "awarded": 1,
         "comment": "Substitutes both limits and subtracts."},
        {"label": "1(b)", "mark_type": "A1", "max_marks": 1, "awarded": 0,
         "comment": "Evaluates $F(1)$ as $1$ instead of $2$, so the final value is wrong."},
        {"label": "1(c)", "mark_type": "B1", "max_marks": 1, "awarded": 1,
         "comment": "States the units correctly."},
    ],
    "summary": "Method is secure throughout. The only lost mark is an arithmetic slip when evaluating "
               "$F(1)$. Slow down on substitution and always re-check the lower limit.",
    "topic": "Calculus",
    "key_takeaway": "Substitute the lower limit into every term of the antiderivative and re-check the sum.",
    "handwriting_confidence": 91,
}


# ---- deepseek ---------------------------------------------------------------

def fake_grade(settings, scheme_pages, student_pages, **_kwargs) -> GradingResult:
    time.sleep(1.2)
    return GradingResult.from_model_json(CANNED_MARKING)


# ---- db (same names and signatures as mathexaminer.db) --------------------

def new_client(settings):
    return object()


def sign_in(client, email, password):
    for profile in (TEACHER, *STUDENTS.values()):
        if profile.email == email.strip().lower():
            return profile
    raise DBError("Incorrect email or password. " + LOGIN_HINT)


def sign_up(client, email, password, name, teacher_code=""):
    raise DBError("Sign-up is disabled in demo mode. " + LOGIN_HINT)


def sign_out(client):
    return None


def download(client, bucket, path):
    return STORE.files[path]


def create_assignment(client, teacher_id, title, filename, data, mime):
    path = f"{teacher_id}/{uuid.uuid4()}.bin"
    STORE.files[path] = data
    STORE.assignments.insert(
        0, {"id": str(uuid.uuid4()), "teacher_id": teacher_id, "title": title, "status": "active",
            "mark_scheme_path": path, "created_at": _iso(_now())}
    )


def list_teacher_assignments(client, teacher_id):
    return [dict(a) for a in STORE.assignments if a["teacher_id"] == teacher_id]


def set_assignment_status(client, assignment_id, status):
    for a in STORE.assignments:
        if a["id"] == assignment_id:
            a["status"] = status


def replace_mark_scheme(client, teacher_id, assignment_id, old_path, filename, data, mime):
    path = f"{teacher_id}/{uuid.uuid4()}.bin"
    STORE.files[path] = data
    for a in STORE.assignments:
        if a["id"] == assignment_id:
            a["mark_scheme_path"] = path


def delete_assignment(client, assignment_id, mark_scheme_path):
    STORE.assignments = [a for a in STORE.assignments if a["id"] != assignment_id]
    STORE.submissions = [s for s in STORE.submissions if s["assignment_id"] != assignment_id]


def list_teacher_submissions(client, assignment_ids):
    return [dict(s) for s in STORE.submissions if s["assignment_id"] in assignment_ids]


def resolve_submission(client, submission_id, score, comment):
    for s in STORE.submissions:
        if s["id"] == submission_id:
            s.update(teacher_score_awarded=score, teacher_comment=comment.strip() or None,
                     resolved_at=_iso(_now()), resolved_by=TEACHER.user_id)


def list_open_assignments(client):
    return [dict(a) for a in STORE.assignments if a["status"] == "active"]


def count_gradings_today(client, student_id):
    since = _iso(_now() - timedelta(days=1))
    return sum(1 for s in STORE.submissions if s["student_id"] == student_id and s["created_at"] >= since)


def upload_student_work(client, student_id, files):
    paths = []
    for _name, data, _mime in files:
        path = f"{student_id}/{uuid.uuid4()}.jpg"
        STORE.files[path] = data
        paths.append(path)
    return paths


def save_submission(client, assignment_id, student_id, paths, result):
    assignment = next(a for a in STORE.assignments if a["id"] == assignment_id)
    student = STUDENTS[student_id]
    row = STORE._row(assignment, student, result.score_awarded, result.score_total, result.confidence,
                     result.topic, False, "student", "", _now())
    row.update(student_work_paths=paths, ai_feedback=result.to_markdown(), key_takeaway=result.key_takeaway)
    STORE.submissions.insert(0, row)
    return row["id"]


def flag_submission(client, submission_id, reason):
    for s in STORE.submissions:
        if s["id"] == submission_id:
            s.update(flagged_for_review=True, flag_source="student",
                     flag_reason=reason.strip() or "Student requested manual review.", flagged_at=_iso(_now()))


def auto_flag_submission(client, submission_id, reason):
    for s in STORE.submissions:
        if s["id"] == submission_id and not s["flagged_for_review"]:
            s.update(flagged_for_review=True, flag_source="ai", flag_reason=reason, flagged_at=_iso(_now()))


def list_student_submissions(client, student_id):
    return [dict(s) for s in STORE.submissions if s["student_id"] == student_id]


DB_FUNCTIONS = (
    "new_client sign_in sign_up sign_out download create_assignment list_teacher_assignments "
    "set_assignment_status replace_mark_scheme delete_assignment list_teacher_submissions "
    "resolve_submission list_open_assignments count_gradings_today upload_student_work "
    "save_submission flag_submission auto_flag_submission list_student_submissions"
).split()


def install() -> None:
    """Route the app's db/deepseek calls to this in-memory backend."""
    module = globals()
    for name in DB_FUNCTIONS:
        setattr(db, name, module[name])
    deepseek.grade = fake_grade
    auth_page.render = _auth_render_with_hint
