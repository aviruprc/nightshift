"""Answers the user RECORDS once in answers.yaml and the mechanisms that apply them: explicit per-skill years (0 allowed), form preferences, language
levels, the national tax id (only for a question that NAMES it) and the SMS opt-in rule. Nothing is answered unless it is recorded."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest  # noqa: E402
from apply import agent, answers_map as am  # noqa: E402
from core import profile  # noqa: E402

EXTRA = {
    "skill_years_explicit": {
        r"accounts (?:payable|receivable)|cash applications|bookkeeping|fund accounting": "0",
        r"\bagile\b|business analysis|\buat\b|mis reporting|dashboard": "2",
        r"\bsql\b": "2",
        r"\bpython\b": "0",
    },
    "form_preferences": {"travel": "No", "shifts": "Yes", "face_to_face_interview": "Yes", "contract_roles": "Yes", "industry": "Retail",
                         "basic_accounting": "No", "manufacturing": "No", "support_experience": "No"},
    "language_proficiency": {"english": {"read": "full", "write": "full", "speak": "full"},
                             "tamil": {"read": "full", "write": "below_full", "speak": "full"}},
    "pan_number": "AAAAA0000A",
}


@pytest.fixture()
def rec(monkeypatch):
    base = profile.answers()
    monkeypatch.setattr(profile, "answers", lambda: {**base, **EXTRA})


def Y(skill):
    return f"How many years of experience do you have in {skill}?"


def test_explicit_zero_and_nonzero_years(rec):
    for sk in ("accounts payable", "cash applications", "bookkeeping", "fund accounting"):
        assert am.answer_for(Y(sk), None) == "0", sk
    for sk in ("Agile", "business analysis", "UAT", "MIS reporting", "dashboard development"):
        assert am.answer_for(Y(sk), None) == "2", sk
    assert am.answer_for(Y("SQL"), None) == "2"


def test_a_yes_no_experience_question_follows_the_years(rec):
    assert am.answer_for("Do you have experience in Accounts Receivable / Accounts Payable?", None) == "No"
    assert am.answer_for("Do you have experience in Agile?", None) == "Yes"


def test_nothing_explicit_is_recorded_means_the_question_stays_parked():
    assert am.explicit_years("how many years of experience do you have in accounts payable?") is None
    assert am.recorded_preference("are you willing to travel?") is None


def test_a_skill_that_is_lacked_and_has_no_figure_stays_parked(rec, monkeypatch):
    base = profile.answers()
    monkeypatch.setattr(profile, "answers", lambda: {**base, **{**EXTRA, "skill_years_explicit": {r"accounts payable": "0"}}})
    assert am.answer_for(Y("accounts payable and Alteryx"), None) is None       # a covered skill never makes an unrecorded lacked one answerable
    assert am.answer_for(Y("Alteryx"), None) is None


def test_recorded_preferences(rec):
    assert am.answer_for("Are you willing to travel?", None) == "No"
    assert am.answer_for("Are you comfortable travelling to the Bengaluru office?", None) != "No"    # a commute in her own city is not travel
    assert am.answer_for("Willingness to work in shifts?", None) == "Yes"
    assert am.answer_for("Are you ok with F2F Interview?", None) == "Yes"
    assert am.answer_for("Are you comfortable with 6 months contractual position?", None) == "Yes"
    assert am.answer_for("What Industry do you specialize in?", None) == "Retail"
    assert am.answer_for("Do you have experience in manufacturing industry? If yes pls specify the years?", None) == "No"
    t = agent._facts_compact()
    assert "RECORDED PREFERENCES" in t and "travel = No" in t


def test_language_levels_full_and_one_below(rec):
    rw, sp = ["Advanced", "Intermediate", "Basic"], ["None", "Native", "Fluent", "Moderate", "Conversational"]
    assert [am.language_level("English", s, rw) for s in ("read", "write")] == ["Advanced", "Advanced"]
    assert am.language_level("English", "speak", sp) == "Fluent" and am.language_level("Tamil", "speak", sp) == "Fluent"
    assert am.language_level("Tamil", "read", rw) == "Advanced"
    assert am.language_level("Tamil", "write", rw) == "Intermediate"                        # a bit less than full: the nearest option below it
    assert am.language_level("Tamil", "write", ["Basic", "Professional", "Full Professional"]) == "Professional"
    assert am.language_level("Tamil", "write", ["Fluent", "Basic"]) is None                  # only Fluent / Basic: park, never guess
    assert am.language_level("French", "read", rw) is None                                   # not recorded: park
    t = agent._facts_compact()
    assert "recorded proficiency" in t and "nearest option BELOW full" in t


def test_a_plain_language_proficiency_string_means_full_for_every_language():
    assert am.language_level("English", "read", ["Basic", "Advanced"]) == "Advanced"      # the fixture records one level for all languages


def test_the_recorded_pan_answers_only_a_question_that_names_the_pan(rec):
    for q in ("PAN number?", "PAN card number", "Permanent Account Number (PAN)", "Enter your PAN"):
        assert am.answer_for(q, None) == "AAAAA0000A", q
    for q in ("National ID number", "Aadhaar number", "The national identifier?", "Pan India location preference?", "Passport number"):
        assert am.answer_for(q, None) is None, q                      # no national-id guess, and 'Pan India' is not the PAN
    t = agent._facts_compact()
    assert "ONLY for a field that itself names the PAN" in t and "AAAAA0000A" in t


def test_an_unrecorded_pan_is_never_invented():
    assert am.answer_for("PAN card number", None) is None
    assert "ONLY for a field that itself names the PAN" not in agent._facts_compact()


def test_sponsorship_is_stated_for_a_job_in_the_home_country_not_as_other_countries_yes():
    t = agent._facts_compact()
    assert "sponsorship / visa NO" in t and "SPONSORSHIP / VISA" in agent.PROMPT


def test_an_sms_opt_in_is_never_yes_but_a_phone_question_is_untouched():
    q = "I agree that Acme may reach out to me via SMS regarding my application"
    assert am.answer_for(q, "j") == "No"
    assert am.answer_for("Do you agree to the terms and conditions?", "j") is None
    assert am.answer_for("WhatsApp number", "j") not in ("No", "Yes")
