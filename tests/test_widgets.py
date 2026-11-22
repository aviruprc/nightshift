"""P0.3 shared widget toolkit on local HTML fixtures: one test per widget quirk seen in live runs. Fix the tool once, every portal benefits."""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import engine, widgets  # noqa: E402

PAGE = """<!doctype html><meta charset=utf-8><body>
<select id=nat><option>Select</option><option>Indian</option><option>American</option></select>
<div><label>How did you hear about us?</label>
 <div id=hear role=combobox tabindex=0 onclick="document.getElementById('lb').style.display='block'">Select one</div>
 <ul id=lb role=listbox style="display:none"><li role=option onclick="pick(this)">Employee referral</li><li role=option onclick="pick(this)">Company Career Site</li><li role=option onclick="pick(this)">Agency</li></ul></div>
<div><input id=school placeholder="Type to search school" oninput="flt(this.value)">
 <ul id=sl role=listbox style="display:none"><li role=option onclick="pk(this)">Larkmoor University, Westfield School of Statistics</li><li role=option onclick="pk(this)">Brightwater College</li></ul><div id=out></div></div>
<label><input type=checkbox id=terms style="opacity:0;position:absolute"><span id=termslab onclick="document.getElementById('terms').checked=!document.getElementById('terms').checked">I accept the terms</span></label>
<script>
function pick(li){document.getElementById('hear').innerText=li.innerText;document.getElementById('lb').style.display='none'}
function flt(v){const sl=document.getElementById('sl');sl.style.display='block';[...sl.children].forEach(li=>li.style.display=li.innerText.toLowerCase().includes(v.toLowerCase())?'block':'none')}
function pk(li){document.getElementById('out').innerText=li.innerText;document.getElementById('school').value=li.innerText;document.getElementById('sl').style.display='none'}
</script></body>"""


@pytest.fixture  # NOT module-scoped: an open sync_playwright from another test makes later sync contexts raise "Sync API inside the asyncio loop"
def pg(tmp_path):
    f = tmp_path / "p.html"
    f.write_text(PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        p = b.new_page()
        p.goto(f.as_uri())
        yield p
        b.close()


def test_native_select_picks_closest_option(pg):
    assert widgets.pick_option(pg, pg.locator("#nat"), "Indian", "Nationality")
    assert pg.locator("#nat").input_value() == "Indian"


def test_custom_listbox_how_did_you_hear_accepts_any_plausible_source(pg):  # bug: navigator looped on 'How Did You Hear' (companies)
    assert widgets.pick_option(pg, pg.locator("#hear"), "Company careers page", "How did you hear about us?")
    assert "Career Site" in pg.locator("#hear").inner_text()


def test_searchable_picker_uses_distinctive_search_terms(pg):  # bug: School picker needs 'Larkmoor' not the whole institution string
    assert widgets.pick_option(pg, pg.locator("#school"), "Larkmoor University, Westfield School of Statistics (WSS)", "School")
    assert "Larkmoor" in pg.locator("#out").inner_text()


def test_visually_hidden_checkbox_is_ticked_through_its_label(pg):  # bug: Oracle/RippleHire checkboxes only registered a real click
    assert engine._tick(pg, pg.locator("#terms"))
    assert pg.locator("#terms").is_checked()


def test_search_terms_prefer_specific_words():
    t = widgets.search_terms("Larkmoor University, Westfield School of Statistics (WSS)")
    assert "Larkmoor" in t and "WSS" in t and t[0].startswith("Larkmoor University")


def test_snapshot_sees_inputs_inside_open_shadow_roots(tmp_path):  # bug: Darwinbox login inputs invisible to the navigator (Evalueserve)
    from apply import agent
    f = tmp_path / "shadow.html"
    f.write_text("<!doctype html><body><login-box></login-box><script>customElements.define('login-box', class extends HTMLElement {constructor(){super();"
                 "this.attachShadow({mode:'open'}).innerHTML='<label>Email<input id=em type=email></label><button>Sign In</button>'}})</script></body>", encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        labels = [(e.get("label") or e.get("text")) for e in snap["els"]]
        assert any("Email" in (x or "") for x in labels) and "Sign In" in labels, labels
        i = next(e["i"] for e in snap["els"] if e["tag"] == "input")
        pg.locator(f'[data-ag="{i}"]').first.fill("a@b.com")  # and the numbered locator pierces the shadow root too
        assert pg.evaluate("document.querySelector('login-box').shadowRoot.querySelector('input').value") == "a@b.com"
        b.close()


WD_PICKER = """<!doctype html><meta charset=utf-8><body>
<div data-automation-id="formField-source"><label>How Did You Hear About Us?*</label>
 <div data-automation-id="multiSelectContainer"><div id=chips data-automation-id="selectedItemList"></div>
  <input id=src type=text oninput="show(this.value)"><span data-automation-id="promptIcon"></span></div>
 <div id=opts></div></div>
<script>
const tree = {"social media": ["LinkedIn", "Facebook"], "job board": ["Naukri", "Indeed"], "career site": []};
function show(v){const o=document.getElementById('opts');o.innerHTML='';
  Object.keys(tree).filter(k=>!v||k.includes(v.toLowerCase())||tree[k].some(c=>c.toLowerCase().includes(v.toLowerCase()))).forEach(k=>{
    const d=document.createElement('div');d.setAttribute('data-automation-id','promptOption');d.textContent=k[0].toUpperCase()+k.slice(1);
    d.onclick=()=>{ if(tree[k].length){o.innerHTML='';tree[k].forEach(c=>{const e=document.createElement('div');e.setAttribute('data-automation-id','promptOption');e.textContent=c;e.onclick=()=>pick(c);o.appendChild(e)})} else pick(d.textContent)};o.appendChild(d)})}
function pick(t){document.getElementById('chips').innerHTML='<div data-automation-id="selectedItem">'+t+'</div>';document.getElementById('opts').innerHTML='';document.getElementById('src').value=''}
</script></body>"""


def test_workday_style_nested_searchable_picker(tmp_path):  # bug: a company 'How Did You Hear' (Workday multiselect with nested categories) looped in the navigator
    f = tmp_path / "wd.html"
    f.write_text(WD_PICKER, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        assert widgets.pick_option(pg, pg.locator("#src"), "Company careers page", "How Did You Hear About Us?*")
        assert pg.locator("[data-automation-id=selectedItem]").count() == 1
        b.close()


# --------------------------------------------------------------------------- a company: a container is not a field
CISCO_STATE = """<!doctype html><meta charset=utf-8><body>
<div data-automation-id="formField-countryRegion" id="wrap">
  <label for="address--countryRegion"><span>State<abbr>*</abbr></span></label>
  <div><div><button aria-haspopup="listbox" type="button" value="" aria-label="State Select One Required"
      name="countryRegion" id="address--countryRegion" aria-invalid="true">Select One</button></div></div>
  <p id="err">The field State is required and must have a value.</p>
</div>
<ul role="listbox" id="lb" style="display:none">
  <li role="option" onclick="pick(this)">Karnataka</li><li role="option" onclick="pick(this)">Kerala</li></ul>
<script>
document.getElementById('address--countryRegion').addEventListener('click', () => {
  document.getElementById('lb').style.display = 'block'; });
function pick(li) { document.getElementById('address--countryRegion').innerText = li.innerText;
  document.getElementById('err').innerText = ''; document.getElementById('lb').style.display = 'none'; }
</script></body>"""


def test_a_workday_container_selector_still_finds_the_picker_inside(tmp_path):
    """`[data-automation-id*="countryRegion"]` matches the WRAPPING div, whose text is never a placeholder: State looked answered,
    nothing was reported in `failed`, and four a company jobs died with 'page stopped changing '. The helper must descend to the control."""
    f = tmp_path / "cisco.html"
    f.write_text(CISCO_STATE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        assert widgets.as_control(pg.locator("#wrap")).evaluate("e => e.tagName.toLowerCase()") == "button"
        failed: list = []
        done = widgets.fill_workday_required(pg, "Errors Found The field State is required and must have a value.", failed=failed)
        assert "State" in done, (done, failed)
        assert "Karnataka" in pg.locator("#address--countryRegion").inner_text()
        assert "State" not in failed
        b.close()


BROKEN_DEVICE = """<!doctype html><meta charset=utf-8><body>
<div data-automation-id="formField-phoneType">
  <label>Phone Device Type<abbr>*</abbr></label>
  <button id=pt aria-haspopup="listbox" aria-label="Phone Device Type Select One Required">Select One</button></div>
<ul role="listbox" id=lb style="display:none"><li role=option aria-disabled="true">Select One</li>
  <li role=option>Landline</li><li role=option>Mobile</li></ul>
<script>
document.getElementById('pt').onclick = () => { document.getElementById('lb').style.display = 'block'; };
</script></body>"""


def test_a_picker_that_clicks_the_option_but_never_shows_it_says_so(tmp_path):
    """a company: 'could not set Phone Device Type' with no evidence of what the picker SAW or clicked, so the failure could not
    be diagnosed without replaying the whole application. The report must name the option clicked and the value the field still shows."""
    f = tmp_path / "broken_device.html"
    f.write_text(BROKEN_DEVICE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        failed: list = []
        done = widgets.fill_workday_required(pg, "The field Phone Device Type is required and must have a value.", failed=failed)
        b.close()
    assert done == [] and len(failed) == 1, (done, failed)
    assert failed[0].startswith("Phone Device Type ("), failed
    assert "Mobile" in failed[0] and "still shows" in failed[0], failed


OS_DEVICE = """<!doctype html><meta charset=utf-8><body>
<div data-automation-id="formField-phoneType"><label>Phone Device Type<abbr>*</abbr></label>
 <button id=pt aria-haspopup="listbox" aria-controls="dev" aria-label="Phone Device Type Select One Required">Select One</button></div>
<ul role="listbox" id=dev style="display:none"><li role="option" aria-disabled="true">Select One</li>
 <li role="option">Mobile/Android</li><li role="option">Mobile/Apple iOS</li><li role="option">Mobile - Other (Non Iphone/Android)</li></ul>
<div class="payment-option">LinkedIn</div>
<script>
document.getElementById('pt').onclick = () => { document.getElementById('dev').style.display = 'block'; };
document.querySelectorAll('#dev li[role=option]').forEach(li => li.onclick = () => {
  if (li.getAttribute('aria-disabled')) return;
  document.getElementById('pt').innerText = li.innerText; document.getElementById('dev').style.display = 'none'; });
</script></body>"""


def test_a_workday_phone_type_list_of_os_variants_is_never_guessed(tmp_path, monkeypatch):
    """a company: the list offers Mobile/Android, Mobile/Apple iOS, Mobile - Other. Fuzzy scoring picked 'Mobile - Other',
    which is a fabricated fact about her phone (Workday may text a code to it). With nothing recorded the field must stay EMPTY and the
    report must name the options, so the job can ask her instead."""
    monkeypatch.setattr(widgets, "phone_device_value", lambda: "")
    f = tmp_path / "os_device.html"
    f.write_text(OS_DEVICE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        failed: list = []
        done = widgets.fill_workday_required(pg, "The field Phone Device Type is required and must have a value.", failed=failed)
        shown = pg.locator("#pt").inner_text()
        b.close()
    assert done == [] and len(failed) == 1, (done, failed)
    assert failed[0].startswith("Phone Device Type (no option equals 'Mobile'"), failed
    assert "Mobile/Apple iOS" in failed[0], failed
    assert "LinkedIn" not in failed[0], f"the report lists an unrelated widget: {failed[0]}"
    assert shown.strip() == "Select One", f"a guessed device type was left on the form: {shown!r}"


def test_her_recorded_phone_type_is_picked_exactly(tmp_path, monkeypatch):
    monkeypatch.setattr(widgets, "phone_device_value", lambda: "Mobile/Apple iOS")
    f = tmp_path / "os_device2.html"
    f.write_text(OS_DEVICE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        failed: list = []
        done = widgets.fill_workday_required(pg, "The field Phone Device Type is required and must have a value.", failed=failed)
        shown = pg.locator("#pt").inner_text()
        b.close()
    assert done == ["Phone Device Type"] and not failed, (done, failed)
    assert shown.strip() == "Mobile/Apple iOS", shown


PHONE_CODE_CHIP = """<!doctype html><meta charset=utf-8><body>
<div data-automation-id="formField-countryPhoneCode"><label>Country Phone Code<abbr>*</abbr></label>
 <div data-automation-id="multiSelectContainer">
  <div data-automation-id="selectedItemList"><div data-automation-id="selectedItem">India (+91)</div></div>
  <input id=code placeholder="Search" aria-required="true" value="">
  <div>1 item selected, India (+91) India (+91)</div></div></div>
<script>document.getElementById('code').addEventListener('click', function () { window.__opened = true; });</script>
</body>"""


def test_a_multiselect_whose_chips_live_outside_the_input_is_not_retried(tmp_path):
    """a company: 'could not set Country Phone Code (still required after 2 attempts)' although the field showed
    '1 item selected, India (+91)'. The input is empty by design (the chip carries the value), so re-picking is both useless and a
    false failure that kills the application."""
    f = tmp_path / "phone_code.html"
    f.write_text(PHONE_CODE_CHIP, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        failed: list = []
        done = widgets.fill_workday_required(pg, "The field Country Phone Code is required and must have a value.", failed=failed)
        opened = pg.evaluate("window.__opened || false")
        b.close()
    assert done == [] and failed == [], (done, failed)
    assert opened is False, "the picker was re-opened on a field that already had a value"


def test_one_unreadable_option_element_does_not_empty_the_option_list():
    """On the real a company page all_inner_texts returns a None for an unrelated [class*=option] element; the old single try/except
    turned that into an EMPTY list, which is what made the picker report 'the list never opened' while the list was open on screen."""
    class L:
        def all_inner_texts(self):
            return [None, "Mobile/Android", "  Mobile/Apple iOS  "]

    class Pg:
        def locator(self, _sel):
            return L()

    assert widgets._visible_options(Pg()) == ["Mobile/Android", "Mobile/Apple iOS"]


def test_a_container_with_no_control_is_reported_not_assumed_answered(tmp_path):
    f = tmp_path / "empty.html"
    f.write_text('<div data-automation-id="formField-countryRegion"><span>State</span></div>', encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        failed: list = []
        done = widgets.fill_workday_required(pg, "The field State is required", failed=failed)
        assert done == [] and failed == ["State"], (done, failed)
        b.close()


DEAD_HEAR = """<!doctype html><meta charset=utf-8><body>
<label>How did you hear about us?</label>
<div id=hear role=combobox tabindex=0>Select one</div>
<ul id=lb role=listbox style="display:none"><li role=option onclick="void 0">Company Career Site</li></ul>
<script>document.getElementById('hear').onclick=()=>{document.getElementById('lb').style.display='block'};</script></body>"""


def test_a_picker_that_gives_up_at_the_arrow_key_fallback_says_why(tmp_path):
    """Every give-up path must leave evidence: the 'how did you hear' last resort (ArrowDown+Enter) returned False with an EMPTY
    LAST_PICK_ERROR, so the failure line again said nothing about what the picker saw or did ."""
    f = tmp_path / "dead_hear.html"
    f.write_text(DEAD_HEAR, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        ok = widgets.pick_option(pg, pg.locator("#hear"), "Company careers page", "How did you hear about us?")
        b.close()
    assert ok is False
    assert widgets.LAST_PICK_ERROR, "a give-up path recorded no reason"
