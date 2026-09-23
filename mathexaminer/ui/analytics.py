"""Teacher analytics: does the AI's confidence line up with class performance?"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from ..config import LOW_CONFIDENCE_THRESHOLD
from ..models import effective_score
from .components import empty_state, page_header


def to_frame(submissions: list[dict]) -> pd.DataFrame:
    rows = []
    for s in submissions:
        awarded, total = effective_score(s)
        if total <= 0:
            continue
        rows.append(
            {
                "Student": s["student_name"],
                "Assignment": s["assignment_title"],
                "Topic": s["topic_tag"],
                "Percent": round(100 * awarded / total, 1),
                "AI percent": round(100 * s["score_awarded"] / total, 1),
                "AI confidence": s["confidence_score"],
                "Flagged": bool(s["flagged_for_review"]),
                "Resolved": bool(s["resolved_at"]),
                "Overridden": s.get("teacher_score_awarded") is not None
                and s["teacher_score_awarded"] != s["score_awarded"],
            }
        )
    return pd.DataFrame(rows)


def render(submissions: list[dict]) -> None:
    page_header("Class analytics", "Spot class-wide weak topics and cases where the AI may be unreliable.")
    df = to_frame(submissions)
    if df.empty:
        empty_state("📊", "No graded submissions yet.")
        return

    open_flags = int(((df["Flagged"]) & (~df["Resolved"])).sum())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Submissions", len(df))
    m2.metric("Average mark", f"{df['Percent'].mean():.0f}%")
    m3.metric("Mean AI confidence", f"{df['AI confidence'].mean():.0f}%")
    m4.metric("Awaiting review", open_flags)

    st.markdown("**AI confidence vs. student mark**")
    st.caption(
        "Each dot is one submission. Low-confidence dots (left of the dashed line) deserve a human look; "
        "high-confidence dots with a very low mark can reveal a systematic marking problem."
    )
    fig = px.scatter(
        df,
        x="AI confidence",
        y="Percent",
        color="Assignment",
        hover_data=["Student", "Topic", "AI percent"],
        range_x=[-2, 102],
        range_y=[-5, 105],
    )
    fig.add_vline(x=LOW_CONFIDENCE_THRESHOLD, line_dash="dash", line_color="#B5492A")
    fig.update_layout(
        margin=dict(l=0, r=0, t=10, b=0), height=400, legend_title_text="",
        legend=dict(orientation="h", yanchor="top", y=-0.2, xanchor="left", x=0),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Average mark by topic**")
    by_topic = df.groupby("Topic", as_index=False).agg(Average=("Percent", "mean"), Submissions=("Percent", "size"))
    fig2 = px.bar(by_topic.sort_values("Average", ascending=False), x="Average", y="Topic", orientation="h", text="Submissions",
                  range_x=[0, 100], color_discrete_sequence=["#1B3A6B"])
    fig2.update_traces(texttemplate="n=%{text}", textposition="inside")
    fig2.update_layout(margin=dict(l=0, r=0, t=10, b=0), height=max(200, 45 * len(by_topic)))
    st.plotly_chart(fig2, use_container_width=True)
