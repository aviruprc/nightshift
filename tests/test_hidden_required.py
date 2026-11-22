"""A required control the page HIDES still blocks the form (KB-50, a company).

`#applicantSource` ("How did you hear about us?") is `required`, lives in a `class="... hidden"` wrapper and was empty,
so `#next` stayed disabled although every visible field was correct. SNAP_JS never saw it, so nothing filled it, and the
navigator named two OPTIONAL SMS checkboxes (aria-required="false") as the missing consent - a claim about the page that
the page contradicted. `widgets.hidden_required` finds such controls, `set_hidden_value` answers one from a recorded
fact and verifies it stuck, and a hidden question her records cannot answer parks the job with the page's own options.
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from apply import agent, widgets  # noqa: E402

FORM = """<html><body>
<form>
  <label for="email">Email address*</label><input id="email" required value="asha.example@example.com">
  <label for="phone">Phone number*</label><input id="phone" required value="9000000001">
  <div class="row form-group" style="display:none">
    <label for="applicantSource">How did you hear about us?<span class="required">*</span></label>
    <select id="applicantSource" required>
      <option value="">Please Select</option>
      <option value="JB_LinkedIn">JB_LinkedIn</option>
    </select>
  </div>
  <input type="checkbox" id="smsOptIn" aria-required="false"><label for="smsOptIn">I consent to receive SMS</label>
  <button type="submit" id="next" disabled>Next</button>
</form></body></html>"""


@pytest.fixture  # function-scoped (AGENTS.md: a module-scoped Playwright fixture breaks later sync_playwright calls)
def page():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b.new_page()
        b.close()


def test_the_hidden_required_source_select_is_found_with_its_options(page):
    page.set_content(FORM)
    items = widgets.hidden_required(page)
    assert [i["id"] for i in items] == ["applicantSource"]
    assert "how did you hear" in items[0]["label"].lower()
    assert items[0]["options"] == ["Please Select", "JB_LinkedIn"]


def test_a_visible_required_field_is_not_reported_again(page):
    """The visible ones are already in the snapshot: reporting them here would double-ask and park good jobs."""
    page.set_content(FORM.replace('style="display:none"', ''))
    assert widgets.hidden_required(page) == []


def test_a_recorded_answer_is_only_set_when_the_page_offers_it(page):
    page.set_content(FORM)
    item = widgets.hidden_required(page)[0]
    # her recorded fact is "Company careers page"; the page offers only JB_LinkedIn -> never invent the match
    assert widgets.set_hidden_value(page, item, "Company careers page") is False
    assert page.locator("#applicantSource").input_value() == ""
    assert widgets.set_hidden_value(page, item, "JB_LinkedIn") is True
    assert page.locator("#applicantSource").input_value() == "JB_LinkedIn"


def test_the_navigator_asks_her_before_claiming_a_consent_box_is_missing():
    src = inspect.getsource(agent.navigate)
    assert "_w.hidden_required(pg)" in src
    assert "_w.set_hidden_value(pg, h_, str(ans))" in src
    assert "NO unticked box is marked required" in src
    # the hidden-required check must come BEFORE the consent claim is built
    assert src.index("_w.hidden_required(pg)") < src.index("required consent box never ticked")
    assert src.index("status=\"needs_you\", screenshot=str(shot),\n                                           detail=(f\"unanswered required: {q}") > 0


def test_the_saved_claim_is_contradicted_by_its_own_html():
    """The two boxes the navigator blamed carry aria-required="false" in the saved page; the hidden select is required."""
    f = ROOT / "data" / "failures" / "corpus_12.html"
    if not f.exists():
        __import__("pytest").skip("saved failure page not present (private corpus in data/failures)")
    html = f.read_text(encoding="utf-8", errors="replace")
    assert 'id="smsOptIn"' in html and 'aria-required="false"' in html
    assert 'id="applicantSource"' in html
    assert 'is-valid hidden' in html
