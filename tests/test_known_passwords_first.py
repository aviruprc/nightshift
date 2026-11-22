"""remember passwords; never reset a company's account when another password we already hold works."""
from apply import workday, accounts


def test_a_held_password_signs_in_without_a_reset(monkeypatch):
    saved = []
    monkeypatch.setattr(workday, "_sign_in", lambda pg, pw: "in" if pw == "OLD" else "badpw")
    monkeypatch.setattr(accounts, "save", lambda h, p, proven=False: saved.append((h, p, proven)) or True)
    monkeypatch.setattr(accounts, "mark_verified", lambda h: None)
    r = workday.try_known_passwords(None, "x.wd1.myworkdayjobs.com", ["NEW"], ["NEW", "OLD"])
    assert r and r[0] is True and saved == [("x.wd1.myworkdayjobs.com", "OLD", True)]


def test_nothing_held_means_none_so_the_single_reset_can_run(monkeypatch):
    monkeypatch.setattr(workday, "_sign_in", lambda pg, pw: "badpw")
    assert workday.try_known_passwords(None, "x.wd1.myworkdayjobs.com", ["A"], ["A", "B"]) is None


def test_a_slow_page_stops_the_guessing(monkeypatch):
    calls = []
    monkeypatch.setattr(workday, "_sign_in", lambda pg, pw: calls.append(pw) or "timeout")
    assert workday.try_known_passwords(None, "h", ["A"], ["B", "C"]) is None and calls == ["B"]


def test_the_two_reset_paths_try_held_passwords_first():
    src = open(workday.__file__, encoding="utf-8").read()
    assert src.count("= try_known_passwords(pg, host") == 2
