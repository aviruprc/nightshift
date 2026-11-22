"""One slow Naukri profile load is not a verdict (a company: parked needs_you 'could not upload the tailored CV', the next job uploaded fine)."""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import errors, naukri_apply  # noqa: E402


class _Loc:
    def __init__(self, pg): self.pg = pg
    @property
    def first(self): return self
    def set_input_files(self, f): self.pg.uploaded = True


class _Pg:
    calls = 0

    def __init__(self, ok):
        self.ok, self.uploaded = ok, False

    def goto(self, *a, **k): pass
    def wait_for_timeout(self, ms): pass
    def locator(self, sel): return _Loc(self)
    def inner_text(self, sel): return f"Uploaded on {datetime.now().strftime('%b %d, %Y')}" if self.ok else "slow"
    def close(self): pass


class _Ctx:
    def __init__(self, results): self.results = list(results)
    def new_page(self): return _Pg(self.results.pop(0))


def test_a_second_try_rescues_a_slow_first_load():
    assert naukri_apply.upload_profile_resume(_Ctx([False, True]), "cv.pdf") is True


def test_two_failures_are_reported_as_transient_not_hers():
    assert naukri_apply.upload_profile_resume(_Ctx([False, False]), "cv.pdf") is False
    assert errors.classify("failed", "the Naukri profile page did not confirm the CV upload after 2 tries (page or network slow); retried later") == "transient"
