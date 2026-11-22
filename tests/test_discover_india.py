"""Parser tests for the India in-house career feeds (no network): each fixture is a real captured response."""
import json
import logging
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from discover import ats_india  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


class _Resp:
    def __init__(self, text="", payload=None, status=200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    """Minimal requests.Session stand-in; one response for every GET/POST (the fetchers query several terms)."""

    def __init__(self, get_resp=None, post_resp=None):
        self.headers = {}
        self._get_resp = get_resp
        self._post_resp = post_resp

    def get(self, *args, **kwargs):
        return self._get_resp

    def post(self, *args, **kwargs):
        return self._post_resp


def _json(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def test_a_real_feed_response_parses_to_bengaluru_jobs(monkeypatch):
    payload = _json("infosys_jobs.json")
    monkeypatch.setattr(ats_india, "_session", lambda: _Session(get_resp=_Resp(payload=payload)))
    jobs = ats_india.infosys({"name": "Infosys"})

    titles = {j["title"] for j in jobs}
    assert titles == {"Oracle Risk Management Cloud Consultant", "Power BI Consultant",
                      "Business Analyst - Investment banking-Q3"}
    assert "Associate Business Analyst" not in titles  # Hyderabad fixture row must be filtered out
    assert all("BANGALORE" in j["location"].upper() for j in jobs)
    assert all(j["posted_at"] and j["posted_at"].startswith("2026-10") for j in jobs)
    assert all("jobReferenceCode=INFSYS-EXTERNAL-" in j["url"] for j in jobs)
    assert all(j["description"] for j in jobs)  # the feed carries the full JD fields


def test_lnt_real_response_parses_and_filters_location(monkeypatch):
    payload = _json("lnt_finance.json")
    monkeypatch.setattr(ats_india, "_session", lambda: _Session(post_resp=_Resp(payload=payload)))
    jobs = ats_india.lnt({"name": "Larsen & Toubro"})

    assert len(jobs) == 1  # only the Bengaluru record; Coimbatore/Gujarat rows are dropped
    j = jobs[0]
    assert "FAA" in j["title"]
    assert "bengaluru" in j["location"].lower()
    assert j["url"].endswith("/job/detail/LNT_F--S_1875735")
    assert j["posted_at"] == "2026-10-06"
    assert "Experience: " in j["description"]


def test_techmahindra_real_search_response_parses_cards(monkeypatch):
    page = (FIX / "techmahindra_page.html").read_text(encoding="utf-8")
    delta = (FIX / "techmahindra_data_analyst.html").read_text(encoding="utf-8")
    monkeypatch.setattr(ats_india, "_session", lambda: _Session(
        get_resp=_Resp(text=page), post_resp=_Resp(text=delta)))
    jobs = ats_india.techmahindra({"name": "Tech Mahindra"})

    assert len(jobs) == 1  # 4 real cards; only the BENGALURU one survives
    j = jobs[0]
    assert j["title"] == "Sr. Data Analyst"   # the title of the saved public posting
    assert j["location"] == "BENGALURU"
    assert j["url"].endswith("CurrentOpportunity.aspx?JobCode=100873")
    assert "Workday" in j["description"]
    assert j["posted_at"] is None  # the public search page carries no date


def test_ibm_real_response_parses_bengaluru_hits(monkeypatch):
    payload = _json("ibm_analyst.json")
    monkeypatch.setattr(ats_india, "_session", lambda: _Session(post_resp=_Resp(payload=payload)))
    jobs = ats_india.ibm({"name": "IBM"})

    assert len(jobs) == 9  # 10 real hits: 9 Bangalore + one "Multiple Cities" (not proven Bengaluru)
    assert all("Bangalore" in j["location"] for j in jobs)
    assert any(j["title"] == "Workforce Analyst" for j in jobs)
    assert all(j["url"].startswith("https://careers.ibm.com/careers/JobDetail?jobId=") for j in jobs)
    assert all(j["posted_at"] is None for j in jobs)  # a company's search API exposes no date


@pytest.mark.parametrize("fn,name", [
    (ats_india.infosys, "Infosys"),
    (ats_india.lnt, "Larsen & Toubro"),
    (ats_india.techmahindra, "Tech Mahindra"),
    (ats_india.ibm, "IBM"),
])
def test_http_error_returns_empty_and_logs(monkeypatch, caplog, fn, name):
    class ErrSession:
        headers = {}

        def get(self, *args, **kwargs):
            raise requests.ConnectionError("network down")

        def post(self, *args, **kwargs):
            raise requests.ConnectionError("network down")

    monkeypatch.setattr(ats_india, "_session", lambda: ErrSession())
    caplog.set_level(logging.WARNING, logger="discover.ats_india")

    assert fn({"name": name}) == []
    assert any(name in rec.getMessage() and rec.levelno == logging.WARNING for rec in caplog.records)


def test_registry_contains_the_four_feeds():
    from discover import ats_api
    for key in ("infosys", "lnt", "techmahindra", "ibm"):
        assert key in ats_api.FETCHERS
    names = {c["name"] for c in ats_api.load_companies()}
    assert {"Infosys", "Larsen & Toubro", "Tech Mahindra", "IBM"} <= names
