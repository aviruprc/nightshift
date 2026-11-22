import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rank import filter as f  # noqa: E402
from rank import ghost  # noqa: E402


def chk(t, loc="Bengaluru", d=""):
    return f.check({"title": t, "location": loc, "description": d})


def test_families_and_titles():
    assert chk("Senior Data Analyst") == ("data_bi", None)
    assert chk("Assistant Vice President - Risk Analytics")[0] == "risk_reg"
    assert chk("Financial Analyst - FP&A")[0] == "finance"
    assert chk("SAS Developer")[0] == "etl_sas"
    assert chk("Operations Analyst")[0] == "generic"
    assert chk("Data Analytics Manager")[1] == "title_excluded"
    assert chk("Vice President, Risk")[1] == "title_excluded"
    assert chk("Software Engineer")[1] == "title_excluded"
    assert chk("Marketing Executive")[1] == "off_target"


def test_location():
    assert chk("Data Analyst", "Remote, India")[1] is None
    assert chk("Data Analyst", "Remote - US")[1] == "location"
    assert chk("Data Analyst", "Pune")[1] == "location"


def test_experience_and_python():
    assert chk("Data Analyst", d="Requires 10+ years of experience")[1] == "experience_10y"
    assert chk("Data Analyst", d="3-5 years of experience in SQL")[1] is None
    assert chk("Data Analyst", d="Strong Python is mandatory")[1] is None  # volume policy: Python only lowers the fit score, never rejects
    assert chk("Data Analyst", d="Python is a plus")[1] is None


def test_ghost():
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert "stale" in ghost.flags({"title": "x", "posted_at": old})
    assert ghost.flags({"title": "x", "posted_at": new}) == []
    assert ghost.is_ghost(ghost.flags({"title": "x", "posted_at": new, "description": "Join our talent pool"}))
    # three listings of one role are usually three SOURCES of one live job, not a ghost: flagged, never retired for it
    assert "repost" in ghost.flags({"title": "x", "posted_at": new}, repost_count=3) and not ghost.is_ghost(ghost.flags({"title": "x", "posted_at": new}, repost_count=3))
    assert not ghost.is_ghost(ghost.flags({"title": "x"}))  # undated is soft


def test_entry_level_titles_in_her_domain_join_the_mix():  #
    from rank import filter as f
    for t in ("Junior Data Analyst", "Associate - Risk Reporting", "MIS Executive", "Finance Officer", "Data Operations Associate"):
        assert f.family(t) is not None, t
    for t in ("Marketing Executive", "Sales Associate", "HR Coordinator", "Executive Assistant", "Associate Software Engineer"):
        assert f.family(t) is None or f.TITLE_EXCLUDE.search(t), t


def test_finance_operations_titles_are_kept_and_managers_stay_out():   # the user (maximise applications): audit of 16,374 feed listings
    from rank import filter as flt
    def keep(title):
        return not flt.check({"title": title, "company": "X", "location": "Bengaluru", "description": "", "url": "", "apply_url": "", "source": "t"})[1]
    for t in ("Institutional Equity Division - Associate, Fund Accounting (Bangalore)", "Associate, Investor Services", "Associate, Travel & Expense Management",
              "Assistant Manager - Internal Audit", "Associate Manager - Accounts Payable", "Senior Associate - Reconciliation", "Payroll Specialist", "Tax Analyst"):
        assert keep(t), t
    for t in ("Manager - Compliance (PMS)", "Associate Manager, Database SRE", "Software Engineer", "Operations Manager, Linehaul", "Associate Director - Legal",
              "Executive - IT Support", "Mechanical Engineer Associate", "Senior Software Development Manager"):
        assert not keep(t), t
