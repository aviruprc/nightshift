"""A refused password reset must say what actually happened, and park it when no retry can help (KB-70).

a company (Regulatory Reporting & Governance Specialist) failed with "the saved login already works, no reset needed" - while the
same run's history said the password had just been rejected twice, the portal's one reset was already used, and the saved page was
Workday's Create Account / Sign In form. `accounts.is_verified(host)` is a claim about a past success, not an outcome of this
attempt; repeating the job every run cannot clear a rejected password when the reset window is closed.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import agent  # noqa: E402


def test_a_rejected_password_is_never_reported_as_a_working_login():
    why, park = agent._reset_refusal(verified=True, rejected="the page said the password is incorrect")
    assert "rejected" in why
    assert "already works" not in why
    assert park is True, "a rejected password plus no reset available is a manual action, not a retry"


def test_a_verified_login_with_no_rejection_keeps_the_old_stop_and_a_retry():
    why, park = agent._reset_refusal(verified=True, rejected=False)
    assert why == "the saved login already works, no reset needed"
    assert park is False, "no page rejection: a retry may still succeed, do not park it"


def test_an_unused_account_still_names_the_one_reset_window():
    why, park = agent._reset_refusal(verified=False, rejected=False)
    assert "already used" in why
    assert park is False


def test_the_navigator_parks_the_parked_case_and_keeps_the_retry_otherwise():
    src = inspect.getsource(agent.navigate)
    assert "_reset_refusal(accounts.is_verified(host), signin_rejected)" in src
    assert "if park:" in src
    i_park = src.index("if park:")
    assert "needs_you" in src[i_park:i_park + 400]
