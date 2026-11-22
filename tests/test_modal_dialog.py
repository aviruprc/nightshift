"""KB-85: a click behind an OPEN modal dialog never lands - name the dialog instead of dying as "page stopped changing".

a company (Oracle HCM, ejgk.fa.em2.oraclecloud.com) failed twice, and, with
`navigator: page stopped changing last: step 4: click #8 NEXT`. Both saved pages (a saved failure page and
a saved failure page) show the same state: the guest-apply form filled (email + "I agree with the terms and conditions"
checked) and, ON TOP, Oracle's `role="dialog" aria-label="Terms and Conditions"` with `body--dialog-opened` on the body and
AGREE (#2) as its accept button. Four clicks meant for #8 NEXT went into the dialog layer and nothing else happened - the model
had no field saying "this control is covered", and the failure named nothing.

The page below reproduces that DOM shape. The rules pinned here: a control behind a modal dialog is marked `covered` with the
dialog's name, the dialog's own controls are marked `dlg`, and a plain overlay (no dialog) is NOT marked - Workday's click_filter
is worked around by the JS-click fallback and must keep working.
"""
import inspect

import pytest
from playwright.sync_api import sync_playwright

from apply import agent

MODAL_PAGE = """<!doctype html><meta charset=utf-8><title>Apply</title>
<body class="app-loaded body--dialog-opened">
<main>
  <input id=email type=email aria-label="Email Address" value="asha@example.com">
  <button id=next type=submit>NEXT</button>
</main>
<div id=layer style="position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:5">
  <div role="dialog" aria-modal="true" aria-label="Terms and Conditions"
       style="position:fixed;top:20%;left:20%;width:320px;background:#fff;z-index:6">
    <h1>Terms and Conditions</h1>
    <p>I consent to the Firm processing my personal data for recruitment purpose.</p>
    <button id=close aria-label="Close">x</button>
    <button id=agree>AGREE</button>
  </div>
</div></body>"""

OVERLAY_PAGE = """<!doctype html><meta charset=utf-8><title>Apply</title>
<body>
<main><input id=email type=email aria-label="Email Address"><button id=next type=submit>NEXT</button></main>
<div id=filter style="position:fixed;left:0;top:0;width:100%;height:100%;z-index:5"></div></body>"""


@pytest.fixture
def page(tmp_path):
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        yield pg
        b.close()


def _load(pg, tmp_path, html, name):
    f = tmp_path / name
    f.write_text(html, encoding="utf-8")
    pg.goto(f.as_uri())
    return pg.evaluate(agent.SNAP_JS)


def _by_text(snap, text):
    return next(e for e in snap["els"] if (e.get("text") or e.get("label") or "").strip().lower() == text.lower())


def test_a_control_behind_a_modal_dialog_is_marked_with_its_name(page, tmp_path):
    snap = _load(page, tmp_path, MODAL_PAGE, "modal.html")
    nxt = _by_text(snap, "NEXT")
    assert nxt.get("covered") == "Terms and Conditions", nxt
    assert not nxt.get("dlg")


def test_the_dialogs_own_controls_are_marked_and_not_covered(page, tmp_path):
    snap = _load(page, tmp_path, MODAL_PAGE, "modal.html")
    agree = _by_text(snap, "AGREE")
    assert agree.get("dlg") == "Terms and Conditions", agree
    assert not agree.get("covered")


def test_a_plain_overlay_is_not_a_dialog_and_is_not_marked(page, tmp_path):
    """Workday's click_filter (a company) is a bare overlay; the JS-click fallback gets through it. Do not refuse those."""
    snap = _load(page, tmp_path, OVERLAY_PAGE, "overlay.html")
    assert not _by_text(snap, "NEXT").get("covered")
    assert not any(e.get("dlg") for e in snap["els"])


def test_dialog_controls_names_what_the_dialog_offers():
    els = [{"i": 0, "text": "NEXT"}, {"i": 2, "text": "AGREE", "dlg": "Terms and Conditions"},
           {"i": 4, "text": "Close", "dlg": "Terms and Conditions"}, {"i": 5, "text": "AGREE", "dlg": "Other"}]
    got = agent.dialog_controls(els, "Terms and Conditions")
    assert "#2 'AGREE'" in got and "#4 'Close'" in got and "Other" not in got


def test_a_click_on_a_covered_control_is_never_stored():
    """A dead click is not a route: memorising it would replay 'click NEXT' onto every page with this dialog open."""
    snap = {"els": [{"i": 0, "tag": "button", "type": "submit", "label": "", "text": "NEXT", "value": "", "covered": "Terms and Conditions"},
                    {"i": 1, "tag": "button", "type": "submit", "label": "", "text": "AGREE", "value": "", "dlg": "Terms and Conditions"}]}
    assert agent._storable([{"do": "click", "i": 0}], snap, "") is None
    kept = agent._storable([{"do": "click", "i": 1}], snap, "")
    assert kept and kept[0]["text"] == "AGREE"


def test_an_old_recipe_no_longer_replays_a_click_behind_the_dialog():
    old = [{"do": "click", "label": "", "text": "NEXT", "tag": "button", "type": "submit", "value": ""}]
    covered = {"els": [{"i": 0, "tag": "button", "type": "submit", "label": "", "text": "NEXT", "value": "", "covered": "Terms and Conditions"}]}
    assert agent._resolve(old, covered, "") == []
    free = {"els": [{"i": 0, "tag": "button", "type": "submit", "label": "", "text": "NEXT", "value": ""}]}
    assert len(agent._resolve(old, free, "")) == 1


def test_the_navigator_refuses_a_click_behind_the_dialog_and_names_it():
    """Source guard: without the refusal, the plan burns every step on a dead click and fails as 'page stopped changing'."""
    src = inspect.getsource(agent.navigate)
    assert 'if (el or {}).get("covered") and do in ("click", "fill", "select", "check", "uncheck"):' in src
    assert "dialog_controls(snap[\"els\"], covered_name)" in src
    assert 'covered {covered_hits} attempted control(s)' in src
