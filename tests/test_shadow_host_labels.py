""" SmartRecruiters (companies ...): 22 attempts, 0 sent. The form is built from web components whose field name is
an attribute of the HOST (<spl-input label="First name">) and whose button text is slotted (<spl-button>Next</spl-button>). The snapshot listed
"spl-form-element_12" for Country/Region, and every text-less button collapsed into one, so Next was never offered.
Evidence: a saved failure page"""
from playwright.sync_api import sync_playwright

from apply import agent

PAGE = """<html><body>
<script>
const def = (name, inner) => customElements.define(name, class extends HTMLElement {
  connectedCallback() { const r = this.attachShadow({mode: 'open'}); r.innerHTML = inner(this); } });
def('spl-input', h => '<input type="text" id="' + h.id + '"' + (h.hasAttribute('required') ? ' required' : '') + '>');
def('spl-autocomplete', h => '<input type="text" id="' + h.id + '" role="combobox">');
def('spl-button', h => '<button type="submit"><slot></slot></button>');
def('two-boxes', h => '<input type="text" id="a1"><input type="text" id="a2">');
</script>
<spl-input id="first-name-input" label="First name" required></spl-input>
<spl-autocomplete id="spl-form-element_12" label="Country/Region" required></spl-autocomplete>
<spl-input id="spl-form-element_11" label="City" required></spl-input>
<two-boxes label="Address"></two-boxes>
<spl-button aria-label="Add experience entry">Add</spl-button>
<spl-button aria-label="Add education entry">Add</spl-button>
<spl-button aria-label="">Next</spl-button>
</body></html>"""


def snap(tmp_path):
    f = tmp_path / "sr.html"
    f.write_text(PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        pg.wait_for_timeout(300)
        s = pg.evaluate(agent.SNAP_JS)
        b.close()
    return s["els"]


def test_fields_are_named_by_their_host_and_every_button_is_listed(tmp_path):
    els = snap(tmp_path)
    labels = [e["label"] for e in els if e["tag"] == "input"]
    assert labels[:3] == ["First name", "Country/Region", "City"], labels
    assert "Address" not in labels                       # a host that holds two boxes names neither of them
    buttons = [(e.get("text") or e.get("label")) for e in els if e["tag"] == "button"]
    assert buttons == ["Add experience entry", "Add education entry", "Next"], buttons


COUNTRY_PAGE = """<html><body>
<script>
customElements.define('spl-select-option', class extends HTMLElement {
  connectedCallback() { const r = this.attachShadow({mode: 'open'}); r.innerHTML = '<div role="option" style="padding:4px"><slot></slot></div>';
    r.firstChild.addEventListener('click', () => { const ac = this.getRootNode().host; ac.pick(this.textContent.trim()); }); } });
customElements.define('spl-autocomplete', class extends HTMLElement {
  connectedCallback() { const r = this.attachShadow({mode: 'open'});
    r.innerHTML = '<input type="text" id="c" role="combobox" aria-invalid="true"><div id="list" role="listbox"></div>';
    const inp = r.querySelector('input'), list = r.querySelector('#list');
    inp.addEventListener('input', () => { list.innerHTML = '';
      for (const n of ['British Indian Ocean Territory', 'India', 'Indonesia']) if (n.toLowerCase().includes(inp.value.toLowerCase()) && inp.value) {
        const o = document.createElement('spl-select-option'); o.textContent = n; list.appendChild(o); } }); }
  pick(v) { const r = this.shadowRoot; r.querySelector('input').value = v; r.querySelector('input').setAttribute('aria-invalid', 'false');
    r.querySelector('#list').innerHTML = ''; window.picked = v; } });
</script>
<spl-autocomplete id="spl-form-element_12" label="Country/Region" required></spl-autocomplete>
</body></html>"""


def test_a_country_typeahead_with_slotted_options_is_really_selected(tmp_path):
    """ a company: 'India' was typed, nothing was picked, the field stayed invalid and Next did nothing."""
    from apply import widgets
    f = tmp_path / "country.html"
    f.write_text(COUNTRY_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        ok = widgets.fill_country(pg, pg.locator("spl-autocomplete input"), "India")
        picked = pg.evaluate("window.picked")
        invalid = pg.locator("spl-autocomplete input").get_attribute("aria-invalid")
        b.close()
    assert ok and picked == "India" and invalid == "false", (ok, picked, invalid)


ENTRY_PAGE = """<html><body>
<script>
customElements.define('spl-autocomplete', class extends HTMLElement {
  connectedCallback() { const r = this.attachShadow({mode: 'open'}); r.innerHTML = '<input type="text" role="combobox" id="' + this.id + '">'; } });
</script>
<spl-autocomplete id="t1" label="Title"></spl-autocomplete>
<spl-autocomplete id="t2" label="Company"></spl-autocomplete>
<spl-autocomplete id="t3" label="Office location"></spl-autocomplete>
<label for="from">From</label><input id="from" type="text" placeholder="Pick a date">
<script>
// a month-year picker that reads YYYY-MM-DD only; anything else becomes January of the year it can find (what SmartRecruiters did)
const f = document.getElementById('from');
f.addEventListener('blur', () => { const v = f.value; let m = v.match(/^(\\d{4})-(\\d{2})(-\\d{2})?$/);
  if (m) { f.value = m[1] + '-' + m[2] + '-01'; return; }
  m = v.match(/(19|20)\\d\\d/); f.value = m ? m[0] + '-01-01' : ''; });
</script></body></html>"""


def test_three_empty_comboboxes_are_three_fields_and_a_month_is_typed_so_that_it_sticks(tmp_path):
    """ SmartRecruiters 'Add experience entry': Title and Company were merged away, and 06/2023 was stored as January 2023."""
    from apply import widgets
    f = tmp_path / "entry.html"
    f.write_text(ENTRY_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        pg.wait_for_timeout(300)
        els = pg.evaluate(agent.SNAP_JS)["els"]
        ok = widgets.fill_month_year(pg, pg.locator("#from"), "06/2023", "From")
        shown = pg.locator("#from").input_value()
        b.close()
    assert sorted(e["label"] for e in els if e["tag"] == "input") == ["Company", "From", "Office location", "Title"]
    assert ok is True and shown == "2023-06-01", (ok, shown)


def test_month_year_reading():
    from apply import widgets
    assert widgets.month_year("03/2024") == widgets.month_year("2024-03-01") == widgets.month_year("Mar 2024") == widgets.month_year("March 2024") == (3, 2024)
    assert widgets.month_year("2023") is None and widgets.month_year("01/06/2023") is None and widgets.month_year("Bengaluru") is None
