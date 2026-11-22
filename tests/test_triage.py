"""Failure triage: the verdict decides where the limited Claude budget goes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from track import triage  # noqa: E402


@pytest.mark.parametrize("detail,expected", [
    ("unanswered required: Are you ok with Third party Payroll?", "cheap_bug"),
    ("unanswered required: could not read the question", "cheap_bug"),
    ("listing closed on LinkedIn", "genuine"),
    ("job page has no Apply button (job closed or removed)", "genuine"),
    ("navigator: {'done': 'blocked', 'why': 'verification locked: maximum attempts reached'}", "genuine"),
    ("navigator: page stopped changing () last: step 9: click #1 Continue", "hard_portal"),
    ("navigator: time budget (420s) used up; last: step 12", "hard_portal"),
    ("navigator: too many steps; last: step 16", "hard_portal"),
    ("navigator: LLM unavailable (RuntimeError)", "infra"),
    ("deepseek-web is signed out", "infra"),
])
def test_verdicts(detail, expected):
    assert triage.verdict(detail) == expected


def test_same_reason_groups_together_regardless_of_numbers_and_steps():
    a = triage.norm("navigator: page stopped changing () last: step 9: click #1 Continue FAILED (TimeoutError)")
    b = triage.norm("navigator: page stopped changing () last: step 14: click #7 Continue FAILED (TimeoutError)")
    assert a == b


def test_retry_prefix_is_ignored_when_grouping():  # 'retry at (transient): navigator: ...' produced empty keys
    a = triage.norm("retry at 10:34 (transient): navigator: time budget (420s) used up; last: step 12: click #7 Continue")
    b = triage.norm("navigator: time budget (420s) used up; last: step 9: click #3 Next")
    assert a == b and a.startswith("navigator: time budget")
