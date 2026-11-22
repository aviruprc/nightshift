"""The deterministic field scanner (EXTRACT_JS) against the real Oracle HCM markup (verified live).

Oracle's guest identity gate has two traps the scanner got wrong:
  - a VISIBLE 184x38 <input name="honey-pot"> right under the email box (a robot trap -> must never be filled or asked about);
  - the consent box is <input type=checkbox id=legal-disclaimer-checkbox class=input-row__hidden-control> at 0x0 with opacity 0,
    labelled 'I agree with the terms and conditions'. The scanner dropped every 0-size control, so the box was never ticked,
    NEXT answered with a validation error, no verification mail was ever sent (mailbox: no Oracle mail at) and the LLM
    navigator then waited for a code that could not arrive.

Fake pages here reproduce that markup exactly; no live site is touched."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import answers_map as am  # noqa: E402
from apply import engine, llm_answer  # noqa: E402

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


def test_a_robot_trap_is_never_seen(page):
    fields = _scan(page, ORACLE_GATE)
    assert not [f for f in fields if "honey" in (f["name"] + " " + f["label"]).lower()], fields


def test_the_styled_consent_box_is_seen_with_its_label(page):
    fields = _scan(page, ORACLE_GATE)
    box = next(f for f in fields if f["type"] == "checkbox")
    assert box["label"].startswith("I agree with the terms and conditions") and box["hidden"] is True
    assert box["required"] is True  # the label's '*' marks it required on Oracle's page
    assert am.CONSENT.search(box["label"]) and not am.MARKETING.search(box["label"])


def test_the_consent_box_can_be_ticked_through_its_label(page):
    pg = page
    fields = _scan(page, ORACLE_GATE)
    box = next(f for f in fields if f["type"] == "checkbox")
    assert engine._tick(pg, pg.locator(f'[data-jh="{box["id"]}"]').first) is True
    assert pg.evaluate("document.getElementById('legal-disclaimer-checkbox').checked") is True


def test_an_unlabelled_hidden_checkbox_is_still_ignored(page):
    fields = _scan(page, '<div><input type="checkbox" name="tracker" style="display:none"><input type="text" id="n" name="n"></div>')
    assert [f["type"] for f in fields] == ["text"], fields


def test_a_normal_visible_form_is_unchanged(page):
    fields = _scan(page, '<div><input type="text" name="city"><input type="checkbox" id="c1"><label for="c1">Remote</label></div>')
    assert {f["name"] or f["label"] for f in fields} == {"city", "Remote"}
    assert all(f["hidden"] is False for f in fields)


def test_the_gate_is_filled_end_to_end_without_asking_the_llm(page, monkeypatch, tmp_path):
    pg = page
    asked = []
    monkeypatch.setattr(llm_answer, "answer", lambda qs, job=None, **kw: asked.extend(q["q"] for q in qs) or {})
    monkeypatch.setattr(engine, "CURRENT_JOB", {"id": "t", "company": "Oracle", "title": "Business Analyst 3-Ops", "jd": ""})
    _scan(pg, ORACLE_GATE)
    unknown, filled = [], []
    engine.fill_page(pg, tmp_path / "cv.pdf", unknown, filled)
    assert any(f[0].startswith("Email Address") and f[1] == "asha.example@example.com" for f in filled), filled
    assert any(f[0] == "consent" for f in filled), filled
    assert pg.evaluate("document.getElementById('legal-disclaimer-checkbox').checked") is True
    assert "honeypot" not in " ".join(asked).lower() and not [u for u in unknown if "honey" in u.lower()]
