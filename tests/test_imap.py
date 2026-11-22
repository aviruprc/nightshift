"""IMAP mail layer on a fake server: parsing, OTP extraction, verification links, receipt rows. No network, no real credentials."""
import sys
from email.message import EmailMessage
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import proof  # noqa: E402
from core import imap  # noqa: E402


def mail(subject, body, sender="Acme Careers <noreply@acme.myworkday.com>", html=None, minutes_ago: float | None = None):
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, "me@x.com", subject
    if minutes_ago is not None:
        m["Date"] = format_datetime(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
    m.set_content(body)
    if html:
        m.add_alternative(html, subtype="html")
    return m.as_bytes()


class FakeIMAP:
    inbox: list[bytes] = []

    def __init__(self, host):
        pass

    def login(self, u, p):
        return "OK", []

    def select(self, box, readonly=True):
        return "OK", []

    def search(self, charset, *crit):
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.inbox)))]

    def fetch(self, i, spec):
        return "OK", [(b"1 (BODY[] {n})", self.inbox[int(i) - 1])]

    def logout(self):
        return "OK", []


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(imap, "password", lambda: "app-password")


def test_parse_decodes_subject_sender_and_html_body():
    FakeIMAP.inbox = [mail("Thank you for applying to Genpact", "plain", html="<p>We <b>received</b> your application for <i>Analyst</i></p>")]
    m = imap.search("anything")[0]
    assert m["subject"] == "Thank you for applying to Genpact" and m["email"] == "noreply@acme.myworkday.com" and m["from"] == "Acme Careers"
    assert "plain" in m["body"]


def test_otp_code_prefers_the_number_next_to_the_word_code():
    FakeIMAP.inbox = [mail("Your verification code", "Hi Asha, your verification code is 482913. It expires in 10 minutes. (c) 2026 Oracle", minutes_ago=1)]   # codes older than 10 minutes are refused
    assert imap.otp_code("oracle", wait_s=0) == "482913"


def test_otp_ignores_years_and_returns_none_without_mail():
    FakeIMAP.inbox = [mail("Welcome", "Copyright 2026. Join us in 2025")]
    assert imap.otp_code("acme", wait_s=0) is None
    FakeIMAP.inbox = []
    assert imap.otp_code("acme", wait_s=0) is None


def test_verification_link_picks_the_verify_link_not_unsubscribe_or_google():
    FakeIMAP.inbox = [mail("Verify your email", "x", html='<a href="https://acme.example.com/unsubscribe?x=1">u</a><a href="https://www.google.com/x">g</a>'
                                                          '<a href="https://acme.example.com/verify?token=abc">Verify</a>')]
    assert imap.verification_link("acme", wait_s=0, allowed=("acme.example.com",)) == "https://acme.example.com/verify?token=abc"


def test_receipts_come_back_in_the_shape_the_matcher_expects():
    FakeIMAP.inbox = [mail("Thank You for Applying to 10079409-WD Senior Analyst- Risk Reporting at MUFG", "Dear Asha, thank you for your interest", sender="MUFGUBHR Workday <mufgub@myworkday.com>"),
                      mail("Jobs for you: Data Analyst and 5 more", "recommended jobs", sender="LinkedIn <jobs-noreply@linkedin.com>")]
    rows = proof.read_receipts(2)
    assert len(rows) == 1 and rows[0]["email"] == "mufgub@myworkday.com"
    assert proof.match_score({"company": "MUFG", "title": "Senior Analyst- Risk Reporting"}, rows[0]) > 0  # the receipt Gmail really held for the a company job


def test_no_password_means_unavailable_and_a_clear_error(monkeypatch):
    monkeypatch.setattr(imap, "password", lambda: None)
    assert not imap.available()
    with pytest.raises(RuntimeError):
        imap.search("x")


def test_reset_link_that_is_only_visible_text_in_an_html_only_email_is_found():  # a company: "no password-reset email arrived" while it sat in the inbox
    url = "https://citi.wd5.myworkdayjobs.com/2/passwordreset/abc123/?redirect=%2Fen-US%2F2%2Fjob%2Fx&username=me%40x.com"
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "Citi Human Resources <noreply@citi.com>", "me@x.com", "Reset your password"
    m.set_content("x")
    m.clear_content()
    m.set_content(f"<html><body><p>Click this link to reset your password {url} The link will expire after 2 hours.</p></body></html>", subtype="html")
    FakeIMAP.inbox = [m.as_bytes()]
    got = imap.verification_link("citi", 1, ())
    assert got and got.startswith("https://citi.wd5.myworkdayjobs.com/2/passwordreset/abc123")

# ---------- recency ----------
OLD = "https://acme.myworkday.com/2/passwordreset/OLD-expired-token"
NEW = "https://acme.myworkday.com/2/passwordreset/NEW-valid-token"


def test_verification_link_ignores_a_reset_mail_from_before_the_request():
    """Two reset mails in the inbox: yesterday's (expired link) and one that arrived after our click. Opening the old one lands on an
    'invalid/expired link' page that looks exactly like the portal rejecting a correct password."""
    FakeIMAP.inbox = [mail("Reset your password", f"click {OLD}", minutes_ago=1500),
                      mail("Reset your password", f"click {NEW}", minutes_ago=0.2)]
    since = imap.time.time() - 60  # we clicked Forgot-password one minute ago
    assert imap.verification_link("acme", 0, (), since=since, want="reset") == NEW
    # without `since` the newest mail is still chosen, which is why the timestamp has to come from the click
    assert imap.verification_link("acme", 0, (), want="reset") == NEW


def test_verification_link_returns_nothing_when_only_an_old_mail_exists():
    FakeIMAP.inbox = [mail("Reset your password", f"click {OLD}", minutes_ago=1500)]
    since = imap.time.time() - 60
    assert imap.verification_link("acme", 0, (), since=since, want="reset") is None


def test_want_keeps_a_reset_request_away_from_a_verification_mail():  # a company: 3 verification mails, an old reset mail in the same inbox
    FakeIMAP.inbox = [mail("Verify your candidate account", "confirm https://acme.myworkday.com/verify/ZZZ", minutes_ago=0.1),
                      mail("Reset your password", f"click {NEW}", minutes_ago=0.3)]
    since = imap.time.time() - 120
    assert imap.verification_link("acme", 0, (), since=since, want="reset") == NEW
    assert "verify/ZZZ" in imap.verification_link("acme", 0, (), since=since, want="verif")


def test_mail_reader_survives_inbox_archiving_and_never_marks_mail_read(monkeypatch):
    """Gmail declutter: receipts and OTP/reset mail are filtered OUT of the Inbox into Jobs/* labels (archived, not deleted).
    The bot must therefore search All Mail (not INBOX), read-only, and fetch with BODY.PEEK so nothing it reads is marked read for her."""
    calls = {"select": [], "fetch": [], "search": []}

    class Spy(FakeIMAP):
        def select(self, box, readonly=False):
            calls["select"].append((box, readonly))
            return "OK", []

        def search(self, charset, *crit):
            calls["search"].append(crit)
            return super().search(charset, *crit)

        def fetch(self, i, spec):
            calls["fetch"].append(spec)
            return super().fetch(i, spec)

    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", Spy)
    FakeIMAP.inbox = [mail("Your verification code", "your verification code is 482913", minutes_ago=0.1)]
    assert imap.otp_code("acme", 0) == "482913"
    proof.read_receipts(2)
    assert calls["select"] and all(box == '"[Gmail]/All Mail"' and ro for box, ro in calls["select"])
    assert calls["fetch"] and all("PEEK" in spec.upper() for spec in calls["fetch"])
    raw = " ".join(str(c) for crit in calls["search"] for c in crit).lower()
    assert "in:inbox" not in raw and "is:unread" not in raw and "label:inbox" not in raw


def test_otp_code_ignores_an_expired_code_from_an_earlier_attempt():
    FakeIMAP.inbox = [mail("Your verification code", "your verification code is 111111", minutes_ago=90),
                      mail("Your verification code", "your verification code is 222222", minutes_ago=0.1)]
    assert imap.otp_code("acme", 0, since=imap.time.time() - 60) == "222222"

