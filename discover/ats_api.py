"""Fetch jobs straight from public ATS JSON APIs. Every fetcher returns list[dict] in the common job shape."""
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
WORKDAY_QUERIES = ["data analyst", "business analyst", "reporting analyst", "risk analyst", "business intelligence",
                   "power bi", "tableau", "sas", "financial analyst", "regulatory", "data quality", "informatica",
                   "returnship", "return to work", "career break", "returner", "relaunch"]   # returnship / career-restart programmes first
try:   # facts.yaml preferences.search_queries overrides this list (see docs/CONFIGURING_FACTS.md)
    from core import profile as _profile
    WORKDAY_QUERIES = _profile.search_queries(WORKDAY_QUERIES, lower=True)
except Exception:  # noqa: BLE001
    pass


def strip_html(s: str) -> str:
    s = html.unescape(s or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=25, **kw)
    r.raise_for_status()
    return r.json()


def _iso_ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else None


def greenhouse_apply_url(slug: str, absolute_url: str, job_id) -> str:
    """The Greenhouse-hosted form for a job whose public page is the company's own site.
    a company: coupang.jobs/?gh_jid=8398431 loads the form in a script-built iframe; "Apply now" never opened it
    for the bot (26 attempts). Greenhouse's own embed URL for the same job serves the form directly (HTTP 200, first_name field)."""
    host = (absolute_url or "").split("/")[2] if "//" in (absolute_url or "") else ""
    if not slug or not job_id or "greenhouse.io" in host:
        return absolute_url
    return f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={job_id}"


def greenhouse(c):
    d = _get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true")
    return [{"title": j["title"], "location": (j.get("location") or {}).get("name", ""), "url": j["absolute_url"],
             "apply_url": greenhouse_apply_url(c["slug"], j["absolute_url"], j.get("id")),
             "description": strip_html(j.get("content", "")), "posted_at": j.get("first_published") or j.get("updated_at")}
            for j in d.get("jobs", [])]


def lever(c):
    out = []
    for j in _get(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json"):
        out.append({"title": j["text"], "location": (j.get("categories") or {}).get("location", ""),
                    "url": j["hostedUrl"], "apply_url": j.get("applyUrl") or j["hostedUrl"],
                    "description": j.get("descriptionPlain", ""), "posted_at": _iso_ms(j.get("createdAt"))})
    return out


def ashby(c):
    d = _get(f"https://api.ashbyhq.com/posting-api/job-board/{c['slug']}?includeCompensation=false")
    # Ashby sets isRemote=true on HYBRID jobs (OpenAI "San Francisco", workplaceType "Hybrid"). Trusting it labelled them "(Remote)" and six
    # applications went to hybrid San Francisco / New York / London roles. Remote only when workplaceType says Remote; the country is always written.
    def loc(j):
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or "")
        base = j.get("location", "") or ""
        if country and country.lower() not in base.lower():
            base = f"{base}, {country}" if base else country
        return base + (" (Remote)" if str(j.get("workplaceType") or "").lower() == "remote" else "")
    return [{"title": j["title"], "location": loc(j),
             "url": j["jobUrl"], "apply_url": j.get("applyUrl") or j["jobUrl"],
             "description": j.get("descriptionPlain", ""), "posted_at": j.get("publishedAt")}
            for j in d.get("jobs", []) if j.get("isListed", True)]


def smartrecruiters(c):
    out, off = [], 0
    while off < 400:
        d = _get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings?limit=100&offset={off}&country=in")
        for j in d.get("content", []):
            loc = j.get("location") or {}
            out.append({"title": j["name"], "location": f"{loc.get('city', '')}, {loc.get('country', '')}"
                        + (" (Remote)" if loc.get("remote") else ""),
                        "url": f"https://jobs.smartrecruiters.com/{c['slug']}/{j['id']}",
                        "description": "", "posted_at": j.get("releasedDate")})
        off += 100
        if off >= d.get("totalFound", 0):
            break
    return out


def workable(c):
    d = _get(f"https://apply.workable.com/api/v1/widget/accounts/{c['slug']}?details=true")
    return [{"title": j["title"], "location": ", ".join(x for x in [j.get("city"), j.get("country")] if x),
             "url": j["url"], "description": strip_html(j.get("description", "")), "posted_at": j.get("published_on")}
            for j in d.get("jobs", [])]


def _wd_posted(s: str):
    s = (s or "").lower()
    now = datetime.now(timezone.utc)
    if "today" in s:
        return now.isoformat()
    if "yesterday" in s:
        return (now - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days", s)
    if m:
        return (now - timedelta(days=int(m.group(1)))).isoformat()
    return None


def workday(c):
    base = f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}"
    seen, out = set(), []
    for q in WORKDAY_QUERIES:
        for off in (0, 20, 40):  # Workday caps page size at 20
            try:
                r = requests.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": off,
                                                       "searchText": f"{q} bengaluru"},
                                  headers={**UA, "Content-Type": "application/json"}, timeout=25)
                r.raise_for_status()
            except requests.RequestException:
                break
            try:
                posts = r.json().get("jobPostings", [])
            except ValueError:
                # HTTP 200 with an HTML page: "Workday is currently unavailable." (weekly maintenance, seen on 51 tenants). Say so; it is retried on the next search.
                raise RuntimeError("Workday answered with a maintenance page, not job data (temporary)") from None
            for j in posts:
                p = j.get("externalPath")
                if not p or p in seen:
                    continue
                seen.add(p)
                out.append({"title": j["title"], "location": j.get("locationsText", ""),
                            "url": f"{base}/{c['site']}{p}", "description": "",
                            "posted_at": _wd_posted(j.get("postedOn")), "_wd_api": f"{api}{p}"})
            if len(posts) < 20:
                break
    return out


def workday_description(job: dict) -> str:
    try:
        d = _get(job["_wd_api"])
        return strip_html(d["jobPostingInfo"].get("jobDescription", ""))
    except Exception:  # noqa: BLE001
        return ""


def workday_open(url: str):
    """True if Workday's public API says the posting exists and can be applied to, False if it says it cannot / is gone (404), None if unknown."""
    m = re.match(r"(https?://([^./]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/(job/.+?)(?:/(?:apply|login|applyManually)[^?#]*)?(?:[?#].*)?$", url or "")
    if not m:
        return None
    try:
        r = requests.get(f"{m.group(1)}/wday/cxs/{m.group(2)}/{m.group(3)}/{m.group(4)}", headers=UA, timeout=20)
        if r.status_code == 404:
            return False
        if r.status_code != 200:
            return None
        return bool(r.json().get("jobPostingInfo", {}).get("canApply", False))
    except Exception:  # noqa: BLE001
        return None


def sr_description(job: dict) -> str:
    try:
        m = re.search(r"smartrecruiters\.com/([^/]+)/(\d+)", job["url"])
        d = _get(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
        secs = (d.get("jobAd") or {}).get("sections", {})
        return strip_html(" ".join((s or {}).get("text", "") for s in secs.values()))
    except Exception:  # noqa: BLE001
        return ""


JD_MARKERS = re.compile(r"responsibilit|qualification|requirement|what you|about the (role|job)|job description|experience", re.I)
ORA_URL = re.compile(r"https?://([^/]+)/hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Za-z]{2})?/sites/([^/]+)/job/(\d+)")


def page_description(html_text: str) -> str:
    """JD out of a public job page: schema.org JSON-LD 'description' (TalentBrew: companies), else the longest string in __NEXT_DATA__ (a company higher.gs.com),
    else the stripped page body when it clearly reads like a JD (a company). Returns "" when nothing convincing is there (never a nav bar / cookie banner)."""
    import json
    best = ""
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html_text or "", re.S):
        try:
            j = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            continue
        for x in (j if isinstance(j, list) else [j]):
            if isinstance(x, dict) and x.get("description"):
                s = strip_html(str(x["description"]))
                best = s if len(s) > len(best) else best
    if len(best) >= 400:
        return best
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text or "", re.S)
    if nd:
        try:
            stack, longest = [json.loads(nd.group(1))], ""
            while stack:
                o = stack.pop()
                if isinstance(o, str):
                    s = strip_html(o)
                    longest = s if len(s) > len(longest) else longest
                elif isinstance(o, dict):
                    stack.extend(o.values())
                elif isinstance(o, list):
                    stack.extend(o)
            if len(longest) >= 400 and JD_MARKERS.search(longest):
                return longest
        except Exception:  # noqa: BLE001
            pass
    body = strip_html(re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</>", " ", html_text or ""))
    return body[:6000] if len(body) >= 600 and len(JD_MARKERS.findall(body)) >= 2 else ""


def lazy_description(job: dict) -> str:
    u = job.get("apply_url") or job.get("url") or ""
    if job.get("ats") == "oracle_hcm" and not job.get("_ora"):  # stored rows lose the discovery-time _ora dict: rebuild it from the URL (JPMC, a company HCM, Oracle, a company...)
        m = ORA_URL.match(u)
        if m:
            job = {**job, "_ora": {"host": m.group(1), "site": m.group(2), "id": m.group(3)}}
    if job.get("ats") in ("oracle_hcm", "taleo", "icims"):
        from discover import ats_oracle
        try:
            return ats_oracle.fetch_description(job) or ""
        except Exception:  # noqa: BLE001
            return ""
    if job.get("ats") in ("custom", "successfactors", "talentbrew", "phenom", "in_house", "sitemap") and not job.get("_wd_api") and u.startswith("http")             and "myworkday" not in u and "linkedin.com" not in u and "naukri.com" not in u:
        try:
            return page_description(requests.get(u, headers=UA, timeout=20).text)
        except Exception:  # noqa: BLE001
            return ""
    # Foundit and Google rows often POINT at a LinkedIn posting (62 Foundit rows); only source "board:linkedin" was fetched, so two
    # returnships (Caterpillar, a company) sat with a 122-character description and were never prepared.
    if job.get("source") == "board:linkedin" or "linkedin.com/jobs/view" in u:
        from discover import linkedin
        return linkedin.fetch_description(u if "linkedin.com/jobs/view" in u else job["url"]) or ""
    if not job.get("_wd_api"):  # derive the public CXS JSON endpoint from the job URL (the runner has only the URL): https://<tenant>.wdN.myworkdayjobs.com/[en-US/]<site>/job/<path>
        m = re.match(r"(https?://([^./]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/(job/.+?)(?:/(?:apply|login|applyManually)[^?#]*)?(?:[?#].*)?$", job.get("apply_url") or job.get("url") or "")
        if m:
            job = {**job, "_wd_api": f"{m.group(1)}/wday/cxs/{m.group(2)}/{m.group(3)}/{m.group(4)}"}
    if job.get("_wd_api"):
        return workday_description(job)
    if job.get("ats") == "smartrecruiters":
        return sr_description(job)
    return ""


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
            "workable": workable, "workday": workday}


def custom(c):
    from discover.ats_custom import CUSTOM_FETCHERS
    return CUSTOM_FETCHERS[c["key"]](c)


FETCHERS["custom"] = custom
from discover import ats_oracle as _ora  # noqa: E402

FETCHERS.update(_ora.FETCHERS)
from discover import ats_misc as _misc  # noqa: E402

for _k, _f in _misc.FETCHERS.items():  # misc has its own oracle_hcm: keep ats_oracle's, expose misc's as misc_oracle_hcm
    FETCHERS[("misc_" + _k) if _k in FETCHERS else _k] = _f
from discover import ats_india as _india  # noqa: E402

FETCHERS.update(_india.FETCHERS)
from discover import ats_sitemap as _sitemap  # noqa: E402

FETCHERS['sitemap'] = _sitemap.fetch   # generic: sitemap + schema.org JobPosting pages (discover/ats_sitemap.py)


def load_companies() -> list[dict]:
    seen, names, out = set(), set(), []
    # companies_misc.yaml goes last: it overlaps oracle/custom registries, so skip its entries by company name
    for f in sorted(ROOT.glob("companies*.yaml"), key=lambda p: (p.name == "companies_misc.yaml", p.name)):
        is_misc = f.name == "companies_misc.yaml"
        for c in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("companies", []) or []:
            if is_misc:
                if c.get("ats") in ("oracle_hcm", "amazon", "goldman") and c["name"].lower() in names:
                    continue  # already covered by ats_oracle / ats_custom; other misc ATSs may be a better route
                if c.get("ats") == "oracle_hcm":
                    c = {**c, "ats": "misc_oracle_hcm"}
            key = (c.get("ats"), c.get("slug") or c.get("tenant") or c.get("key") or c.get("host") or c.get("name"))
            if key in seen or c.get("ats") not in FETCHERS:
                continue
            names.add(c["name"].lower())
            seen.add(key)
            out.append(c)
    return out


def fetch_company(c: dict):
    """Returns (company, jobs, error)."""
    try:
        jobs = FETCHERS[c["ats"]](c)
        for j in jobs:
            j.update(source=f"ats:{c['ats']}", ats=c["ats"], company=c["name"])
            j.setdefault("apply_url", j["url"])
        return c, jobs, None
    except Exception as e:  # noqa: BLE001
        return c, [], f"{type(e).__name__}: {str(e)[:100]}"


def fetch_all(companies=None, workers=12):
    companies = companies or load_companies()
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fetch_company, companies))
