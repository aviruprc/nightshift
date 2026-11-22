"""The ops failure-class grouping must read the ONE taxonomy (apply/errors.py).

Real bug this names: `runner.handle_failure` has always written a bare `llm_down` string while
the registry's canonical key is `llm_unavailable`. Grouping by the raw column would show the same
class twice, and a later rename would orphan history. Legacy strings must merge, and an unknown
stored value must still be shown as stored (never silently re-bucketed).
"""
import sqlite3

from track import live

SCHEMA = """
CREATE TABLE jobs(id TEXT PRIMARY KEY, status TEXT, fail_class TEXT);
CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, job_id TEXT, kind TEXT, detail TEXT);
"""


def make_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def add(c, jid, status, fail_class):
    c.execute("INSERT INTO jobs(id,status,fail_class) VALUES(?,?,?)", (jid, status, fail_class))


def test_legacy_llm_down_rows_merge_into_the_canonical_bucket():
    c = make_db()
    add(c, "a", "failed", "llm_down")          # what runner.py writes today
    add(c, "b", "failed", "llm_unavailable")   # the registry key
    add(c, "c", "failed", "logic")
    classes = {row["cls"]: row for row in live.failure_classes(c)}
    assert "llm_down" not in classes
    assert classes["llm_unavailable"]["n"] == 2
    assert classes["llm_unavailable"]["label"] == "LLM unavailable"
    assert classes["logic"]["n"] == 1


def test_an_unknown_stored_class_is_shown_exactly_as_stored():
    c = make_db()
    add(c, "a", "failed", "some_future_class")
    classes = {row["cls"]: row for row in live.failure_classes(c)}
    assert classes["some_future_class"]["n"] == 1
    assert classes["some_future_class"]["label"] == "some_future_class"


def test_non_failure_statuses_are_still_excluded():
    c = make_db()
    add(c, "a", "candidate", "logic")
    add(c, "b", "needs_you", "captcha")
    assert [row["cls"] for row in live.failure_classes(c)] == ["captcha"]
