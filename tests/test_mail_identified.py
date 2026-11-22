"""t stuck, the email is not identified properly".
The mail search spent almost its whole wait on the FIRST hint; a hint that cannot match starved the one that does."""
import re
import time

from apply import agent, mail
from core import imap


def test_every_hint_is_asked_in_the_first_round(monkeypatch):
    asked = []

    def fake(ctx, hint, wait_s=120, since=None, accept_old=False):
        asked.append((hint, wait_s))
        return "482913" if hint == "ExxonMobil" else None
    monkeypatch.setattr(mail, "otp_code", fake)
    t0 = time.time()
    assert agent.code_from_mail(None, ["ExxonMobil code", "ExxonMobil", "successfactors"]) == "482913"
    assert time.time() - t0 < 3                      # the matching second hint is reached at once, not after 120 s on the first
    assert asked[0] == ("ExxonMobil code", 0) and asked[1] == ("ExxonMobil", 0)


def test_a_code_mail_that_names_no_hint_is_still_found_when_it_just_arrived(monkeypatch):
    def fake(ctx, hint, wait_s=120, since=None, accept_old=False):
        if hint == agent.GENERIC_CODE_HINT:
            assert since and time.time() - since <= 181    # only mail from the last 3 minutes
            return "771204"
        return None
    monkeypatch.setattr(mail, "otp_code", fake)
    assert agent.code_from_mail(None, ["Acme code", "Acme"]) == "771204"


def test_no_mail_gives_up_inside_the_budget(monkeypatch):
    monkeypatch.setattr(mail, "otp_code", lambda *a, **k: None)
    t0 = time.time()
    assert agent.code_from_mail(None, ["Acme"], total_s=2, poll_s=0.5) is None
    assert time.time() - t0 < 6


def test_real_code_subjects_are_recognised():
    for subj in ("One-Time Password for Candidate Account Verification", "Your One-Time Password (OTP) for Profile Creation", "Confirm your identity",
                 "Identity Confirmation", "Your verification code", "Verification Code - Example Corp", "Your Oracle identity verification"):
        assert re.search(imap.CODE_SUBJECT, subj, re.I), subj


def test_reset_link_search_asks_every_hint_before_waiting(monkeypatch):
    asked = []

    def fake(ctx, hint, wait_s=120, allowed=(), since=None, want=""):
        asked.append((hint, wait_s))
        return "https://wf.wd1.myworkdayjobs.com/reset/abc" if hint == 'subject:"reset your password"' else None
    monkeypatch.setattr(mail, "verification_link", fake)

    class Pg:
        url = "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/login"
    t0 = time.time()
    link = agent._fetch_mail_link(Pg(), None, "https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/1", "Wells Fargo", kind="reset")
    assert link and time.time() - t0 < 3
    assert all(w == 0 for _, w in asked) and asked[0][0] == "from:wf"      # the tenant hint that cannot match a company no longer eats the wait
