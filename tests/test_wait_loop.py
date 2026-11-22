"""A plan that ends in 'wait' must not hide an unchanged page forever (a company,: 'click Apply now + wait'
26 times on the same job description, one model call each, then 'too many steps')."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402


def test_waits_excuse_an_unchanged_page_only_twice():
    assert not agent.wait_exhausted(1) and not agent.wait_exhausted(2) and agent.wait_exhausted(3)


def test_the_loop_guard_counts_a_waited_page_after_that():
    src = inspect.getsource(agent.navigate)
    assert 'seen_sigs[("waited", sig)]' in src and "wait_exhausted(" in src
