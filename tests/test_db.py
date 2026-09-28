"""db.py against a tiny in-memory stand-in for the supabase-py client (tables + storage)."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from mathexaminer import db
from mathexaminer.config import BUCKET_SCHEMES, BUCKET_SUBMISSIONS
from mathexaminer.models import GradingResult


class _Query:
    def __init__(self, fake, table):
        self.fake, self.table = fake, table
        self.filters, self.op, self.payload, self.count = [], "select", None, None

    def select(self, cols="*", count=None):
        self.fake.selects.append((self.table, cols))
        self.count = count
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, values):
        self.op, self.payload = "update", values
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r: r.get(col) >= val)
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.fake.tables.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": f"{self.table}-{len(rows) + 1}", "created_at": _now(), **self.payload}
            rows.append(row)
            return SimpleNamespace(data=[row], count=None)
        hits = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            self.fake.tables[self.table] = [r for r in rows if r not in hits]
        elif self.op == "update":
            for r in hits:
                r.update(self.payload)
        return SimpleNamespace(data=[dict(r) for r in hits], count=len(hits) if self.count else None)


class _Bucket:
    def __init__(self, fake, name):
        self.fake, self.name = fake, name

    def remove(self, paths):
        self.fake.removed.setdefault(self.name, []).extend(paths)


class FakeSupabase:
    def __init__(self, **tables):
        self.tables = {k: list(v) for k, v in tables.items()}
        self.removed: dict[str, list[str]] = {}
        self.selects: list[tuple[str, str]] = []
        self.storage = SimpleNamespace(from_=lambda name: _Bucket(self, name))

    def table(self, name):
        return _Query(self, name)


def _now():
    return datetime.now(timezone.utc).isoformat()


def result(confidence=90):
    return GradingResult.from_model_json({
        "marks": [{"label": "1", "mark_type": "M1", "max_marks": 2, "awarded": 1, "comment": "ok"}],
        "summary": "s", "topic": "Calculus", "key_takeaway": "k", "handwriting_confidence": confidence,
    })


# ---------------------------------------------------------------- daily cap


def test_reserve_grading_counts_attempts_and_refuses_over_the_cap():
    service = FakeSupabase()
    db.reserve_grading(service, "stu", cap=2)
    db.reserve_grading(service, "stu", cap=2)
    with pytest.raises(db.DBError, match="2 gradings"):
        db.reserve_grading(service, "stu", cap=2)
    assert db.count_gradings_today(service, "stu") == 2  # the refused attempt was taken back


def test_reserve_grading_ignores_old_attempts_and_other_students():
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    service = FakeSupabase(grading_attempts=[{"student_id": "stu", "created_at": old},
                                             {"student_id": "other", "created_at": _now()}])
    db.reserve_grading(service, "stu", cap=1)
    assert db.count_gradings_today(service, "stu") == 1


# ---------------------------------------------------------------- saving


def test_save_submission_records_the_ai_flag_in_the_same_insert():
    service = FakeSupabase(assignments=[{"id": "a1", "status": "active"}])
    sid = db.save_submission(service, "a1", "stu", ["stu/x.jpg"], result(40), auto_flag_reason="low confidence")
    (row,) = service.tables["submissions"]
    assert row["id"] == sid
    assert (row["flagged_for_review"], row["flag_source"], row["flag_reason"]) == (True, "ai", "low confidence")
    assert (row["student_id"], row["score_awarded"], row["score_total"]) == ("stu", 1, 2)


def test_save_submission_without_flag_leaves_it_unflagged():
    service = FakeSupabase(assignments=[{"id": "a1", "status": "active"}])
    db.save_submission(service, "a1", "stu", [], result())
    assert service.tables["submissions"][0].get("flagged_for_review") is not True


@pytest.mark.parametrize("assignments", [[], [{"id": "a1", "status": "closed"}]])
def test_save_submission_refuses_closed_or_missing_assignments(assignments):
    service = FakeSupabase(assignments=assignments)
    with pytest.raises(db.DBError, match="no longer open"):
        db.save_submission(service, "a1", "stu", ["stu/x.jpg"], result())
    assert "submissions" not in service.tables or not service.tables["submissions"]
    assert service.removed[BUCKET_SUBMISSIONS] == ["stu/x.jpg"]  # the uploaded work is cleaned up


# ---------------------------------------------------------------- deleting


def test_delete_assignment_also_removes_the_students_files():
    teacher = FakeSupabase(
        assignments=[{"id": "a1"}, {"id": "a2"}],
        submissions=[{"assignment_id": "a1", "student_work_paths": ["s1/p.jpg", "s1/q.jpg"]},
                     {"assignment_id": "a1", "student_work_paths": ["s2/r.pdf"]},
                     {"assignment_id": "a2", "student_work_paths": ["s3/keep.jpg"]}],
    )
    service = FakeSupabase()
    db.delete_assignment(teacher, service, "a1", "t/scheme.pdf")
    assert [a["id"] for a in teacher.tables["assignments"]] == ["a2"]
    assert teacher.removed[BUCKET_SCHEMES] == ["t/scheme.pdf"]
    assert sorted(service.removed[BUCKET_SUBMISSIONS]) == ["s1/p.jpg", "s1/q.jpg", "s2/r.pdf"]


# ---------------------------------------------------------------- teacher queries


def test_teacher_list_leaves_out_feedback_and_fetches_it_on_demand():
    client = FakeSupabase(submissions=[{"id": "s1", "assignment_id": "a1", "ai_feedback": "long text"}])
    (row,) = db.list_teacher_submissions(client, ["a1"])
    (_, cols), = [sel for sel in client.selects if sel[0] == "submissions"]
    assert "ai_feedback" not in cols and "*" not in cols
    assert row["student_name"] == "Unknown" and row["assignment_title"] == ""
    assert db.submission_feedback(client, ["s1"]) == {"s1": "long text"}
    assert db.submission_feedback(client, []) == {}
