"""A blocked verdict must reach the failure detail as a sentence, and a sign-in wall must park instead of failing forever (KB-71).

a company (Financial Analyst Controllership) stored `navigator: {'done': 'blocked', 'why': 'Sign-in rejected twice due to wrong
password...'}` - the model nested its whole verdict object inside `why`, and the raw dict repr went into `status_detail`, where it
is read by a human and by the next diagnosis. The same job also kept failing on every run for a password the site rejects while
the portal's one reset was already used (KB-70): a retry cannot clear that.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import agent  # noqa: E402


def test_a_nested_verdict_object_becomes_its_sentence():
    why = {"done": "blocked", "why": "Sign-in rejected twice due to wrong password; needs a manual reset"}
    assert agent._why_text(why) == "Sign-in rejected twice due to wrong password; needs a manual reset"
    assert agent._why_text({"done": "blocked", "why": {"why": "nested twice"}}) == "nested twice"


def test_a_plain_string_or_none_is_untouched():
    assert agent._why_text("the page is broken") == "the page is broken"
    assert agent._why_text(None) == ""


def test_a_rejected_signin_on_a_verified_account_is_a_wall():
    assert agent._signin_wall("Sign-in rejected twice due to wrong password", verified=True) is True
    assert agent._signin_wall({"why": "Your account is locked after too many login attempts"}, verified=True) is True


def test_anything_else_is_not_a_signin_wall():
    assert agent._signin_wall("Sign-in rejected twice", verified=False) is False, "an unverified account may simply need a new one"
    assert agent._signin_wall("the page did not load", verified=True) is False
    assert agent._signin_wall("", verified=True) is False


def test_the_navigator_uses_the_text_helper_and_parks_the_wall():
    src = inspect.getsource(agent.navigate)
    assert "why_txt = _why_text(plan.get(\"why\"))" in src
    assert "{plan.get('why') or done}" not in src, "a dict repr must never reach the stored detail again"
    i = src.index("_signin_wall(why_txt")
    window = src[i:i + 1200]
    assert "needs_you" in window, "the sign-in wall branch must park the job"
    assert "saved_password=accounts.get(host) is not None" in src, "an unverified saved password can be a wall too (KB-78)"
    assert "rejected=signin_rejected" in src, "but only with the page's own rejection as evidence"


def test_a_verdict_returned_as_a_STRING_is_also_flattened():
    """A portal stored the object's repr verbatim because the model wrote its verdict as a JSON string."""
    why = "{'done': 'blocked', 'why': 'saved password rejected: needs a manual reset'}"
    assert agent._why_text(why) == "saved password rejected: needs a manual reset"


def test_a_saved_password_rejection_counts_as_a_wall():
    assert agent._signin_wall("saved password rejected: needs a manual reset", verified=True) is True


def test_the_stop_message_carries_the_phrase_the_page_actually_showed():
    """KB-73: a company's saved page had `errs: []`, so "the page said so" could not be checked - the matched words must travel with it."""
    said = agent._signin_stop(2, True, "Your account has been locked")
    assert "Your account has been locked" in said
    assert "the page said" in said
    assert "Not a closure" in said, "a rejection is still transient, never a closure"
    no_hit = agent._signin_stop(2, True, "")
    assert "a rejection message was on the page" in no_hit


def test_no_rejection_still_never_invents_one():
    txt = agent._signin_stop(2, False)
    assert "rejected" not in txt and "wrong password" not in txt


def test_a_verdict_nested_inside_done_is_lifted():
    """KB-78 (a company): the model put its whole verdict inside `done`."""
    plan = {"think": "checking", "done": {"done": "blocked", "why": "saved password rejected: needs a manual reset"}}
    lifted = agent._lift_nested_verdict(plan)
    assert lifted["done"] == "blocked"
    assert lifted["think"] == "checking"
    assert agent._why_text(lifted["why"]) == "saved password rejected: needs a manual reset"
    as_string = agent._lift_nested_verdict({"done": "{'done': 'blocked', 'why': 'password rejected'}"})
    assert as_string["done"] == "blocked" and as_string["why"] == "password rejected"
    assert agent._lift_nested_verdict({"done": "applied"})["done"] == "applied"
    assert agent._lift_nested_verdict("not a plan") == "not a plan"


def test_the_navigator_lifts_the_verdict_before_it_dispatches():
    src = inspect.getsource(agent.navigate)
    assert "plan = _lift_nested_verdict(plan)" in src
    # the model's think now comes before _why_text(done) ('blocked' alone hid ALLEN Digital's expired posting); still a sentence
    assert "or _why_text(done) or 'blocked'" in src, "even an odd verdict shape must store a sentence, never a repr"


def test_a_saved_password_rejection_parks_even_before_the_account_is_verified():
    """KB-78: a company's saved password was rejected and its one reset is used; no retry can clear that."""
    why = "saved password rejected: needs a manual reset"
    assert agent._signin_wall(why, verified=False, saved_password=True, rejected=True) is True
    assert agent._signin_wall(why, verified=False, saved_password=False, rejected=True) is False, "no saved account: create one, not a wall"
    assert agent._signin_wall(why, verified=False, saved_password=True, rejected=False) is False, "the model's word alone is not page evidence"
    assert agent._signin_wall("the page did not load", verified=False, saved_password=True, rejected=True) is False
