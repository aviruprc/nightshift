"""A withdrawn Workday requisition says so in words the closure check must know (KB-61).

a company: the run reported "navigator: page stopped changing (Search for Jobs page is loaded)"
(filed as OUR bug, `logic`). The page, opened headless afterwards, says "Careers ... Sign In | Search for Jobs | The page you
are looking for doesn't exist. | Search for Jobs" - Workday's own answer for a requisition that is gone, and the same
reason its /apply route bounced to the job-search page mid-run. `engine.CLOSED` knew "not found" but not "doesn't exist".
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import engine  # noqa: E402


@pytest.mark.parametrize("text", [
    "The page you are looking for doesn't exist.",
    "The page you are looking for doesn’t exist.",   # a curly apostrophe is still the same sentence
    "The page you are looking for does not exist.",
    "Sorry, this page no longer exists.",
    "The page you could not find has been removed",
])
def test_a_missing_page_is_a_closure(text):
    assert engine.CLOSED.search(text), text


def test_a_real_form_that_mentions_a_missing_page_is_not_closed():
    """Only a page WITHOUT any field can be a closure: the same sentence above a form is a note, not the job's state."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<body><p>The page you are looking for doesn't exist.</p>"
                       "<p>Apply anyway:</p><input name=q><textarea name=cover></textarea></body>")
        assert engine.page_state(pg) == "ok"
        pg.set_content("<body><p>Careers</p><p>Sign In</p><p>Search for Jobs</p>"
                       "<p>The page you are looking for doesn't exist.</p></body>")
        assert engine.page_state(pg) == "closed"
        b.close()


def test_the_old_phrasings_still_close_a_job():
    for t in ("This job is no longer available", "The position has been filled", "This posting has expired"):
        assert engine.CLOSED.search(t), t
