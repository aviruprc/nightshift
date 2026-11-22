r"""The operator's STOP marker is a command, not a stale flag - and the navigator never spins in silence on a blank page.

KB-42,: `data/STOP` was created at to restart the applier for the KB-41 code. `runner.run` deletes the marker at the
start AND the end of a run, so the worker that honoured the stop deleted it on its way out, `worker.ps1`'s
`while (-not (Test-Path data\STOP))` saw nothing to stop it and started a WHOLE NEW applier at (still old code), and the
guardian's supervisor never saw a reason to stay down. The same deletion is why the dashboard's "start run" has to clear it explicitly
(track/live.py:687) - that one action stays the only place allowed to.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, runner  # noqa: E402


def test_the_runner_never_deletes_the_stop_marker():
    for fn in (runner.run,):
        src = inspect.getsource(fn)
        assert "STOP.unlink" not in src, "runner.run() deletes the operator's STOP marker - the wrapper will restart the applier"
    assert "stopped = STOP.exists()" in inspect.getsource(runner.run), "the run must still report that it stopped on the marker"


def test_the_worker_wrapper_stops_when_the_marker_is_present():
    src = (ROOT / "worker.ps1").read_text(encoding="utf-8")
    assert "while (-not (Test-Path data\\STOP))" in src, "the wrapper's stop condition changed; re-check that a stop cannot restart a run"


def test_a_finished_run_keeps_the_worker_in_its_loop():
    """: a run that DID work ended in `break`, so supervise.ps1 exited and the applier idled 3-9 minutes until the
    guardian's next tick noticed (->). keep applying until Sunday EOD. The stop marker is still the only exit."""
    src = (ROOT / "worker.ps1").read_text(encoding="utf-8")
    body = src[src.index("if ($p.ExitCode -eq 0)"):]
    body = body[:body.index("Start-Sleep 10")]
    statements = [ln.strip() for ln in body.splitlines()]
    assert "continue" in statements, "a finished run must continue the wrapper loop, not end the supervisor"
    assert "break" not in statements, "a finished run must not end the supervisor (only data\\STOP may)"


def test_a_page_that_never_renders_is_named_and_bounded():
    src = inspect.getsource(agent.navigate)
    assert "snap_fails += 1" in src, "snapshot failures are still swallowed without a counter"
    assert "snap_fails >= 8" in src, "the snapshot-failure loop has no bound"
    assert "the page never rendered" in src, "the failure still reports 'too many steps' for a page that never rendered"
    i = src.index("snap_fails += 1")
    assert "reload(" in src[i:i + 600], "a blank Workday page is never reloaded, it is only waited on"


def test_the_dashboard_is_still_the_only_explicit_way_to_clear_a_stale_marker():
    src = (ROOT / "track" / "live.py").read_text(encoding="utf-8")
    assert "STOP.unlink(missing_ok=True)" in src, "start_run must still clear a stale marker, or the next start is blocked"
