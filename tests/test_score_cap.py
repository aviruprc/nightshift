"""A score whose own reason names a missing mandatory qualification must fall below the bar (three real reasons)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rank.score import capped_score  # noqa: E402


def test_ca_and_sap_mandatory_reasons_are_capped_below_the_bar():
    assert capped_score(40, True, "Finance controller requires CA qualification she lacks.", "Finance Controller - CA") < 35
    assert capped_score(45, True, "FP&A relevant but SAP S/4HANA/SAC mandatory skills she lacks.", "SAP R2R FP&A Consultant") < 35
    assert capped_score(50, False, "Role requires CPA licence she lacks.", "Controller") < 35   # even if the flag was not set


def test_a_reason_without_a_missing_mandatory_item_is_untouched():
    assert capped_score(35, False, "Tax/GST compliance role, adjacent finance but not her core.", "Tax Consultant") == 35


def test_python_only_gap_keeps_the_old_rule():
    assert capped_score(70, True, "Python is mandatory and she lacks it.", "Data analyst, python, sql") == 64


def test_python_plus_other_missing_items_is_capped_and_lacking_is_matched():
    for r in ("Mandatory Python/Alteryx/Databricks skills she lacks", "Pentaho and Python required; she lacks both tools",
              "requiring machine learning and Python skills she lacks", "Requires GCP experience which is in her lacking skills list."):
        assert capped_score(45, True, r, "data analyst") < 35, r


def test_optional_wording_is_not_a_missing_mandatory():
    assert capped_score(45, False, "She lacks Python, which is not mandatory.", "analyst") == 45


def test_missing_or_not_held_required_items_are_capped():
    for r in ("BA skills transferable but Salesforce platform experience missing",   # a company, applied at 42
              "Good fit but she does not have SAP FICO", "No experience in Guidewire, which the role requires"):
        assert capped_score(42, False, r, "analyst") < 35, r
    assert capped_score(42, False, "Tableau experience missing but it is a plus", "analyst") == 42


def test_years_gate_uses_her_real_total_from_facts():
    from rank.score import her_total_years, years_cap
    facts = {"experience": [{"start": "Sep 2022", "end": "Jan 2025"}, {"start": "Jan 2021", "end": "Mar 2021"},
                            {"start": "Oct 2018", "end": "Jul 2020"}]}
    assert her_total_years(facts) == 4.5
    assert years_cap(40, "Requires 5+ years of experience in data analytics", 3.8) == 25
    assert years_cap(40, "3-5 years of experience", 3.8) == 40     # a range reads its lower end
    assert years_cap(40, "4-6 yrs experience", 3.8) == 40          # within one year of hers
    assert years_cap(40, "No figure stated", 3.8) == 40


def test_a_ceiling_is_not_a_minimum():
    from rank.score import years_cap
    assert years_cap(62, "Experience: Up to 6 years' experience in Financial Planning and Analysis", 3.8) == 62
    assert years_cap(62, "Maximum 8 years of experience", 3.8) == 62


def test_review_cases_employer_history_preferred_and_returnships_are_not_minimums():
    from rank.score import years_cap
    assert years_cap(60, "With 25 years of experience serving banks, we hire analysts. 2+ years of experience in SQL.", 3.8) == 60
    assert years_cap(60, "Our firm has over 50 years of experience. Requirements: 3 years experience.", 3.8) == 60
    assert years_cap(60, "Requirements: 3+ years of experience in reporting. Preferred: 6+ years of experience in banking.", 3.8) == 60
    assert years_cap(60, "Returnship programme for women with 5+ years of prior work experience and a career break", 3.8,
                     title="Returnship Program - Analyst") == 60
    assert years_cap(60, "Minimum 6 years of experience in FP&A is required", 3.8) == 25   # a real minimum still caps


def test_the_5_plus_rule_is_the_same_in_the_filter_and_the_prompt():
    """Skip roles whose stated minimum is N+ years (facts.yaml preferences.max_stated_min_years; the fixture says 5)."""
    from rank import filter as flt, score
    job = {"title": "Data Analyst", "location": "Bengaluru", "description": "Minimum 5 years of experience in data analysis. " + "x" * 400}
    assert flt.check(job, skip_location=True)[1] == "experience_5y"
    ok = dict(job, description="3-5 years of experience in data analysis. Our firm has 50 years of experience. " + "x" * 400)
    assert flt.check(ok, skip_location=True)[1] is None
    rendered = score.render_prompt([])
    assert "5+ years" in rendered and "8+ years" not in rendered


def test_informatica_cloud_is_a_skill_the_scorer_is_told_she_lacks():
    """Reviewer: a company 'Informatica Cloud Developer' scored 75 although answers_map refuses cloud/IICS as her experience."""
    from core import profile
    assert "Informatica Cloud (IICS)" in profile.facts()["not_claimable"]
    from rank import score
    assert "{lacks}" in score.PROMPT
