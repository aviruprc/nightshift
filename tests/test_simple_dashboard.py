""". The home page shows four things; the full one is at /full."""
from pathlib import Path

from track import live, simple


def test_each_parked_job_gets_one_action():
    assert simple.kind_of("CAPTCHA: the form is filled; solve the CAPTCHA, then submit") == "captcha"
    assert simple.kind_of("saved password rejected: needs a manual reset") == "password"
    assert simple.kind_of("unanswered required: How many years of experience do you have in UAT?") == "question"
    assert set(simple.ACTION) == {"captcha", "password", "question", "other"}


def test_the_home_page_is_the_full_dashboard_and_the_short_page_is_at_simple():
    """. The full dashboard is home again."""
    src = Path(live.__file__).read_text(encoding="utf-8")
    assert 'SIMPLE if _p == "/simple" else PAGE' in src and "/api/simple" in src and live.SIMPLE.exists() and live.PAGE.exists()


def test_the_page_escapes_what_it_shows_and_only_links_to_http():
    html = live.SIMPLE.read_text(encoding="utf-8")
    assert "function safeUrl" in html and "esc(n.company)" in html and "esc(n.reason)" in html     # job text comes from outside: never raw HTML
