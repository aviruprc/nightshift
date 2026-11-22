"""P0.2b password vault: saved passwords are never lost and the same portal is never reset twice."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import accounts, workday  # noqa: E402


@pytest.fixture
def vault(tmp_path, monkeypatch):
    store = {}
    monkeypatch.setattr(accounts.keyring, "get_password", lambda s, h: store.get(h))
    monkeypatch.setattr(accounts.keyring, "set_password", lambda s, h, p: store.__setitem__(h, p))
    monkeypatch.setattr(accounts.keyring, "delete_password", lambda s, h: store.pop(h, None))
    monkeypatch.setattr(accounts, "MIRROR", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "mirror_enabled", lambda: True)   # the plain-file mirror is opt-in (settings accounts_mirror)
    monkeypatch.setattr(accounts.dbm, "DB", lambda *a, **k: type("D", (), {"c": type("C", (), {"execute": lambda *a: None, "commit": lambda *a: None})()})())
    return store


def test_save_writes_keyring_and_mirror(vault):
    accounts.save("acme.wd3.myworkdayjobs.com", "Pw#1")
    assert vault["acme.wd3.myworkdayjobs.com"] == "Pw#1"
    assert "acme.wd3.myworkdayjobs.com" in json.loads(accounts.MIRROR.read_text())


def test_the_plain_file_mirror_is_off_unless_switched_on(vault, monkeypatch):
    monkeypatch.setattr(accounts, "mirror_enabled", lambda: False)
    accounts.save("acme2.wd3.myworkdayjobs.com", "Pw#9")
    assert vault["acme2.wd3.myworkdayjobs.com"] == "Pw#9" and not accounts.MIRROR.exists()


def test_mirror_is_read_when_keyring_is_empty(vault):  # bug: a company's password missing from keyring
    accounts.save("wf.wd1.myworkdayjobs.com", "Pw#2")
    vault.clear()
    assert accounts.get("wf.wd1.myworkdayjobs.com") == "Pw#2"


def test_test_hosts_never_touch_the_real_store(vault):
    accounts.save("x.invalid", "nope")
    assert not vault and not accounts.MIRROR.exists()


def test_no_second_reset_within_30_days_after_a_confirmed_one(tmp_path, monkeypatch):
    monkeypatch.setattr(workday, "ROOT_DATA", tmp_path)
    host = "genpact.wd108.myworkdayjobs.com"
    assert workday._reset_allowed(host)
    workday._reset_used(host)
    assert not workday._reset_allowed(host)  # same day
    (tmp_path / "reset_log.json").write_text(json.dumps({host: {"date": (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"), "confirmed": True}}))
    assert not workday._reset_allowed(host)  # 10 days later: still a bug signal, not a reset
    (tmp_path / "reset_log.json").write_text(json.dumps({host: {"date": (datetime.now() - timedelta(days=31)).strftime("%Y-%m-%d"), "confirmed": True}}))
    assert workday._reset_allowed(host)


def test_an_unconfirmed_reset_is_retried_on_a_later_day_never_blocked_for_30(tmp_path, monkeypatch):
    """Target / a company: a reset whose page was never checked was recorded as done and blocked the account for 30 days,
    although the site still held the old password. Unknown is retried, never permanent."""
    monkeypatch.setattr(workday, "ROOT_DATA", tmp_path)
    host = "invesco.wd1.myworkdayjobs.com"
    workday._reset_used(host, confirmed=False)
    assert not workday._reset_allowed(host)  # still one attempt per day: no churn in the same run
    (tmp_path / "reset_log.json").write_text(json.dumps({host: {"date": (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                                                                  "confirmed": False}}))
    assert workday._reset_allowed(host)  # tomorrow (or later) a fresh attempt is allowed
    # a plain-string entry came from the code that never checked the page: unconfirmed, so the same rule applies
    (tmp_path / "reset_log.json").write_text(json.dumps({host: (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")}))
    assert workday._reset_allowed(host)


class _Click:
    def click(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass


class _Count:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n

    def nth(self, i):
        return _Click()

    @property
    def first(self):
        return _Click()


class _ResetPage:
    """Only what the reset flow touches; the outcome is what `inner_text` says."""

    def __init__(self, body="", pw_boxes=1, signin=False):
        self.body, self.pw_boxes, self.signin = body, pw_boxes, signin

    def inner_text(self, *a, **k):
        return self.body

    def locator(self, sel):
        if "input[type=password]" in sel:
            return _Count(self.pw_boxes)
        if "signIn" in sel:
            return _Count(1 if self.signin else 0)
        return _Count(0)


def test_reset_confirmation_needs_the_page_to_say_so():
    ok, why = workday._reset_confirmed(_ResetPage("Your password has been reset. You can now sign in."))
    assert ok and "reset" in why
    ok, why = workday._reset_confirmed(_ResetPage("Password must contain at least one number and one special character"))
    assert not ok and "must contain" in why  # the page's own words, not our guess
    ok, why = workday._reset_confirmed(_ResetPage("Reset your password"))
    assert not ok and "did not submit" in why  # the form is still on screen: nothing was applied
    ok, _ = workday._reset_confirmed(_ResetPage("Sign In", pw_boxes=0, signin=True))
    assert ok  # form accepted, back on the sign-in page
    ok, why = workday._reset_confirmed(_ResetPage("Sign In", pw_boxes=0, signin=False))
    assert not ok and "without a success message" in why  # an unreadable page is UNKNOWN, never a confirmation


class _Ctx:
    def __init__(self, rp):
        self.rp = rp

    def new_page(self):
        return self.rp


class _FormPage(_ResetPage):
    def __init__(self, body):
        super().__init__(body, pw_boxes=2)

    def goto(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def get_by_role(self, *a, **k):
        return _Count(0)

    def content(self):
        return "<html>reset</html>"

    url = "https://invesco.wd1.myworkdayjobs.com/reset"

    def close(self):
        pass


def test_an_unconfirmed_reset_form_leaves_the_vault_untouched_and_retries_later(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(workday, "ROOT_DATA", tmp_path)
    monkeypatch.setattr(accounts, "LEDGER", tmp_path / "reset_ledger.json")
    monkeypatch.setattr(workday, "mail", type("M", (), {"verification_link": staticmethod(lambda *a, **k: "https://invesco.wd1.myworkdayjobs.com/reset/x")}))
    host = "invesco.wd1.myworkdayjobs.com"
    accounts.save(host, "Old#Pw1")
    ok, why = workday.reset_and_sign_in(_FormPage("Reset your password"), _Ctx(_FormPage("Reset your password")), host)
    assert not ok and why.startswith("sign-in did not complete")  # transient to the caller, not 'needs you'
    assert accounts.get(host) == "Old#Pw1"  # the generated password was never applied, so it must not replace hers
    assert not (tmp_path / "reset_ledger.json").exists() or host not in json.loads((tmp_path / "reset_ledger.json").read_text())
    log = json.loads((tmp_path / "reset_log.json").read_text())
    assert log[host]["confirmed"] is False


def test_a_confirmed_reset_stores_the_new_password_and_both_ledgers(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(workday, "ROOT_DATA", tmp_path)
    monkeypatch.setattr(accounts, "LEDGER", tmp_path / "reset_ledger.json")
    monkeypatch.setattr(workday, "mail", type("M", (), {"verification_link": staticmethod(lambda *a, **k: "https://acme.wd3.myworkdayjobs.com/reset/x")}))
    monkeypatch.setattr(workday, "_sign_in", lambda pg, pw: "in")
    host = "acme.wd3.myworkdayjobs.com"
    accounts.save(host, "Old#Pw2")
    ok, why = workday.reset_and_sign_in(_Page(), _Ctx(_FormPage("Your password has been reset")), host)
    assert ok and why == "password reset and signed in"
    assert accounts.get(host) not in (None, "Old#Pw2")  # the password the site now has, not the old one
    assert json.loads((tmp_path / "reset_log.json").read_text())[host]["confirmed"] is True
    assert host in json.loads((tmp_path / "reset_ledger.json").read_text())  # one ledger for save and reset_allowed


def test_a_silent_sign_in_after_a_confirmed_reset_is_transient_not_needs_you(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(workday, "ROOT_DATA", tmp_path)
    monkeypatch.setattr(accounts, "LEDGER", tmp_path / "reset_ledger.json")
    monkeypatch.setattr(workday, "mail", type("M", (), {"verification_link": staticmethod(lambda *a, **k: "https://acme.wd3.myworkdayjobs.com/reset/x")}))
    monkeypatch.setattr(workday, "_sign_in", lambda pg, pw: "timeout")
    monkeypatch.setattr(workday, "_banner", lambda pg: "")
    ok, why = workday.reset_and_sign_in(_Page(), _Ctx(_FormPage("Your password has been reset")), "acme.wd3.myworkdayjobs.com")
    assert not ok and why.startswith("sign-in did not complete") and "never answered" in why


def test_an_explicit_wrong_password_after_a_confirmed_reset_still_needs_her(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(workday, "ROOT_DATA", tmp_path)
    monkeypatch.setattr(accounts, "LEDGER", tmp_path / "reset_ledger.json")
    monkeypatch.setattr(workday, "mail", type("M", (), {"verification_link": staticmethod(lambda *a, **k: "https://acme.wd3.myworkdayjobs.com/reset/x")}))
    monkeypatch.setattr(workday, "_sign_in", lambda pg, pw: "badpw")
    monkeypatch.setattr(workday, "_banner", lambda pg: "You may have entered the wrong email address or password")
    ok, why = workday.reset_and_sign_in(_Page(), _Ctx(_FormPage("Your password has been reset")), "acme.wd3.myworkdayjobs.com")
    assert not ok and why.startswith("signed in failed after reset:")


class _Loc:
    def __init__(self):
        self.first = self

    def click(self, *a, **k):
        pass

    def fill(self, *a, **k):
        pass

    def __getattr__(self, name):  # anything else the flow touches: harmless defaults
        return lambda *a, **k: True if name in ("is_checked", "is_visible") else 0 if name == "count" else self


class _Page:
    def locator(self, *a, **k):
        return _Loc()

    def wait_for_selector(self, *a, **k):
        pass

    def wait_for_timeout(self, *a, **k):
        pass

    def reload(self, *a, **k):
        pass

    def get_by_role(self, *a, **k):
        return _Count(0)


@pytest.mark.parametrize("outcome", ["timeout", "error", "verify", "signin"])
def test_no_reset_unless_password_is_explicitly_wrong(vault, monkeypatch, outcome):
    accounts.save("acme.wd3.myworkdayjobs.com", "Pw#3")
    called = []
    monkeypatch.setattr(workday, "wait_outcome", lambda *a, **k: outcome)
    monkeypatch.setattr(workday, "_click_submit_like", lambda *a, **k: None)
    monkeypatch.setattr(workday, "reset_and_sign_in", lambda *a, **k: called.append(1) or (True, "reset"))
    ok, why = workday.sign_in_or_create(_Page(), None, "acme.wd3.myworkdayjobs.com", dry_run=False)
    assert not called and not ok and "without touching the password" in why


def test_wrong_password_triggers_exactly_one_reset(vault, monkeypatch):
    accounts.save("acme.wd3.myworkdayjobs.com", "Pw#4")
    called = []
    monkeypatch.setattr(workday, "wait_outcome", lambda *a, **k: "badpw")
    monkeypatch.setattr(workday, "_click_submit_like", lambda *a, **k: None)
    monkeypatch.setattr(workday, "reset_and_sign_in", lambda *a, **k: called.append(1) or (True, "reset"))
    workday.sign_in_or_create(_Page(), None, "acme.wd3.myworkdayjobs.com", dry_run=False)
    assert called == [1]


def test_new_account_password_is_saved_before_submit(vault, monkeypatch):
    seen = {}
    monkeypatch.setattr(workday, "_click_submit_like", lambda *a, **k: seen.setdefault("pw_at_submit", accounts.get("new.wd1.myworkdayjobs.com")))
    monkeypatch.setattr(workday, "wait_outcome", lambda *a, **k: "error")
    try:
        workday.sign_in_or_create(type("P", (_Page,), {"inner_text": lambda *a, **k: ""})(), None, "new.wd1.myworkdayjobs.com", dry_run=False)
    except Exception:  # noqa: BLE001 (the fake page cannot finish the flow; we only care about the order)
        pass
    assert seen.get("pw_at_submit")


def test_saved_password_is_not_proof_of_an_account(vault, monkeypatch):  # bug: a company sign-in rejected twice; a password saved during a failed sign-up looked like an account
    rows = {}

    class C:
        def execute(self, q, a=()):
            if q.startswith("INSERT"):
                rows[a[0]] = 0
            if q.startswith("UPDATE"):
                rows[a[0]] = 1
            return self

        def fetchone(self):
            return None

        def commit(self):
            pass

    class D:
        c = C()
    accounts.save("cgi.njoyn.com", "Pw#1")
    assert accounts.get("cgi.njoyn.com") == "Pw#1"  # saved...
    r = type("R", (), {"__getitem__": lambda s, i: 0})()
    monkeypatch.setattr(accounts.dbm, "DB", lambda *a, **k: type("D2", (), {"c": type("C2", (), {"execute": lambda *a: type("X", (), {"fetchone": lambda s: r})()})()})())
    assert accounts.is_verified("cgi.njoyn.com") is False  # ...but not verified until a sign-in/submission worked


def test_generated_passwords_use_only_universally_accepted_symbols():  # bug: a company rejected '=+?-' symbols as 'no special character'
    import re
    for _ in range(300):
        p = accounts.generate_password()
        assert len(p) == 14 and re.search(r"[A-Z]", p) and re.search(r"[a-z]", p) and re.search(r"\d", p) and re.search(r"[@#$%]", p)
        assert re.fullmatch(r"[A-Za-z0-9@#$%]+", p), p
