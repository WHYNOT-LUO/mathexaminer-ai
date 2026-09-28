"""Student portal: submit work for AI marking, and browse the personal Error Book."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from .. import db, deepseek, images
from ..config import BUCKET_SCHEMES, LOW_CONFIDENCE_THRESHOLD, Settings
from ..models import effective_score
from .components import (
    alert,
    empty_state,
    format_date,
    override_note,
    page_header,
    result_cards,
    section_label,
    takeaway_box,
)

WORK_TYPES = ["png", "jpg", "jpeg", "webp", "pdf"]
SCHEME_MAX_PAGES = 6


def render(client, service, profile, settings: Settings) -> None:
    tab_submit, tab_book = st.tabs(["Submit work", "Error Book"])
    with tab_submit:
        _submit_tab(client, service, profile, settings)
    with tab_book:
        _error_book_tab(client, profile)


@st.cache_data(ttl=900, max_entries=32, show_spinner=False)
def _scheme_pages(_service, path: str) -> list[bytes]:
    # Downloaded with the service client: students cannot read mark schemes themselves.
    return images.to_jpeg_pages(db.download(_service, BUCKET_SCHEMES, path), max_pages=SCHEME_MAX_PAGES)


# ------------------------------------------------------------------ submit


def _submit_tab(client, service, profile, settings: Settings) -> None:
    page_header("Submit work", "Pick an assignment, upload your handwritten solution, get examiner-style feedback.")

    last = st.session_state.get("last_result")
    if last:
        _result_view(client, last)
        return

    try:
        assignments = db.list_open_assignments(client)
        used_today = db.count_gradings_today(service, profile.user_id)
    except db.DBError as exc:
        alert("error", str(exc))
        return
    if not assignments:
        empty_state("📭", "No open assignments yet. Check back soon.")
        return

    section_label("Step 1 - Choose an assignment")
    chosen = st.selectbox(
        "Assignment", assignments, format_func=lambda a: a["title"], label_visibility="collapsed"
    )

    section_label("Step 2 - Upload your work")
    st.caption("Photos or scans (PNG, JPG, WEBP) or a PDF. Up to 4 pages in total. Flat, well-lit photos mark best.")
    nonce = st.session_state.get("upload_nonce", 0)
    files = st.file_uploader(
        "Your work", type=WORK_TYPES, accept_multiple_files=True,
        label_visibility="collapsed", key=f"work_{nonce}",
    )
    remaining = max(0, settings.max_gradings_per_day - used_today)
    st.caption(f"Gradings left in the last 24 hours: {remaining} of {settings.max_gradings_per_day}")

    if files:
        blobs = [f.getvalue() for f in files]
        try:
            pages = images.files_to_jpeg_pages(blobs)
        except images.ImageError as exc:
            alert("error", str(exc))
            return
        cols = st.columns(min(len(pages), 4))
        for col, page in zip(cols, pages, strict=False):
            col.image(page, width="stretch")

        if st.button("⚡ Grade my work", disabled=remaining == 0, key="grade_btn"):
            _grade(client, service, profile, settings, chosen, files, pages)

    if err := st.session_state.pop("grade_error", None):
        alert("error", err)


def _grade(client, service, profile, settings: Settings, assignment: dict, files, pages: list[bytes]) -> None:
    try:
        with st.spinner("Reading the mark scheme and marking your work - this usually takes under a minute…"):
            scheme = _scheme_pages(service, assignment["mark_scheme_path"])
            # Checked again here, not just when the page drew the button: other tabs may have used it up.
            db.reserve_grading(service, profile.user_id, settings.max_gradings_per_day)
            result = deepseek.grade(settings, scheme, pages)
            paths = db.upload_student_work(
                client, profile.user_id, [(f.name, f.getvalue(), f.type) for f in files]
            )
            flag_reason = (
                f"AI handwriting confidence {result.confidence}% is below {LOW_CONFIDENCE_THRESHOLD}%."
                if result.confidence < LOW_CONFIDENCE_THRESHOLD
                else None
            )
            submission_id = db.save_submission(
                service, assignment["id"], profile.user_id, paths, result, auto_flag_reason=flag_reason
            )
            auto_flagged = flag_reason is not None  # saved in the same insert, so this is what the teacher sees
    except (deepseek.GradingError, images.ImageError, db.DBError) as exc:
        st.session_state.grade_error = str(exc)
        st.rerun()
        return

    st.session_state.last_result = {
        "submission_id": submission_id,
        "assignment_title": assignment["title"],
        "result": result,
        "flagged": auto_flagged,
        "auto_flagged": auto_flagged,
    }
    st.rerun()


def _result_view(client, last: dict) -> None:
    result = last["result"]
    section_label(f"Feedback - {last['assignment_title']}")
    result_cards(result.score_awarded, result.score_total, result.topic, result.confidence, result.key_takeaway)
    if last["auto_flagged"]:
        alert("info", "The AI was not sure it read your handwriting correctly, so this was sent to your teacher for a check.")
    st.markdown("---")
    st.markdown(result.to_markdown())

    st.markdown("<br>", unsafe_allow_html=True)
    if last["flagged"]:
        st.success("Flagged for teacher review.")
    else:
        with st.expander("🚩 Disagree with this mark? Ask your teacher to review it"):
            with st.form("dispute_form"):
                reason = st.text_area(
                    "What did the AI miss or misread? (optional)",
                    placeholder='e.g. "It read my 4 as a 9 in step 2."', max_chars=1000,
                )
                if st.form_submit_button("Send to teacher →"):
                    try:
                        db.flag_submission(client, last["submission_id"], reason)
                        last["flagged"] = True
                        st.rerun()
                    except db.DBError as exc:
                        alert("error", str(exc))

    if st.button("← Grade another", key="grade_another"):
        st.session_state.pop("last_result", None)
        st.session_state.upload_nonce = st.session_state.get("upload_nonce", 0) + 1
        st.rerun()


# ------------------------------------------------------------------ error book


def _error_book_tab(client, profile) -> None:
    page_header("Error Book", "Every piece of work you have submitted, with the one lesson from each.")
    try:
        rows = db.list_student_submissions(client, profile.user_id)
    except db.DBError as exc:
        alert("error", str(exc))
        return
    if not rows:
        empty_state("📖", "Nothing here yet. Submit your first piece of work.")
        return

    frame = pd.DataFrame(
        [
            {"Topic": r["topic_tag"], "Percent": round(100 * a / t) if t else 0}
            for r in rows
            for a, t in [effective_score(r)]
        ]
    )
    m1, m2 = st.columns(2)
    m1.metric("Submissions", len(rows))
    m2.metric("Average mark", f"{frame['Percent'].mean():.0f}%")

    by_topic = frame.groupby("Topic", as_index=False).agg(Average=("Percent", "mean"), Attempts=("Percent", "size"))
    if len(by_topic) > 1:
        fig = px.bar(by_topic.sort_values("Average", ascending=False), x="Average", y="Topic", orientation="h",
                     range_x=[0, 100], color_discrete_sequence=["#B5492A"], text="Attempts")
        fig.update_traces(texttemplate="n=%{text}", textposition="inside")
        fig.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=max(180, 42 * len(by_topic)))
        st.caption("Your weakest topics are at the top.")
        st.plotly_chart(fig, width="stretch")

    topics = sorted({r["topic_tag"] for r in rows})
    picked = st.multiselect("Filter by topic", topics)
    for r in rows:
        if picked and r["topic_tag"] not in picked:
            continue
        awarded, total = effective_score(r)
        with st.expander(f"{r['assignment_title']} · {awarded}/{total} · {r['topic_tag']} · {format_date(r['created_at'])}"):
            if r["key_takeaway"]:
                takeaway_box(r["key_takeaway"], key=f"takeaway_{r['id']}")
            override_note(r)
            st.markdown(r["ai_feedback"])
