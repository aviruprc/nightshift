"""daily LinkedIn discovery + startup emails; the 'queue low' signal must count only jobs that clear the worker's real fit bar."""
import inspect
from track import guardian


def test_queue_signal_uses_the_real_fit_bar(monkeypatch):
    from apply import runner
    monkeypatch.setattr(runner, "min_score", lambda r=None: 45)   # the live bar is a setting (35 since): the test pins its own

    class D:
        def rows(self, *a, **k):
            return [{"score": 40, "company": "X", "family": "data_bi", "apply_url": "u", "url": "u"}] * 50
    assert guardian.fit_queue(D()) == 0


def test_guardian_schedules_the_daily_jobs():
    src = inspect.getsource(guardian.main)
    assert "outreach.daily()" in src and 'hunt.py", "discover"]' in src.replace("'", '"').replace('"hunt.py", "discover"', '"hunt.py", "discover"')


def test_daily_tasks_survive_a_guardian_restart(tmp_path, monkeypatch):
    """: a restart re-ran the daily startup emails because the 'last ran' time lived only in memory."""
    monkeypatch.setattr(guardian, "DATA", tmp_path)
    assert guardian._daily_state() == {}
    guardian._save_daily(last_outreach=123.0)
    guardian._save_daily(last_full=456.0)
    assert guardian._daily_state() == {"last_outreach": 123.0, "last_full": 456.0}
    assert "_daily_state().get(\"last_outreach\"" in inspect.getsource(guardian.main)
