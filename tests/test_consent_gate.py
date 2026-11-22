"""A page that ASKS for its consent boxes gets them ticked, and a positive hint is never reported as the fault (a company, KB-44).

a company "Legal Entity Analyst" (jobs.ubs.com, BrassRing) died with "navigator: page stopped changing (You have signed in Successfully)
last: step 9: click #9 Back". The saved page (`a saved failure page`) says what the page really wanted:
"Note: You must AGREE to proceed." ... "Please check all consent checkboxes to continue." with two unticked boxes
("a company processing your Personal Data in the context of your application…", "The cross-border transfer of your Personal Data…")
and a DISABLED "Agree" button. Those boxes never say "I consent", so the generic consent rule never matched; and the reported reason
was the sign-in success message, because the fallback took the first message on the page.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent  # noqa: E402

UBS_ERRS = ["You have signed in Successfully", "Please check all consent checkboxes to continue.",
            "Agree button is enabled.", "Agree button is enabled."]


def _cb(i, label, checked=False):
    return {"i": i, "tag": "input", "type": "checkbox", "label": label, "text": label[:60], "checked": checked}


UBS_ELS = [
    _cb(8, "Dark modeLight Mode"),
    _cb(29, "UBS processing your Personal Data in the context of your application"),
    _cb(30, "The cross-border transfer of your Personal Data to above mentioned entities"),
    _cb(31, "I agree to receive marketing communications about future roles"),
]


def test_the_two_privacy_boxes_are_ticked_and_nothing_else():
    snap = {"text": "Thank you for your interest in a job at UBS Note: You must AGREE to proceed.", "errs": UBS_ERRS, "els": UBS_ELS}
    assert agent.consent_gate_boxes(snap) == [29, 30]


def test_a_page_that_does_not_ask_for_consent_is_left_alone():
    snap = {"text": "Please answer the following questions.", "errs": [], "els": UBS_ELS}
    assert agent.consent_gate_boxes(snap) == []


def test_an_already_checked_box_is_not_ticked_again():
    els = [_cb(29, "UBS processing your Personal Data", checked=True), _cb(30, "The cross-border transfer of your Personal Data")]
    snap = {"text": "You must agree to proceed", "errs": [], "els": els}
    assert agent.consent_gate_boxes(snap) == [30]


def test_the_reported_reason_is_what_the_page_complains_about_not_a_success_message():
    snap = {"text": "", "errs": UBS_ERRS, "els": []}
    why = agent._complaint_or_other(snap)
    assert "consent checkboxes" in why.lower(), why
    assert "signed in successfully" not in why.lower(), why


def test_when_the_page_only_says_positive_things_nothing_is_reported_as_the_fault():
    snap = {"text": "", "errs": ["You have signed in Successfully", "Agree button is enabled."], "els": []}
    assert agent._complaint_or_other(snap) == ""


def test_the_navigator_actually_ticks_the_boxes_it_decided_on():
    import inspect
    src = inspect.getsource(agent.navigate)
    assert "for i_ in consent_gate_boxes(snap)" in src, "the decision is made but never acted on"
