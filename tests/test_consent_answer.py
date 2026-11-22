"""A privacy-consent question is Yes; a notice-period question is her notice period; a marketing opt-in is neither.

a company (`0f08ec68`, "Specialist Data Analytics"): the Workday Application Questions page ended with the
consent dropdown still reading "Select One" and the banner "Error - Where applicable law requires my explicit consent ... as described
in the Privacy Notice for Applicants ... is required and must have a value." Two defects were behind it:
  1. `agent._named_required_fields` extracted NOTHING for that banner: the sentence split stops the name at the first '.' of "e.g.,"
     and the name is capped at 90 characters, so no field was ever repaired.
  2. `answer_for` would have answered the question with her NOTICE PERIOD ('Immediate'), because the bare rule `notice` matched the
     words "Privacy Notice" - an unrecorded fact typed into a Yes/No consent dropdown.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, answers_map  # noqa: E402

CONSENT_Q = ("Where applicable law requires my explicit consent (e.g., for processing sensitive data, conducting background checks or "
             "transferring my personal information outside my home country), I consent to that specific processing as described in the "
             "Privacy Notice for Applicants. I understand I may withdraw this consent at any time.")
BANNER = ("Errors Found Error - " + CONSENT_Q + " The field " + CONSENT_Q + " is required and must have a value.")


def test_a_privacy_consent_question_is_answered_yes_never_with_her_notice_period():
    assert answers_map.answer_for(CONSENT_Q) == "Yes"


def test_a_notice_period_question_still_uses_her_recorded_notice_period():
    from core import profile
    want = profile.answers()["notice_period"]
    for q in ("What is your notice period?", "How much notice do you need to serve?", "Notice period (in weeks)?"):
        assert answers_map.answer_for(q) == want, q


def test_a_marketing_opt_in_is_never_answered_yes():
    assert answers_map.answer_for("I agree to receive marketing communications and job alerts") is None


def test_an_accuracy_attestation_is_yes():
    assert answers_map.answer_for("I agree that the information I have provided is accurate") == "Yes"


def test_the_banner_naming_a_long_consent_question_is_extracted_whole():
    names = agent._named_required_fields({"text": BANNER, "errs": []})
    assert names, "the banner named a field and nothing was extracted"
    assert any("explicit consent" in n.lower() for n in names), names
    assert any(answers_map.answer_for(n) == "Yes" for n in names), [answers_map.answer_for(n) for n in names]
