"""A missing Easy Apply control is a page state to be proven, never a bare negative (KB-77).

Five jobs known to have Easy Apply (a company 78, HRM Counsel x2, iConsultera, a company) returned "Easy Apply button not found" /
"could not finish the Easy Apply steps" in two minutes right after two successful sends, and nothing was saved to check:
no screenshot, no page text. A slow load, a challenge page and an already-applied job are indistinguishable from the
message. The flow now reloads once, reads the page's own "Applied" label, saves a screenshot + the page's text on every
failure exit, and words the missing control so `errors.classify` treats it as TRANSIENT (KB-58), never a closure.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from apply import easy, errors  # noqa: E402


class _El:
    def __init__(self, text):
        self.text = text

    def inner_text(self):
        return self.text


class _Loc:
    def __init__(self, els):
        self.els = [_El(t) for t in els]

    def all(self):
        return self.els


class _Pg:
    def __init__(self, els):
        self.els = els
        self.reloaded = 0
        self.shots = []
        self.closed = False

    def locator(self, sel):
        return _Loc(self.els)

    def reload(self, **kw):
        self.reloaded += 1

    def wait_for_timeout(self, ms):
        pass

    def inner_text(self, sel):
        return "  Easy Apply   page   text  "

    def screenshot(self, path=None, full_page=True):
        self.shots.append(str(path))

    def close(self):
        self.closed = True


def test_the_easy_apply_control_is_found():
    btn, applied = easy.scan(_Pg(["Save", "Easy Apply", "Applied"]))
    assert btn is not None and applied is True


def test_an_already_applied_job_says_so_instead_of_no_button():
    btn, applied = easy.scan(_Pg(["Save", "Applied"]))
    assert btn is None and applied is True


def test_a_page_with_no_controls_reports_neither():
    assert easy.scan(_Pg([])) == (None, False)


def test_the_missing_control_wording_is_transient_not_a_closure():
    assert errors.classify("failed", "no apply button on the LinkedIn job page after a reload; page says: 'Sign in'") == "transient"
    assert errors.classify("failed", "did not show the next step in the Easy Apply form") == "transient"


def test_evidence_is_a_screenshot_and_one_line_of_the_page(tmp_path):
    pg = _Pg([])
    shot, txt = easy.evidence(pg, "job123", "easy_missing")
    assert "job123_easy_missing" in shot and shot.endswith(".png")
    assert txt == "Easy Apply page text"


def test_the_flow_reloads_once_and_never_reports_a_bare_negative():
    import inspect
    src = inspect.getsource(easy.apply_easy)
    assert "pg.reload(" in src, "one reload before believing the control is absent"
    assert "evidence(pg, job_id" in src, "every failure exit must leave evidence behind"
    assert '"Easy Apply button not found"' not in src
