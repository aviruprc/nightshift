"""Workday 'My Experience' is truthful or absent - never the CV parser's leftovers (a company, KB-41).

a company's Finance Analyst application (job, `a saved failure page`) had FOUR experience rows:
two identical Example Retail rows, an empty row, and a fourth beyond her history - with Start/End dates missing on every one. Save and Continue
looped on "The field From is required" (twice) plus To / Job Title / Company, and the run died with "too many steps".
facts.yaml is the only source of dates, so a row we did not write must be DELETED, never submitted; and a date only counts as set when
the widget's own inputs read back exactly - the old check accepted any string CONTAINING the year, so a stale 06/2023 "passed" a 2023 check.
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, workday  # noqa: E402
from core import profile  # noqa: E402

EXPERIENCE = profile.facts()["experience"]


def _row(i, title="", company="", month="", year=""):
    """One Workday experience row: a Delete button in the row's own header, then the formField-* controls."""
    return f"""
<div class="row" id="row{i}">
  <h5>Work Experience {i}</h5><button type="button" onclick="this.closest('.row').remove()">Delete</button>
  <div data-automation-id="formField-jobTitle"><label>Job Title</label><input value="{title}"></div>
  <div data-automation-id="formField-companyName"><label>Company</label><input value="{company}"></div>
  <div data-automation-id="formField-location"><label>Location</label><input value=""></div>
  <div data-automation-id="formField-currentlyWorkHere"><label>I currently work here</label><input type="checkbox"></div>
  <div data-automation-id="formField-startDate"><label>From</label>
    <input data-automation-id="dateSectionMonth-input" value="{month}"><input data-automation-id="dateSectionYear-input" value="{year}">
    <button type="button" data-automation-id="dateSectionMonth-display" class="disp">MM</button>
    <button type="button" data-automation-id="dateSectionYear-display" class="disp">YYYY</button></div>
  <div data-automation-id="formField-endDate"><label>To</label>
    <input data-automation-id="dateSectionMonth-input" value=""><input data-automation-id="dateSectionYear-input" value="">
    <button type="button" data-automation-id="dateSectionMonth-display" class="disp">MM</button>
    <button type="button" data-automation-id="dateSectionYear-display" class="disp">YYYY</button></div>
  <div data-automation-id="formField-roleDescription"><label>Role Description</label><textarea></textarea></div>
</div>"""


PAGE = """<!doctype html><meta charset=utf-8><title>My Experience</title><body>
<h2>My Experience</h2>
<div id="rows">""" + "".join([
    _row(1, "Marketing Analyst", "Example Retail Co", "09", "2022"),   # wrong end date, Start present
    _row(2, "Marketing Analyst", "Example Retail Co"),                 # duplicate of row 1, no dates
    _row(3),                                                      # entirely empty
    _row(4, "Marketing Analyst", "Example Retail Co"),                 # beyond her three real roles
]) + """</div>
<div data-automation-id="formField-schoolName"><label>School or University</label><input value=""></div>
<div data-automation-id="formField-degree"><label>Degree</label>
  <button type="button" aria-haspopup="listbox">Select One</button>
  <ul role="listbox" style="display:none"><li role="option">Select One</li><li role="option">MSc</li>
  <li role="option">Bachelor of Engineering</li></ul></div>
<div data-automation-id="formField-firstYearAttended"><label>From</label>
  <input data-automation-id="dateSectionYear-input" value="">
  <button type="button" data-automation-id="dateSectionYear-display" class="disp">YYYY</button></div>
<div data-automation-id="formField-lastYearAttended"><label>To</label>
  <input data-automation-id="dateSectionYear-input" value="">
  <button type="button" data-automation-id="dateSectionYear-display" class="disp">YYYY</button></div>
<div data-automation-id="formField-schoolName"><label>School or University</label><input value=""></div>
<div data-automation-id="formField-degree"><label>Degree</label>
  <button type="button" aria-haspopup="listbox">Select One</button>
  <ul role="listbox" style="display:none"><li role="option">Select One</li><li role="option">MSc</li>
  <li role="option">Bachelor of Engineering</li></ul></div>
<div data-automation-id="formField-firstYearAttended"><label>From</label>
  <input data-automation-id="dateSectionYear-input" value="">
  <button type="button" data-automation-id="dateSectionYear-display" class="disp">YYYY</button></div>
<div data-automation-id="formField-lastYearAttended"><label>To</label>
  <input data-automation-id="dateSectionYear-input" value="">
  <button type="button" data-automation-id="dateSectionYear-display" class="disp">YYYY</button></div>
<script>
  // the display tile swallows the keyboard, as it does on the real widget (the digits never reach the inputs)
  document.querySelectorAll('button.disp').forEach(b => b.onclick = () => document.body.focus());
  document.querySelectorAll('[aria-haspopup=listbox]').forEach(b => b.onclick = () => {
    const l = b.parentElement.querySelector('[role=listbox]'); l.style.display = 'block';
    l.querySelectorAll('[role=option]').forEach(o => o.onclick = () => { b.innerText = o.innerText; l.style.display = 'none'; });
  });
</script></body>"""


@pytest.fixture
def exp_page(tmp_path):
    f = tmp_path / "my_experience.html"
    f.write_text(PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        yield pg
        b.close()


def test_surplus_rows_are_deleted_and_only_her_real_three_remain(exp_page):
    pg = exp_page
    filled = []
    assert workday.repair_experience(pg, filled) is True
    titles = pg.locator('[data-automation-id="formField-jobTitle"] input').evaluate_all("els => els.map(e => e.value)")
    companies = pg.locator('[data-automation-id="formField-companyName"] input').evaluate_all("els => els.map(e => e.value)")
    assert len(titles) == len(EXPERIENCE) == 3, titles
    assert titles == [e["title"] for e in EXPERIENCE], titles
    assert companies == [e["company"] for e in EXPERIENCE], companies
    assert pg.locator(".row").count() == 3
    assert any(a == "experience rows beyond her history" and "deleted 1" in b for a, b in filled), filled


def test_every_row_gets_her_real_dates_and_none_is_left_empty(exp_page):
    pg = exp_page
    workday.fill_experience(pg, [])
    starts = pg.locator('[data-automation-id="formField-startDate"]').evaluate_all(
        "els => els.map(e => [...e.querySelectorAll('input')].map(i => i.value).join('/'))")
    ends = pg.locator('[data-automation-id="formField-endDate"]').evaluate_all(
        "els => els.map(e => [...e.querySelectorAll('input')].map(i => i.value).join('/'))")


    def want(month, year):
        m = workday.MONTHS[month[:3].lower()]
        return f"{m:02d}/{year}"

    assert starts == [want(e["start"].split()[0], e["start"].split()[1]) for e in EXPERIENCE], starts
    assert ends == [want(e["end"].split()[0], e["end"].split()[1]) for e in EXPERIENCE], ends


def test_a_stale_date_is_replaced_exactly_not_accepted_by_substring(exp_page):
    """The 09/2022 row: desired is Jan 2025. Typing cannot land (the display tile swallows it), so the widget must be repaired by fill
    and verified - the old 'year in vals' check would have said 'set' with the wrong month and year still on the page."""
    pg = exp_page
    box = pg.locator('[data-automation-id="formField-endDate"]').first
    assert workday._type_date(box, 1, "2025") is True
    m, y = workday._read_date(box)
    assert (m, y) == ("01", "2025"), (m, y)


def test_a_date_that_is_already_right_is_not_touched(exp_page):
    pg = exp_page
    box = pg.locator('[data-automation-id="formField-startDate"]').first
    pg.evaluate("() => { window.dispClicks = 0; document.querySelectorAll('button.disp').forEach(b => b.addEventListener('click', () => window.dispClicks++)); }")
    assert workday._type_date(box, 9, "2022") is True
    assert workday._read_date(box) == ("09", "2022")
    assert pg.evaluate("() => window.dispClicks") == 0, "a date that already reads back exactly was clicked and retyped"


def test_the_navigator_repairs_workday_experience_before_asking_her_for_fields():
    src = __import__("inspect").getsource(agent.navigate)
    assert "repair_experience" in src, "the navigator never rewrites Workday 'My Experience'"
    assert src.index("repair_experience") < src.index("for lab_ in _repair_named_required"), \
        "the experience section must be repaired before a banner-named field is treated as a question for her"
    assert "exp_runs < 2" in src, "the repair is unbounded (it would retype dates on every step)"
