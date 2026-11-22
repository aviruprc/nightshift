"""A CAPTCHA hand-off never opens a job in a city she did not ask for (a company, Pune, opened)."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import assist  # noqa: E402


class _Row(dict):
    def keys(self):
        return super().keys()


def test_a_pune_job_is_not_opened_and_bengaluru_is():
    assert "outside your locations" in assist.not_hers(_Row(location="Pune Division, Maharashtra, India"))
    assert assist.not_hers(_Row(location="Bengaluru, Karnataka, India")) == ""
    assert "not_hers(r)" in inspect.getsource(assist.serve)
