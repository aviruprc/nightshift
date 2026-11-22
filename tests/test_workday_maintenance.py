"""One Workday tenant in maintenance pauses every Workday job for a while (: 77 tenants answered 'maintenance page' at once)."""
import inspect
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import runner  # noqa: E402


def test_maintenance_on_one_tenant_pauses_all_workday_jobs_but_not_other_sites():
    runner.WD_MAINT["until"] = 0.0
    wf = {"apply_url": "https://wf.wd1.myworkdayjobs.com/x", "url": ""}
    other = {"apply_url": "https://www.naukri.com/job-1", "url": ""}
    assert runner.workday_paused(wf) == ""
    assert runner.note_workday_maintenance("navigator: Workday is currently unavailable (maintenance page)", wf["apply_url"])
    assert "maintenance" in runner.workday_paused(wf)
    assert runner.workday_paused(other) == ""
    runner.WD_MAINT["until"] = time.time() - 1
    assert runner.workday_paused(wf) == ""


def test_other_failures_and_other_hosts_do_not_start_a_pause():
    runner.WD_MAINT["until"] = 0.0
    assert not runner.note_workday_maintenance("page stopped changing ()", "https://x.wd1.myworkdayjobs.com/y")
    assert not runner.note_workday_maintenance("service interruption", "https://careers.example.com/y")
    assert "workday_paused(r)" in inspect.getsource(runner.run) and "note_workday_maintenance(" in inspect.getsource(runner.handle_failure)
