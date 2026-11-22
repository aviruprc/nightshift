"""widgets.upload_via_dialog: a button that opens the file chooser, or a dialog with a file input + Attach (real browser, local HTML)."""
from apply import widgets

CHOOSER = """<html><body><button id=b onclick="document.getElementById('f').click()">Upload a CV</button><input id=f type=file style="display:none"
 onchange="document.title = this.files[0].name"></body></html>"""
DIALOG = """<html><body><button id=b onclick="document.getElementById('d').style.display='block'">Upload a CV</button>
<div id=d role=dialog style="display:none"><input type=file onchange="window.picked=this.files[0].name"><button onclick="document.title='attached:'+window.picked">Attach</button></div></body></html>"""


def _run(html, tmp_path):
    from playwright.sync_api import sync_playwright
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.set_content(html)
        how = widgets.upload_via_dialog(pg, pg.locator("#b"), pdf)
        title = pg.title()
        b.close()
    return how, title


def test_a_button_that_opens_the_file_chooser_gets_the_file(tmp_path):
    assert _run(CHOOSER, tmp_path) == ("file chooser", "cv.pdf")


def test_a_dialog_with_a_file_input_gets_the_file_and_attach_is_pressed(tmp_path):
    assert _run(DIALOG, tmp_path) == ("dialog", "attached:cv.pdf")


def test_nothing_to_click_returns_empty():
    assert widgets.upload_via_dialog(None, None, "x.pdf") == ""


def test_a_required_resume_field_is_never_repaired_like_a_text_field():
    from apply import agent
    snap = {"els": [{"i": 1, "tag": "span", "type": "button", "label": "* Resume / CV Upload a CV Opens a dialogue", "invalid": True}],
            "text": "The following fields require a valid input: Resume / CV", "errs": ["Resume / CV is required"]}
    failed, questions = [], []
    assert agent._repair_named_required(None, snap, failed, questions) == [] and failed == [] and questions == []
