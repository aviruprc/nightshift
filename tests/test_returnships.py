"""Returnship / career-restart discovery. No network, no browser."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from discover import aggregators, ats_india, boards, linkedin, naukri_search, returnships  # noqa: E402
from rank import filter as f  # noqa: E402


def chk(t, loc="Bengaluru", d=""):
    return f.check({"title": t, "location": loc, "description": d})


# ------------------------------------------------------------------ filter: her kind of returnship
def test_returnship_analyst_titles_are_kept():
    assert chk("Data Analyst - Returnship") == ("returnship", None)
    assert chk("Risk Analyst (Return to Work)") == ("returnship", None)
    assert chk("Finance Returnship Programme 2026") == ("returnship", None)
    assert chk("Career Restart - Business Analyst") == ("returnship", None)
    assert chk("Treasury Analyst | Re-entry Programme") == ("returnship", None)


def test_generic_returnship_programme_is_kept():
    assert chk("Returnship Programme 2026") == ("returnship", None)
    assert chk("Returner Programme - 2026 Cohort") == ("returnship", None)
    assert chk("Career Break - Returner Programme") == ("returnship", None)


def test_returnship_detected_from_the_description_only():
    assert chk("Analyst Programme 2026", d="A 16-week returnship for people returning to work.")[0] == "returnship"
    # a generic programme title whose JD names her domain
    assert chk("Returnship Programme 2026", d="Openings for data analysts and BI analysts.")[0] == "returnship"


def test_other_domain_returnship_is_rejected():
    assert chk("Mechanical Engineering Returnship")[1] == "off_target"
    assert chk("Returnship - Civil Design")[1] == "off_target"
    assert chk("Nursing Returnship Programme")[1] == "off_target"
    assert chk("Marketing Returnship - Brand Team")[1] == "off_target"


def test_no_regression_for_ordinary_titles():
    assert chk("Senior Data Analyst") == ("data_bi", None)
    assert chk("Financial Analyst - FP&A")[0] == "finance"
    assert chk("Operations Analyst")[0] == "generic"
    assert chk("Marketing Executive")[1] == "off_target"          # non-returnship off-target still rejected
    assert chk("Mechanical Engineer")[1] == "off_target"
    assert chk("Data Analytics Manager")[1] == "title_excluded"


def test_location_and_experience_rules_still_apply():
    assert chk("Data Analyst Returnship", loc="Pune")[1] == "location"
    assert chk("Data Analyst Returnship", loc="Remote - US")[1] == "location"
    assert chk("Data Analyst Returnship", d="Requires 10+ years of experience")[1] == "experience_10y"


def test_marker_is_stable_and_the_real_family_is_recoverable():
    fam, why = chk("Risk Analyst - Returnship")
    assert (fam, why) == ("returnship", None)
    # the stored marker is the family column value; the real family is recomputed deterministically
    assert f.family("Risk Analyst - Returnship") == "risk_reg"
    assert f.family("Data Analyst - Returnship") == "data_bi"
    assert f.RETURNSHIP_FAMILY == "returnship"


# ------------------------------------------------------------------ vocabulary
@pytest.mark.parametrize("term", [t for t in returnships.CANONICAL_TERMS if t != "encore"])
def test_canonical_vocabulary_matches(term):
    assert returnships.RETURN_RE.search(term), term


def test_bare_encore_is_not_a_returnship_marker():
    # "Encore" alone is also an ordinary word / employer name: never label such a job a returnship.
    assert not returnships.matches("Encore Capital Group")
    assert not returnships.matches("Senior Associate - Encore Capital")
    assert returnships.matches("Deloitte Encore Programme 2026")
    assert chk("Data Analyst - Encore Capital")[0] == "data_bi"


# ------------------------------------------------------------------ discovery queries
def test_query_builders_include_the_returnship_queries():
    for mod in (boards, linkedin, aggregators, naukri_search):
        for q in returnships.RETURNSHIP_QUERIES:
            assert q in mod.QUERIES, (mod.__name__, q)
    for qs in (ats_india.LNT_QUERIES, ats_india.IBM_QUERIES, ats_india.TM_QUERIES):
        assert "returnship" in qs
        assert "return to work" in qs
    assert any("returnship" in q.lower() for q in aggregators.GOOGLE_QUERIES)


def test_extend_is_deduplicated():
    q = returnships.extend(["Data Analyst", "Returnship"])
    assert q.count("Returnship") == 1
    assert q.count("Data Analyst") == 1
