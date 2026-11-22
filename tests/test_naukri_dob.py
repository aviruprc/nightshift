"""Naukri's Date-of-Birth widget is three number boxes, not a chip and not one text box (KB-69).

a company parked twice ("Naukri showed no option matching ... 'Bengaluru, Karnataka, India'", then "unanswered
required: Current Location?"). The drawer saved under `data/failures/*_OmnicomGlob_stuck.html` shows why: the panel had moved on
to 'Date of Birth' rendered as `input[type=number][name=day|month|year]` with a DISABLED Save, and `answer_chat`'s generic text
branch would fill the DAY box with the whole answer - a number input cannot take it, so the panel never advanced and the next
report named the previous question instead of the real one.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import naukri_apply  # noqa: E402

DRAWER_HTML = """
<div class="chatbot_Drawer">
  <ul><li><span>Date of Birth</span></li></ul>
  <div id="dob">
    <input type="number" name="day" placeholder="DD" maxlength="2">
    <input type="number" name="month" placeholder="MM" maxlength="2">
    <input type="number" name="year" placeholder="YYYY" maxlength="4">
  </div>
  <div><div class="sendMsg">Save</div></div>
</div>"""


def _page(pw, html):
    b = pw.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page()
    pg.set_content(html)
    return b, pg, pg.locator(".chatbot_Drawer").first


def test_the_saved_drawer_is_recognised_as_a_dob_widget():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, pg, drawer = _page(pw, DRAWER_HTML)
        assert naukri_apply.dob_widget(drawer) is True
        b.close()


def test_an_ordinary_text_question_is_not_a_dob_widget():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, pg, drawer = _page(pw, "<div class='chatbot_Drawer'><span>Current Location</span>"
                                  "<input type='text' name='answer'></div>")
        assert naukri_apply.dob_widget(drawer) is False
        b.close()


def test_her_recorded_date_is_typed_into_all_three_boxes():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b, pg, drawer = _page(pw, DRAWER_HTML)
        ok, notes = naukri_apply.fill_dob_widget(drawer)
        assert ok is True, notes
        assert drawer.locator("input[name=day]").input_value() == "17"
        assert drawer.locator("input[name=month]").input_value() == "9"
        assert drawer.locator("input[name=year]").input_value() == "1993"
        b.close()


def test_a_box_that_swallows_the_value_is_reported_never_claimed():
    from playwright.sync_api import sync_playwright
    html = DRAWER_HTML.replace('name="month"', 'name="month" readonly')
    with sync_playwright() as pw:
        b, pg, drawer = _page(pw, html)
        ok, notes = naukri_apply.fill_dob_widget(drawer)
        assert ok is False, notes
        assert any("month" in n for n in notes), notes
        assert drawer.locator("input[name=month]").input_value() == ""
        b.close()


def test_the_widget_is_handled_before_the_question_text_is_parsed():
    """The fix must run before the chip/option logic, or the DOB question is answered as a text question again."""
    src = inspect.getsource(naukri_apply.answer_chat)
    assert src.index("if dob_widget(drawer):") < src.index("qs = [i for i, x in enumerate(lines)")
    assert "_drawer_evidence(pg, drawer, \"dob\")" in src


def test_the_widget_is_answered_once_then_reported_if_it_stays():
    """KB-72: the widget stays in the panel's DOM, so refilling it every turn burned all 12 turns (a company)."""
    src = inspect.getsource(naukri_apply.answer_chat)
    assert "if dob_done:" in src
    assert src.index("if dob_done:") < src.index("ok_dob, dob_notes = fill_dob_widget(drawer)")
    assert "dob_done = True" in src
    assert "_drawer_evidence(pg, drawer, \"dob_stuck\")" in src


def test_the_question_itself_is_never_offered_back_as_an_option():
    q = "Do you have a bachelor's degree or higher qualification?"
    opts = [q, "Yes", "No"]
    assert naukri_apply.pick_option(q, opts, "Yes") == "Yes"
    assert naukri_apply.pick_option(q, opts, "") is None
    # a degree-LEVEL list still answers with her real level
    assert naukri_apply.pick_option(q, ["Bachelor's Degree", "Doctorate"], "Yes") == "Bachelor's Degree"


def test_too_many_turns_leaves_the_panel_behind():
    src = inspect.getsource(naukri_apply.answer_chat)
    assert "_drawer_evidence(pg, drawer, \"toomany\")" in src
    assert "last asked:" in src
