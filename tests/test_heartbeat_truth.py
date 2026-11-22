"""A heartbeat must be lit by the thing it claims is alive (KB-80).

a company Data Governance (worker 25164), -> 14:12: the applier's main thread blocked inside a Playwright call (py-spy: the
sync driver waiting on `select`; the browser, its renderers and Python all at 0 % CPU) and the navigator's own 600 s
budget never got a chance to fire because the budget is checked at the TOP of its step loop. Both watchdogs that exist
for exactly this - `runner._watchdog` (600 s) and `track/guardian.py` (720 s) - read `run_state.updated_at`, and every
activity line refreshed it. The `llm-prober` thread logs "gemini is still unavailable ..." every couple of minutes, so
the wedged worker looked perfectly healthy and would have stayed wedged forever.

A status light must be computed from the work itself: only the applier's own thread (the main thread of `hunt.py run`)
may refresh the heartbeat. Background keep-alive/probe threads write activity lines, nothing more.
"""
import inspect
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import activity, runstate  # noqa: E402

OLD = 1.0  # a heartbeat long past either watchdog: nothing may leave this value in place


def _stale_state(tmp_path, monkeypatch):
    """A run_state file whose heartbeat is ancient, and a module state to match. Never the live file."""
    p = tmp_path / "run_state.json"
    p.write_text(json.dumps({"status": "running", "label": "A", "updated_at": OLD}), encoding="utf-8")
    monkeypatch.setattr(runstate, "PATH", p)
    monkeypatch.setattr(runstate, "_state", {"status": "running", "label": "A", "updated_at": OLD}, raising=False)
    return p


def _updated_at(p: Path) -> float:
    return json.loads(p.read_text(encoding="utf-8"))["updated_at"]


def test_a_background_thread_cannot_keep_the_applier_alive(tmp_path, monkeypatch):
    p = _stale_state(tmp_path, monkeypatch)
    done = threading.Event()

    def probe():
        runstate.touch()
        done.set()

    threading.Thread(target=probe, daemon=True).start()
    assert done.wait(5)
    assert _updated_at(p) == OLD, "a probe thread refreshed the applier's heartbeat"


def test_the_applier_thread_does_refresh_the_heartbeat(tmp_path, monkeypatch):
    p = _stale_state(tmp_path, monkeypatch)
    runstate.touch()  # called from the main thread == the applier loop
    assert _updated_at(p) > OLD


def test_a_prober_activity_line_does_not_refresh_the_heartbeat(tmp_path, monkeypatch):
    """The exact shape that hid the stall: `core.llm` logs from the `llm-prober` thread via core.activity."""
    p = _stale_state(tmp_path, monkeypatch)
    monkeypatch.setattr(activity, "PATH", tmp_path / "activity.jsonl")
    done = threading.Event()

    def prober():
        activity.log("info", "AI prober: gemini is still unavailable (temporary network/timeout problem (TimeoutError))")
        done.set()

    threading.Thread(target=prober, daemon=True).start()
    assert done.wait(5)
    assert (tmp_path / "activity.jsonl").exists(), "the activity line itself must still be written"
    assert _updated_at(p) == OLD, "a background prober line kept a wedged applier looking alive"

    activity.log("info", "navigator step 5: filled the form")  # the applier's own thread: this one IS progress
    assert _updated_at(p) > OLD


def test_the_guardian_kills_on_that_same_stale_heartbeat(tmp_path, monkeypatch):
    """Guardian side of the same file: a heartbeat 15 min old must read as a stalled worker (> 720 s)."""
    from track import guardian
    monkeypatch.setattr(guardian, "DATA", tmp_path)
    (tmp_path / "run_state.json").write_text(
        json.dumps({"status": "running", "label": "A", "updated_at": time.time() - 900}), encoding="utf-8")
    ages = guardian.heartbeat_ages()
    assert ages.get("A", 0) > 720, ages


def test_a_dead_workers_state_file_is_not_a_stalled_worker(tmp_path, monkeypatch):
    """: run_state_B.json (worker B killed long ago, never wrote `finished`) read 'silent 580 min' and the guardian
    killed the healthy worker A every 15 min. A file older than the oldest live worker's start belongs to nobody."""
    from track import guardian
    monkeypatch.setattr(guardian, "DATA", tmp_path)
    now = time.time()
    (tmp_path / "run_state.json").write_text(json.dumps({"status": "running", "label": "A", "updated_at": now - 30}), encoding="utf-8")
    (tmp_path / "run_state_B.json").write_text(json.dumps({"status": "running", "label": "B", "updated_at": now - 35000}), encoding="utf-8")
    ages = guardian.heartbeat_ages(since=now - 600)   # the live worker started 10 min ago
    assert list(ages) == ["A"], ages
    assert guardian.heartbeat_ages(since=float("inf")) == {}   # no worker alive: nothing to kill
    # a live worker that is genuinely wedged (its own file stale, but written after it started) still reads as stalled
    (tmp_path / "run_state.json").write_text(json.dumps({"status": "running", "label": "A", "updated_at": now - 900}), encoding="utf-8")
    assert guardian.heartbeat_ages(since=now - 1200).get("A", 0) > 720


def test_the_runner_watchdog_reads_the_same_heartbeat():
    from apply import runner
    src = inspect.getsource(runner._watchdog)
    assert "updated_at" in src and "os._exit(3)" in src, "the runner watchdog must still act on this heartbeat"


def test_a_wedge_is_a_transient_attempt_not_a_verdict(tmp_path, monkeypatch):
    """KB-80: the hard exit used to skip every record, so the wedging job stayed `candidate` and was first in line again on the
    relaunch. `_record_wedge` writes the attempt through the normal policy before the process exits."""
    from apply import errors, runner
    from core import db as dbm
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never touch the real applications ledger
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "w.db")
    db = dbm.DB(tmp_path / "w.db")
    jid, _ = db.upsert_job({"source": "t", "company": "Citi", "title": "Data Governance", "location": "Bengaluru",
                            "url": "https://x.test/w", "apply_url": "https://x.test/w"})
    db.set_status(jid, "candidate")
    monkeypatch.setattr(dbm, "DB", lambda *a, **k: db)

    got = runner._record_wedge({"now": {"id": jid, "company": "Citi", "title": "Data Governance",
                                        "step": "LLM navigator: driving the portal"}}, 600)
    assert got == jid
    row = db.c.execute("SELECT status, attempts, fail_class, next_attempt_at FROM jobs WHERE id=?", (jid,)).fetchone()
    assert row["status"] == "candidate", (dict(row), "a freeze is a retry, not a rejection")
    assert row["attempts"] == 1 and row["next_attempt_at"], dict(row)
    assert row["fail_class"] == "transient", dict(row)
    assert errors.classify("failed", "the applier froze inside a browser call (no progress for 600s) on 'x'") == "transient"
    # the normal policy still ends the retries: after MAX_TRANSIENT it is our bug, not an endless loop
    assert errors.next_attempt("transient", errors.MAX_TRANSIENT) is None


def test_a_wedge_with_no_current_job_records_nothing(monkeypatch):
    """Queue preparation has no job: the watchdog must still restart, and must not invent a row."""
    from apply import runner
    assert runner._record_wedge({"now": {"company": "Preparing the queue"}}, 600) == ""


def test_the_watchdog_records_before_it_exits():
    from apply import runner
    src = inspect.getsource(runner._watchdog)
    assert src.index("_record_wedge(s, limit)") < src.index("os._exit(3)")


def test_a_wedged_action_names_itself_in_the_activity_log():
    """KB-81: the freeze left only "step 4: <plan reason>"; the action that never returned was never written down."""
    src = inspect.getsource(__import__("apply.agent", fromlist=["navigate"]).navigate)
    act = src.index('navigator step {step + 1}: acting:')
    click = src.index('if do == "click" and el and SEARCH_NAV.search')
    assert act > click, "the pre-action line must sit after the refused-site-search check"
    assert src.index('activity.log("ai", f"navigator step {step + 1}: acting:') < src.index('signin_click = bool('),         "the line must be written BEFORE the action is dispatched"
