"""A queue is built once; a row that changed under the worker must not be attempted (a company)."""
from apply import runner


class FakeDB:
    def __init__(self, row):
        self.row = row

    def rows(self, where, params=()):
        return [self.row] if self.row is not None else []


def test_a_candidate_is_attempted():
    assert runner.still_queued(FakeDB({"id": "a", "status": "candidate", "next_attempt_at": None}), {"id": "a"})


def test_submitted_parked_or_closed_rows_are_left_alone():
    for st in ("unconfirmed", "applied", "needs_you", "closed", "skipped", "already_applied"):
        assert not runner.still_queued(FakeDB({"id": "a", "status": st, "next_attempt_at": None}), {"id": "a"}), st


def test_a_retry_time_in_the_future_is_respected():
    assert not runner.still_queued(FakeDB({"id": "a", "status": "candidate", "next_attempt_at": "2999-01-01T00:00:00"}), {"id": "a"})


def test_a_row_the_database_no_longer_holds_is_never_attempted():
    assert not runner.still_queued(FakeDB(None), {"id": "gone"})


def test_a_job_with_no_fetchable_description_steps_out_of_the_front_of_the_line(tmp_path):
    import sqlite3
    from datetime import datetime
    class D:
        c = sqlite3.connect(":memory:")
    D.c.execute("CREATE TABLE jobs (id TEXT, next_attempt_at TEXT)")
    D.c.execute("INSERT INTO jobs VALUES ('a', NULL)")
    runner.defer_no_description(D, {"id": "a"}, hours=6)
    when = D.c.execute("SELECT next_attempt_at FROM jobs WHERE id='a'").fetchone()[0]
    assert when and 5.5 * 3600 < (datetime.fromisoformat(when) - datetime.now()).total_seconds() < 6.5 * 3600
