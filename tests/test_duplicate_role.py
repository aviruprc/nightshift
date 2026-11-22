"""One role is applied to ONCE, even when the prepared queue holds two rows for it.

a company: R800105 and R800100 are the same role, company and city (same fingerprint) and were applied two minutes
apart. The queue is built once, so `already_applied` was evaluated before the first row became 'unconfirmed'. An application
cannot be withdrawn, so the per-row guard is what prevents a duplicate.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import runner  # noqa: E402
from core import db as dbm  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never write test rows into the real applications ledger
    return dbm.DB(tmp_path / "t.db")


def _add(d, url, title="Performance and Analytics Business Analyst, AVP", location="Bangalore"):
    jid, _ = d.upsert_job({"company": "State Street", "title": title, "location": location, "url": url, "source": "board:linkedin"})
    return jid


def _row(d, jid):
    return dict(d.c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone())


def test_the_other_row_of_an_already_sent_role_is_named(db):
    first = _add(db, "https://careers.statestreet.com/global/en/job/STSTGLOBALR800105EXTERNALENGLOBAL/x")
    second = _add(db, "https://careers.statestreet.com/global/en/job/STSTGLOBALR800100EXTERNALENGLOBAL/x")
    db.set_status(first, "unconfirmed", "submitted")
    why = runner.duplicate_role(db, _row(db, second), set())
    assert first in why and "unconfirmed" in why


def test_a_role_handled_earlier_in_this_run_is_not_retried_even_if_the_first_attempt_failed(db):
    first = _add(db, "https://careers.statestreet.com/global/en/job/STSTGLOBALR1/x")
    second = _add(db, "https://careers.statestreet.com/global/en/job/STSTGLOBALR2/x")
    db.set_status(first, "failed", "navigator: too many steps")
    why = runner.duplicate_role(db, _row(db, second), {_row(db, first)["fingerprint"]})
    assert "earlier in this run" in why


def test_a_different_role_is_left_alone(db):
    first = _add(db, "https://careers.statestreet.com/global/en/job/STSTGLOBALR3/x")
    other = _add(db, "https://careers.statestreet.com/global/en/job/STSTGLOBALR4/x", title="Financial Reporting, Associate 2")
    db.set_status(first, "unconfirmed", "submitted")
    assert runner.duplicate_role(db, _row(db, other), set()) == ""
