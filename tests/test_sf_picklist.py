"""SAP SuccessFactors paginated picklists (a company + a company): the input SHOWS 'Yes' but is still invalid unless the option was chosen
from the list with the keyboard. The mock reproduces that contract: typing alone changes nothing; ArrowDown+Enter sets title and clears
aria-invalid. (The live a company page is behind a login; this tests our logic against the documented behaviour, the live run is the proof.)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402

PAGE = """<html><body>
<input id="i" class="rcmpaginatedselectinput fd-input" role="combobox" aria-invalid="true" title="" autocomplete="off">
<script>
const i = document.getElementById('i'); let armed = false;
i.addEventListener('keydown', e => {
  if (e.key === 'ArrowDown') armed = true;
  if (e.key === 'Enter' && armed) { i.setAttribute('title', 'Yes'); i.value = 'Yes'; i.setAttribute('aria-invalid', 'false'); }
});
</script></body></html>"""

PLAIN = "<html><body><input id='i' type='text'></body></html>"


def test_the_sf_picklist_is_chosen_with_the_keyboard_and_verified():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(PAGE)
        loc = pg.locator("#i")
        assert agent.sf_picklist_input(loc)
        assert agent.sf_picklist_pick(pg, loc, "Yes") is True
        assert agent.sf_picklist_state(loc) == ("Yes", False)
        pg.set_content(PLAIN.replace("<input", "<input class='x'"))
        assert not agent.sf_picklist_input(pg.locator("#i"))
        b.close()


def test_an_unverified_pick_is_reported_as_failed_not_true():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(PAGE.replace("armed = true;", "armed = false;"))   # a list that never accepts the keyboard choice
        assert agent.sf_picklist_pick(pg, pg.locator("#i"), "Yes") is False
        b.close()
