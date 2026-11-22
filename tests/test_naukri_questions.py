"""A Naukri screening question that cannot be answered must leave the panel behind (KB-59).

Three live rows were `failed` with "unanswered required: <question> (Naukri showed no option matching the recorded answer
'Yes'/'No')" and nothing to diagnose them with:
  * Thyrocare - "Please mention if you have any offer in hand?" answer 'No'
  * Wow Momo Foods - "Are you currently working in the FMCG industry?" answer 'No'
  * a company Global Solutions - "Current Location?" answer 'Bengaluru, Karnataka, India'
The recorded detail was cut by a 300-character slice BEFORE the options list, the saved screenshot shows only that a drawer
existed, and no DOM was kept. The option picker itself handles these questions (see the regression below), so the failure was
in what the drawer actually showed - which is exactly what was thrown away.
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import engine, naukri_apply  # noqa: E402


class FakeDrawer:
    def __init__(self, text, html):
        self._text, self._html = text, html

    def inner_text(self):
        return self._text

    def inner_html(self):
        return self._html


def test_the_options_survive_a_long_question():
    q = ("Please mention if you are currently working in the fast moving consumer goods industry and whether you "
         "have any offer in hand at this moment in time?")
    msg = naukri_apply._no_option_message(q, "No", ["Yes I have an offer", "No, I do not have any offer"])
    assert len(msg) <= 300
    assert "'No'" in msg
    assert "matched none of:" in msg
    assert "No, I do not have any offer" in msg, msg


def test_no_options_is_said_plainly_not_left_blank():
    msg = naukri_apply._no_option_message("Current Location?", "Bengaluru, Karnataka, India", [])
    assert "matched none of: none" in msg


def test_the_panel_is_saved_as_text_and_html(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "SHOTS", tmp_path / "screenshots")
    monkeypatch.setattr(engine, "CURRENT_JOB", {"company": "Thyrocare", "title": "Business Analyst"})

    class Pg:
        def content(self):
            return "<html>page</html>"

    path = naukri_apply._drawer_evidence(Pg(), FakeDrawer("Any offer in hand?\nYes\nNo", "<div class=chatbot_Drawer>Yes No</div>"), "nomatch")
    assert path.endswith(".txt")
    p = Path(path)
    assert p.exists() and "offer in hand" in p.read_text(encoding="utf-8")
    html = p.with_suffix(".html")
    assert html.exists() and "chatbot_Drawer" in html.read_text(encoding="utf-8")


def test_the_panel_save_never_breaks_the_application(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "SHOTS", tmp_path / "screenshots")
    monkeypatch.setattr(engine, "CURRENT_JOB", {})

    class Bad:
        def inner_text(self):
            raise RuntimeError("detached")

        def inner_html(self):
            raise RuntimeError("detached")

    class BadPg:
        def content(self):
            raise RuntimeError("detached")

    assert naukri_apply._drawer_evidence(BadPg(), Bad(), "x").endswith(".txt")   # still names the file
    assert list((tmp_path / "failures").glob("*.txt"))   # SHOTS is data/screenshots, its parent is data/


def test_every_unanswered_question_path_saves_the_panel():
    src = inspect.getsource(naukri_apply.answer_chat)
    for tag in ('_drawer_evidence(pg, drawer, "illegible")',
                '_drawer_evidence(pg, drawer, "stuck")',
                '_drawer_evidence(pg, drawer, "nomatch")',
                '_drawer_evidence(pg, drawer, "noanswer")'):
        assert tag in src, tag
    # the answer + options message replaces the order that lost them to the 300-char slice
    assert "_no_option_message(q, ans, options)" in src
    assert "options: {seen})" not in src


def test_a_failed_naukri_question_saves_the_whole_page_too():
    src = inspect.getsource(naukri_apply.apply_naukri)
    i = src.index('detail=f"unanswered required: {q}"')
    window = src[i:i + 400]
    assert "_agent._save_failure(pg, engine.CURRENT_JOB or {}, res)" in window


@pytest.mark.parametrize("q,options,ans,expected", [
    ("Do you have a bachelor's degree or higher qualification?", ["Yes", "No"], "Yes", "Yes"),
    ("Are you currently residing in Bengaluru, Karnataka or willing to relocate to Bengaluru, Karnataka?", ["Yes", "No"], "Yes", "Yes"),
    ("Are you currently working in the FMCG industry?", ["Yes", "No"], "No", "No"),
    ("Please mention if you have any offer in hand?", ["Yes", "No"], "No", "No"),
    ("Current Location?", ["Bengaluru", "Bangalore", "Mumbai"], "Bengaluru, Karnataka, India", "Bengaluru"),
])
def test_the_picker_still_answers_these_questions(q, options, ans, expected):
    """The live failures were NOT the picker: given the real chips it chooses correctly, so what went missing was the
    evidence of what the drawer actually offered."""
    assert naukri_apply.pick_option(q, options, ans) == expected
