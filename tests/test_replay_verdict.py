"""A remembered plan that changes nothing is stale, and must not be replayed again in this run.

a company (`51b9c2ef`, plus the / attempts): the Workday "Application Questions" page had every required
answer filled (four "No" dropdowns, salary 1500000 - all present in a saved failure page) yet "Save and
Continue" did not advance. The navigator spent steps 12-16 "replaying a remembered plan for this page", each replay clicking the same
button, nothing changed, and the job died as "too many steps" without the model ever being asked to look at the page again.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent  # noqa: E402


def test_a_replay_that_changed_nothing_is_dead():
    assert agent.replay_verdict(("sigA", [{"do": "click"}]), True, "sigA") == "dead"


def test_a_replay_that_moved_the_page_on_is_kept():
    assert agent.replay_verdict(("sigA", [{"do": "click"}]), True, "sigB") == "ok"


def test_a_fresh_plan_that_changed_nothing_is_not_a_dead_recipe():
    # the LLM's own attempt failing says nothing about the recipe store: it must not disable replays for the whole host
    assert agent.replay_verdict(("sigA", [{"do": "click"}]), False, "sigA") == "none"


def test_no_plan_at_all_is_nothing_to_judge():
    assert agent.replay_verdict(None, False, "sigA") == "none"


def test_the_navigator_acts_on_the_verdict_and_stops_replaying():
    src = inspect.getsource(agent.navigate)
    assert "verdict = replay_verdict(" in src
    assert "replays_dead = True" in src
    assert "replay_allowed(pg, snap, replays_dead)" in src, "a code page may never be replayed (KB-79)"


SWISS_RE_PASSCODE_TEXT = (
    "Career Opportunities: Create an Account Already a registered user? Please sign in *indicates a required field. "
    "Email Address: asha.example@example.com We have sent a one-time password to your email address. If you do not find "
    "this email in your primary inbox, please check your spam or bulk email folders. *Passcode: Show The passcode entered is "
    "incorrect. Please try again. Continue You can request for a new passcode in 01:36 minutes.")


def test_a_passcode_page_is_never_replayed():
    """KB-79: the a company passcode page's recipe was a bare 'click Continue' - replayed before the code was typed, it submitted
    an EMPTY passcode and the attempt died as 'sign-in did not complete'."""
    snap = {"text": SWISS_RE_PASSCODE_TEXT}
    assert agent.code_page(None, snap) is True
    assert agent.replay_allowed(None, snap) is False
    assert agent.replay_allowed(None, {"text": "Application Questions"}, False) is True, "an ordinary page is still replayable"
    assert agent.replay_allowed(None, {"text": "Application Questions"}, True) is False, "a dead recipe still stops replaying"
