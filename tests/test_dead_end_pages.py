"""Mid-flow dead ends must be named for what they are, not filed as our bug or promised a retry they will not get.

Three live cases from:
  * a company - Workday's 'Introduce Yourself' answered "Your information has been submitted.",
    then a blank page; the navigator concluded "no job application form exists" (a page-state claim, KB-46 style).
  * a company - the apply link opened a DarwinBox LOGIN page and the run reported "page stopped changing",
    stamped `logic` with no retry, although a sign-in wall is nothing of ours.
  * a company - "could not set School or University ... retried later" while `logic` means no retry at all.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import agent, errors  # noqa: E402


def test_the_workday_info_submitted_page_is_recognised_as_a_dead_end():
    # the real page from the saved screenshot: a header, that one sentence, nothing to click
    snap = {"text": "Home Introduce Yourself Your information has been submitted. Follow Us Candidate Privacy Policy",
            "els": [{"i": 0, "tag": "a", "text": "Home"}, {"i": 1, "tag": "a", "text": "Introduce Yourself"}]}
    assert agent.info_submitted_dead_end(snap) is True


def test_a_non_workday_final_confirmation_is_never_reloaded_away():
    """'Your information has been submitted.' on an unknown site may BE the submission: only Workday's Introduce Yourself step
    (or a page that says it) is treated as a dead end, so a real application is never thrown away by a reload."""
    snap = {"url": "https://jobs.example.com/apply/confirmation", "text": "Thank you. Your information has been submitted.",
            "els": [{"i": 0, "tag": "a", "text": "Home"}]}
    assert agent.info_submitted_dead_end(snap) is False
    snap["url"] = "https://acme.wd5.myworkdayjobs.com/apply"
    assert agent.info_submitted_dead_end(snap) is True


def test_a_real_application_page_that_mentions_submission_is_not_held_back():
    snap = {"text": "Your information has been submitted. Continue to My Experience",
            "els": [{"i": n} for n in range(30)]}
    assert agent.info_submitted_dead_end(snap) is False
    assert agent.info_submitted_dead_end({"text": "My Experience", "els": []}) is False


def test_a_workday_dead_end_is_retried_never_closed():
    d = ("navigator: Workday saved the 'Introduce Yourself' step at paloaltonetworks.wd5.myworkdayjobs.com but did not show "
         "the next step after 2 reloads - the profile step is done, the application was NOT sent; retried later, not a closure")
    assert errors.classify("failed", d) == "transient"
    assert errors.status_for("transient") == "candidate"


def test_a_login_wall_is_not_our_bug():
    d = ("navigator: sign-in did not complete at gene.darwinbox.in - the apply link opens a login page and no sign-in was "
         "completed; retried later, not a closure")
    assert errors.classify("failed", d) == "transient"


def test_the_navigator_reloads_before_calling_the_info_page_a_dead_end():
    src = inspect.getsource(agent.navigate)
    assert "info_submitted_dead_end(snap)" in src
    assert "info_waits <= 2" in src
    assert src.index("info_submitted_dead_end(snap)") < src.index("res.update(status=\"failed\", screenshot=str(shot),\n                           detail=(f\"navigator: Workday saved")
    # the dead-end branch must run before the LLM is asked anything about this page
    assert src.index("info_submitted_dead_end(snap)") < src.index("auth_page = any(")


def test_a_login_page_is_named_instead_of_page_stopped_changing():
    src = inspect.getsource(agent.navigate)
    #  (a company, SuccessFactors): a page with password boxes that is ALSO an application form is not a login page
    assert "if auth_page and not application_fields_page(snap):" in src
    assert "sign-in did not complete at" in src


def test_our_picker_failure_promises_the_retry_the_policy_gives():
    """KB-52 first removed a promise the policy did not keep; KB-58 then made the retry real (a list still rendering fails once and
    succeeds in a fresh session). Either way the message and the policy must agree - and it is never 'closed'."""
    d = ("navigator: could not set State (still required after 2 attempts); the posting is still open - it is our picker that failed, "
         "so retried later; a code fix is needed if it repeats")
    assert errors.classify("failed", d) == "transient"
    assert errors.next_attempt("transient", 0) is not None
    assert errors.status_for("transient") == "candidate"
