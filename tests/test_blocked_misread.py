"""A navigator that denies a page full of controls has misread the snapshot (a company,, KB-46).

a company ("AM- Financial Planning & Analysis") failed with
"navigator: {'done': 'blocked', 'why': 'Target job apply interface did not load; page shows unrelated job listings and no
interactive elements to act on.'}" and was stamped `fail_class='logic'` (no automatic retry). The screenshot the same run saved
(`data/screenshots//_agent.png`) shows the real job page: title, Job ID 882635278, Mumbai, and an
orange **Apply** button, next to a "Similar Jobs" sidebar. The page was fine; the model's reading of the snapshot was not.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, errors  # noqa: E402

TATA_WHY = ("Target job apply interface did not load; page shows unrelated job listings and no interactive elements to act on.")
ELS = [{"i": 0, "tag": "button", "text": "Apply"}, {"i": 1, "tag": "a", "text": "Login"},
       {"i": 2, "tag": "a", "text": "Saved Jobs"}, {"i": 3, "tag": "a", "text": "Sr Manager - Global..."}]


def test_the_tata_claim_is_refused_because_the_snapshot_lists_controls():
    snap = {"url": "https://jobs.tatacommunications.com/jobs/882635278", "text": "AM- Financial Planning & Analysis Job ID 882635278",
            "els": ELS}
    assert agent.blocked_misread(snap, TATA_WHY) is True


def test_a_page_that_really_has_nothing_is_not_overruled():
    snap = {"text": "", "els": []}
    assert agent.blocked_misread(snap, TATA_WHY) is False
    pdf = {"text": "PDF", "els": [{"i": 0, "tag": "a", "text": "download"}]}
    assert agent.blocked_misread(pdf, "Page is a PDF asset URL with no interactive elements; cannot continue") is False


def test_a_different_kind_of_block_is_left_alone():
    snap = {"text": "Too many attempts", "els": ELS}
    assert agent.blocked_misread(snap, "Account is locked; too many failed attempts") is False
    assert agent.blocked_misread(snap, "") is False


def test_a_navigator_claim_that_the_page_never_loaded_is_never_a_permanent_logic_failure():
    detail = f"navigator: {TATA_WHY}"
    assert errors.classify("failed", detail) == "transient"
    assert errors.classify("failed", "navigator: the page never rendered - 8 snapshots failed at https://x") == "transient"


def test_the_navigator_refuses_the_claim_and_makes_the_model_look_again():
    import inspect
    src = inspect.getsource(agent.navigate)
    assert "blocked_misread(snap, str(plan.get(\"why\") or \"\"))" in src, "the misread check exists but the navigator never asks it"
    i = src.index("blocked_misread(snap")
    assert "continue" in src[i:i + 1400], "a refused 'blocked' must continue the loop, not end the job"
