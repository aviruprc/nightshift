"""A split date of birth must be either her real date or empty - never half-filled (KB-62).

a company (Oracle CXS apply): the navigator filled "Date of Birth Year" = 1993 and left "Date of Birth
Month" empty. The page warned "You need to enter the whole date otherwise the date won't be saved.", the drawer never
advanced, and the job failed as "navigator: page stopped changing". The saved snapshot (`a saved failure page`) shows
month='' / year='1993'. A half-date can never be submitted, and a wrong one would be a fabricated fact, so the completion is
verified by re-reading the control and anything that does not match her recorded date is reported, not replaced.
"""
import inspect
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import agent, widgets  # noqa: E402


def test_a_month_is_matched_by_name_or_number():
    assert widgets._part_matches("month", "January", 1)
    assert widgets._part_matches("month", "Jan", 1)
    assert widgets._part_matches("month", "1", 1)
    assert widgets._part_matches("month", "01", 1)
    assert not widgets._part_matches("month", "", 1)
    assert not widgets._part_matches("month", "March", 1)
    assert widgets._part_matches("year", "1993", 1993)
    assert widgets._part_matches("day", "25", 25)
    assert not widgets._part_matches("day", "2", 25)


def test_the_split_date_labels_are_recognised():
    snap = {"els": [
        {"i": 62, "label": "Date of Birth Month", "value": ""},
        {"i": 63, "label": "Date of Birth Year", "value": "1993"},
        {"i": 30, "label": "Phone Number", "value": "9000000001"},
        {"i": 8, "label": "Month of experience", "value": ""},
    ]}
    groups = {}
    for e in snap["els"]:
        m = widgets.DOB_PART_RX.match(e["label"])
        if m and widgets.DOB_Q.search(e["label"]):
            groups.setdefault(m.group("base").strip().lower(), {})[m.group("part").lower()] = e
    assert list(groups) == ["date of birth"]
    assert set(groups["date of birth"]) == {"month", "year"}


def test_the_missing_month_is_completed_and_the_year_left_alone():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content(
            "<body><label>Date of Birth Month <input data-ag='1' aria-label='Date of Birth Month' value=''></label>"
            "<label>Date of Birth Year <input data-ag='2' aria-label='Date of Birth Year' value='1993'></label></body>")
        snap = {"els": [{"i": 1, "label": "Date of Birth Month", "value": ""},
                        {"i": 2, "label": "Date of Birth Year", "value": "1993"}]}
        notes = widgets.fill_missing_dob_parts(pg, snap, dob=datetime(1993, 9, 17))
        assert any(n.startswith("Date of Birth Month: set to September") for n in notes), notes
        assert pg.locator("[data-ag='1']").input_value() == "September"
        assert pg.locator("[data-ag='2']").input_value() == "1993"
        b.close()


def test_a_wrong_existing_value_is_reported_never_overwritten():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<body><input data-ag='1' aria-label='Date of Birth Month' value='March'>"
                       "<input data-ag='2' aria-label='Date of Birth Year' value='1995'></body>")
        snap = {"els": [{"i": 1, "label": "Date of Birth Month", "value": "March"},
                        {"i": 2, "label": "Date of Birth Year", "value": "1995"}]}
        notes = widgets.fill_missing_dob_parts(pg, snap, dob=datetime(1993, 9, 17))
        assert any("shows 'March'" in n for n in notes), notes
        assert any("shows '1995'" in n for n in notes), notes
        assert pg.locator("[data-ag='1']").input_value() == "March"   # untouched: a submitted fact is not our guess to change
        b.close()


def test_no_date_of_birth_on_the_page_is_a_no_op():
    assert widgets.fill_missing_dob_parts(None, {"els": [{"i": 1, "label": "Phone Number", "value": ""}]},
                                          dob=datetime(1993, 9, 17)) == []


def test_the_navigator_completes_a_split_date_before_it_asks_the_model():
    src = inspect.getsource(agent.navigate)
    assert "fill_missing_dob_parts(pg, snap)" in src
    assert src.index("fill_missing_dob_parts(pg, snap)") < src.index("if page_loading(snap):")


def test_earliest_available_date_is_completed_with_today_and_experience_start_dates_are_not_touched():
    """Oracle HCM job 350896: 'Earliest Available Date Month = 06' was typed by the model, a date nobody recorded."""
    from datetime import date
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<body><input data-ag='1' aria-label='Earliest Available Date Day' value=''>"
                       "<input data-ag='2' aria-label='Earliest Available Date Month' value=''>"
                       "<input data-ag='3' aria-label='Earliest Available Date Year' value=''>"
                       "<input data-ag='4' aria-label='Start Date Month' value=''></body>")
        snap = {"els": [{"i": 1, "label": "Earliest Available Date Day", "value": ""}, {"i": 2, "label": "Earliest Available Date Month", "value": ""},
                        {"i": 3, "label": "Earliest Available Date Year", "value": ""}, {"i": 4, "label": "Start Date Month", "value": ""}]}
        notes = widgets.fill_available_date_parts(pg, snap)
        today = date.today()
        assert pg.locator("[data-ag='3']").input_value() == str(today.year), notes
        assert pg.locator("[data-ag='2']").input_value() == widgets.MONTHS[today.month - 1]
        assert pg.locator("[data-ag='4']").input_value() == ""          # an experience row's start date is never this
        b.close()
