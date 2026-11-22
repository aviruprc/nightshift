"""A confirmation on screen is a confirmation, wherever it sits in the DOM (KB-76).

a company International (Power BI developer) and a company Global Services (SQL Data Analyst) were recorded as
"submitted; no confirmation seen" - the saved post-submit screenshot plainly shows "Your application was sent to <company>!"
and "Application status: Application submitted". `apply_easy` read `pg.inner_text("body")[:4000]` and searched that; a
LinkedIn job page is longer than 4000 characters before the modal ever starts, so a real proof was thrown away and the
message claimed something false about the page.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apply import easy  # noqa: E402


class _Loc:
    def __init__(self, text=""):
        self.text = text

    def count(self):
        return 1 if self.text else 0

    @property
    def first(self):
        return self

    def inner_text(self):
        return self.text


class _Pg:
    def __init__(self, body="", dialog=""):
        self.body, self.dialog = body, dialog

    def locator(self, sel):
        return _Loc(self.dialog)

    def inner_text(self, sel):
        return self.body


LONG_JD = "Job description. " * 600  # 12,000 characters before the confirmation is ever reached


def test_a_confirmation_after_a_long_job_description_is_found():
    pg = _Pg(body=LONG_JD + "Your application was sent to Antal International!")
    assert easy.confirmation(pg) is not None


def test_the_dialog_is_read_even_when_the_body_is_trimmed():
    pg = _Pg(body="short page", dialog="Your application was sent to Sutherland Global Service Limited!")
    m = easy.confirmation(pg)
    assert m and "Sutherland" in pg.dialog


def test_a_page_without_a_confirmation_is_not_a_submit():
    assert easy.confirmation(_Pg(body=LONG_JD + "Application status: not submitted")) is None


def test_easy_apply_uses_the_helper_not_a_4000_char_slice():
    import inspect
    src = inspect.getsource(easy.apply_easy)
    assert "confirmation(pg)" in src
    assert 'inner_text("body")[:4000]' not in src
