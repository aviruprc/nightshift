"""The rule-based engine must read her inbox for an emailed code instead of parking the job (KB-49).

Oracle HCM / BrassRing hand the application to a six-box 'Confirm Your Identity' step after mailing a code.
The navigator learned this in KB-45; the generic engine (companies) still parked the job as
`needs_you` - "unanswered required: Enter verification code digit 1 of six." - a question for the user whose
only answer is a code the tool can read itself. A code that never arrives is a mail delay (`transient`),
never a personal question and never a closure.
"""
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from apply import agent, engine, errors  # noqa: E402

FAILURES = ROOT / "data" / "failures"
CV = ROOT / "tests" / "fixtures" / "profile" / "Asha_Example_Resume.pdf"   # generated from the fake profile

CODE_PAGE_HTML = """<html><body>
<form onsubmit="return false">
  <h2>Confirm Your Identity</h2>
  <p>The verification code was sent to this email address: asha.example@example.com.
     When you get the code, type the code into the field to confirm your identity and complete your job application.</p>
  <input maxlength="1" aria-label="Enter verification code digit 1 of six." required>
  <input maxlength="1" aria-label="Enter verification code digit 2 of six." required>
  <input maxlength="1" aria-label="Enter verification code digit 3 of six." required>
  <input maxlength="1" aria-label="Enter verification code digit 4 of six." required>
  <input maxlength="1" aria-label="Enter verification code digit 5 of six." required>
  <input maxlength="1" aria-label="Enter verification code digit 6 of six." required>
  <button type="submit">VERIFY</button>
  <button type="button">Send New Code</button>
</form></body></html>"""


ORACLE_LABELS = [f"Enter verification code digit {i} of six." for i in range(1, 7)]


def _oracle_labels():
    """The six labels recorded from the a company Oracle page (1104_014718).

    `data/failures` is EVIDENCE, and daily maintenance prunes it (470 files deleted on), so a test may not
    depend on one old file surviving: the recorded labels are embedded here. When the artifact is still on disk it is
    used instead, which keeps a live cross-check for as long as the evidence exists."""
    p = FAILURES / "corpus_11.json"
    if not p.exists():
        return ORACLE_LABELS
    rec = json.loads(p.read_text(encoding="utf-8"))
    return [e["label"] for e in rec["snap"]["els"] if "code digit" in (e.get("label") or "")]


def test_the_oracle_code_boxes_are_recognised_as_a_code_field():
    labels = _oracle_labels()
    assert len(labels) == 6
    assert engine.CODE_UNKNOWN.search("; ".join(labels))
    assert engine.CODE_UNKNOWN.search("unanswered required: Enter verification code digit 1 of six.")


def test_a_workday_phone_code_or_a_real_question_is_not_a_code_page():
    """'Country Phone Code' is a Workday field answered from her facts: it must keep its needs_you path."""
    for label in ("Country Phone Code", "Enter country phone code", "Passport number",
                  "How many years of experience do you have in ETL development?",
                  "Are you comfortable with a third-party payroll?"):
        assert not engine.CODE_UNKNOWN.search(label), label


def test_a_code_that_never_arrives_is_retried_not_parked():
    for note in ("no code arrived in her inbox", "the page is not asking for an emailed code",
                 "the code arrived but the code boxes could not be filled"):
        d = f"no verification email could be used for this page ({note}); retried later, not a closure"
        assert errors.classify("failed", d) == "transient", note


def test_the_engine_tries_the_emailed_code_before_parking_the_job():
    src = inspect.getsource(engine.apply_to_url)
    assert "otp_tries = 0" in src
    assert "otp_tries < 2 and CODE_UNKNOWN.search(joined)" in src
    assert "_agent.otp_rescue(pg, pg.context" in src
    assert 'res["unknown"] = [u for u in res["unknown"] if not CODE_UNKNOWN.search(u)]' in src
    assert "not a closure" in src
    # the rescue must be tried BEFORE the job is parked as a question for her
    assert src.index("_agent.otp_rescue(") < src.index('detail="unanswered required: " + joined')


@pytest.fixture  # function-scoped: a module-scoped Playwright fixture breaks later sync_playwright calls (AGENTS.md)
def page():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b.new_page()
        b.close()


def test_the_engine_types_the_emailed_code_and_does_not_park_the_job(page, tmp_path, monkeypatch):
    form = tmp_path / "code.html"
    form.write_text(CODE_PAGE_HTML, encoding="utf-8")
    asked = {}

    def fake_otp(ctx, sender_hint, wait_s=120, since=None):
        asked.update(hint=sender_hint, since=since)
        return "123456"

    monkeypatch.setattr(agent.mail, "otp_code", fake_otp)
    monkeypatch.setattr(engine, "CURRENT_JOB", {"id": "t_otp", "company": "American Express", "title": "", "jd": ""})
    r = engine.apply_to_url(page, form.as_uri(), CV, dry_run=True, job_id="t_otp")

    assert asked.get("hint"), "the engine never asked the inbox for a code"
    assert asked.get("since"), "only mail newer than the 'Send New Code' click may be used"
    boxes = page.locator("input[maxlength='1']")
    assert boxes.count() == 6
    assert "".join(boxes.nth(i).input_value() for i in range(6)) == "123456"
    assert r["status"] != "needs_you", r
    assert "verification code digit" not in (r.get("detail") or "").lower()
