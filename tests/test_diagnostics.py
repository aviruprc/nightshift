"""Tests for the generic evidence reader (apply/diagnostics.py).

The REAL saved snapshots in data/failures/ are the ground truth for the failures; the
synthetic cases pin the rules that a saved page cannot demonstrate (Confirmation vs candidate home,
"no evidence -> transient"). No network, no browser, no database.
"""

import json
from pathlib import Path

import pytest

from apply import answers_map as am
from apply import diagnostics as dx

FAILURES = Path(__file__).resolve().parents[1] / "data" / "failures"


def test_consent_and_marketing_wording_is_the_one_shared_definition():
    """The reader and the fillers must classify the same checkbox the same way (no copied regexes)."""
    assert dx.CONSENT_RX is am.CONSENT
    assert dx.MARKETING_RX is am.MARKETING

# The nine snapshots the user named as the real test corpus (KB-11..KB-19).
CORPUS = [
    "corpus_01.json",
    "corpus_02.json",
    "corpus_03.json",
    "corpus_04.json",
    "corpus_05.json",
    "corpus_06.json",
    "corpus_07.json",
    "corpus_08.json",
    "corpus_09.json",
]


def load(name: str) -> dict:
    path = FAILURES / name
    if not path.exists():   # the corpus is private saved pages (data/failures, never published): skipped on a fresh install
        pytest.skip(f"saved failure snapshot not present: {name} (private corpus in data/failures)")
    return json.loads(path.read_text(encoding="utf-8"))


def kinds(problems) -> list:
    return [problem.kind for problem in problems]


def only(problems, kind):
    matches = [problem for problem in problems if problem.kind == kind]
    assert matches, f"expected a {kind} problem, got {kinds(problems)}"
    return matches[0]


def page(**overrides) -> dict:
    base = {"url": "https://example.test/apply", "title": "Apply", "heads": [], "errs": [], "text": "", "els": []}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- real corpus


def test_a_consent_box_keeps_next_disabled():
    """KB/a company: the required consent box was never accepted, so Next stayed disabled."""
    problems = dx.diagnose(load("corpus_01.json"))
    consent = only(problems, dx.CONSENT_REQUIRED)
    assert "consent to the terms and conditions" in consent.field
    assert consent.fixable and consent.action.startswith("check:")
    disabled = only(problems, dx.ELEMENT_NOT_ACTIONABLE)
    assert disabled.field == "Next"
    assert "disabled" in disabled.evidence


def test_the_repair_checks_consent_before_retrying():
    actions = dx.next_actions(dx.diagnose(load("corpus_01.json")))
    assert actions[0].startswith("check:")
    assert actions[-1] == "retry"
    status, detail = dx.terminal(dx.diagnose(load("corpus_01.json")))
    assert status == "failed"
    assert "CONSENT_REQUIRED" in detail and "retried later" in detail


def test_a_banner_names_the_required_phone_device_type():
    """KB-19: the real cause is in the banner, not in 'page stopped changing'."""
    problems = dx.diagnose(load("corpus_02.json"))
    required = only(problems, dx.FIELD_REQUIRED)
    assert required.field == "Phone Device Type"
    assert "The field Phone Device Type is required and must have a value" in required.evidence
    assert required.action == "select:Phone Device Type"
    assert dx.next_actions(problems) == ["select:Phone Device Type", "retry"]
    status, detail = dx.terminal(problems)
    assert status == "failed" and "Phone Device Type" in detail


def test_a_verification_request_is_not_a_wrong_password():
    """KB-18: the page says 'An email has been sent to you', never 'wrong password'."""
    problems = dx.diagnose(load("corpus_03.json"))
    assert dx.AUTH_VERIFY_PENDING in kinds(problems)
    assert dx.AUTH_REJECTED not in kinds(problems)
    assert "An email has been sent to you" in only(problems, dx.AUTH_VERIFY_PENDING).evidence
    status, detail = dx.terminal(problems)
    assert status == "failed"  # verification is automatable, so never needs_you
    assert "wrong password" not in detail.lower()
    actions = dx.next_actions(problems)
    assert actions[0] == "open_mail:verify"
    assert actions.index("open_mail:verify") < actions.index("sign-in")


def test_a_country_left_on_afghanistan_and_must_not_be_submitted():
    """KB-17: a country field is a fact; the form held 'Afghanistan'."""
    problems = dx.diagnose(load("corpus_04.json"))
    mismatch = only(problems, dx.COUNTRY_MISMATCH)
    assert mismatch.field == "Country/Region"
    assert "Afghanistan" in mismatch.evidence
    assert mismatch.action == "select:Country/Region=India"
    assert dx.CONFIRMED not in kinds(problems)  # the final submit never took
    status, detail = dx.terminal(problems)
    assert status == "failed" and "Afghanistan" in detail
    assert dx.next_actions(problems) == ["select:Country/Region=India", "retry"]


def test_a_sign_in_form_rendered_twice_is_auth_required():
    """Visa: two identical sign-in copies, the first copy's Create Account was inert."""
    problems = dx.diagnose(load("corpus_05.json"))
    auth = only(problems, dx.AUTH_REQUIRED)
    assert "2 password field" in auth.evidence
    assert "duplicate" in auth.evidence
    assert dx.AUTH_REJECTED not in kinds(problems)
    assert dx.terminal(problems)[0] == "failed"


def test_a_sign_in_does_not_trigger_a_password_reset():
    """A visible sign-in form is not proof the saved password is wrong (reset only on page rejection)."""
    actions = dx.next_actions(dx.diagnose(load("corpus_05.json")))
    assert actions[:3] == ["fill:Email Address", "fill:Password", "sign-in"]
    assert not any(action.startswith("open_mail:reset") for action in actions)


def test_a_consent_control_is_missing_from_the_element_list():
    """a company/Oracle: the error says consent is missing while the native checkbox is not in the snapshot."""
    problems = dx.diagnose(load("corpus_06.json"))
    consent = only(problems, dx.CONSENT_REQUIRED)
    assert "agree with the terms and conditions" in consent.field.lower()
    assert "You need to agree to the terms and conditions" in consent.evidence
    hidden = only(problems, dx.ELEMENT_NOT_ACTIONABLE)
    assert "no consent checkbox is in the element list" in hidden.evidence
    assert dx.terminal(problems)[0] == "failed"


def test_a_wrong_password_is_reported_only_when_the_page_says_it():
    says_so = dx.diagnose(load("corpus_07.json"))
    assert dx.AUTH_REJECTED in kinds(says_so)
    assert "wrong email address or password" in only(says_so, dx.AUTH_REJECTED).evidence
    assert dx.next_actions(says_so)[0] == "open_mail:reset"

    silent = dx.diagnose(load("corpus_08.json"))
    assert dx.AUTH_REJECTED not in kinds(silent)
    auth = only(silent, dx.AUTH_REQUIRED)
    assert "empty sign-in shell" in auth.evidence
    assert "wrong password" not in dx.terminal(silent)[1].lower()


def test_a_signup_wall_is_not_a_closure():
    """KB-16: the apply URL opens a talent signup page; the job is not closed."""
    problems = dx.diagnose(load("corpus_09.json"))
    wall = only(problems, dx.SIGNUP_WALL)
    assert "talent/signup" in wall.evidence
    assert dx.JOB_CLOSED not in kinds(problems)
    assert dx.terminal(problems)[0] == "failed"
    assert dx.next_actions(problems) == ["create_account", "retry"]


def test_no_corpus_snapshot_is_ever_closed_needs_you_or_applied():
    """None of the nine pages proves a closure, a human-only fact, or a submission."""
    for name in CORPUS:
        problems = dx.diagnose(load(name))
        status, detail = dx.terminal(problems)
        assert status == "failed", f"{name}: {status} ({detail})"
        assert "page stopped changing" not in detail.lower(), name
        assert detail.startswith("transient"), name


def test_diagnose_does_not_mutate_the_snapshot():
    """The reader is pure: a saved record must read the same afterwards (it is evidence for an audit)."""
    record = load("corpus_05.json")
    before = json.dumps(record, sort_keys=True)
    dx.diagnose(record)
    dx.terminal(dx.diagnose(record))
    dx.next_actions(dx.diagnose(record))
    assert json.dumps(record, sort_keys=True) == before


def test_diagnose_accepts_the_whole_saved_record_and_the_raw_page():
    record = load("corpus_03.json")
    from_record = dx.diagnose(record)
    from_page = dx.diagnose(record["snap"])
    assert kinds(from_record) == kinds(from_page)
    assert from_record == from_page


# --------------------------------------------------------------------------- synthetic: CONFIRMED


def test_confirmation_after_the_final_submit_is_applied():
    problems = dx.diagnose(page(
        url="https://acme.test/apply/thanks",
        title="Application submitted",
        text="Thank you for applying. Your application has been successfully submitted.",
        final_click=True,
    ))
    assert kinds(problems) == [dx.CONFIRMED]
    assert "after OUR final Submit" in problems[0].evidence
    assert dx.terminal(problems)[0] == "applied"
    assert dx.next_actions(problems) == ["stop:applied"]


def test_candidate_home_is_never_a_confirmation():
    """KB-13: 'Application Received' in My Applications is an OLD application's status."""
    problems = dx.diagnose(page(
        url="https://acme.wd1.myworkdayjobs.com/candidateHome",
        title="My Applications",
        text="My Applications Date Submitted Application Received 24 July 2026 R-01361795",
        els=[{"i": 0, "tag": "a", "text": "My Applications"}],
        final_click=True,
    ))
    assert dx.CONFIRMED not in kinds(problems)
    assert dx.terminal(problems)[0] == "failed"


def test_success_wording_without_a_final_click_is_unknown_not_applied():
    """Real archive case (a company): an application-STATUS page ran out its budget."""
    problems = dx.diagnose(page(
        url="https://careers.americanexpress.com/applied",
        title="Governance & Management",
        text="Status Application Submitted Applied on 04/11/2026 Analyst-Control Management",
        els=[],
    ))
    assert dx.CONFIRMED not in kinds(problems)
    ambiguous = only(problems, dx.UNKNOWN)
    assert "does not show it followed OUR final Submit" in ambiguous.evidence
    assert dx.terminal(problems)[0] == "failed"


def test_a_status_snapshot_is_never_a_confirmation():
    """a saved failure page: 'Application Submitted ... Applied on 04/11/2026'
    is a status page; the run ended on its time budget, so nothing proves it followed our click."""
    problems = dx.diagnose(load("corpus_10.json"))
    assert dx.CONFIRMED not in kinds(problems)
    assert dx.terminal(problems)[0] == "failed"


def test_a_page_with_a_password_field_never_confirms():
    problems = dx.diagnose(page(
        text="Thank you for applying. Your application has been successfully submitted.",
        els=[{"i": 0, "tag": "input", "type": "password", "label": "Password*", "req": True}],
        final_click=True,
    ))
    assert dx.CONFIRMED not in kinds(problems)
    assert dx.terminal(problems)[0] == "failed"


def test_a_submit_button_left_on_the_page_never_confirms():
    problems = dx.diagnose(page(
        text="Thank you for applying. Your application has been successfully submitted.",
        els=[{"i": 0, "tag": "button", "type": "submit", "text": "Submit application"}],
        final_click=True,
    ))
    assert dx.CONFIRMED not in kinds(problems)


def test_a_caller_that_knows_no_final_click_happened_can_suppress_confirmation():
    problems = dx.diagnose(page(
        text="Thank you for applying. Your application has been submitted.",
        final_click=False,
    ))
    assert dx.CONFIRMED not in kinds(problems)


# --------------------------------------------------------------------------- synthetic: one rule


def test_no_evidence_is_unknown_and_transient_never_an_invented_cause():
    problems = dx.diagnose(page(url="https://x.test/job/1", title="Job", text="Apply for this role"))
    assert kinds(problems) == [dx.UNKNOWN]
    assert "no recognised signal" in problems[0].evidence
    status, detail = dx.terminal(problems)
    assert status == "failed"
    assert detail.startswith("transient")
    assert "page stopped changing" not in detail.lower()
    assert dx.next_actions(problems) == ["reload", "retry"]


def test_closed_needs_explicit_closed_wording():
    problems = dx.diagnose(page(text="This job is no longer accepting applications."))
    assert kinds(problems) == [dx.JOB_CLOSED]
    assert dx.terminal(problems)[0] == "closed"
    assert dx.next_actions(problems) == ["stop:closed"]


def test_closed_wording_is_ignored_while_a_form_is_present():
    problems = dx.diagnose(page(
        text="This job is no longer accepting applications.",
        els=[{"i": 0, "tag": "input", "type": "text", "label": "Email*", "req": True}],
    ))
    assert dx.JOB_CLOSED not in kinds(problems)
    assert dx.terminal(problems)[0] == "failed"


def test_captcha_is_the_only_visible_route_to_needs_you():
    problems = dx.diagnose(page(text="Please complete the reCAPTCHA to continue."))
    assert kinds(problems) == [dx.CAPTCHA]
    assert dx.terminal(problems)[0] == "needs_you"
    assert dx.next_actions(problems) == ["stop:needs_you"]


def test_unanswered_required_question_is_needs_you():
    problems = dx.diagnose(page(text="unanswered required: do you hold a valid passport?"))
    assert dx.HUMAN_QUESTION in kinds(problems)
    assert dx.terminal(problems)[0] == "needs_you"


def test_pdf_url_is_a_document_not_a_form():
    problems = dx.diagnose(page(url="https://acme.test/docs/job-123.pdf", title="Job description"))
    assert kinds(problems) == [dx.PDF_OR_NO_FORM]
    assert dx.terminal(problems)[0] == "failed"
    assert dx.next_actions(problems) == ["stop:transient"]


def test_external_evidence_can_escalate_a_page_that_cannot_show_the_outcome():
    problems = dx.diagnose(page(text="Some page with nothing to read"))
    assert dx.terminal(problems)[0] == "failed"
    status, detail = dx.terminal(problems, {"closed": "Workday CXS canApply=false"})
    assert status == "closed" and "canApply=false" in detail
    status, detail = dx.terminal(problems, {"applied": "receipt R-01369315"})
    assert status == "applied" and "R-01369315" in detail


def test_invalid_email_on_a_form_is_not_a_rejected_sign_in():
    problems = dx.diagnose(page(
        errs=["Invalid email address"],
        text="Invalid email address",
        els=[{"i": 0, "tag": "input", "type": "email", "label": "Email*", "req": True}],
    ))
    assert dx.AUTH_REJECTED not in kinds(problems)
    assert dx.FORM_VALIDATION_ERRORS in kinds(problems)


def test_the_invisible_recaptcha_badge_is_not_a_challenge():
    problems = dx.diagnose(page(text="This site is protected by reCAPTCHA and the Google Privacy Policy apply."))
    assert dx.CAPTCHA not in kinds(problems)
    assert dx.terminal(problems)[0] == "failed"


def test_a_toast_is_not_a_validation_error():
    """a company: 'Assoc Analyst ... page is loaded' is a status toast, not a form error."""
    problems = dx.diagnose(page(
        url="https://medline.wd5.myworkdayjobs.com/x/login",
        title="Sign In",
        errs=["Assoc Analyst Business Operations page is loaded"],
        text="Sign In Email Address* Password* Sign In",
        els=[
            {"i": 0, "tag": "input", "type": "text", "label": "Email Address*", "req": True},
            {"i": 1, "tag": "input", "type": "password", "label": "Password*", "req": True},
        ],
    ))
    assert dx.FORM_VALIDATION_ERRORS not in kinds(problems)
    assert dx.AUTH_REQUIRED in kinds(problems)
