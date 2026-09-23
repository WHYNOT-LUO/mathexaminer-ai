import pytest

from mathexaminer.models import GradingResult, ResultParseError, effective_score


def payload(**over):
    base = {
        "marks": [
            {"label": "1(a)", "mark_type": "M1", "max_marks": 1, "comment": "Correct method", "awarded": 1},
            {"label": "1(a)", "mark_type": "A1", "max_marks": 1, "comment": "Slip in sign", "awarded": 0},
            {"label": "1(b)", "mark_type": "B2", "max_marks": 2, "comment": "Half credit", "awarded": 1},
        ],
        "summary": "Good method, careless sign error.",
        "topic": "Calculus",
        "key_takeaway": "Check signs when differentiating.",
        "handwriting_confidence": 82,
    }
    base.update(over)
    return base


def test_totals_are_computed_from_items_not_trusted():
    r = GradingResult.from_model_json(payload(score_awarded=99, score_total=1))
    assert (r.score_awarded, r.score_total, r.percent) == (2, 4, 50)


def test_awarded_is_clamped_to_max_marks():
    data = payload()
    data["marks"][0]["awarded"] = 7
    data["marks"][1]["awarded"] = -3
    r = GradingResult.from_model_json(data)
    assert [m.awarded for m in r.marks] == [1, 0, 1]


def test_unknown_topic_falls_back_to_other():
    assert GradingResult.from_model_json(payload(topic="Astrology")).topic == "Other"


def test_confidence_clamped_and_defaulted():
    assert GradingResult.from_model_json(payload(handwriting_confidence=250)).confidence == 100
    assert GradingResult.from_model_json(payload(handwriting_confidence="abc")).confidence == 50


def test_string_numbers_accepted():
    data = payload()
    data["marks"][0]["max_marks"] = "2"
    data["marks"][0]["awarded"] = "1"
    assert GradingResult.from_model_json(data).score_total == 5


@pytest.mark.parametrize("bad", [None, [], "x", {"marks": []}, {"marks": "no"}, {"marks": [{"max_marks": 0}]}])
def test_unusable_output_raises(bad):
    with pytest.raises(ResultParseError):
        GradingResult.from_model_json(bad)


def test_markdown_has_icons_and_score():
    md = GradingResult.from_model_json(payload()).to_markdown()
    assert "✅ **1(a) M1** (1/1)" in md
    assert "❌ **1(a) A1** (0/1)" in md
    assert "🟡 **1(b) B2** (1/2)" in md
    assert "**2 / 4 marks**" in md


def test_effective_score_prefers_teacher_override():
    row = {"score_awarded": 3, "score_total": 10, "teacher_score_awarded": 8}
    assert effective_score(row) == (8, 10)
    assert effective_score({**row, "teacher_score_awarded": None}) == (3, 10)
    assert effective_score({**row, "teacher_score_awarded": 0}) == (0, 10)
