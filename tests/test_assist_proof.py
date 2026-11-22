"""A CAPTCHA submission made by the user follows the SAME proof standard as the bot's.

The live defect this locks in: job (a company 'Business Analyst - Payments', applied through Greenhouse after she
solved the CAPTCHA) was written as 'applied' with NO proof at all, and a later receipt pass then attached an AmbitionBox mail
'You applied to a company Technologies' - a company-only notice from a different job board - as if it proved that role. A success
page after the human presses Submit is a SEND: it must be 'unconfirmed' with the page proof until an employer receipt names it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import assist  # noqa: E402
from core import db as dbm  # noqa: E402


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never touch the real applications.jsonl
    d = dbm.DB(tmp_path / "t.db")
    jid, _ = d.upsert_job({"source": "board:linkedin", "company": "Capco", "title": "Business Analyst – Payments",
                           "location": "Bengaluru", "url": "https://x.test/1", "apply_url": "https://job-boards.greenhouse.io/capco/jobs/8068889"})
    d.set_status(jid, "needs_you", "CAPTCHA tab is open and pre-filled", needs_you_reason="CAPTCHA tab is open and pre-filled")
    return d, jid


def test_a_captcha_submit_is_unconfirmed_with_a_page_proof_not_applied(tmp_path, monkeypatch):
    d, jid = _db(tmp_path, monkeypatch)
    assist.record_captcha_submit(d, jid, "Thank you for applying! Your application has been submitted.", "C:/shots/x.png")
    row = d.rows("id=?", (jid,))[0]
    assert row["status"] == "unconfirmed", row["status"]          # never 'applied' without a receipt
    assert row["email_proof"] is None                              # nothing claims an employer receipt
    assert (row["page_proof"] or "").startswith("PAGE: submitted by you after the CAPTCHA")
    assert row["applied_at"]                                       # it IS a send: it must count in 'sent today'
    assert not row["needs_you_reason"]


def test_an_employer_receipt_can_still_promote_it(tmp_path, monkeypatch):
    d, jid = _db(tmp_path, monkeypatch)
    assist.record_captcha_submit(d, jid, "Application submitted", "C:/shots/x.png")
    from apply import proof
    job = d.rows("id=?", (jid,))[0]
    receipt = {"from": "Capco Careers", "email": "careers@capco.com", "subject": "Your application for Business Analyst – Payments",
               "snippet": "We have received your application for Business Analyst – Payments.", "links": []}
    assert proof.match_score(job, receipt) > 0
    assert proof.names_this_job(job, receipt) is True


def test_a_closed_captcha_tab_leaves_a_durable_instruction_not_a_dead_one(tmp_path, monkeypatch):
    """The helper's tabs die with the process; 17 parked rows still said 'the tab is open, press Submit' hours later."""
    d, jid = _db(tmp_path, monkeypatch)
    assert assist.park_open_captcha_tabs(d, {jid: object()}) == 1
    row = d.rows("id=?", (jid,))[0]
    assert row["status"] == "needs_you"
    assert "tab is open" not in (row["needs_you_reason"] or "")
    assert "https://job-boards.greenhouse.io/capco/jobs/8068889" in row["needs_you_reason"]
    assert row["needs_you_reason"].startswith("CAPTCHA: open the job and submit it yourself")


def test_parking_never_touches_a_row_that_is_no_longer_waiting(tmp_path, monkeypatch):
    d, jid = _db(tmp_path, monkeypatch)
    d.set_status(jid, "unconfirmed", "submitted by you after the CAPTCHA", needs_you_reason=None)
    assert assist.park_open_captcha_tabs(d, {jid: object()}) == 0
    assert d.rows("id=?", (jid,))[0]["status"] == "unconfirmed"
