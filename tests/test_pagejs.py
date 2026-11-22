"""ONE field-scanner truth.

Real bugs these tests name:
  * two Oracle HCM jobs were lost because `engine.EXTRACT_JS` (rules) and `agent.SNAP_JS`
    (navigator) decided differently what a form control is. `EXTRACT_JS` had no trap wording for
    the visible <input name="honey-pot"> and dropped the 0x0 consent checkbox; the navigator's
    wording list had 'honeypot' but not the hyphenated 'honey-pot'.
  * a hidden-but-real styled checkbox/radio must stay in the model; a hidden text box is noise.

`agent.SNAP_JS` still carries its private copy (agent.py is owned by another agent); the exact
patch is recorded in the task report. `EXTRACT_JS` is built from `apply.pagejs`, so at least the
rules scanner and these tests read the one definition.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import answers_map as am  # noqa: E402
from apply import engine, pagejs  # noqa: E402

# Oracle HCM guest identity gate, reproduced from the live page (verified).
ORACLE_GATE = """<!doctype html><meta charset=utf-8>
<style>.input-row__hidden-control{position:absolute;width:0;height:0;opacity:0;padding:0;margin:0;border:0}</style>
<div class="apply-flow">
  <label for="primary-email">Email Address *</label>
  <input type="email" id="primary-email" name="primary-email" required>
  <input type="text" name="honey-pot" tabindex="-1">
  <input type="checkbox" id="legal-disclaimer-checkbox" class="input-row__hidden-control">
  <label for="legal-disclaimer-checkbox">I agree with the terms and conditions *</label>
  <input type="file">
  <button>NEXT</button>
</div>"""


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright  # function-scoped: module scope breaks later sync_playwright calls
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        yield pg
        b.close()


def _scan(pg, html):
    pg.set_content(html)
    return pg.evaluate(engine.EXTRACT_JS)


# --------------------------------------------------------------------------- one definition


def test_extract_js_is_built_from_the_shared_helpers():
    """The deterministic scanner must not keep a second copy of the trap/visibility/label rules."""
    assert pagejs.HELPERS_JS in engine.EXTRACT_JS
    assert pagejs.TRAP_RX_SRC in engine.EXTRACT_JS


def test_snap_js_is_built_from_the_shared_helpers():
    """The navigator must read the SAME rules (: its private list missed 'honey-pot', so the LLM was still offered Oracle's trap)."""
    from apply import agent
    assert pagejs.HELPERS_JS in agent.SNAP_JS
    assert pagejs.TRAP_RX_SRC in agent.SNAP_JS
    # HELPERS_JS itself defines each helper ONCE; a second definition would mean SNAP_JS kept a private copy (which is how the lists drifted).
    for helper in ("const vis =", "const lab =", "const trapBlob ="):
        assert agent.SNAP_JS.count(helper) == 1, f"duplicate definition in SNAP_JS: {helper}"


def test_the_navigator_never_offers_the_honeypot_and_does_offer_the_consent_box(page):
    from apply import agent
    page.set_content(ORACLE_GATE)
    els = page.evaluate(agent.SNAP_JS)["els"]
    blob = " ".join((e.get("label") or "") + " " + (e.get("text") or "") + " " + (e.get("value") or "") for e in els).lower()
    assert "honey" not in blob, els
    assert any(e["type"] == "checkbox" and "terms and conditions" in (e["label"] or "") for e in els), els
    assert any(e["type"] == "email" for e in els), els


@pytest.mark.parametrize("word", [
    "Enter website. This input is for robots only, do not enter if you're human.",  # a company trap
    "leave this field blank",
    "Leave it blank",
    "honeypot",
    "honey-pot",       # Oracle HCM: SNAP_JS's old list missed this spelling
    "honey pot",
    "bot-trap",
    "bot trap",
    "do not fill",
    "url2",
    "captcha",
])
def test_the_shared_trap_list_covers_both_scanners_old_wordings(word):
    assert pagejs.is_trap_text(word), word


def test_a_real_company_name_is_not_a_trap():
    """The old engine list used bare 'honey', which would also swallow a company field."""
    for text in ("Honeywell location", "Robots and automation interest"):
        assert not pagejs.is_trap_text(text), text


# --------------------------------------------------------------------------- real Oracle markup


def test_the_hyphenated_trap_is_never_numbered_and_the_consent_box_is(page):
    fields = _scan(page, ORACLE_GATE)
    assert not [f for f in fields if "honey" in (f["name"] + " " + f["label"]).lower()], fields
    box = next(f for f in fields if f["type"] == "checkbox")
    assert "terms and conditions" in box["label"]
    assert box["hidden"] is True  # hidden-but-real is reported, not silently dropped
    assert am.CONSENT.search(box["label"]) and not am.MARKETING.search(box["label"])


def test_an_opacity_zero_text_input_is_a_trap_not_a_field(page):
    """A hidden text box has no visible control a human could use: the geometry rule drops it."""
    fields = _scan(page, '<input type="text" name="email2" style="opacity:0;width:100px;height:20px">'
                         '<input type="text" id="real" name="real">')
    assert [f["name"] for f in fields] == ["real"], fields


def test_a_hidden_radio_with_a_label_is_kept_like_a_styled_checkbox(page):
    """Radio groups are styled the same way as Oracle's consent box; dropping them broke answers."""
    fields = _scan(page, '<style>.hc{width:0;height:0;opacity:0}</style>'
                         '<input type="radio" id="r1" name="shift" class="hc" value="yes">'
                         '<label for="r1">Yes, I am available</label>')
    assert [f["type"] for f in fields] == ["radio"], fields


def test_an_unlabelled_hidden_checkbox_is_still_ignored(page):
    fields = _scan(page, '<div><input type="checkbox" name="tracker" style="display:none">'
                         '<input type="text" id="n" name="n"></div>')
    assert [f["type"] for f in fields] == ["text"], fields


def test_a_hidden_file_input_is_kept(page):
    """The upload control is always behind a styled button; its real input is usually hidden."""
    fields = _scan(page, '<input type="file" name="cv" style="display:none">')
    assert [f["type"] for f in fields] == ["file"], fields
