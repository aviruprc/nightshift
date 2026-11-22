"""The navigator's step budget must fit a real application, and a spent budget must say where it ran out (KB-68).

"too many steps" was the largest failure class (21 queued jobs in data/fix_backlog.md). The saved pages show the applier was NOT
looping - a company sat on "Review" (current step 5 of 5), a company on "Voluntary Disclosures", a company on "Review" - it had
used 16 steps on a 5-step Workday application and was stopped one click short. A repeated page (seen_sigs>=3), a dead replay
(KB-39) and a sign-in loop (SIGNIN_MAX) are what stop a stuck page; the raw step count must not be tighter than the flow.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import agent  # noqa: E402


def _defaults():
    p = inspect.signature(agent.navigate).parameters
    return p["max_steps"].default, p["budget_s"].default


def test_the_step_budget_fits_a_five_step_workday_application():
    steps, budget = _defaults()
    assert steps >= 24, f"max_steps={steps} stops a normal Workday application mid-flow"
    assert budget >= 540, f"budget_s={budget} would kill a long-but-progressing application"


def test_the_budget_is_still_bounded():
    steps, budget = _defaults()
    assert steps <= 40 and budget <= 900, (steps, budget)


def test_running_out_of_steps_names_the_budget_and_the_page():
    src = inspect.getsource(agent.navigate)
    assert "too many steps ({max_steps} steps)" in src, "the failure must say how many steps were allowed"
    assert "| page: " in src, "the failure must name the page heading it ran out on"


def test_a_repeated_page_still_stops_early_rather_than_burning_the_new_budget():
    """The bigger budget is only safe while the no-progress guards stay in front of it."""
    src = inspect.getsource(agent.navigate)
    assert "if seen_sigs.get(sig, 0) >= 3:" in src
    assert "page stopped changing" in src
    assert agent.SIGNIN_MAX <= 3
