"""An emailed verification code is not a dead end: her inbox IS readable (a company x2 and a company,, KB-45).

a company / and a company were parked with
"Email verification code (OTP) required; cannot access candidate's email inbox" / "cannot retrieve OTP from email".
The saved screenshot of the a company page is a normal Oracle HCM "Confirm Your Identity" step (six code boxes, VERIFY,
"Send New Code"), and the inbox proves the code arrived: two "Confirm your identity" mails from "a company Human Resources"
at and, and `imap.otp_code('a company ...', since=)` returns a 6-digit code. `apply/mail.py` already had
`otp_code`; what was missing was anyone using it when the MODEL said the inbox was unreachable.
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, errors  # noqa: E402

PAGE = """<!doctype html><meta charset=utf-8><title>Confirm Your Identity</title><body>
<h1>Confirm Your Identity</h1>
<p>The verification code was sent to this email address: asha.example@example.com. When you get the code,
type the code into the field to confirm your identity and complete your job application.</p>
<div id="boxes">
 <input maxlength="1" aria-label="code digit 1"><input maxlength="1" aria-label="code digit 2">
 <input maxlength="1" aria-label="code digit 3"><input maxlength="1" aria-label="code digit 4">
 <input maxlength="1" aria-label="code digit 5"><input maxlength="1" aria-label="code digit 6">
</div>
<button id="verify" type="button">VERIFY</button>
<button id="resend" type="button">Send New Code</button>
<script>document.getElementById('resend').onclick = () => { window.__resent = true; };</script>
</body>"""


@pytest.fixture
def code_pg(tmp_path):
    f = tmp_path / "confirm.html"
    f.write_text(PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        yield pg
        b.close()


def test_the_page_that_asks_for_a_code_is_recognised(code_pg):
    snap = code_pg.evaluate(agent.SNAP_JS)
    assert agent.code_page(code_pg, snap) is True
    code_pg.evaluate("() => document.querySelectorAll('#boxes input').forEach(e => { e.style.display = 'none'; })")
    assert agent.code_page(code_pg, {"text": "Please answer the following questions."}) is False, "an ordinary form is not a code page"


def test_every_code_box_gets_one_digit(code_pg):
    assert agent.fill_code(code_pg, "123456") is True
    got = code_pg.evaluate("() => [...document.querySelectorAll('#boxes input')].map(e => e.value).join('')")
    assert got == "123456", got


def test_the_rescue_asks_the_page_for_a_fresh_code_and_types_it(code_pg, monkeypatch):
    calls = []

    def fake_otp(ctx, hint, wait_s=120, since=None):
        calls.append({"hint": hint, "since": since})
        return "654321"

    monkeypatch.setattr(agent.mail, "otp_code", fake_otp)
    note, requested = agent.otp_rescue(code_pg, None, {"text": code_pg.inner_text("body")}, {"company": "Emerson"})
    assert note.startswith("the emailed code"), note
    assert code_pg.evaluate("() => window.__resent === true"), "the page's own 'Send New Code' was never used: a stale code could be typed"
    assert requested is not None, "the time of the send-code click must be kept: only newer mail may be used"
    assert all(c["since"] is not None for c in calls), calls
    got = code_pg.evaluate("() => [...document.querySelectorAll('#boxes input')].map(e => e.value).join('')")
    assert got == "654321", got


def test_no_code_in_the_inbox_says_exactly_that(code_pg, monkeypatch):
    monkeypatch.setattr(agent.mail, "otp_code", lambda ctx, hint, wait_s=120, since=None: None)
    note, _ = agent.otp_rescue(code_pg, None, {"text": code_pg.inner_text("body")}, {"company": "Emerson"})
    assert note == "no code arrived in her inbox", note


def test_a_missing_code_is_transient_never_our_logic_bug():
    detail = ("navigator: the page asks for an emailed verification code but no code arrived in her inbox; "
              "retried later, not a closure")
    assert errors.classify("failed", detail) == "transient"


def test_the_navigator_uses_the_rescue_before_it_gives_up():
    import inspect
    src = inspect.getsource(agent.navigate)
    assert "otp_runs < 2 and code_page(pg, snap)" in src, "the code page is detected but never rescued"
    assert "otp_rescue(pg, ctx, snap, job" in src, "the rescue exists but the navigator never calls it"
    assert agent.PROMPT.count("cannot access the inbox") == 1, "the prompt must tell the model the inbox is readable"
    assert '"otp"' in agent.PROMPT, "the prompt must still name the otp action"
