"""A job found today is FRESH, not 99 days old (KB-74/75).

`fresh` read `(age_days(...) or 99) <= 7`. age_days returns 0.0 for a posting seen seconds ago - and 0.0 is falsy - so
every freshly discovered job was read as 99 days old and dropped from the queue. Worse, `parse_dt` tagged a naive
timestamp as UTC although `dbm.now` writes the machine's local clock (IST +), so a row created seconds ago was
future-dated and clamped to exactly 0.0 - the applier ran with an empty queue while fresh rows sat in the database.
`age_or` keeps the "unknown age sorts last / is dropped" intent; 0 stays 0.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import runner  # noqa: E402
from core import db as dbm  # noqa: E402
from rank import ghost  # noqa: E402


def test_a_row_written_by_dbm_now_is_zero_days_old_not_future_dated():
    a = ghost.age_days(None, dbm.now())
    assert a is not None and 0.0 <= a < 0.01, a  # local clock, not "+ into the future"


def test_age_or_keeps_zero_and_uses_the_default_only_when_unknown():
    assert ghost.age_or(None, dbm.now()) < 0.01
    assert ghost.age_or(None, None) == 99
    assert ghost.age_or("2026-10-01", None) > 7


def test_fresh_keeps_a_job_discovered_today():
    assert runner.fresh([{"posted_at": None, "first_seen": dbm.now()}]) != []
    assert runner.fresh([{"posted_at": dbm.now()[:10], "first_seen": None}]) != []


def test_fresh_still_drops_stale_and_unknown_jobs():
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    assert runner.fresh([{"posted_at": old, "first_seen": old}]) == []
    assert runner.fresh([{"posted_at": None, "first_seen": None}]) == []


def test_the_buggy_idiom_is_not_left_anywhere_in_the_queue_or_dashboard():
    for rel in ("apply/runner.py", "track/live.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert 'age_days(r["posted_at"], r["first_seen"]) or 99' not in src, rel


def test_a_returnship_is_never_aged_out_by_the_freshness_cut():
    from apply import runner
    old = {"family": "returnship", "posted_at": "2026-09-18T17:21:32+00:00", "first_seen": "2026-11-10T12:13:05"}
    plain = {"family": "data_bi", "posted_at": "2026-09-18T17:21:32+00:00", "first_seen": "2026-09-18T12:13:05"}
    assert runner.fresh([old, plain]) == [old]


def test_a_company_on_the_do_not_apply_list_is_recognised(tmp_path, monkeypatch):
    import json
    from apply import runner
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"skip_companies": ["mufg"]}), encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert runner.skipped_company({"company": "MUFG Bank, Ltd."}) and not runner.skipped_company({"company": "Goldman Sachs"})
