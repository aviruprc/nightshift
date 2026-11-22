"""Unproven 'submitted' claims.

Two rules under test:
1. runner.record_submission stamps applied_at on a page-proved send, so 'sent today' counts it even while it awaits the receipt email.
2. the LLM navigator saying {"done":"applied"} without ever clicking a final Submit is NOT a send: nothing was sent, so the job must fail
   (transient), not sit in 'unconfirmed' forever.
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, runner  # noqa: E402
from core import llm as corellm  # noqa: E402


class _C:
    def __init__(self):
        self.calls = []

    def execute(self, *a):
        self.calls.append(a)


class _DB:
    def __init__(self):
        self.c = _C()
        self.status = None
        self.extra = {}

    def set_status(self, jid, status, detail="", **extra):
        self.status, self.detail, self.extra = status, detail, extra


def test_page_proved_send_gets_applied_at_and_stays_unconfirmed():
    db = _DB()
    runner.record_submission(db, "j1", {"status": "applied", "detail": "navigator: confirmation page",
                                        "proof": "PAGE: 'Application Submitted' modal"}, False, {})
    assert db.status == "unconfirmed", db.status  # page proof only: not 'applied' until the receipt arrives
    assert db.extra.get("applied_at"), "applied_at missing: the send would never count as sent today"


def test_account_proof_is_applied_immediately():
    db = _DB()
    runner.record_submission(db, "j2", {"status": "applied", "detail": "Naukri shows 'Applied'", "proof": "ACCOUNT: Naukri badge"}, False, {})
    assert db.status == "applied" and db.extra.get("applied_at")


CLAIM_ONLY = """<!doctype html><meta charset=utf-8><title>Apply</title><body>
<h1>Data Analyst</h1><label for=n>Full name</label><input id=n><button>Submit application</button></body>"""


def test_model_claim_without_a_final_click_is_a_failure(tmp_path, monkeypatch):
    f = tmp_path / "claim.html"
    f.write_text(CLAIM_ONLY, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    monkeypatch.setattr(corellm, "ask_json", lambda *a, **k: {"think": "sure", "done": "applied"})
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx, pg = b.new_context(), None
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), None, {"id": "tclaim", "company": "Acme", "title": "Data Analyst"}, dry_run=False)
        b.close()
    assert res["status"] == "failed", res
    assert "never clicked a final Submit" in res["detail"], res["detail"]
