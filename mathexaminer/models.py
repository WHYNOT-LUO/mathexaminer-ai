"""Structured grading result. Totals are computed here, never trusted from the model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TOPICS = (
    "Pure Math",
    "Calculus",
    "Algebra",
    "Trigonometry",
    "Mechanics",
    "Statistics",
    "Probability",
    "Vectors",
    "Series",
    "Other",
)


class ResultParseError(ValueError):
    """The model returned JSON that does not describe a usable marking."""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):  # OverflowError: inf / 1e999 from the model's JSON
        return default


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


@dataclass(frozen=True)
class MarkItem:
    label: str
    mark_type: str
    max_marks: int
    awarded: int
    comment: str


@dataclass(frozen=True)
class GradingResult:
    marks: tuple[MarkItem, ...]
    summary: str
    topic: str
    key_takeaway: str
    confidence: int

    MAX_MARKS_PER_ITEM = 20  # one mark-scheme entry never carries more; keeps totals sane and DB-safe

    @property
    def score_awarded(self) -> int:
        return sum(m.awarded for m in self.marks)

    @property
    def score_total(self) -> int:
        return sum(m.max_marks for m in self.marks)

    @property
    def percent(self) -> int:
        return round(100 * self.score_awarded / self.score_total) if self.score_total else 0

    @classmethod
    def from_model_json(cls, data: Any) -> GradingResult:
        if not isinstance(data, dict):
            raise ResultParseError("Model output is not a JSON object.")
        raw_marks = data.get("marks")
        if not isinstance(raw_marks, list) or not raw_marks:
            raise ResultParseError("Model output contains no marks.")

        items: list[MarkItem] = []
        for raw in raw_marks:
            if not isinstance(raw, dict):
                continue
            max_marks = min(max(0, _as_int(raw.get("max_marks"), 1)), cls.MAX_MARKS_PER_ITEM)
            awarded = min(max(0, _as_int(raw.get("awarded"))), max_marks)
            items.append(
                MarkItem(
                    label=_clean(raw.get("label"), 40),
                    mark_type=_clean(raw.get("mark_type"), 12),
                    max_marks=max_marks,
                    awarded=awarded,
                    comment=_clean(raw.get("comment"), 600),
                )
            )
        if not items or sum(m.max_marks for m in items) == 0:
            raise ResultParseError("Model output contains no markable steps.")

        topic = _clean(data.get("topic"), 40)
        if topic not in TOPICS:
            topic = "Other"
        return cls(
            marks=tuple(items),
            summary=_clean(data.get("summary"), 1200),
            topic=topic,
            key_takeaway=_clean(data.get("key_takeaway"), 400),
            confidence=min(100, max(0, _as_int(data.get("handwriting_confidence"), 50))),
        )

    def to_markdown(self) -> str:
        lines = ["## Step-by-Step Marking", ""]
        for m in self.marks:
            if m.max_marks == 0:
                icon = "⚪"  # a zero-mark entry: nothing to earn, so neither earned nor lost
            elif m.awarded == m.max_marks:
                icon = "✅"
            elif m.awarded == 0:
                icon = "❌"
            else:
                icon = "🟡"
            tag = " ".join(part for part in (m.label, m.mark_type) if part)
            lines.append(f"- {icon} **{tag}** ({m.awarded}/{m.max_marks}) — {m.comment}")
        lines += [
            "",
            "## Score",
            "",
            f"**{self.score_awarded} / {self.score_total} marks**",
            "",
            "## Examiner's Summary",
            "",
            self.summary,
        ]
        return "\n".join(lines)


def effective_score(row: dict) -> tuple[int, int]:
    """(awarded, total) for a submission row; a teacher override wins over the AI mark."""
    total = _as_int(row.get("score_total"))
    override = row.get("teacher_score_awarded")
    awarded = _as_int(override) if override is not None else _as_int(row.get("score_awarded"))
    return awarded, total
