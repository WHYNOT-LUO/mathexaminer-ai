"""Small rendering helpers shared by all pages. Every dynamic string is HTML-escaped."""

from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

import streamlit as st

from ..config import LOW_CONFIDENCE_THRESHOLD

_CSS = Path(__file__).with_name("styles.css")


def esc(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def inject_css() -> None:
    st.markdown(f"<style>{_CSS.read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)
    st.markdown('<div class="top-rule"></div>', unsafe_allow_html=True)


def alert(kind: str, message: str) -> None:
    icon = {"error": "⚠", "success": "✓", "info": "ℹ"}.get(kind, "")
    st.markdown(f'<div class="alert alert-{kind}">{icon} {esc(message)}</div>', unsafe_allow_html=True)


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f'<p class="page-title">{esc(title)}</p>', unsafe_allow_html=True)
    st.markdown(f'<p class="page-subtitle">{esc(subtitle)}</p>', unsafe_allow_html=True)


def section_label(text: str) -> None:
    st.markdown(f'<p class="section-label">{esc(text)}</p>', unsafe_allow_html=True)


def empty_state(icon: str, message: str) -> None:
    st.markdown(
        f'<div class="empty-state"><span class="empty-icon">{icon}</span><p>{esc(message)}</p></div>',
        unsafe_allow_html=True,
    )


def welcome_banner(name: str, email: str, role: str) -> None:
    icon, label = ("📋", "Teacher Console") if role == "teacher" else ("🎓", "Student Portal")
    st.markdown(
        f"""<div class="welcome-banner"><div class="wb-left">
        <h2>{icon} {label}</h2><p>Signed in as <strong>{esc(name)}</strong> · {esc(email)}</p></div>
        <span class="role-badge">{esc(role.capitalize())}</span></div>""",
        unsafe_allow_html=True,
    )


def footer(model: str) -> None:
    st.markdown(
        f'<div class="footer">MathExaminer AI · DeepSeek ({esc(model)}) &amp; Supabase<br>'
        "AI feedback is a guide only. Always verify with your teacher.</div>",
        unsafe_allow_html=True,
    )


def pct_color(pct: int) -> str:
    return "#1E6B45" if pct >= 70 else "#92620A" if pct >= 50 else "#B5492A"


def confidence_color(score: int) -> str:
    return "#1E6B45" if score >= 75 else "#92620A" if score >= 50 else "#B5492A"


def format_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return ""


def result_cards(awarded: int, total: int, topic: str, confidence: int, takeaway: str) -> None:
    pct = round(100 * awarded / total) if total else 0
    color = pct_color(pct)
    conf_color = confidence_color(confidence)
    conf_hint = (
        "⚠ Low - consider flagging for teacher review"
        if confidence < LOW_CONFIDENCE_THRESHOLD
        else "✓ Handwriting read clearly"
        if confidence >= 75
        else "Reasonably legible"
    )
    c1, c2, c3 = st.columns([2, 2, 3])
    c1.markdown(
        f"""<div class="result-stat-card" style="border-color:{color}33; background:{color}0d;">
        <div class="result-stat-label">Marks</div>
        <div class="result-stat-value" style="color:{color};">{int(awarded)}<span class="result-stat-denom"> / {int(total)}</span></div>
        <div class="result-stat-pct" style="color:{color};">{pct}%</div></div>""",
        unsafe_allow_html=True,
    )
    c2.markdown(
        f"""<div class="result-stat-card"><div class="result-stat-label">Topic</div>
        <div class="result-topic-badge">{esc(topic)}</div></div>""",
        unsafe_allow_html=True,
    )
    c3.markdown(
        f"""<div class="result-stat-card"><div class="result-stat-label">Handwriting confidence</div>
        <div style="display:flex; align-items:center; gap:0.6rem; margin-top:0.5rem;">
        <div class="conf-bar-wrap"><div class="conf-bar-fill" style="width:{int(confidence)}%; background:{conf_color};"></div></div>
        <span class="conf-score" style="color:{conf_color};">{int(confidence)}%</span></div>
        <div style="font-size:0.7rem; color:var(--muted); margin-top:0.3rem;">{conf_hint}</div></div>""",
        unsafe_allow_html=True,
    )
    if takeaway:
        st.markdown(
            f"""<div class="takeaway-box"><span class="takeaway-label">📌 Key takeaway</span>
            <p class="takeaway-text">{esc(takeaway)}</p></div>""",
            unsafe_allow_html=True,
        )


def override_note(row: dict) -> None:
    """Show the teacher's decision on a submission, if any."""
    if not row.get("resolved_at"):
        return
    score = row.get("teacher_score_awarded")
    parts = ["Reviewed by your teacher."]
    if score is not None:
        parts.append(f"Final mark: {score} / {row.get('score_total', 0)}.")
    if row.get("teacher_comment"):
        parts.append(f"Comment: {row['teacher_comment']}")
    st.markdown(f'<div class="override-note">👩‍🏫 {esc(" ".join(parts))}</div>', unsafe_allow_html=True)
