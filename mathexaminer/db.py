"""Supabase access: auth, database and storage. One client per browser session.

The client holds the signed-in user's JWT in memory, so it must never be shared between
sessions (do not wrap it in ``st.cache_resource``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client, create_client

from .config import BUCKET_SCHEMES, BUCKET_SUBMISSIONS, Settings


class DBError(Exception):
    """A database or auth call failed. ``str(exc)`` is safe to show to users."""


@dataclass(frozen=True)
class Profile:
    user_id: str
    email: str
    name: str
    role: str


def new_client(settings: Settings) -> Client:
    return create_client(settings.supabase_url, settings.supabase_key)


# ------------------------------------------------------------------ auth


def sign_in(client: Client, email: str, password: str) -> Profile:
    try:
        resp = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        raise DBError(_auth_message(exc, "Login failed")) from exc
    user = resp.user
    if user is None:
        raise DBError("Login failed. Please try again.")
    try:
        row = client.table("users").select("name, role").eq("id", user.id).single().execute().data
    except Exception as exc:
        client.auth.sign_out()
        raise DBError(
            "Signed in, but your profile was not found. Was supabase/schema.sql applied?"
        ) from exc
    return Profile(user_id=user.id, email=email, name=row["name"], role=row["role"])


def sign_up(client: Client, email: str, password: str, name: str, teacher_code: str = "") -> None:
    metadata: dict[str, Any] = {"name": name}
    if teacher_code.strip():
        metadata["teacher_code"] = teacher_code.strip()
    try:
        client.auth.sign_up({"email": email, "password": password, "options": {"data": metadata}})
    except Exception as exc:
        raise DBError(_auth_message(exc, "Sign-up failed")) from exc


def sign_out(client: Client) -> None:
    try:
        client.auth.sign_out()
    except Exception:
        pass


def _auth_message(exc: Exception, prefix: str) -> str:
    msg = str(exc).lower()
    if "invalid login" in msg or "invalid_credentials" in msg:
        return "Incorrect email or password."
    if "email not confirmed" in msg:
        return "Please confirm your email address before logging in."
    if "already registered" in msg or "already been registered" in msg:
        return "An account with this email already exists."
    if "password" in msg and ("short" in msg or "least" in msg or "weak" in msg):
        return "That password is too weak. Use at least 8 characters."
    if "rate limit" in msg or "too many" in msg:
        return "Too many attempts. Please wait a minute and try again."
    return f"{prefix}. Please check your details and try again."


# ------------------------------------------------------------------ storage


def _upload(client: Client, bucket: str, folder: str, filename: str, data: bytes, mime: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    ext = "".join(ch for ch in ext if ch.isalnum())[:5] or "bin"
    path = f"{folder}/{uuid.uuid4()}.{ext}"
    try:
        client.storage.from_(bucket).upload(
            path=path, file=data, file_options={"content-type": mime or "application/octet-stream"}
        )
    except Exception as exc:
        raise DBError("Could not upload the file. Please try again.") from exc
    return path


def download(client: Client, bucket: str, path: str) -> bytes:
    try:
        return client.storage.from_(bucket).download(path)
    except Exception as exc:
        raise DBError("Could not download a stored file.") from exc


def _remove(client: Client, bucket: str, paths: list[str]) -> None:
    if paths:
        try:
            client.storage.from_(bucket).remove(paths)
        except Exception:
            pass  # orphaned files are harmless; never block the user-facing action


# ------------------------------------------------------------------ teacher


def create_assignment(client: Client, teacher_id: str, title: str, filename: str, data: bytes, mime: str) -> None:
    path = _upload(client, BUCKET_SCHEMES, teacher_id, filename, data, mime)
    try:
        client.table("assignments").insert(
            {"teacher_id": teacher_id, "title": title, "mark_scheme_path": path}
        ).execute()
    except Exception as exc:
        _remove(client, BUCKET_SCHEMES, [path])
        raise DBError("Could not create the assignment.") from exc


def list_teacher_assignments(client: Client, teacher_id: str) -> list[dict]:
    try:
        return (
            client.table("assignments")
            .select("id, title, status, mark_scheme_path, created_at")
            .eq("teacher_id", teacher_id)
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        raise DBError("Could not load your assignments.") from exc


def set_assignment_status(client: Client, assignment_id: str, status: str) -> None:
    if status not in ("active", "closed"):
        raise ValueError(status)
    try:
        client.table("assignments").update({"status": status}).eq("id", assignment_id).execute()
    except Exception as exc:
        raise DBError("Could not update the assignment.") from exc


def replace_mark_scheme(
    client: Client, teacher_id: str, assignment_id: str, old_path: str, filename: str, data: bytes, mime: str
) -> None:
    path = _upload(client, BUCKET_SCHEMES, teacher_id, filename, data, mime)
    try:
        client.table("assignments").update({"mark_scheme_path": path}).eq("id", assignment_id).execute()
    except Exception as exc:
        _remove(client, BUCKET_SCHEMES, [path])
        raise DBError("Could not replace the mark scheme.") from exc
    _remove(client, BUCKET_SCHEMES, [old_path])


def delete_assignment(client: Client, assignment_id: str, mark_scheme_path: str) -> None:
    try:
        client.table("assignments").delete().eq("id", assignment_id).execute()
    except Exception as exc:
        raise DBError("Could not delete the assignment.") from exc
    _remove(client, BUCKET_SCHEMES, [mark_scheme_path])


def list_teacher_submissions(client: Client, assignment_ids: list[str]) -> list[dict]:
    """All submissions for the given assignments, with student and assignment names attached."""
    if not assignment_ids:
        return []
    try:
        rows = (
            client.table("submissions")
            .select("*, assignments(title), users:student_id(name)")
            .in_("assignment_id", assignment_ids)
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        raise DBError("Could not load submissions.") from exc
    for row in rows:
        row["assignment_title"] = (row.pop("assignments", None) or {}).get("title", "")
        row["student_name"] = (row.pop("users", None) or {}).get("name", "Unknown")
    return rows


def resolve_submission(client: Client, submission_id: str, score: int | None, comment: str) -> None:
    try:
        client.rpc(
            "resolve_submission",
            {"p_submission_id": submission_id, "p_score": score, "p_comment": comment},
        ).execute()
    except Exception as exc:
        raise DBError("Could not save your review. Is the score within the total?") from exc


# ------------------------------------------------------------------ student


def list_open_assignments(client: Client) -> list[dict]:
    try:
        return (
            client.table("assignments")
            .select("id, title, mark_scheme_path, created_at")
            .eq("status", "active")
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        raise DBError("Could not load assignments.") from exc


def count_gradings_today(client: Client, student_id: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    try:
        resp = (
            client.table("submissions")
            .select("id", count="exact")
            .eq("student_id", student_id)
            .gte("created_at", since)
            .execute()
        )
        return resp.count or 0
    except Exception as exc:
        raise DBError("Could not check your daily grading allowance.") from exc


def upload_student_work(client: Client, student_id: str, files: list[tuple[str, bytes, str]]) -> list[str]:
    paths: list[str] = []
    try:
        for filename, data, mime in files:
            paths.append(_upload(client, BUCKET_SUBMISSIONS, student_id, filename, data, mime))
    except DBError:
        _remove(client, BUCKET_SUBMISSIONS, paths)
        raise
    return paths


def save_submission(
    client: Client, assignment_id: str, student_id: str, paths: list[str], result: Any
) -> str:
    row = {
        "assignment_id": assignment_id,
        "student_id": student_id,
        "student_work_paths": paths,
        "ai_feedback": result.to_markdown(),
        "confidence_score": result.confidence,
        "score_awarded": result.score_awarded,
        "score_total": result.score_total,
        "topic_tag": result.topic,
        "key_takeaway": result.key_takeaway,
    }
    try:
        data = client.table("submissions").insert(row).execute().data
        return data[0]["id"]
    except Exception as exc:
        _remove(client, BUCKET_SUBMISSIONS, paths)
        raise DBError("The work was graded but could not be saved. Please try again.") from exc


def flag_submission(client: Client, submission_id: str, reason: str) -> None:
    try:
        client.rpc("flag_submission", {"p_submission_id": submission_id, "p_reason": reason}).execute()
    except Exception as exc:
        raise DBError("Could not flag this submission. Please try again.") from exc


def auto_flag_submission(client: Client, submission_id: str, reason: str) -> None:
    try:
        client.rpc("auto_flag_submission", {"p_submission_id": submission_id, "p_reason": reason}).execute()
    except Exception:
        pass  # advisory only; the student still sees the result


def list_student_submissions(client: Client, student_id: str) -> list[dict]:
    try:
        rows = (
            client.table("submissions")
            .select("*, assignments(title)")
            .eq("student_id", student_id)
            .order("created_at", desc=True)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        raise DBError("Could not load your history.") from exc
    for row in rows:
        row["assignment_title"] = (row.pop("assignments", None) or {}).get("title", "Deleted assignment")
    return rows
