"""apply to everything she is eligible for - and nothing she is not.
(1) Six applications went to hybrid San Francisco / New York / London jobs because a feed's remote flag was trusted.
(2) The title filter dropped roles she can do. Both from data: the live Ashby feed and data/rejected_sample.jsonl (6,907 listings)."""
from discover import ats_api
from rank import filter as flt

chk = lambda title, loc="Bengaluru": flt.check({"title": title, "location": loc, "description": ""})  # noqa: E731


def test_a_remote_job_for_another_place_is_not_hers():
    for loc in ("San Francisco (Remote)", "New York, NY (HQ) (Remote)", "London (Remote)", "Remote - Ireland", "Remote - Washington D.C.",
                "San Francisco, United States", "Remote (Sweden), Sweden (Remote)"):
        assert flt.location_ok(loc) is False, loc
        assert flt.location_ok(loc, remote_flag=True) is False, loc


def test_real_remote_and_bengaluru_still_pass():
    for loc in ("Remote", "Remote - India", "Bangalore, India (Remote)", "Remote (Worldwide)", "Work from home", "Anywhere in India", "Hybrid - Bengaluru",
                "Remote (APAC)"):
        assert flt.location_ok(loc) is True, loc


def test_a_stored_remote_elsewhere_row_is_a_conflict_at_queue_time():
    assert flt.location_conflict("San Francisco (Remote)") and flt.location_conflict("Remote - Ireland")
    assert not flt.location_conflict("Remote") and not flt.location_conflict("Bengaluru (Remote)") and not flt.location_conflict("Remote - India")


def test_ashby_hybrid_is_not_remote(monkeypatch):
    feed = {"jobs": [
        {"title": "Compensation Operations Analyst", "location": "San Francisco", "isRemote": True, "workplaceType": "Hybrid", "jobUrl": "u1",
         "address": {"postalAddress": {"addressCountry": "United States"}}},
        {"title": "Data Analyst", "location": "Bengaluru", "isRemote": True, "workplaceType": "Remote", "jobUrl": "u2",
         "address": {"postalAddress": {"addressCountry": "India"}}}]}
    monkeypatch.setattr(ats_api, "_get", lambda url: feed)
    a, b = ats_api.ashby({"slug": "x"})
    assert a["location"] == "San Francisco, United States" and not flt.location_ok(a["location"])
    assert b["location"] == "Bengaluru, India (Remote)" and flt.location_ok(b["location"])


def test_titles_she_is_eligible_for_are_kept():
    for t in ("Analytics Consultant", "KYC Representative (APAC)", "Senior Business Accountant - Cash and Bank", "Senior Internal Auditor",
              "Data Management Consultant", "Lead Data Analyst", "Manager, OTCR, Risk Reporting", "Manager - MI Analyst", "Lead Business Analyst",
              "Manager - Business Finance"):
        assert chk(t)[1] is None, (t, chk(t))


def test_titles_that_are_not_hers_stay_out():
    for t in ("Senior Manager, Market Risk Management", "Data Analytics Program Manager", "Business Analyst - Tech Lead", "Software Engineer",
              "Business Development Representative", "Teradata Developer", "Elite Banker - Branch Banking", "Product Manager, Risk",
              "Director, Financial Reporting", "Engineering Manager", "Sales Manager", "Data Engineer", "Business Transformation Consultant",
              "Data Analytics Manager", "FP&A Manager", "Head, Financial Crime Assurance"):
        assert chk(t)[1] is not None, t


def test_company_site_titles_kept_after_the_1116_audit():
    """: 20,894 rejected Bengaluru listings on company sites were re-read. These are reporting / finance-operations
    jobs she can do; the fit score still judges each posting."""
    from rank import filter as f
    for t in ("Sales Operations Analyst I", "Sales MIS Analyst", "Data Analyst Lead I", "Senior Lead Analyst", "FP&A Lead Analyst", "Specialist, Product Control",
              "Expert- Reg Reporting", "Compliance Professional", "Payment Lifecycle Operations Associate", "Associate I, Loan Servicing",
              "State Street Markets Recon Operations, Associate 2"):
        assert f.check({"title": t, "location": "Bengaluru"})[0], t


def test_noise_the_1116_widening_must_not_let_in():
    from rank import filter as f
    for t in ("Sales Executive", "Inside Sales Manager", "Tech Lead", "Lead Data Engineer", "Customer Service Executive - Personal Loans", "Security Operations Specialist",
              "Train Operations Solution Expert", "People Operations Associate", "AWS Platform Operations - Officer", "Field Operations Executive"):
        assert not f.check({"title": t, "location": "Bengaluru"})[0], t


def test_audit_1122_qa_in_kyc_and_sales_reporting_analyst_titles_pass_but_engineers_and_sellers_do_not():
    from rank import filter as flt
    ok = lambda t: flt.check({"title": t, "description": "x" * 500, "location": "Bengaluru"}, skip_location=True)[1] is None   # noqa: E731
    assert ok("KYC QA Expert - Associate")
    assert ok("Reporting Analyst-RD- Liability Sales, Salary & Trust")
    assert not ok("QA Engineer - Selenium")
    assert not ok("Software QA Analyst")
    assert not ok("Senior Account Executive, Sales")
    assert not ok("Sales Manager - Retail Loans")
