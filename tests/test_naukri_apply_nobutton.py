"""Naukri's 'no Apply button' path (apply/naukri_apply.py).

Bug (a company, live run): a job with no Apply control was parked as `needs_you` with the guess "(signed out or
layout changed)" and NO screenshot, so the two causes could not be told apart afterwards. A signed-out session is ours to repair; a
signed-in page with no Apply control is the only case that belongs on her list. Both now save the page as evidence.
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import engine, naukri_apply  # noqa: E402

SIGNED_OUT = """<!doctype html><meta charset=utf-8><body>
<header><a href="/nlogin/login">Login</a><a href="/register">Register</a></header>
<h1>MIS Executive</h1><p>Kalyani Motors</p><button>Save</button></body>"""

SIGNED_IN_NO_APPLY = """<!doctype html><meta charset=utf-8><body>
<header><a href="/mnjuser/homepage">My Naukri</a><a href="/nlogin/logout">Logout</a></header>
<h1>MIS Executive</h1><p>Kalyani Motors</p><button>Save</button></body>"""


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "SHOTS", tmp_path / "shots")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        c = b.new_context()
        yield c
        b.close()


def _run(ctx, tmp_path, name, html):
    p = tmp_path / f"{name}.html"
    p.write_text(html, encoding="utf-8")
    return naukri_apply.apply_naukri(ctx, p.as_uri(), tmp_path / "cv.pdf", dry_run=True, job_id=name)


def test_a_signed_out_session_is_ours_to_retry_not_hers_to_solve(ctx, tmp_path):
    res = _run(ctx, tmp_path, "signed_out", SIGNED_OUT)
    assert res["status"] == "failed", res
    assert "signed out" in res["detail"]
    assert res["screenshot"] and Path(res["screenshot"]).exists()


def test_a_signed_in_page_without_an_apply_control_is_for_her_and_keeps_the_evidence(ctx, tmp_path):
    res = _run(ctx, tmp_path, "signed_in", SIGNED_IN_NO_APPLY)
    assert res["status"] == "needs_you", res
    assert "signed-in" in res["detail"]
    assert res["screenshot"] and Path(res["screenshot"]).exists()
