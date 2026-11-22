"""KB-23 step 2: a hand-off URL captured from the "Apply on company site" control must be stored and re-queued.

Before this, every hand-off ended at `deferred_board` with a note and was never seen again: 164 real jobs sat there while the
Workday / Oracle adapters never got a chance. The URL may only come from the control itself (see
`tests/test_naukri_company_site.py`); this file pins what the runner does with it.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import runner  # noqa: E402
from core import db as dbm  # noqa: E402

WD = "https://acme.wd5.myworkdayjobs.com/en-US/Acme/job/Bangalore/Data-Analyst_R123"


@pytest.fixture
def row(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never write test rows into the real applications ledger
    d = dbm.DB(tmp_path / "t.db")
    jid, _ = d.upsert_job({"company": "Acme", "title": "Data Analyst", "url": "https://www.naukri.com/job-listings-x",
                           "source": "board:naukri"})
    r = dict(d.c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())
    return d, r


def _now(d, jid):
    return dict(d.c.execute("SELECT status, apply_url, status_detail FROM jobs WHERE id=?", (jid,)).fetchone())


def test_a_captured_workday_target_is_stored_and_requeued(row):
    d, r = row
    out = runner.route_board_direct(d, r, {"status": "board_direct", "detail": f"Naukri sends this to the company site (acme.wd5) -> {WD}",
                                           "apply_url": WD})
    cur = _now(d, r["id"])
    assert out == "routed"
    assert cur["status"] == "candidate" and cur["apply_url"] == WD
    assert "hand-off" in cur["status_detail"]


def test_without_a_target_the_job_stays_deferred(row):
    d, r = row
    out = runner.route_board_direct(d, r, {"status": "board_direct", "detail": "Naukri sends this to the company site"})
    cur = _now(d, r["id"])
    assert out == "deferred" and cur["status"] == "deferred_board"
    assert cur["apply_url"] == "https://www.naukri.com/job-listings-x"  # nothing invented


def test_the_workday_skipped_placeholder_is_never_stored_as_a_target(row):
    d, r = row
    out = runner.route_board_direct(d, r, {"status": "board_direct", "detail": "Workday skipped in this run (flow still being tested)",
                                           "apply_url": WD})
    assert out == "skipped" and _now(d, r["id"])["apply_url"] == "https://www.naukri.com/job-listings-x"
