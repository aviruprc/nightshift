"""KB-57: a look-alike institution must never be written into an application that cannot be corrected.

a company - the saved page shows element 28:
`option "Larkmore Cyber University, press delete to clear value."` under the field `School or University*`, while her fact is
"Larkmoor University, Westfield School of Statistics (WSS)". The fuzzy picker scored 'larkmoor' against 'larkmore' high enough to click it.
These tests drive the REAL picker against a Workday-shaped list, so the refusal (and the honest success) are both proven in a browser.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from apply import widgets  # noqa: E402

HER_SCHOOL = "Larkmoor University, Westfield School of Statistics (WSS)"

PAGE = """<!doctype html><meta charset=utf-8><body>
<label for="school">School or University*</label>
<input id="school" role="combobox" aria-haspopup="listbox" aria-expanded="false" aria-label="School or University">
<ul id="lb" role="listbox" style="display:none">OPTIONS</ul>
<script>
const inp = document.getElementById('school'), lb = document.getElementById('lb');
inp.addEventListener('click', () => { lb.style.display = 'block'; });
document.querySelectorAll('#lb [role=option]').forEach(o => o.addEventListener('click', () => {
  inp.value = o.innerText; lb.style.display = 'none';
}));
</script></body>"""


def _page_with(options):
    items = "".join(f"<li role='option'>{o}</li>" for o in options)
    return PAGE.replace("OPTIONS", items)


@pytest.fixture  # function-scoped: AGENTS.md forbids module-scoped Playwright fixtures
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b
        b.close()


def test_a_list_without_her_school_is_refused_and_named(browser):
    pg = browser.new_page()
    pg.set_content(_page_with(["Larkmore Cyber University", "Korea University"]))
    ok = widgets.pick_option(pg, pg.locator("#school").first, HER_SCHOOL, "School or University")
    assert ok is False, "a near-miss institution was accepted"
    assert "larkmoor" in widgets.LAST_PICK_ERROR.lower(), widgets.LAST_PICK_ERROR
    assert pg.locator("#school").input_value() == "" or "korea" not in pg.locator("#school").input_value().lower()


def test_her_real_school_is_picked_when_the_list_offers_it(browser):
    pg = browser.new_page()
    pg.set_content(_page_with(["Larkmore Cyber University", "Larkmoor University, Westfield School of Statistics"]))
    ok = widgets.pick_option(pg, pg.locator("#school").first, HER_SCHOOL, "School or University")
    assert ok is True, widgets.LAST_PICK_ERROR
    assert "larkmoor" in pg.locator("#school").input_value().lower()


def test_a_degree_field_is_not_treated_as_an_institution(browser):
    """'MBA' must keep matching 'Master of Business Administration'-style lists: only institution fields get the strict rule."""
    pg = browser.new_page()
    pg.set_content(_page_with(["MBA", "Master of Business Administration"]))
    ok = widgets.pick_option(pg, pg.locator("#school").first, "MBA", "Degree")
    assert ok is True, widgets.LAST_PICK_ERROR
    assert pg.locator("#school").input_value().upper() == "MBA"
