"""A skeleton page says 'Loading'; it is never a form to click Save on (a company,, KB-47).

`a saved failure page` (job, Workday `icon.wd3.myworkdayjobs.com`, step 4 of 5
"Voluntary Disclosures") holds the page text "Personal information Loading Terms and Conditions Loading Back Save and
Continue" and only 11 elements. The saved HTML shows Workday's skeleton blocks (`<div class="css-i19yjz">Loading</div>` with
`aria-hidden` placeholder bars). The navigator clicked "Save and Continue" sixteen times and the job was stamped
`fail_class='logic'` - no retry - although the page had simply not rendered yet.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, errors  # noqa: E402

ICON_TEXT = ("Skip to main content Settings asha.example@example.com Careers Page Search for Jobs Candidate Home Job Alerts "
             "Back to Job Posting Financial Analyst II completed step 1 of 5 My Information completed step 2 of 5 My Experience "
             "completed step 3 of 5 Application Questions current step 4 of 5 Voluntary Disclosures step 5 of 5 Review "
             "Voluntary Disclosures * Indicates a required field Personal information Loading Terms and Conditions Loading "
             "Back Save and Continue ICON Privacy Policy 2026 Workday, Inc. All rights reserved.")
FEW = [{"i": n, "tag": "button", "text": "x"} for n in range(11)]
MANY = [{"i": n, "tag": "input", "label": f"field {n}"} for n in range(40)]


def test_the_icon_skeleton_is_recognised_as_loading():
    assert agent.page_loading({"text": ICON_TEXT, "els": FEW}) is True


def test_a_loaded_page_that_mentions_loading_elsewhere_is_not_held_back():
    assert agent.page_loading({"text": ICON_TEXT, "els": MANY}) is False, "a full form with one lazy label must not stop the navigator"
    assert agent.page_loading({"text": "Application Questions Please answer the following", "els": FEW}) is False


def test_a_page_stuck_on_loading_is_named_and_transient():
    detail = ("navigator: the page's sections never rendered - stayed on 'Loading' for 7 checks (~40 s) at "
              "icon.wd3.myworkdayjobs.com, reloaded once; retried later")
    assert errors.classify("failed", detail) == "transient", "a skeleton page must be retried, never parked as our logic bug"


def test_the_navigator_waits_and_reloads_instead_of_clicking_save():
    import inspect
    src = inspect.getsource(agent.navigate)
    assert "if page_loading(snap):" in src, "the skeleton check exists but the navigator never asks it"
    i = src.index("if page_loading(snap):")
    window = src[i:i + 2000]
    assert "pg.wait_for_timeout(5000)" in window, "a loading page must be waited on"
    assert "pg.reload(" in window, "a loading page that never renders must be reloaded once"
    assert "continue" in window, "the loading page must skip the LLM step, not be plan-driven"
    assert "never rendered" in window, "the give-up path must name the real cause"
