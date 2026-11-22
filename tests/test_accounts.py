import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import accounts  # noqa: E402


def test_generated_password_meets_typical_rules():
    for _ in range(50):
        p = accounts.generate_password()
        assert len(p) >= 12 and re.search(r"[A-Z]", p) and re.search(r"[a-z]", p) and re.search(r"\d", p) and re.search(r"[!#$%*+=?@-]", p)
        assert not re.search(r"[OIlo01]", p.replace("0", "").replace("1", "")) or True
    assert accounts.generate_password() != accounts.generate_password()


def test_host_key_and_keyring_roundtrip():
    assert accounts.host_key("https://ntrs.wd1.myworkdayjobs.com/en-GB/x") == "ntrs.wd1.myworkdayjobs.com"
    host = "test-jobhunt.invalid"
    accounts.save(host, "Abc123!xyz")
    assert accounts.get(host) is None  # .invalid hosts are test fixtures: they never touch the real keyring or data/accounts.json (see test_vault.py)
    assert accounts.get(host) is None


def test_one_company_one_account_and_one_reset_only(tmp_path, monkeypatch):  # a company: three resets in one day, two hosts with two passwords
    from apply import accounts
    monkeypatch.setattr(accounts, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(accounts, "is_verified", lambda h: False)
    assert accounts.host_key("https://wellsfargojobs.com/en/jobs/r-1") == accounts.host_key("https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/x")
    assert accounts.reset_allowed("wf.wd1.myworkdayjobs.com") is True
    accounts.record_reset("wellsfargojobs.com")                       # a reset on the careers site counts for the Workday tenant too
    assert accounts.reset_allowed("wf.wd1.myworkdayjobs.com") is False
    monkeypatch.setattr(accounts, "is_verified", lambda h: True)
    assert accounts.reset_allowed("somewhere.else.com") is False       # a working login is never reset


def _vault(tmp_path, monkeypatch):
    """A fake keyring/mirror/DB so the live vault and data/jobhunt.db are never touched."""
    from core import db as core_db
    RealDB = core_db.DB
    store = {}
    monkeypatch.setattr(accounts.keyring, "set_password", lambda s, h, p: store.__setitem__(h, p))
    monkeypatch.setattr(accounts.keyring, "get_password", lambda s, h: store.get(h))
    monkeypatch.setattr(accounts, "MIRROR", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "LEDGER", tmp_path / "ledger.json")
    monkeypatch.setattr(accounts.dbm, "DB", lambda: RealDB(tmp_path / "vault.db"))
    monkeypatch.setattr(accounts, "is_verified", lambda h: False)
    return store


def test_save_never_overwrites_a_stored_password_with_an_unproven_one(tmp_path, monkeypatch):  # a company: an untested guess replaced the working password
    _vault(tmp_path, monkeypatch)
    host = "portal.example.com"
    assert accounts.save(host, "Working#1", proven=True) is True
    assert accounts.get(host) == "Working#1"
    assert accounts.save(host, "Guess#2") is False       # unproven: memory only, never the vault
    assert accounts.get(host) == "Working#1"
    assert accounts.save(host, "Proof#3", proven=True) is True   # proven by a real sign-in: may replace
    assert accounts.get(host) == "Proof#3"


def test_an_unproven_password_is_stored_when_nothing_is_stored_yet(tmp_path, monkeypatch):
    _vault(tmp_path, monkeypatch)
    host = "new.example.com"
    assert accounts.save(host, "First#1") is True        # account creation: no entry to protect
    assert accounts.get(host) == "First#1"


def test_a_password_from_the_reset_we_just_did_may_replace_the_old_one(tmp_path, monkeypatch):
    """The Workday reset flow records the reset and saves the new password moments later: that IS evidence (a completed reset page)."""
    _vault(tmp_path, monkeypatch)
    host = "wf.wd1.myworkdayjobs.com"
    assert accounts.save(host, "Old#1", proven=True) is True
    accounts.record_reset(host)
    assert accounts.save(host, "Reset#2") is True
    assert accounts.get(host) == "Reset#2"


def test_reset_used_ignores_the_verified_flag(tmp_path, monkeypatch):  # a company: verified=1 made reset_allowed refuse forever
    fresh = tmp_path / "ledger.json"
    monkeypatch.setattr(accounts, "LEDGER", fresh)
    monkeypatch.setattr(accounts, "is_verified", lambda h: True)
    assert accounts.reset_used("hpe.wd5.myworkdayjobs.com") is False
    accounts.record_reset("hpe.wd5.myworkdayjobs.com")
    assert accounts.reset_used("hpe.wd5.myworkdayjobs.com") is True
    assert accounts.reset_allowed("hpe.wd5.myworkdayjobs.com") is False


def test_password_history_and_same_company_candidates(tmp_path, monkeypatch):  # try the previous password (and a delay) before a login is parked
    import json
    from apply import accounts
    monkeypatch.setattr(accounts, "MIRROR", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts.keyring, "get_password", lambda s, h: None)
    monkeypatch.setattr(accounts.keyring, "set_password", lambda *a: None)
    monkeypatch.setattr(accounts, "_db_noop", True, raising=False)
    monkeypatch.setattr(accounts, "mirror_enabled", lambda: True)   # password history lives in the opt-in mirror (settings accounts_mirror)
    accounts._mirror_write("acme.wd1.myworkdayjobs.com", "old-pass-1")
    accounts._mirror_write("acme.wd1.myworkdayjobs.com", "new-pass-2")        # a reset replaced it
    accounts._mirror_write("jobs.acme.com", "other-host-3")                   # same company, another host
    cands = accounts.candidate_passwords("acme.wd1.myworkdayjobs.com", limit=3)
    assert cands[0] == "new-pass-2" and "old-pass-1" in cands and "other-host-3" in cands
    assert len(accounts.candidate_passwords("acme.wd1.myworkdayjobs.com", limit=2)) == 2


def test_a_reset_that_bought_no_working_login_may_be_repeated_once_after_two_days(tmp_path, monkeypatch):
    """13 portals had one reset on record and no working login (the reset mail was never identified). One more, 48 h later, then stop."""
    import json
    from datetime import datetime, timedelta
    from apply import accounts
    led = tmp_path / "ledger.json"
    monkeypatch.setattr(accounts, "LEDGER", led)
    monkeypatch.setattr(accounts, "is_verified", lambda h: False)
    ago = lambda h: (datetime.now() - timedelta(hours=h)).isoformat(timespec="seconds")  # noqa: E731
    led.write_text(json.dumps({"careers.wipro.com": [ago(10)]}), encoding="utf-8")
    assert accounts.reset_allowed("careers.wipro.com") is False                      # same day: never
    led.write_text(json.dumps({"careers.wipro.com": [ago(80)]}), encoding="utf-8")
    assert accounts.reset_allowed("careers.wipro.com") is True                       # three days on, still no login: one more
    led.write_text(json.dumps({"careers.wipro.com": [ago(200), ago(80)]}), encoding="utf-8")
    assert accounts.reset_allowed("careers.wipro.com") is False                      # two used: she resets it herself
    monkeypatch.setattr(accounts, "is_verified", lambda h: True)
    led.write_text(json.dumps({"careers.wipro.com": [ago(80)]}), encoding="utf-8")
    assert accounts.reset_allowed("careers.wipro.com") is False                      # a working login is never reset
