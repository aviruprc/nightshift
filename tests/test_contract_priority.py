"""."""
from apply import runner
from discover import boards, linkedin, naukri_search, returnships


def test_contract_postings_are_recognised():
    for t in ("Data Analyst (Contract)", "Business Analyst - C2H", "Reporting Analyst | Contract to Hire", "MIS Analyst - 6 months contract",
              "Financial Analyst (Fixed Term)", "Temporary Data Analyst", "Freelance BI Analyst", "Collections Specialist - Contractor role"):
        assert returnships.is_contract(t), t
    assert returnships.is_contract("Data Analyst", "Employment type: Contract (12 months), on the payroll of our staffing partner.")


def test_ordinary_jobs_about_contracts_are_not_contract_work():
    for t in ("Contract Management Analyst", "Contracts Manager", "Contract Compliance Specialist", "Data Analyst", "Contract Analyst"):
        assert not returnships.is_contract(t), t
    assert not returnships.is_contract("Data Analyst", "x" * 700 + " you will review vendor contracts and temporary staffing plans")


def test_every_board_searches_for_contract_work():
    for mod in (boards, linkedin, naukri_search):
        assert "Contract Data Analyst" in mod.QUERIES, mod.__name__


def test_contracts_go_right_behind_returnships(monkeypatch):
    monkeypatch.setattr(runner.learn, "stats", lambda d: {})
    q = [{"id": "a", "title": "Data Analyst", "description": "", "family": "data_bi", "apply_url": "https://x.com/1", "url": "", "company": "A"},
         {"id": "c", "title": "Data Analyst (Contract)", "description": "", "family": "data_bi", "apply_url": "https://y.com/1", "url": "", "company": "C"},
         {"id": "r", "title": "Returnship Analyst", "description": "", "family": "returnship", "apply_url": "https://z.com/1", "url": "", "company": "R"}]
    assert [r["id"] for r in runner.keep_sends_flowing(q, None)] == ["r", "c", "a"]


def test_a_returnship_called_an_internship_is_kept():
    """: 'PMO Intern (Returnship Program)' (SISA, Keka, Bengaluru) was rejected three times by the word 'intern'."""
    from rank import filter as f
    fam, why = f.check({"title": "PMO Intern (Returnship Program)", "location": "Bengaluru, India"})
    assert fam and not why, (fam, why)
    assert f.check({"title": "Data Analyst Intern", "location": "Bengaluru"})[1] == "title_excluded"
    assert f.check({"title": "Director, Returnship Program", "location": "Bengaluru"})[1] == "title_excluded"


def test_a_linkedin_posting_found_on_another_board_gets_its_description(monkeypatch):
    """: two returnships found by Foundit point at linkedin.com/jobs/view/...; their description was never fetched."""
    from discover import ats_api, linkedin
    monkeypatch.setattr(linkedin, "fetch_description", lambda url: "FULL " + url)
    got = ats_api.lazy_description({"source": "board:foundit", "ats": "board", "url": "https://www.foundit.in/job/1",
                                    "apply_url": "https://www.linkedin.com/jobs/view/4490749445/"})
    assert got == "FULL https://www.linkedin.com/jobs/view/4490749445/"
