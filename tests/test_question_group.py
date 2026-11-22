"""A Workday question dropdown must be findable BY ITS QUESTION, or a banner cannot be repaired (KB-43).

a company (`a saved failure page`, Business Finance Senior Analyst): the Application Questions page
held two dropdowns. The AI-tools one was answered ("Yes"); the right-to-work one was still "Select One" and its banner said
"The field Do you have the right to work (citizen or valid work permit) for the location you are applying to? is required".
The control's whole accessible name is `" Select One Required"` — the question lives in the fieldset <legend>. So the repair could not
match the name to the control, fell back to the only invalid element and opened that control, was offered
"Select One / I would like my personal information to …" twice, and the job was reported as "could not set Do you have the right to
work … (still required after 2 attempts)". The answer itself is a recorded fact ("Yes").
"""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent  # noqa: E402

QUESTION = ("Do you have the right to work (citizen or valid work permit) for the location you are applying to?")
PAGE = """<!doctype html><meta charset=utf-8><title>Application Questions</title><body>
<h2>Application Questions</h2>
<div role="group" aria-labelledby="primaryQuestionnaire-section">
 <div data-automation-id="formField-b622109cfedf103787fd2a523da80000">
  <fieldset><legend><div data-automation-id="richText"><p><b>Do you have the right to work (citizen or valid work permit)
   for the location you are applying to?</b><abbr title="required">*</abbr></p></div></legend>
   <button id="q1b" type="button" aria-haspopup="listbox" value="" aria-label=" Select One Required" aria-invalid="true">Select One</button>
   <ul id="q1l" role="listbox" style="display:none"><li role="option">Select One</li><li role="option">Yes</li>
   <li role="option">No</li></ul>
  </fieldset>
 </div>
 <div data-automation-id="formField-b622109cfedf103787fd2b856b9f0001">
  <fieldset><legend><div data-automation-id="richText"><p><b>Deutsche Bank would like to use Artificial Intelligence (AI) tools
   to help recruiters review your application. Do you consent to the use of AI Tools as described above?</b></p></div></legend>
   <button id="q2b" type="button" aria-haspopup="listbox" aria-label=" Yes Required">Yes</button>
   <ul id="q2l" role="listbox" style="display:none"><li role="option">Select One</li><li role="option">Yes</li></ul>
  </fieldset>
 </div>
</div>
<script>
document.querySelectorAll('button[aria-haspopup]').forEach(b => b.onclick = () => {
  const l = document.getElementById(b.id.replace('b', 'l')); l.style.display = 'block';
  l.querySelectorAll('[role=option]').forEach(o => o.onclick = () => {
    b.innerText = o.innerText; b.setAttribute('aria-label', ' ' + o.innerText + ' Required');
    b.removeAttribute('aria-invalid'); l.style.display = 'none'; });
});
</script></body>"""


@pytest.fixture
def questions_page(tmp_path):
    f = tmp_path / "questions.html"
    f.write_text(PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        yield pg
        b.close()


def test_the_snapshot_carries_the_question_of_a_picker_that_is_named_only_select_one(questions_page):
    snap = questions_page.evaluate(agent.SNAP_JS)
    btn = next(e for e in snap["els"] if e.get("text") == "Select One")
    assert "Select One Required" in (btn.get("label") or "")
    assert "right to work" in (btn.get("group") or "").lower(), btn
    answered = next(e for e in snap["els"] if e.get("text") == "Yes" and e.get("tag") == "button")
    assert "AI Tools" in (answered.get("group") or ""), answered


def test_the_banner_named_question_is_answered_yes_on_its_own_control(questions_page):
    pg = questions_page
    snap = pg.evaluate(agent.SNAP_JS)
    snap["text"] = snap["text"] + " Errors Found The field " + QUESTION + " is required and must have a value."
    failed, questions = [], []
    done = agent._repair_named_required(pg, snap, failed=failed, questions=questions)
    assert pg.locator("#q1b").inner_text() == "Yes", (pg.locator("#q1b").inner_text(), done, failed)
    assert not any("right to work" in f.lower() for f in failed), failed
    assert questions == [], questions
    assert pg.locator("#q2b").inner_text() == "Yes", "the already-answered AI question was changed"
