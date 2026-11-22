"""KB-7: the Naukri 'Applied' badge must be read in the worker's LIVE signed-in context, not in a profile copy (which shows a login
wall and confirmed 0/13). These tests use a fake Playwright context/page: no browser, no network, no live database.

Each test names the give-up/negative path it locks in: a badge promotes, an absent badge does not, and a sign-in wall or a timeout is
'unknown' and changes nothing.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import proof, runner  # noqa: E402
from core import activity  # noqa: E402
from core import db as dbm  # noqa: E402

NAU = "https://www.naukri.com/job-listings-data-analyst-cargill-bengaluru-4-to-9-years-300926927166"
NAU2 = "https://www.naukri.com/job-listings-risk-analyst-acme-bengaluru-0-to-2-years-301026018967"


class FakePage:
    """Only the Playwright surface proof.check_naukri_badges uses."""

    def __init__(self, states):
        self.states = states  # url -> page-state dict, or an exception instance to raise from goto
        self.goto_calls = []
        self.closed = False

    def goto(self, url, **kw):
        self.goto_calls.append(url)
        val = self.states.get(url)
        if isinstance(val, BaseException):
            raise val
        return None

    def wait_for_timeout(self, ms):
        return None

    def evaluate(self, js):
        assert "apply" in js.lower()  # the real badge JS, not some other evaluate
        return self.states[self.goto_calls[-1]]

    def close(self):
        self.closed = True


class FakeCtx:
    def __init__(self, page):
        self.page = page
        self.new_pages = 0

    def new_page(self):
        self.new_pages += 1
        return self.page


def state(text="Apply", url=NAU, **over):
    """A Naukri job page with a single visible apply control (or none when text is None)."""
    base = {
        "url": url, "password": False, "wall": "", "gone": "", "notice": "",
        "candidates": ([{"tag": "button", "id": "apply-button", "cls": "apply-button", "text": text,
                         "visible": True, "disabled": text == "Applied", "in_apply": True}] if text else []),
    }
    base.update(over)
    return base


def job(jid="j1", url=NAU, company="Cargill"):
    return {"id": jid, "apply_url": url, "company": company, "title": "Data Analyst", "applied_at": "2026-11-04T22:40:08"}


@pytest.fixture(autouse=True)
def fresh_naukri_state(monkeypatch):
    monkeypatch.setattr(runner, "NAUKRI_LIVE", {"until": 0.0, "logged_signout": False})


@pytest.fixture
def logs(monkeypatch):
    out = []
    monkeypatch.setattr(activity, "log", lambda kind, msg, **kw: out.append((kind, msg)))
    return out


@pytest.fixture
def ndb(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never write test rows into the real applications ledger
    db = dbm.DB(tmp_path / "t.db")
    jid, _ = db.upsert_job({"source": "board:naukri", "company": "Cargill", "title": "Data Analyst",
                            "location": "Bengaluru", "url": NAU, "apply_url": NAU})
    db.set_status(jid, "unconfirmed", "submitted: waiting for the receipt email", applied_at="2026-11-04T22:40:08")
    return db, jid


# ---------- the live-context badge reader ----------
def test_badge_present_on_the_job_page_is_applied():
    pg = FakePage({NAU: state("Applied")})
    out = proof.check_naukri_badges(FakeCtx(pg), [job()], log=lambda *a: None)
    v = out["jobs"]["j1"]
    assert v["verdict"] == "applied" and "Applied badge" in v["evidence"] and pg.closed


def test_badge_absent_stays_not_applied():
    pg = FakePage({NAU: state("Apply")})
    out = proof.check_naukri_badges(FakeCtx(pg), [job()], log=lambda *a: None)
    assert out["jobs"]["j1"]["verdict"] == "not_applied"


def test_signin_wall_is_unknown_never_not_applied_and_stops_after_one_page():
    pg = FakePage({NAU: state(None, password=True, wall="Login to apply"),
                   NAU2: state("Applied", url=NAU2)})
    out = proof.check_naukri_badges(FakeCtx(pg), [job("j1", NAU), job("j2", NAU2)], log=lambda *a: None)
    assert out["signed_out"] is True
    assert out["jobs"]["j1"]["verdict"] == "unknown"
    assert out["jobs"]["j2"]["verdict"] == "unknown"  # untouched, explicitly unknown
    assert pg.goto_calls == [NAU]                      # signed out: no second page load


def test_login_redirect_url_is_a_wall():
    pg = FakePage({NAU: state(None, url="https://www.naukri.com/nlogin/login")})
    out = proof.check_naukri_badges(FakeCtx(pg), [job()], log=lambda *a: None)
    assert out["signed_out"] and out["jobs"]["j1"]["verdict"] == "unknown"


def test_timeout_is_unknown_never_not_applied():
    pg = FakePage({NAU: TimeoutError("Page.goto: Timeout 45000ms exceeded")})
    out = proof.check_naukri_badges(FakeCtx(pg), [job()], log=lambda *a: None)
    v = out["jobs"]["j1"]
    assert v["verdict"] == "unknown" and "TimeoutError" in v["evidence"] and not out["signed_out"]


def test_a_page_that_redirected_away_from_this_job_is_unknown():
    # e.g. Naukri hands off to the company site: an 'Applied' control there does not prove THIS Naukri job id was applied to.
    pg = FakePage({NAU: state("Applied", url="https://careers.example.com/jobs/123")})
    out = proof.check_naukri_badges(FakeCtx(pg), [job()], log=lambda *a: None)
    assert out["jobs"]["j1"]["verdict"] == "unknown" and "moved away" in out["jobs"]["j1"]["evidence"]


def test_naukri_digest_style_text_is_never_proof():  # 'you applied for 4 jobs' names no single job (KB-9)
    pg = FakePage({NAU: state(None, notice="Naukri: You applied for 4 jobs on 02 Nov")})
    out = proof.check_naukri_badges(FakeCtx(pg), [job()], log=lambda *a: None)
    assert out["jobs"]["j1"]["verdict"] == "unknown"


def test_exactly_one_page_is_reused_for_all_jobs_and_closed():
    pg = FakePage({NAU: state("Applied"), NAU2: state("Apply", url=NAU2)})
    ctx = FakeCtx(pg)
    out = proof.check_naukri_badges(ctx, [job("j1", NAU), job("j2", NAU2)], log=lambda *a: None)
    assert ctx.new_pages == 1 and pg.goto_calls == [NAU, NAU2] and pg.closed
    assert out["jobs"]["j1"]["verdict"] == "applied" and out["jobs"]["j2"]["verdict"] == "not_applied"


def test_a_given_page_is_reused_and_not_closed():
    pg = FakePage({NAU: state("Apply")})
    ctx = FakeCtx(FakePage({}))
    proof.check_naukri_badges(ctx, [job()], log=lambda *a: None, page=pg)
    assert ctx.new_pages == 0 and pg.closed is False


# ---------- runner wiring: promotion path + guard ----------
def test_runner_promotes_only_with_a_real_badge_and_keeps_the_real_applied_at(ndb):
    db, jid = ndb
    pg = FakePage({NAU: state("Applied")})
    n = runner._maybe_verify_naukri(FakeCtx(pg), db, {}, log=lambda *a: None)
    row = db.rows("id=?", (jid,))[0]
    assert n == 1 and row["status"] == "applied"
    assert row["email_proof"].startswith("ACCOUNT: Naukri Applied badge")
    assert row["applied_at"] == "2026-11-04T22:40:08"  # the real submission time is kept, never reset


def test_runner_badge_absent_keeps_the_job_unconfirmed(ndb, logs):
    db, jid = ndb
    pg = FakePage({NAU: state("Apply")})
    n = runner._maybe_verify_naukri(FakeCtx(pg), db, {}, log=lambda *a: None)
    row = db.rows("id=?", (jid,))[0]
    assert n == 0 and row["status"] == "unconfirmed" and row["email_proof"] is None


def test_runner_signin_wall_changes_nothing_logs_once_and_backs_off(ndb, logs):
    db, jid = ndb
    pg = FakePage({NAU: state(None, password=True, wall="Login to apply")})
    n = runner._maybe_verify_naukri(FakeCtx(pg), db, {}, log=lambda *a: None)
    row = db.rows("id=?", (jid,))[0]
    assert n == 0 and row["status"] == "unconfirmed" and row["email_proof"] is None  # never demoted or blocked
    assert runner.NAUKRI_LIVE["until"] > time.time()
    assert sum("signed out" in msg for _, msg in logs) == 1
    pg2 = FakePage({NAU: state("Applied")})
    assert runner._maybe_verify_naukri(FakeCtx(pg2), db, {}, log=lambda *a: None) == 0
    assert pg2.goto_calls == []  # back-off: no page opened, no retry every job
    assert len(logs) == 1


def test_runner_timeout_keeps_the_job_unconfirmed(ndb, logs):
    db, jid = ndb
    pg = FakePage({NAU: TimeoutError("Page.goto: Timeout 45000ms exceeded")})
    n = runner._maybe_verify_naukri(FakeCtx(pg), db, {}, log=lambda *a: None)
    row = db.rows("id=?", (jid,))[0]
    assert n == 0 and row["status"] == "unconfirmed" and row["email_proof"] is None


def test_verify_naukri_copy_stops_at_the_wall_and_backs_off(tmp_path, monkeypatch, logs):
    """The legacy copy-based check must not keep trying every job / every proof cycle after Naukri shows its login wall."""
    import playwright.sync_api as pws

    from apply import browser as browser_mod
    import core.db as core_db

    db = dbm.DB(tmp_path / "t.db")
    db._ledger = lambda *a, **k: None
    jid, _ = db.upsert_job({"source": "board:naukri", "company": "Cargill", "title": "Data Analyst",
                            "location": "Bengaluru", "url": NAU, "apply_url": NAU})
    db.set_status(jid, "unconfirmed", "waiting", applied_at="2026-11-04T22:40:08")
    monkeypatch.setattr(core_db, "DB", lambda *a, **k: db)
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(proof, "ROOT", tmp_path)
    monkeypatch.setattr(proof.subprocess, "run", lambda *a, **k: None)

    wall = state(None, password=True, wall="Login to apply")

    class Pg:
        def __init__(self):
            self.goto_calls = []

        def goto(self, url, **kw):
            self.goto_calls.append(url)

        def wait_for_timeout(self, ms):
            return None

        def evaluate(self, js):
            return wall

        def close(self):
            return None

    class Ctx:
        def __init__(self):
            self.page = Pg()

        def new_page(self):
            return self.page

        def close(self):
            return None

    launches = []

    class PW:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pws, "sync_playwright", lambda: PW())
    monkeypatch.setattr(browser_mod, "launch", lambda pw, headless=True: (launches.append(1), Ctx())[1])

    assert proof.verify_naukri(log=lambda *a: None) == 0
    assert len(launches) == 1 and (tmp_path / "data" / "naukri_proof_wall").exists()
    assert sum("signed out" in msg for _, msg in logs) == 1
    assert proof.verify_naukri(log=lambda *a: None) == 0
    assert len(launches) == 1  # fresh wall marker: skipped, no profile copy and no browser


def test_worker_calls_the_live_badge_check_after_a_naukri_send():  # KB-7 wiring: a plain 'unconfirmed' send must also be checked
    import inspect
    src = inspect.getsource(runner.run)
    assert "_maybe_verify_naukri(ctx, d, st, log)" in src
    assert 's == "unconfirmed"' in src


def test_runner_ignores_rows_that_are_already_applied_or_proved(ndb):
    db, jid = ndb
    other, _ = db.upsert_job({"source": "board:naukri", "company": "Acme", "title": "Risk Analyst",
                              "location": "Bengaluru", "url": NAU2, "apply_url": NAU2})
    db.set_status(other, "applied", "already proven", email_proof="ACCOUNT: keep me")
    pg = FakePage({NAU: state("Applied")})
    runner._maybe_verify_naukri(FakeCtx(pg), db, {}, log=lambda *a: None)
    assert pg.goto_calls == [NAU]  # only the unconfirmed, unproved row is checked
    assert db.rows("id=?", (other,))[0]["email_proof"] == "ACCOUNT: keep me"
