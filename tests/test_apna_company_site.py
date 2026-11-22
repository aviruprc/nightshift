"""Apna listings for her roles say "Apply on company
website"; the page data carries the company's own URL, so no Apna account is needed. Evidence: https://apna.co/job/1794971002 (a company, Oracle)."""
from discover import aggregators as ag

PAGE = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Data Analyst","description":"&lt;p&gt;' + "Analyse data and build dashboards. " * 20
        + '&lt;/p&gt;"}</script><script>self.__next_f.push([1,"{\\"external_job_url\\":\\"https://hcbt.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/260416\\"}"])</script>')


class R:
    def __init__(self, text):
        self.text = text


def test_the_company_site_link_and_the_description_are_read(monkeypatch):
    monkeypatch.setattr(ag, "_get", lambda url, **k: R(PAGE))
    ext, desc = ag.apna_detail("https://apna.co/job/1794971002")
    assert ext == "https://hcbt.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/260416"
    assert len(desc) >= 400 and "<" not in desc


def test_a_listing_with_no_company_link_keeps_its_apna_url(monkeypatch):
    monkeypatch.setattr(ag, "_get", lambda url, **k: R("<html>Apply on apna app</html>"))
    assert ag.apna_detail("https://apna.co/job/1") == ("", "")


def test_a_link_back_to_apna_is_not_a_company_site(monkeypatch):
    monkeypatch.setattr(ag, "_get", lambda url, **k: R('"external_job_url":"https://apna.co/job/1"'))
    assert ag.apna_detail("https://apna.co/job/1")[0] == ""


def test_a_failed_fetch_is_not_an_error(monkeypatch):
    def boom(url, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(ag, "_get", boom)
    assert ag.apna_detail("https://apna.co/job/1") == ("", "")
