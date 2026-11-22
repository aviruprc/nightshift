"""Mail formats seen in the user's real inbox that the reader must handle."""
from apply import accounts, agent
from core import imap


def test_a_letters_and_digits_code_is_read():
    body = "Hello Asha Here is the code required to complete your form on the Toast careers website. JYH18 This email was generated because of a form"
    assert imap.alphanumeric_code("Please verify your login at Toast Careers " + body) == "JYH18"


def test_a_requisition_number_or_year_is_never_a_code():
    assert imap.alphanumeric_code("Verification: your application R163902 for 2026 was received") is None
    assert imap.alphanumeric_code("your reference JYH18 thanks") is None


def test_a_reset_link_host_is_in_scope():
    scope = agent._link_scope("https://wf.wd1.myworkdayjobs.com/en-US/x", "https://wf.wd1.myworkdayjobs.com/en-US/x")
    link = "https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/passwordreset/"
    assert any(a in link.lower() for a in scope)


def test_another_tenants_link_is_still_refused():
    scope = agent._link_scope("https://hitachi.wd1.myworkdayjobs.com/x", "https://hitachi.wd1.myworkdayjobs.com/x")
    assert not any(a in "https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/passwordreset/".lower() for a in scope)


def test_the_right_portals_mail_is_found_behind_a_newer_mail_of_another_portal(monkeypatch):
    hitachi = {"subject": "Reset your password for your candidate account", "date": "Tue, 10 Nov 2026 14:25:17 +0530",
               "links": ["https://hitachi.wd1.myworkdayjobs.com/hitachi/passwordreset/abc"]}
    wells = {"subject": "Reset your password for your candidate account", "date": "Tue, 10 Nov 2026 12:39:10 +0530",
             "links": ["https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/passwordreset/"]}
    monkeypatch.setattr(imap, "search", lambda q, limit=10: [hitachi, wells])
    link = imap.verification_link("subject:reset", wait_s=0, allowed=("wf.wd1.myworkdayjobs.com", "wd1.myworkdaysite.com/recruiting/wf/"), want="reset")
    assert link == "https://wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/passwordreset/"
    link = imap.verification_link("subject:reset", wait_s=0, allowed=("hitachi.wd1.myworkdayjobs.com",), want="reset")
    assert "hitachi" in link


def _mail(code, age_s, subject="Your verification code"):
    import time
    from email.utils import formatdate
    return {"subject": subject, "body": f"Please confirm your identity using the one-time passcode: {code} This code will expire in 10 minutes.",
            "date": formatdate(time.time() - age_s), "links": []}


def test_a_stale_code_is_not_typed_while_the_fresh_one_is_in_transit(monkeypatch):
    import time
    calls = {"n": 0}

    def fake_search(q, limit=10):   # first look: only the 5-minute-old code; later the fresh one has arrived
        calls["n"] += 1
        return [_mail("111111", 300)] if calls["n"] < 3 else [_mail("222222", 5), _mail("111111", 300)]
    monkeypatch.setattr(imap, "search", fake_search)
    monkeypatch.setattr(imap, "FRESH_WAIT_S", 30)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert imap.otp_code("Goldman", wait_s=60) == "222222"


def test_with_a_request_time_only_mail_after_it_counts(monkeypatch):
    import time
    monkeypatch.setattr(imap, "search", lambda q, limit=10: [_mail("111111", 200)])
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert imap.otp_code("Goldman", wait_s=0, since=time.time() - 30) is None


def test_a_date_ending_minus_0000_is_utc_not_local_time():
    import time
    from email.utils import formatdate
    assert abs((time.time() - 5) - imap.msg_ts({"date": formatdate(time.time() - 5)})) < 3   # formatdate writes "-0000"
    assert abs((time.time() - 5) - imap.msg_ts({"date": formatdate(time.time() - 5, usegmt=False, localtime=True)})) < 3
