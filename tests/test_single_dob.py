"""A single date-of-birth control must be set and VERIFIED, or left alone (KB-66).

a company Acceleration Center failed four times with "page stopped changing
(Enter all mandatory fields: This field is required.)" - and all four saved snapshots show 'Date of Birth*' EMPTY
while Gender, Country, City, Marital Status and Nationality were filled. The control is a react-datepicker text box,
which only commits on Enter/blur; nothing in the applier could put a date into it.
"""
import inspect
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import agent, widgets  # noqa: E402


def test_a_shown_date_parses_back_to_her_date():
    dob = datetime(1993, 9, 17)
    for shown in ("17/09/1993", "17-09-1993", "1993-09-17", "09/17/1993", "17 September 1993", "17 Sep 1993"):
        assert widgets._date_value_matches(shown, dob) is True, shown
    for shown in ("", "17/09/1992", "16/09/1993", "1993-09-16", "not a date"):
        assert widgets._date_value_matches(shown, dob) is False, shown


def test_the_single_date_of_birth_is_committed_and_verified():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<body><input data-ag='16' id='dob' aria-label='Date of Birth' value=''>"
                       "<script>document.getElementById('dob').addEventListener('keydown',"
                       "e => { if (e.key === 'Enter') document.getElementById('dob').blur(); });</script></body>")
        snap = {"els": [{"i": 16, "label": "Date of Birth*", "value": "", "tag": "input", "type": "text"}]}
        notes = widgets.fill_single_dob(pg, snap, dob=datetime(1993, 9, 17))
        assert any(n.startswith("Date of Birth*: set to") for n in notes), notes
        assert pg.locator("[data-ag='16']").input_value() == "17/09/1993"
        b.close()


def test_a_field_that_swallows_the_value_is_reported_and_left_empty():
    """A value we cannot read back is not a value we may claim: the field must stay empty and the note must say so."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<body><input data-ag='16' readonly aria-label='Date of Birth' value=''></body>")
        snap = {"els": [{"i": 16, "label": "Date of Birth*", "value": "", "tag": "input", "type": "text"}]}
        notes = widgets.fill_single_dob(pg, snap, dob=datetime(1993, 9, 17))
        assert any("could not be set" in n for n in notes), notes
        assert pg.locator("[data-ag='16']").input_value() == ""
        b.close()


def test_a_filled_or_split_control_is_never_touched():
    snap = {"els": [{"i": 1, "label": "Date of Birth", "value": "17/09/1993"},
                    {"i": 2, "label": "Date of Birth Month", "value": ""},
                    {"i": 3, "label": "Phone Number", "value": ""}]}
    assert widgets.fill_single_dob(None, snap, dob=datetime(1993, 9, 17)) == []


def test_the_navigator_completes_a_single_date_of_birth_too():
    src = inspect.getsource(agent.navigate)
    assert "fill_single_dob(pg, snap)" in src
    assert src.index("fill_missing_dob_parts(pg, snap)") < src.index("fill_single_dob(pg, snap)")
