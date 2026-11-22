"""One SVG icon must never make a page unreadable (KB-65).

a company failed four times and a company once with
"the page's form could not be read - 8 snapshots failed ... last error: Page.evaluate: TypeError: Cannot read
properties of undefined (reading 'trim')" - both on Workday LOGIN pages. SNAP_JS collected error banners with
`[class*=error i]`, which also matches Workday's error ICON (`<svg class="... error ...">`), and an SVGElement
has no `innerText`: `e.innerText.trim` threw and killed the entire snapshot, so the navigator could not even
see the Sign In form it was standing on. Chrome reproduces it exactly (see the first test).
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import agent, pagejs  # noqa: E402


def test_the_svg_innertext_failure_is_reproducible():
    """The exact browser behaviour the fix is built on: an SVGElement's innerText is undefined."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content('<svg class="error-icon"><path class="error-path"></path></svg>')
        assert pg.evaluate("() => typeof document.querySelector('svg').innerText") == "undefined"
        threw = pg.evaluate("""() => { try { [...document.querySelectorAll('[class*=error]')].map(e => e.innerText.trim()); return ''; }
                                            catch (e) { return String(e); } }""")
        assert "reading 'trim'" in threw
        b.close()


def test_a_snapshot_survives_an_error_icon_and_reads_the_form():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content(
            "<body><h1>Sign In</h1>"
            "<div role='alert'><svg class='error-icon'><path class='error-path'></path></svg>Invalid user name or password.</div>"
            "<label>Email Address <input id='email' type='email'></label>"
            "<label>Password <input id='pw' type='password'></label>"
            "<button>Sign In</button></body>")
        snap = pg.evaluate(agent.SNAP_JS)          # used to raise TypeError on this page
        assert "Sign In" in snap["heads"]
        assert any("Invalid user name" in e for e in snap["errs"]), snap["errs"]
        labels = [e.get("label", "") for e in snap["els"]]
        assert any("Email Address" in x for x in labels) and any("Password" in x for x in labels), labels
        b.close()


def test_a_label_icon_does_not_break_the_label_helper():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<body><div class='field'><svg class='label-icon'><path></path></svg>"
                       "<input id='a' type='text'></div></body>")
        label = pg.evaluate("() => {" + pagejs.LAB_JS + " return lab(document.getElementById('a')); }")
        assert isinstance(label, str)
        b.close()


def test_no_unguarded_innertext_trim_is_left_in_the_snapshots():
    def code_lines(src):
        return [ln for ln in src.splitlines() if not ln.strip().startswith("//")]
    for src in (agent.SNAP_JS, pagejs.LAB_JS):
        assert all("innerText.trim()" not in ln for ln in code_lines(src)), src[:200]
    assert "elText" in agent.SNAP_JS
