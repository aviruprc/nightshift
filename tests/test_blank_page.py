"""KB-87: a page that renders NOTHING is a page state, not our bug.

a company (Finance Analyst, Workday `cisco.wd5.myworkdayjobs.com/.../apply`) evaluated to ZERO controls and ZERO
characters at, and the navigator stored the model's own wording - "Application page returned empty (no elements/text) after
multiple waits; cannot proceed with form fill or submit." - which matches no transient rule and was filed `logic` (our bug) after 2
attempts. The same URL renders a 12-control shell anonymously, so nothing about the JOB was wrong: the form never rendered.

The rule: look once more (a reload), then report it in words `errors.classify` reads as transient ("never rendered"). Never after
a final Submit click - a blank page there may be the send in progress.
"""
import inspect

from apply import agent, errors


def test_a_blank_page_is_transient_not_our_bug():
    detail = ("navigator: the page never rendered - 0 controls, 0 characters at cisco.wd5.myworkdayjobs.com after 2 looks; "
              "retried later, not a closure")
    assert errors.classify("failed", detail) == "transient"


def test_the_navigator_looks_once_more_before_it_believes_a_blank_page():
    src = inspect.getsource(agent.navigate)
    assert 'not (snap.get("els") or []) and not (snap.get("text") or "").strip()' in src
    assert "blank_seen == 1" in src             # one reload, then the verdict
    assert "the page never rendered - 0 controls, 0 characters" in src
    assert "final_step is None and not" in src  # never after a final Submit: the confirmation may be in progress


def test_the_models_blank_page_wording_is_no_longer_what_reaches_the_database():
    """The model's own sentence classifies as `logic`; ours must not. This pins that the stored detail is the navigator's."""
    old = ("navigator: Application page returned empty (no elements/text) after multiple waits; cannot proceed with form fill "
           "or submit.")
    assert errors.classify("failed", old) != "transient"     # why the wording had to change, not just the reload
    new = "navigator: the page never rendered - 0 controls, 0 characters at x after 2 looks; retried later, not a closure"
    assert errors.classify("failed", new) == "transient"
