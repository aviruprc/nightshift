"""The queue is built once: a score cap written after that must still stop the job before it is applied to ."""
import inspect
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import runner  # noqa: E402


class _D:
    def __init__(self, score):
        self.c = sqlite3.connect(":memory:")
        self.c.execute("CREATE TABLE jobs(id TEXT, score INT)")
        self.c.execute("INSERT INTO jobs VALUES('j1', ?)", (score,))


def test_a_row_capped_after_the_queue_was_built_is_not_applied(monkeypatch):
    monkeypatch.setattr(runner, "min_score", lambda r=None: 35)
    r = {"id": "j1", "score": 72, "url": "", "apply_url": ""}       # the queue's snapshot still says 72
    assert "below the bar" in runner.score_now_below_bar(_D(25), r)
    assert runner.score_now_below_bar(_D(60), r) == ""
    assert runner.score_now_below_bar(_D(None), r) == ""


def test_both_apply_loops_re_read_the_score():
    assert inspect.getsource(runner.run).count("score_now_below_bar(d, r)") == 2
