"""Naukri's own reCAPTCHA wall pauses Naukri; it is not 'signed out' and never bypassed (after a long offline spell every Naukri job page showed it)."""
import inspect
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import errors, naukri_apply, runner  # noqa: E402


def test_the_naukri_wall_is_recognised_and_normal_pages_are_not():
    assert naukri_apply.human_check("To continue your request please check the box to let us know you're human I'm not a robot reCAPTCHA")
    assert not naukri_apply.human_check("Data Analyst - Reporting Example Corp Apply Save Job description ...")


def test_a_human_check_pauses_naukri_then_it_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(naukri_apply, "PAUSE_FILE", tmp_path / "p.json")   # a test never writes the live data/ file
    naukri_apply.PAUSE["until"] = 0.0
    assert not naukri_apply.paused()
    naukri_apply.note_human_check(60)
    assert naukri_apply.paused()
    naukri_apply.PAUSE["until"] = time.time() - 1
    (tmp_path / "p.json").write_text('{"until": 0}')
    assert not naukri_apply.paused()


def test_the_flow_checks_before_it_calls_a_missing_apply_button_signed_out():
    src = inspect.getsource(naukri_apply.apply_naukri)
    assert src.index("human_check(body)") < src.index("signed out (no Apply button")
    assert "paused()" in inspect.getsource(runner.run) and "human_check(text)" in inspect.getsource(runner.browser_description)
    assert errors.classify("failed", "Naukri is showing its human check (reCAPTCHA) on job pages; Naukri is paused for 60 minutes; retried later") in ("transient", "captcha")


def test_the_pause_survives_a_worker_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(naukri_apply, "PAUSE_FILE", tmp_path / "naukri_pause.json")
    naukri_apply.PAUSE["until"] = 0.0
    naukri_apply.note_human_check(60)
    naukri_apply.PAUSE["until"] = 0.0          # a relaunched process starts with an empty memory
    assert naukri_apply.paused()               # ... but the file still says paused
