"""Tiny live run-state file read by the dashboard (atomic writes)."""
import json
import os
import threading
import time
from pathlib import Path

import os as _os

# one state file per applier so parallel runs don't overwrite each other (B = run on the profile copy)
_LABEL = _os.environ.get("JOBHUNT_LABEL") or ("B" if _os.environ.get("JOBHUNT_PROFILE") else "A")
PATH = Path(__file__).resolve().parent.parent / "data" / ("run_state.json" if _LABEL == "A" else f"run_state_{_LABEL}.json")
_state: dict = {}


def touch():
    """Heartbeat only ('I am alive'), called on every activity line - but only from the APPLIER's own thread.

    KB-80: every activity line refreshed this heartbeat, including the `llm-prober` background thread's
    "gemini is still unavailable ..." line every couple of minutes. When the applier's main thread blocked inside a
    Playwright call (a company, -14:12: `page.evaluate` never returned, Chrome and Python both at 0% CPU),
    BOTH watchdogs - `runner._watchdog` (600 s) and the guardian (720 s) - read a fresh heartbeat and the worker sat
    wedged for 15+ minutes while the queue looked healthy. A heartbeat must be lit by the thing it claims is alive:
    the applier loop runs on the main thread, so a probe/keep-alive thread can no longer stand in for it."""
    if _state.get("finished") or not _state.get("status"):
        return
    if threading.current_thread() is not threading.main_thread():
        return
    update()


def update(**kw):
    _state.update(kw)
    _state["label"] = _LABEL
    _state["updated_at"] = time.time()
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    for attempt in range(8):  # Windows: the dashboard may be reading the file at this instant; retry, never crash a run over a status file
        try:
            tmp.write_text(json.dumps(_state), encoding="utf-8")
            os.replace(tmp, PATH)
            return
        except OSError:
            time.sleep(0.15 * (attempt + 1))
    try:
        PATH.write_text(json.dumps(_state), encoding="utf-8")
    except OSError:
        pass


def read() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
