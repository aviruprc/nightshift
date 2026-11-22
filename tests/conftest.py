import os
from pathlib import Path

import pytest

os.environ["JOBHUNT_NO_LLM"] = "1"  # tests never call the LLM: deterministic and zero tokens
# Tests run against an INVENTED person (tests/fixtures/profile), never your real cv/facts.yaml or data/answers.yaml. Set before any
# project module is imported, because several modules read the profile at import time.
_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "profile")
os.environ.setdefault("JOBHUNT_FACTS", os.path.join(_FIX, "facts.yaml"))
os.environ.setdefault("JOBHUNT_ANSWERS", os.path.join(_FIX, "answers.yaml"))
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")  # never read or write the machine's Credential Manager

ROOT = Path(__file__).resolve().parent.parent
LIVE_CONTROL = [ROOT / "data" / n for n in ("STOP", "GUARDIAN_STOP", "supervisor.lock")]


@pytest.fixture(autouse=True)
def _no_test_may_touch_a_live_control_file():
    """The suite must never create or delete the REAL data/STOP, data/GUARDIAN_STOP or data/supervisor.lock.

    `test_start_run_clamps_a_bad_target_instead_of_crashing` called `live.action({"type": "start_run"})`, which runs
    `STOP.unlink(missing_ok=True)`; the live marker was deleted three seconds after it was created, the applier never stopped, and the
    pre-fix worker went on to submit the same a company role twice. A test that needs these paths must monkeypatch the module
    constant (e.g. `live.STOP`), never reach the real one."""
    before = {p: p.exists() for p in LIVE_CONTROL}
    yield
    after = {p: p.exists() for p in LIVE_CONTROL}
    assert after == before, f"a test changed a live control file (existed before -> after): {before} -> {after}"


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """Tests must never write into the REAL activity log, run state or usage log (they polluted the live dashboard with 'AI answered: Because X')."""
    from core import activity, llm, runstate
    monkeypatch.setattr(activity, "PATH", tmp_path / "activity.jsonl")
    monkeypatch.setattr(runstate, "PATH", tmp_path / "run_state.json")
    monkeypatch.setattr(llm, "USAGE_FILE", tmp_path / "llm_usage.jsonl")
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "provider_cooldown.json")  # tests put fake providers to rest: never in the real file
    monkeypatch.setattr(llm, "DOWN_FILE", tmp_path / "claude_down.json")
