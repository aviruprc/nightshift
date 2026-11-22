"""Fetchers for mid-tier / enterprise ATSs: SuccessFactors, Phenom, Eightfold, TalentBrew, Avature, Jobvite,
BambooHR plus a universal schema.org JobPosting sitemap fallback.

Same contract as ats_api.py: fetcher(company_dict) -> list[dict] with
title, location, url, apply_url, description (may be ""), posted_at (ISO or None).

Only jobs whose location looks like Bengaluru / India are returned (override with `loc_re` in the company dict).
Register with: from discover.ats_misc import FETCHERS as MISC_FETCHERS; FETCHERS.update(MISC_FETCHERS)
"""
import html as _html
import json
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin, urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
DEFAULT_QUERIES = ["analyst", "data", "risk", "finance", "reporting", "business intelligence", "operations"]
LOC_RE = re.compile(r"(?i:bengaluru|bangalore|\bindia\b)|\bIND\b|\(IN\)")
CITY_RE = re.compile(r"bengaluru|bangalore", re.I)


def _strip(s: str) -> str:
    s = _html.unescape(s or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def _loc_ok(c: dict, text: str) -> bool:
    pat = re.compile(c["loc_re"], re.I) if c.get("loc_re") else LOC_RE
    return bool(pat.search(text or ""))


def _join_locs(*parts):
    seen = []
    for p in parts:
        for x in ([p] if not isinstance(p, (list, tuple)) else p):
            x = str(x).strip()
            if x and x not in seen:
                seen.append(x)
    return " | ".join(seen)


def _queries(c):
    return c.get("queries") or DEFAULT_QUERIES


def _http(method, url, **kw):
    kw.setdefault("timeout", 30)
    h = {**UA, **kw.pop("headers", {})}
    r = requests.request(method, url, headers=h, **kw)
    r.raise_for_status()
    return r


def _iso_epoch(sec):
    try:
        return datetime.fromtimestamp(int(sec), tz=timezone.utc).isoformat() if sec else None
    except (TypeError, ValueError, OSError):
        return None


def _dedupe(jobs):
    seen, out = set(), []
    for j in jobs:
        if j["url"] in seen:
            continue
        seen.add(j["url"])
        out.append(j)
    return out


# --------------------------------------------------------------------------------------------- Phenom
def phenom(c):
    """c: host (e.g. careers.cisco.com), optional prefix (/global/en), queries, size."""
    host = c["host"]
    s = requests.Session()
    s.headers.update(UA)
    home = s.get(f"https://{host}{c.get('prefix', '/')}", timeout=30)
    t = home.text
    page_id = c.get("page_id") or (re.search(r'"pageId":"(\w+)"', t) or [None, "page11"])[1]
    prefix = c.get("prefix") or (re.sub(r"/+$", "", urlparse(home.url).path) if home.url else "")
    if not re.match(r"^/\w\w/\w\w", prefix or ""):
        prefix = c.get("prefix") or "/global/en"
    out = []
    for q in _queries(c):
        frm = 0
        while frm < 600:
            body = {"lang": "en_global", "deviceType": "desktop", "country": "global", "pageName": "search-results",
                    "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": frm, "jobs": True, "counts": True,
                    "all_fields": ["category", "country", "state", "city", "type"], "size": 100, "clearAll": False,
                    "jdsource": "facets", "isSliderEnable": False, "pageId": page_id, "siteType": "external",
                    "keywords": f"{q} {c.get('kw_suffix', '')}".strip(), "global": True, "selected_fields": {},
                    "locationData": {}}
            r = s.post(f"https://{host}/widgets", json=body, timeout=40)
            r.raise_for_status()
            rs = r.json().get("refineSearch") or {}
            jobs = (rs.get("data") or {}).get("jobs") or []
            for j in jobs:
                loc = j.get("location") or j.get("cityStateCountry") or ", ".join(
                    x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
                locs = _join_locs(loc, j.get("multi_location") or [])
                if not _loc_ok(c, locs):
                    continue
                seq = j.get("jobSeqNo") or j.get("jobId") or j.get("reqId")
                slug = re.sub(r"[^A-Za-z0-9]+", "-", j.get("title", "")).strip("-")
                url = f"https://{host}{prefix}/job/{seq}/{slug}"
                out.append({"title": j.get("title", ""), "location": locs, "url": url,
                            "apply_url": j.get("applyUrl") or url, "description": j.get("descriptionTeaser", ""),
                            "posted_at": j.get("postedDate") or j.get("dateCreated")})
            frm += 100
            if len(jobs) < 100 or frm >= (rs.get("totalHits") or 0):
                break
    return _dedupe(out)


# ------------------------------------------------------------------------------------------- Eightfold
def _ef_pcsx(c):
    """Newer Eightfold 'PCS' sites where /api/apply/v2 answers 403 'Not authorized for PCSX'."""
    host, domain = c["host"], c["domain"]
    s = requests.Session()
    s.headers.update(UA)
    s.get(f"https://{host}/careers", timeout=30)
    out = []
    for q in c.get("queries") or [""]:
        start = 0
        while start < 500:
            r = s.get(f"https://{host}/api/pcsx/search", timeout=40,
                      params={"domain": domain, "query": q, "location": c.get("location", "Bengaluru"), "start": start,
                              "sort_by": "relevance"})
            r.raise_for_status()
            d = r.json().get("data", {})
            pos = d.get("positions") or []
            for p in pos:
                loc = _join_locs(p.get("locations") or [], p.get("standardizedLocations") or [])
                if not _loc_ok(c, loc):
                    continue
                url = f"https://{host}{p.get('positionUrl') or '/careers/job/' + str(p['id'])}"
                out.append({"title": p.get("name", ""), "location": loc, "url": url, "apply_url": url, "description": "",
                            "posted_at": _iso_epoch(p.get("postedTs") or p.get("creationTs")), "_ef_id": p["id"]})
            start += len(pos) or 10
            if not pos or start >= (d.get("count") or 0):
                break
    out = _dedupe(out)

    def desc(j):
        try:
            d = s.get(f"https://{host}/api/pcsx/position_details", params={"position_id": j["_ef_id"], "domain": domain, "hl": "en"},
                      timeout=30).json()
            j["description"] = _strip(d["data"].get("jobDescription", ""))
        except Exception:  # noqa: BLE001
            pass
    if c.get("descriptions", True):
        with ThreadPoolExecutor(6) as ex:
            list(ex.map(desc, out[:80]))
    for j in out:
        j.pop("_ef_id", None)
    return out


def eightfold(c):
    """c: host (e.g. portal.careers.hsbc.com or aexp.eightfold.ai), domain (e.g. hsbc.com), location, queries."""
    try:
        return _ef_classic(c)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (401, 403):
            return _ef_pcsx(c)
        raise


def _ef_classic(c):
    host, domain = c["host"], c["domain"]
    base = f"https://{host}/api/apply/v2/jobs"
    out = []
    for q in c.get("queries") or [""]:
        start = 0
        while start < 500:
            d = _http("GET", base, params={"domain": domain, "location": c.get("location", "Bengaluru"),
                                           "query": q, "start": start, "num": 10, "sort_by": "timestamp"}).json()
            pos = d.get("positions") or []
            for p in pos:
                loc = "; ".join(p.get("locations") or [p.get("location", "")])
                if not _loc_ok(c, loc):
                    continue
                url = p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{p['id']}"
                out.append({"title": p.get("name", ""), "location": loc, "url": url, "apply_url": url,
                            "description": _strip(p.get("job_description", "")), "posted_at": _iso_epoch(p.get("t_create") or p.get("t_update")),
                            "_ef_id": p["id"]})
            start += 10
            if not pos or start >= (d.get("count") or 0):
                break
    out = _dedupe(out)

    def desc(j):
        try:
            d = _http("GET", f"{base}/{j['_ef_id']}", params={"domain": domain}).json()
            j["description"] = _strip(d.get("job_description", ""))
        except Exception:  # noqa: BLE001
            pass
    if c.get("descriptions", True):
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(desc, [j for j in out if not j["description"]][:80]))
    for j in out:
        j.pop("_ef_id", None)
    return out


# --------------------------------------------------------------------------------------- SuccessFactors
def _sf_rss(c):
    host = c["host"]
    out = []
    cities = c.get("cities") or ["Bengaluru", "Bangalore"]
    for city, q in [(ci, qq) for ci in cities for qq in _queries(c)]:
        kw = f"({q}) AND locationSearch:({city})"
        r = _http("GET", f"https://{host}/services/rss/job/", params={"locale": c.get("locale", "en_US"), "rows": 300, "keywords": kw},
                  timeout=90)
        for it in ET.fromstring(r.content).findall(".//item"):
            link = (it.findtext("link") or "").split("?")[0]
            title = it.findtext("title") or ""
            if not link or title.startswith("No jobs currently"):
                continue
            m = re.search(r"\(([^()]*(?:Bengaluru|Bangalore|India|IND)[^()]*)\)\s*$", title, re.I)
            loc = m.group(1) if m else city
            posted = None
            try:
                posted = parsedate_to_datetime(it.findtext("pubDate")).astimezone(timezone.utc).isoformat()
            except Exception:  # noqa: BLE001
                pass
            out.append({"title": re.sub(r"\s*\([^()]*\)\s*$", "", title) if m else title, "location": loc, "url": link, "apply_url": link,
                        "description": _strip(it.findtext("description") or "")[:6000], "posted_at": posted})
    return out


def _sf_v1(c):
    """Newer SF career sites: POST /services/recruiting/v1/jobs (location facet is broken on some tenants so
    we page through keyword results and filter on custprimecity client-side)."""
    host = c["host"]
    s = requests.Session()
    s.headers.update(UA)
    s.get(f"https://{host}/search/", timeout=30)
    out = []
    city_re = re.compile(c.get("city_re", r"bengaluru|bangalore"), re.I)

    def page(args):
        q, pn = args
        for _ in range(3):
            try:
                r = s.post(f"https://{host}/services/recruiting/v1/jobs", timeout=40,
                           json={"locale": c.get("locale", "en_US"), "pageNumber": pn, "sortBy": "", "keywords": q, "location": "",
                                 "facetFilters": {}, "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""})
                return r.json()
            except Exception:  # noqa: BLE001
                continue
        return {}

    for q in _queries(c):
        first = page((q, 0))
        total = first.get("totalJobs", 0)
        pages = min((total + 9) // 10, c.get("max_pages", 120))
        results = list(first.get("jobSearchResult", []))
        with ThreadPoolExecutor(8) as ex:
            for d in ex.map(page, [(q, p) for p in range(1, pages)]):
                results += d.get("jobSearchResult", [])
        for j in results:
            r = j.get("response", j)
            city = r.get("custprimecity") or " ".join(r.get("custCity", []) if isinstance(r.get("custCity"), list) else [str(r.get("custCity", ""))])
            if not city_re.search(city or "") and not city_re.search(json.dumps(r.get("jobLocationShort", "")) or ""):
                continue
            slug = r.get("unifiedUrlTitle") or r.get("urlTitle") or ""
            url = f"https://{host}/job/{slug}/{r['id']}-{c.get('locale', 'en_US')}"
            out.append({"title": _html.unescape(r.get("unifiedStandardTitle", "")), "location": f"{city}, " + ", ".join(r.get("custCountryRegion", []) or []),
                        "url": url, "apply_url": url, "description": "",
                        "posted_at": _us_date(r.get("unifiedStandardStart"))})
    return out


def _us_date(s):
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    return None


def successfactors(c):
    """c: host (careers.wipro.com), mode: rss (default, Bengaluru filter is server-side) | v1 (client-side filter)."""
    jobs = _sf_v1(c) if c.get("mode") == "v1" else _sf_rss(c)
    return _dedupe(jobs)


# --------------------------------------------------------------------------------------------- TalentBrew
def _tb_parse(h):
    rows = []
    for m in re.finditer(r'<a href="([^"]*/job/[^"]+)"[^>]*data-job-id="\d+"[^>]*>(.*?)</a>', h, re.S):
        inner = m.group(2)
        chunk = inner if "job-location" in inner else h[m.end():m.end() + 700]
        lm = re.search(r'class="job-location[^"]*">(.*?)</', chunk, re.S)
        dm = re.search(r'job-date-posted">([^<]+)<', chunk)
        title = re.sub(r"^\s*<h2>|</h2>\s*$", "", inner.strip())
        loc = lm.group(1) if lm else ""
        rows.append((m.group(1), _strip(re.sub(r'<span class="job-location.*', "", title, flags=re.S)), _strip(loc), dm.group(1).strip() if dm else None))
    return rows


def talentbrew(c):
    """Radancy TalentBrew: c: host (careers.moodys.com), prefix (/en or ""), queries optional (default: all jobs).
    Uses the /search-jobs/results JSON; falls back to paging the server-rendered /search-jobs?k=&p= HTML."""
    host, prefix = c["host"], c.get("prefix", "/en")
    s = requests.Session()
    s.headers.update(UA)
    s.get(f"https://{host}{prefix}", timeout=30)
    out = []
    for q in c.get("queries") or [""]:
        page, pages = 1, 1
        while page <= min(pages, c.get("max_pages", 15)):
            r = s.get(f"https://{host}{prefix}/search-jobs/results", timeout=40,
                      params={"ActiveFacetID": 0, "CurrentPage": page, "RecordsPerPage": 100, "Distance": 50, "RadiusUnitType": 0,
                              "Keywords": q, "Location": "", "ShowRadius": "False", "IsPagination": "False", "FacetType": 0,
                              "SearchResultsModuleName": "Search Results", "SortCriteria": 0, "SortDirection": 0,
                              "SearchType": 5 if q else 6, "ResultsType": 0})
            h = r.json().get("results", "")
            if not h:  # server-rendered fallback
                r = s.get(f"https://{host}{prefix}/search-jobs", timeout=40,
                          params={"k": q, "l": c.get("location", "Bengaluru, Karnataka"), "p": page})
                h = r.text
            m = re.search(r'data-total-pages="(\d+)"', h)
            pages = int(m.group(1)) if m else 1
            for href, title, loc, dp in _tb_parse(h):
                if not _loc_ok(c, loc):
                    continue
                url = urljoin(f"https://{host}", href)
                out.append({"title": title, "location": loc, "url": url, "apply_url": url, "description": "",
                            "posted_at": _us_date(dp) if dp else None})
            page += 1
    return _dedupe(out)


# ----------------------------------------------------------------------------------------------- Avature
def avature(c):
    """c: base (https://www.metlifecareers.com/en_US/ml). Server-rendered SearchJobs list, 6/page via jobOffset."""
    base = c["base"].rstrip("/")
    out = []
    for q in c.get("queries") or [""]:
        off = 0
        while off < c.get("max_offset", 600):
            t = _http("GET", f"{base}/SearchJobs/{quote(q)}", params={"listFilterMode": 1, "jobRecordsPerPage": 50, "jobOffset": off}).text
            arts = re.findall(r"<article.*?</article>", t, re.S)
            if not arts:
                break
            for a in arts:
                m = re.search(r'<a[^>]*href="([^"]*JobDetail[^"]*)"[^>]*>\s*(.*?)\s*</a>', a, re.S)
                if not m:
                    continue
                loc = _strip((re.search(r'list-item-location">(.*?)</span>', a, re.S) or [0, ""])[1])
                if not _loc_ok(c, loc):
                    continue
                posted = None
                dm = re.search(r'list-item-posted">([^<]+)<', a)
                if dm:
                    try:
                        posted = datetime.strptime(dm.group(1).strip(), "%d-%b-%Y").replace(tzinfo=timezone.utc).isoformat()
                    except ValueError:
                        pass
                url = m.group(1)
                out.append({"title": _strip(m.group(2)), "location": loc, "url": url, "apply_url": url, "description": "", "posted_at": posted})
            off += len(arts)
            tot = re.search(r"\d+\s*-\s*\d+\s*of\s*(\d+)|of\s*(\d+)\s*results", t)
            if tot and off >= int(tot.group(1) or tot.group(2)):
                break
    return _dedupe(out)


# ------------------------------------------------------------------------------------------------ Jobvite
def jobvite(c):
    """c: slug (jobs.jobvite.com/{slug}). Server-rendered list of all openings."""
    t = _http("GET", f"https://jobs.jobvite.com/{c['slug']}/jobs").text
    out = []
    rows = re.findall(r'<tr>\s*<td class="jv-job-list-name">\s*<a href="([^"]+)">(.*?)</a>\s*</td>\s*<td class="jv-job-list-location">(.*?)</td>', t, re.S)
    for href, title, loc in rows:
        loc = _strip(loc)
        if not _loc_ok(c, loc):
            continue
        url = urljoin("https://jobs.jobvite.com", href)
        out.append({"title": _strip(title), "location": loc, "url": url, "apply_url": url, "description": "", "posted_at": None})
    return _dedupe(out)


# ----------------------------------------------------------------------------------------------- BambooHR
def bamboohr(c):
    """c: slug (subdomain of bamboohr.com)."""
    base = f"https://{c['slug']}.bamboohr.com"
    d = _http("GET", f"{base}/careers/list").json()
    out = []
    for j in d.get("result", []):
        loc_d = j.get("location") or {}
        loc = ", ".join(x for x in [loc_d.get("city"), loc_d.get("state")] if x) or (j.get("atsLocation") or {}).get("country", "")
        al = j.get("atsLocation") or {}
        loc_full = ", ".join(x for x in [al.get("city") or loc_d.get("city"), al.get("state") or loc_d.get("state"), al.get("country")] if x)
        if not _loc_ok(c, loc_full):
            continue
        url = f"{base}/careers/{j['id']}"
        out.append({"title": j.get("jobOpeningName", ""), "location": loc_full + (" (Remote)" if j.get("isRemote") else ""),
                    "url": url, "apply_url": url, "description": "", "posted_at": None})

    def desc(j):
        try:
            dd = _http("GET", f"{j['url']}/detail").json()["result"]["jobOpening"]
            j["description"] = _strip(dd.get("description", ""))
            j["posted_at"] = (dd.get("datePosted") or None) and dd["datePosted"] + "T00:00:00+00:00"
        except Exception:  # noqa: BLE001
            pass
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(desc, out[:60]))
    return out


# ----------------------------------------------------------------------------------------------- Oracle HCM
def oracle_hcm(c):
    """Oracle Recruiting Cloud (candidate experience). c: host (jpmc.fa.oraclecloud.com), site (CX_1001), lang (en)."""
    host, site = c["host"], c["site"]
    url = f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    out = []
    hdr = {"Accept": "application/json"}
    fac = _http("GET", url, params={"onlyData": "true", "finder": f"findReqs;siteNumber={site},facetsList=LOCATIONS;limit=1"},
                headers=hdr, timeout=60).json()["items"][0].get("locationsFacet") or []
    ids = [f["Id"] for f in fac if re.fullmatch(r"India|(Bengaluru|Bangalore).*", f["Name"], re.I)
           and re.fullmatch(r"India", f["Name"], re.I)] or [f["Id"] for f in fac if re.search(r"bengaluru|bangalore", f["Name"], re.I)]
    for lid in ids[:1]:
        off = 0
        while off < c.get("max_jobs", 700):
            fin = f"findReqs;siteNumber={site},selectedLocationsFacet={lid},limit=50,offset={off},sortBy=POSTING_DATES_DESC"
            r = _http("GET", url, params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": fin},
                      headers=hdr, timeout=60)
            items = r.json().get("items") or [{}]
            reqs = items[0].get("requisitionList") or []
            for j in reqs:
                loc = _join_locs(j.get("PrimaryLocation", ""), [x.get("Name", "") for x in j.get("secondaryLocations") or []])
                if not _loc_ok(c, loc):
                    continue
                u = f"https://{host}/hcmUI/CandidateExperience/{c.get('lang', 'en')}/sites/{site}/job/{j['Id']}"
                out.append({"title": j.get("Title", ""), "location": loc, "url": u, "apply_url": u,
                            "description": j.get("ShortDescriptionStr") or "",
                            "posted_at": (j.get("PostedDate") + "T00:00:00+00:00") if j.get("PostedDate") else None})
            off += 50
            if len(reqs) < 50 or off >= (items[0].get("TotalJobsCount") or 0):
                break
    return _dedupe(out)


# -------------------------------------------------------------------------- Amazon / a company (public JSON)
def amazon(c):
    """amazon.jobs search.json. c: queries (default analyst terms), city."""
    out = []
    for q in c.get("queries") or ["analyst", "business intelligence", "finance", "data"]:
        off = 0
        while off < 400:
            d = _http("GET", "https://www.amazon.jobs/en/search.json",
                      params={"normalized_country_code[]": "IND", "city": c.get("city", "Bengaluru"), "base_query": q, "offset": off,
                              "result_limit": 100, "sort": "recent"}).json()
            for j in d.get("jobs", []):
                url = "https://www.amazon.jobs" + j["job_path"]
                posted = None
                try:
                    posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    pass
                out.append({"title": j["title"], "location": j.get("normalized_location") or j.get("location", ""), "url": url,
                            "apply_url": j.get("url_next_step") or url,
                            "description": _strip(" ".join(j.get(k) or "" for k in ("description", "basic_qualifications", "preferred_qualifications"))),
                            "posted_at": posted})
            off += 100
            if off >= d.get("hits", 0):
                break
    return _dedupe(out)


def goldman(c):
    """a company higher.gs.com GraphQL (public). c: queries."""
    q = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $searchQueryInput) { totalCount items "
         "{ roleId corporateTitle jobTitle jobFunction locations { primary state country city } status division skills } } }")
    out = []
    for term in c.get("queries") or ["analyst", "data", "risk", "finance"]:
        pn = 0
        while pn < 10:
            body = {"operationName": "GetRoles", "query": q, "variables": {"searchQueryInput": {
                "page": {"pageSize": 50, "pageNumber": pn}, "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"},
                "filters": [{"filterCategoryType": "LOCATION", "filters": [{"filter": "India", "subFilters": [
                    {"filter": "Karnataka", "subFilters": [{"filter": "Bengaluru", "subFilters": []}]}]}]}],
                "experiences": ["PROFESSIONAL", "EARLY_CAREER"], "searchTerm": term}}}
            d = _http("POST", "https://api-higher.gs.com/gateway/api/v1/graphql", json=body,
                      headers={"Origin": "https://higher.gs.com", "Content-Type": "application/json"}).json()["data"]["roleSearch"]
            for i in d.get("items", []):
                loc = "; ".join(", ".join(x for x in [l.get("city"), l.get("state"), l.get("country")] if x) for l in i.get("locations") or [])
                url = f"https://higher.gs.com/roles/{i['roleId']}"
                out.append({"title": i["jobTitle"], "location": loc, "url": url, "apply_url": url,
                            "description": f"{i.get('division') or ''} | {i.get('jobFunction') or ''} | {', '.join(i.get('skills') or [])}",
                            "posted_at": None})
            pn += 1
            if (pn * 50) >= d.get("totalCount", 0):
                break
    return _dedupe(out)


# ------------------------------------------------------------------------------------ JSON-LD + sitemap
def _jobposting_nodes(doc):
    if isinstance(doc, list):
        for x in doc:
            yield from _jobposting_nodes(x)
    elif isinstance(doc, dict):
        t = doc.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield doc
        for k in ("@graph", "mainEntity", "itemListElement"):
            if k in doc:
                yield from _jobposting_nodes(doc[k])


def _ld_location(jp):
    locs = jp.get("jobLocation") or []
    if isinstance(locs, dict):
        locs = [locs]
    names = []
    for l in locs:
        addr = (l or {}).get("address") if isinstance(l, dict) else l
        for a in (addr if isinstance(addr, list) else [addr]):
            if isinstance(a, str):
                names.append(a)
            elif isinstance(a, dict):
                parts = []
                for k in ("addressLocality", "addressRegion", "addressCountry"):
                    v = a.get(k)
                    if isinstance(v, dict):
                        v = v.get("name")
                    if v:
                        parts.append(str(v))
                names.append(", ".join(parts))
    if jp.get("jobLocationType") == "TELECOMMUTE":
        names.append("Remote")
    return "; ".join(n for n in dict.fromkeys(names) if n)


def jsonld_page(url):
    t = _http("GET", url, timeout=30).text
    for m in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', t, re.S | re.I):
        try:
            doc = json.loads(_html.unescape(m) if "&quot;" in m else m)
        except ValueError:
            continue
        for jp in _jobposting_nodes(doc):
            org = jp.get("hiringOrganization")
            return {"title": _strip(jp.get("title", "")), "location": _ld_location(jp), "url": url,
                    "apply_url": jp.get("url") or url, "description": _strip(jp.get("description", ""))[:8000],
                    "posted_at": jp.get("datePosted") or None}
    return None


def _sitemap_urls(url, depth=0, limit=60000):
    r = _http("GET", url, timeout=60)
    txt = r.text
    locs = re.findall(r"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", txt, re.S)
    locs = [_html.unescape(x.strip()) for x in locs]
    if "<sitemapindex" in txt and depth < 2:
        out = []
        for sm in locs:
            try:
                out += _sitemap_urls(sm, depth + 1)
            except requests.RequestException:
                continue
            if len(out) > limit:
                break
        return out
    return locs


def jsonld_sitemap(c):
    """c: sitemap (url or list), url_re (regex a job URL must match, default '/job'), slug_loc_re (regex applied to the URL
    itself to pre-filter by city, e.g. 'bengaluru|bangalore'), slug_title_re (optional title keyword prefilter on URL),
    max_pages (default 150)."""
    maps = c["sitemap"] if isinstance(c["sitemap"], list) else [c["sitemap"]]
    urls = []
    for m in maps:
        urls += _sitemap_urls(m)
    url_re = re.compile(c.get("url_re", r"/jobs?/|/job-|/careers?/.+"), re.I)
    urls = [u for u in dict.fromkeys(urls) if url_re.search(u)]
    if c.get("slug_loc_re"):
        sl = re.compile(c["slug_loc_re"], re.I)
        urls = [u for u in urls if sl.search(u)]
    if c.get("slug_title_re"):
        st = re.compile(c["slug_title_re"], re.I)
        urls = [u for u in urls if st.search(u)]
    urls = urls[:min(c.get("max_pages", 150), 300)]

    def one(u):
        try:
            return jsonld_page(u)
        except Exception:  # noqa: BLE001
            return None
    with ThreadPoolExecutor(10) as ex:
        res = [j for j in ex.map(one, urls) if j]
    return _dedupe([j for j in res if _loc_ok(c, j["location"])])


_RAW = {"successfactors": successfactors, "phenom": phenom, "eightfold": eightfold, "talentbrew": talentbrew,
        "avature": avature, "jobvite": jobvite, "bamboohr": bamboohr, "oracle_hcm": oracle_hcm, "amazon": amazon,
        "goldman": goldman, "jsonld_sitemap": jsonld_sitemap}
HARD_TIMEOUT = 150  # seconds of wall clock per company


def _recent(jobs, days):
    if not days:
        return jobs
    cut = datetime.now(timezone.utc).timestamp() - days * 86400
    keep = []
    for j in jobs:
        p = j.get("posted_at")
        try:
            ts = datetime.fromisoformat(p.replace("Z", "+00:00").replace("+0000", "+00:00")).timestamp() if p else None
        except (ValueError, AttributeError):
            ts = None
        if ts is None or ts >= cut:
            keep.append(j)
    return keep


def _soft(fn):
    """Fail-soft wrapper: never raises, never hangs past HARD_TIMEOUT, drops postings older than max_age_days (default 60)."""
    def run(c):
        box = []

        def work():
            try:
                box.append(fn(c))
            except Exception:  # noqa: BLE001
                box.append([])
        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(c.get("timeout", HARD_TIMEOUT))
        jobs = box[0] if box else []
        return _recent(jobs, c.get("max_age_days", 60))
    run.__name__ = fn.__name__
    return run


FETCHERS = {k: _soft(v) for k, v in _RAW.items()}
