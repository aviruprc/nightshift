"""A field the page calls required although it SHOWS a value was never committed (KB-67).

a company (Workday 'My Experience') failed at "click #32 Save and Continue" with "Errors Found ... Error-URL The field URL
is required and must have a value", while the saved snapshot lists `URL*` = "https://www.linkedin.com/in/asha-example".
Workday commits inputs on blur; a value typed by Playwright can sit in the DOM without ever reaching the page's state.
The repair re-fires events on the SAME value and verifies it did not change - it never edits a fact.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import widgets  # noqa: E402


def test_only_fields_the_page_names_are_recommitted():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<body><h2>Errors Found</h2><p>Error-URL The field URL is required and must have a value.</p>"
                       "<input data-ag='29' aria-label='URL*' value='https://www.linkedin.com/in/asha-example'>"
                       "<input data-ag='10' aria-label='Job Title*' value='Marketing Analyst'></body>")
        snap = {"heads": ["Errors Found"],
                "text": "Error-URL The field URL is required and must have a value.",
                "els": [{"i": 29, "label": "URL*", "value": "https://www.linkedin.com/in/asha-example"},
                        {"i": 10, "label": "Job Title*", "value": "Marketing Analyst"}]}
        tried = set()
        notes = widgets.recommit_page_required(pg, snap, tried)
        assert any(n.startswith("URL*: re-committed") for n in notes), notes
        assert not any(n.startswith("Job Title*") for n in notes), notes
        assert pg.locator("[data-ag='29']").input_value() == "https://www.linkedin.com/in/asha-example"
        assert pg.locator("[data-ag='10']").input_value() == "Marketing Analyst"
        assert widgets.recommit_page_required(pg, snap, tried) == []      # once per field per job
        b.close()


def test_a_page_without_an_error_is_left_completely_alone():
    snap = {"heads": ["My Information"], "text": "Job Title Company",
            "els": [{"i": 1, "label": "Job Title*", "value": "Analyst"}]}
    assert widgets.recommit_page_required(None, snap, set()) == []


def test_an_empty_field_is_never_touched():
    snap = {"heads": ["Errors Found"], "text": "The field URL is required and must have a value.",
            "els": [{"i": 1, "label": "URL*", "value": ""}]}
    assert widgets.recommit_page_required(None, snap, set()) == []
