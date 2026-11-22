"""discover/ats_sitemap.py: the generic sitemap + JobPosting reader (no network: pages are fixtures)."""
import json

from discover import ats_sitemap as sm


def _ld(title, city="Bengaluru", country="India", valid="2099-01-01", desc="<p>Build reports.</p>"):
    data = {"@context": "https://schema.org", "@type": "JobPosting", "title": title, "description": desc, "datePosted": "2026-11-05", "validThrough": valid,
            "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": city, "addressCountry": country}},
            "hiringOrganization": {"@type": "Organization", "name": "Acme"}}
    return f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body></body></html>'


def test_a_json_ld_job_in_bengaluru_is_read_with_its_description():
    j = sm.parse_job("https://x.com/job/1", _ld("Data Analyst"))
    assert j["title"] == "Data Analyst" and "Bengaluru" in j["location"] and j["description"] == "Build reports." and j["posted_at"] == "2026-11-05"


def test_a_job_outside_india_or_already_expired_is_dropped():
    assert sm.parse_job("https://x.com/job/2", _ld("Analyst", city="Austin", country="United States")) is None
    assert sm.parse_job("https://x.com/job/3", _ld("Analyst", valid="2020-01-01")) is None


def test_graph_wrapped_postings_are_found():
    node = {"@type": "JobPosting", "title": "Risk Analyst", "description": "d", "jobLocation": {"address": {"addressLocality": "Bangalore", "addressCountry": "IN"}}}
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [{"@type": "WebPage"}, node]}) + "</script>"
    assert sm.parse_job("https://x.com/job/4", html)["title"] == "Risk Analyst"


def test_a_page_without_json_ld_is_read_from_its_title_tag():
    body = "<html><head><title>Financial Analyst - Bangalore, Bengaluru, Karn&#x101;taka, India | Acme Careers</title></head><body>" + \
           "<h1>x</h1><p>Job Description Summary " + "Prepare month end reports and reconcile ledgers. " * 12 + "</p></body></html>"
    j = sm.parse_job("https://x.com/en/jobs/r1/financial-analyst-bangalore/", body)
    assert j and j["title"] == "Financial Analyst" and "Bengaluru" in j["location"] and j["description"].startswith("Job Description")


def test_a_title_page_for_another_country_is_dropped():
    body = "<html><head><title>Analyst - Chicago, Illinois, United States | Acme</title></head><body>Job Description " + "x" * 400 + "</body></html>"
    assert sm.parse_job("https://x.com/en/jobs/r2/analyst/", body) is None


def test_city_urls_are_fetched_first_and_slug_filter_limits_the_rest():
    locs = ["https://x.com/job/sales-rep", "https://x.com/job/data-analyst", "https://x.com/job/bengaluru/anything", "https://x.com/category/jobs/", "https://x.com/about"]
    picked = sm.pick_urls(locs, {"job_re": "/job/"})
    assert picked == ["https://x.com/job/bengaluru/anything", "https://x.com/job/data-analyst"]


def test_sitemap_index_is_followed(monkeypatch):
    pages = {"https://x.com/robots.txt": "Sitemap: https://x.com/index.xml",
             "https://x.com/index.xml": "<sitemapindex><sitemap><loc>https://x.com/jobs.xml</loc></sitemap></sitemapindex>",
             "https://x.com/jobs.xml": "<urlset><url><loc>https://x.com/job/a</loc></url><url><loc>https://x.com/job/b</loc></url></urlset>"}

    class R:
        def __init__(self, u):
            self.status_code, self.text, self.content = (200, pages[u], pages[u].encode()) if u in pages else (404, "", b"")

    monkeypatch.setattr(sm, "_get", lambda u, **kw: R(u))
    assert sm.sitemap_locs("x.com") == ["https://x.com/job/a", "https://x.com/job/b"]


def test_a_next_js_page_with_a_job_description_meta_tag_is_read():
    desc = "&lt;p&gt;" + "Support the valuation practice with models and reports. " * 8 + "&lt;/p&gt;"
    html = f'<html><head><meta name="job-description" content="{desc}"/></head><body><div>Kroll Contact us</div><h1>Analyst, Valuation Services</h1>' \
           '<div>Valuation Services | Bangalore, India | 21015021</div></body></html>'
    j = sm.parse_job("https://careers.x.com/en/job/bangalore/analyst/1", html)
    assert j and j["title"] == "Analyst, Valuation Services" and "Bangalore" in j["location"] and "valuation practice" in j["description"]


def test_a_meta_page_for_another_country_is_dropped():
    desc = "&lt;p&gt;" + "text " * 80 + "&lt;/p&gt;"
    html = f'<html><head><meta name="job-description" content="{desc}"/></head><body><h1>Analyst</h1><div>Tax | London, United Kingdom | 9</div></body></html>'
    assert sm.parse_job("https://careers.x.com/en/job/london/analyst/9", html) is None


def test_a_rate_limited_page_is_not_cached_as_not_a_job(monkeypatch, tmp_path):
    monkeypatch.setattr(sm, "CACHE", tmp_path / "c.json")
    monkeypatch.setattr(sm, "sitemap_locs", lambda host, explicit=None, max_files=40: ["https://x.com/job/data-analyst-1"])

    class R:
        status_code, text = 429, ""

    monkeypatch.setattr(sm, "_get", lambda u, **kw: R())
    assert sm.fetch({"host": "x.com", "job_re": "/job/"}) == []
    assert sm._load_cache() == {}
