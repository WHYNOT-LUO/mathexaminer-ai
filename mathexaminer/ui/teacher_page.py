"""Teacher console: assignments, review queue, analytics."""

from __future__ import annotations

import streamlit as st

from .. import db, images
from ..config import BUCKET_SUBMISSIONS
from ..models import effective_score
from . import analytics
from .components import (
    alert,
    empty_state,
    esc,
    format_date,
    page_header,
    section_label,
)

SCHEME_TYPES = ["png", "jpg", "jpeg", "webp", "pdf"]
SCHEME_MAX_PAGES = 6


def render(client, profile) -> None:
    try:
        assignments = db.list_teacher_assignments(client, profile.user_id)
        submissions = db.list_teacher_submissions(client, [a["id"] for a in assignments])
    except db.DBError as exc:
        alert("error", str(exc))
        return

    pending = [s for s in submissions if s["flagged_for_review"] and not s["resolved_at"]]
    tab_assign, tab_queue, tab_stats = st.tabs(
        ["Assignments", f"Review queue ({len(pending)})" if pending else "Review queue", "Analytics"]
    )
    with tab_assign:
        _assignments_tab(client, profile, assignments, submissions)
    with tab_queue:
        _review_tab(client, pending, submissions)
    with tab_stats:
        analytics.render(submissions)


# ------------------------------------------------------------------ assignments


def _validated_scheme(uploaded) -> bytes:
    """Read an uploaded mark scheme and make sure the grader will be able to open it later."""
    data = uploaded.getvalue()
    images.to_jpeg_pages(data, max_pages=SCHEME_MAX_PAGES)
    return data


def _assignments_tab(client, profile, assignments: list[dict], submissions: list[dict]) -> None:
    page_header("Assignments", "Create assignments and upload the official mark scheme.")

    if notice := st.session_state.pop("teacher_notice", None):
        alert("success", notice)

    with st.expander("＋ New assignment", expanded=not assignments):
        with st.form(f"new_assignment_{st.session_state.get('new_asgn_nonce', 0)}"):
            title = st.text_input("Title", placeholder="Integration Test 1 - June 2024", max_chars=200)
            scheme = st.file_uploader(
                f"Mark scheme (PNG, JPG, WEBP or PDF up to {SCHEME_MAX_PAGES} pages)", type=SCHEME_TYPES
            )
            if st.form_submit_button("Create assignment →"):
                if not title.strip():
                    alert("error", "Please enter a title.")
                elif scheme is None:
                    alert("error", "Please upload the mark scheme.")
                else:
                    try:
                        data = _validated_scheme(scheme)
                        with st.spinner("Uploading…"):
                            db.create_assignment(
                                client, profile.user_id, title.strip(), scheme.name, data, scheme.type
                            )
                        st.session_state.teacher_notice = f'"{title.strip()}" created.'
                        st.session_state.new_asgn_nonce = st.session_state.get("new_asgn_nonce", 0) + 1
                        st.rerun()
                    except (images.ImageError, db.DBError) as exc:
                        alert("error", str(exc))

    section_label("Your assignments")
    if not assignments:
        empty_state("📭", "No assignments yet. Create one above.")
        return

    counts: dict[str, int] = {}
    for s in submissions:
        counts[s["assignment_id"]] = counts.get(s["assignment_id"], 0) + 1

    for a in assignments:
        closed = a["status"] == "closed"
        badge = '<span class="asgn-badge badge-closed">Closed</span>' if closed else '<span class="asgn-badge badge-ok">Active</span>'
        st.markdown(
            f"""<div class="assignment-row"><div>
            <div class="asgn-title-t">📋 {esc(a['title'])}</div>
            <div class="asgn-url-t">{counts.get(a['id'], 0)} submission(s) · created {esc(format_date(a['created_at']))}</div>
            </div>{badge}</div>""",
            unsafe_allow_html=True,
        )
        with st.expander("Manage"):
            _manage_assignment(client, profile, a, counts.get(a["id"], 0))


def _manage_assignment(client, profile, a: dict, n_submissions: int) -> None:
    closed = a["status"] == "closed"
    key = a["id"]
    if st.button("Reopen for students" if closed else "Close (stop new submissions)", key=f"status_{key}"):
        try:
            db.set_assignment_status(client, a["id"], "active" if closed else "closed")
            st.rerun()
        except db.DBError as exc:
            alert("error", str(exc))

    st.markdown("**Replace mark scheme**")
    new_file = st.file_uploader("New mark scheme", type=SCHEME_TYPES, key=f"newscheme_{key}", label_visibility="collapsed")
    if new_file and st.button("Replace", key=f"replace_{key}"):
        try:
            data = _validated_scheme(new_file)
            db.replace_mark_scheme(
                client, profile.user_id, a["id"], a["mark_scheme_path"], new_file.name, data, new_file.type
            )
            st.session_state.teacher_notice = f'Mark scheme for "{a["title"]}" replaced.'
            st.rerun()
        except (images.ImageError, db.DBError) as exc:
            alert("error", str(exc))

    st.markdown("**Delete**")
    confirm = st.checkbox(
        f"Permanently delete this assignment and its {n_submissions} submission(s)", key=f"confirm_del_{key}"
    )
    if st.button("Delete assignment", key=f"del_{key}", disabled=not confirm):
        try:
            db.delete_assignment(client, a["id"], a["mark_scheme_path"])
            st.session_state.teacher_notice = f'"{a["title"]}" deleted.'
            st.rerun()
        except db.DBError as exc:
            alert("error", str(exc))


# ------------------------------------------------------------------ review queue


def _review_tab(client, pending: list[dict], submissions: list[dict]) -> None:
    page_header("Review queue", "Submissions flagged by students, or by the AI when it could not read the handwriting.")

    if notice := st.session_state.pop("review_notice", None):
        alert("success", notice)

    if not pending:
        empty_state("✅", "Nothing waiting for review.")
    for s in pending:
        source = "Student flagged" if s["flag_source"] == "student" else "Flagged by AI (low confidence)"
        label = f"{s['student_name']} · {s['assignment_title']} · AI {s['score_awarded']}/{s['score_total']} · {source}"
        with st.expander(label):
            _review_card(client, s)

    resolved = [s for s in submissions if s["resolved_at"]]
    if resolved:
        with st.expander(f"Resolved ({len(resolved)})"):
            for s in resolved:
                awarded, total = effective_score(s)
                st.markdown(
                    f"**{esc(s['student_name'])}** · {esc(s['assignment_title'])} · final {awarded}/{total}"
                    + (f" · _{esc(s['teacher_comment'])}_" if s.get("teacher_comment") else "")
                )


def _review_card(client, s: dict) -> None:
    if s.get("flag_reason"):
        alert("info", f"Reason: {s['flag_reason']}")
    st.caption(f"AI confidence in handwriting: {s['confidence_score']}%")
    st.markdown(s["ai_feedback"])

    if st.toggle("Show student work", key=f"show_{s['id']}"):
        for path in s["student_work_paths"]:
            try:
                for page in images.to_jpeg_pages(db.download(client, BUCKET_SUBMISSIONS, path)):
                    st.image(page, use_container_width=True)
            except (db.DBError, images.ImageError) as exc:
                alert("error", str(exc))

    with st.form(f"resolve_{s['id']}"):
        score = st.number_input(
            "Final mark", min_value=0, max_value=max(s["score_total"], 0), value=min(s["score_awarded"], s["score_total"]), step=1
        )
        comment = st.text_area("Comment to the student (optional)", max_chars=2000)
        if st.form_submit_button("Save review →"):
            try:
                db.resolve_submission(client, s["id"], int(score), comment)
                st.session_state.review_notice = "Review saved."
                st.rerun()
            except db.DBError as exc:
                alert("error", str(exc))
