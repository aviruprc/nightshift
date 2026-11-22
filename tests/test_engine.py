import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from apply import engine  # noqa: E402

FORMS = ROOT / "tests" / "forms"
CV = ROOT / "tests" / "fixtures" / "profile" / "Asha_Example_Resume.pdf"   # generated from the fake profile


@pytest.fixture  # function-scoped: a module-scoped Playwright fixture breaks later sync_playwright calls in the same session (AGENTS.md)
def page():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b.new_page()
        b.close()


def run(page, name, dry):
    return engine.apply_to_url(page, (FORMS / name).as_uri(), CV, dry_run=dry, job_id="t_" + name.split(".")[0])


def test_dry_run_fills_but_does_not_submit(page):
    r = run(page, "basic.html", True)
    assert r["status"] == "dry_run_ok", r
    assert page.locator("#t").is_checked() and not page.locator("#m").is_checked()  # consent yes, marketing no
    assert page.input_value("#fn") == "Asha" and page.input_value("#ph") == "9000000001"
    assert "Thank you" not in page.inner_text("body")


def test_live_submit_confirms(page):
    r = run(page, "basic.html", False)
    assert r["status"] == "applied", r


def test_unknown_required_question_parks_job(page):
    r = run(page, "unknown.html", False)
    assert r["status"] == "needs_you" and "Why do you want" in r["detail"], r
    assert "Thank you" not in page.inner_text("body")  # never submitted


def test_multistep(page):
    r = run(page, "multistep.html", False)
    assert r["status"] == "applied", r
