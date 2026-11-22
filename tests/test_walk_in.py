"""A Naukri walk-in interview listing is not a layout change and not retryable (a company).
Evidence: data/screenshots//.png - title 'Walk-in || ...', 'Time and Venue 21st November, AM - 1:00 PM',
button 'I am interested', no 'Apply'."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import errors, naukri_apply  # noqa: E402

BODY = ("Walk-in || Administrative Support Specialist Identity and Access Operations l1,l2 Cyraac Services\n"
        "Time and Venue\n21st November , 10.00 AM - 1.00 PM\n5th Floor, Commerce Mantri 12, BTM Bengaluru").replace("\n", "\n")


def test_a_walk_in_listing_is_named_and_parked_for_her():
    d = naukri_apply.walk_in_detail(BODY, {"i am interested": 1, "save": 1})
    assert "walk-in interview listing" in d and "21st November" in d
    assert errors.classify("needs_you", d) == "personal"


def test_a_normal_job_or_a_page_with_an_apply_button_is_not_a_walk_in():
    assert naukri_apply.walk_in_detail("Data Analyst role. Time and Venue none", {"i am interested": 1}) == ""
    assert naukri_apply.walk_in_detail(BODY, {"apply": 1}) == ""


def test_the_missing_button_wording_stays_transient():
    assert errors.classify("needs_you", "no Apply button on a signed-in Naukri page (layout change)") == "transient"
