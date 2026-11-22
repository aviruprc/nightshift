"""Fetchers for Indian employers with in-house career portals: companies.

Same contract as ats_api.py: fetcher(company_dict) -> list[dict] with
title, location, url, apply_url, description (may be ""), posted_at (ISO or None).

Endpoints verified live on (plain `requests`, no browser, no API key):
  a company intapgateway.infosysapps.com careersci/search/intapjbsrch/getCareerSearchJobs (one JSON array)
  a company larsentoubrocareers.peoplestrong.com /api/cp/rest/altone/cp/jobs/v1 (POST body {}, searchString query)
  a company careers.techmahindra.com CurrentOpportunity.aspx (ASP.NET async postback; HTML cards)
  a company www-api.ibm.com/search/api/v2 (Elasticsearch-style POST)

Only jobs whose location looks like Bengaluru / remote-India are returned. Failures are logged and return []
so one broken company can never break discovery.
"""
import html
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from discover import returnships

log = logging.getLogger(__name__)

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 30

BLR_RE = re.compile(r"bengaluru|bangalore|bangal", re.I)
REMOTE_RE = re.compile(r"\bremote\b|work from home|\bwfh\b", re.I)
INDIA_RE = re.compile(r"\bindia\b|,\s*in\b|\bind\b", re.I)

INFOSYS_API = ("https://intapgateway.infosysapps.com/careersci/search/intapjbsrch/"
               "getCareerSearchJobs?sourceId=1,21&searchText=ALL")
LNT_API = "https://larsentoubrocareers.peoplestrong.com/api/cp/rest/altone/cp/jobs/v1"
IBM_API = "https://www-api.ibm.com/search/api/v2"
TM_PAGE = "https://careers.techmahindra.com/CurrentOpportunity.aspx"

# Queries are deliberately broad (title OR description on some portals); rank/filter.py does the
# title-family decision after discovery, so a feed should not silently drop a family it cannot see.
# These portals match titles with OR / substring semantics, so the returnship terms are short single
# strings (a long query would flood the feed with every analyst job). Same deadlines/delays as before.
LNT_QUERIES = ["analyst", "data", "risk", "finance", "business intelligence", "reporting",
               "governance", "sql", "etl", "sas", "tableau", "power bi", "Bangalore", "Karnataka",
               *returnships.RETURNSHIP_SEARCH_STRINGS]
IBM_QUERIES = ["analyst", "data", "risk", "finance", "business intelligence", "governance",
               "reporting", "compliance", "sql", "etl", "sas", "tableau", "power bi", "mis",
               *returnships.RETURNSHIP_SEARCH_STRINGS]
TM_QUERIES = ["analyst", "data", "risk", "finance", "business intelligence", "reporting",
              "governance", "sql", "etl", "sas", "tableau", "power bi", "Bangalore",
              *returnships.RETURNSHIP_SEARCH_STRINGS]

IBM_FIELDS = ["title", "url", "description", "language", "field_keyword_05", "field_keyword_08",
              "field_keyword_17", "field_keyword_18", "field_keyword_19"]


def _strip(s) -> str:
    s = html.unescape(str(s or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def _target_loc(loc: str, india_known: bool = False) -> bool:
    """Bengaluru, or remote/work-from-home when the country is known to be India."""
    loc = loc or ""
    if BLR_RE.search(loc):
        return True
    return bool(REMOTE_RE.search(loc) and (india_known or INDIA_RE.search(loc)))


def _dedupe(jobs):
    seen, out = set(), []
    for j in jobs:
        u = j.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(j)
    return out


def _recent(jobs, max_age_days=60):
    cut = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    out = []
    for j in jobs:
        p = j.get("posted_at")
        if not p:
            out.append(j)
            continue
        try:
            dt = datetime.fromisoformat(str(p).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            out.append(j)
            continue
        if dt >= cut:
            out.append(j)
    return out


def _session():
    s = requests.Session()
    s.headers.update(UA)
    return s


def _get(s, url, **kw):
    r = s.get(url, timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


def _post(s, url, **kw):
    r = s.post(url, timeout=kw.pop("timeout", TIMEOUT), **kw)
    r.raise_for_status()
    return r


def _safe(name, fn, c):
    """Never raise: log the real failure and return an empty feed so discovery keeps going."""
    try:
        return _recent(fn(c), c.get("max_age_days", 60))
    except Exception as e:  # noqa: BLE001
        log.warning("%s feed failed: %s: %s", name, type(e).__name__, str(e)[:200])
        return []


# ------------------------------------------------------------------------------------------- a company
def _infosys(c):
    rows = _get(_session(), INFOSYS_API, timeout=90).json()
    today = datetime.now(timezone.utc).date().isoformat()
    out = []
    for j in rows:
        loc = _strip(j.get("location"))
        if not _target_loc(loc):
            continue
        expiry = (j.get("expiryDate") or "")[:10]
        if expiry and expiry < today:
            continue  # the public API can keep an expired requisition for a day
        ref = j.get("referenceCode")
        if not ref:
            continue
        url = f"https://career.infosys.com/jobdesc?jobReferenceCode={quote(str(ref))}&sourceId={j.get('sourceId') or 1}"
        desc = _strip(" ".join(str(j.get(k) or "") for k in (
            "postingDescription", "rolesResponsibilities", "technicalRequirement",
            "additionalResponsibility", "educationalRequirement")))
        out.append({"title": _strip(j.get("postingTitle")), "location": loc, "url": url,
                    "description": desc, "posted_at": j.get("createdOn") or None})
    return _dedupe(out)


# --------------------------------------------------------------------------------------- a company (PeopleStrong)
def _lnt(c):
    s = _session()
    deadline = time.time() + c.get("deadline_s", 120)
    out = []
    for q in (c.get("queries") or LNT_QUERIES):
        off = 0
        for _ in range(int(c.get("max_pages", 3))):  # 99/page; "data" is the only query near 2 pages
            if time.time() > deadline:
                log.warning("L&T feed hit its %ss deadline; returning %d jobs", c.get("deadline_s", 120), len(out))
                return out
            d = _post(s, LNT_API, params={"offset": off, "limit": 99, "searchString": q},
                      json={}, timeout=40).json()
            rows = d.get("response") or []
            total = int(d.get("totalRecords") or 0)
            for j in rows:
                loc = _strip(j.get("locationHierarchyComplete") or j.get("locationHierarchy"))
                if not _target_loc(loc, india_known=bool(INDIA_RE.search(loc))):
                    continue
                url = j.get("jobDetailUrl")
                if not url:
                    continue
                sk = j.get("skills") if isinstance(j.get("skills"), dict) else {}
                skills = [str(x) for x in (sk.get("mustTohave") or []) + (sk.get("goodtohave") or []) if x]
                parts = []
                if skills:
                    parts.append("Skills: " + ", ".join(skills))
                for label, key in (("Experience", "expRange"), ("Role", "roleType"),
                                   ("Program", "programType"), ("Unit", "organizationUnit"),
                                   ("Designation", "designation")):
                    v = _strip(j.get(key))
                    if v:
                        parts.append(f"{label}: {v}")
                out.append({"title": _strip(j.get("jobTitle")), "location": loc, "url": url,
                            "description": _strip("; ".join(parts)),
                            "posted_at": j.get("jobPostedDate") or None})
            off += 99
            if off >= total or not rows:
                break
        time.sleep(float(c.get("delay_s", 0)))  # politeness between search terms
    return _dedupe(out)


# ------------------------------------------------------------------------------- a company (ASP.NET)
TM_SM = "ctl00$ContentPlaceHolder1$ToolkitScriptManager1"
TM_BTN = "ctl00$ContentPlaceHolder1$btnFreeSearch"
TM_TEXT = "ctl00$ContentPlaceHolder1$txtAdvanceSearch"


def _tm_form(text):
    d = {}
    for m in re.finditer(r"<input[^>]*>", text):
        tag = m.group(0)
        name = re.search(r'name="([^"]+)"', tag)
        typ = re.search(r'type="([^"]+)"', tag)
        val = re.search(r'value="([^"]*)"', tag)
        if name and typ and typ.group(1) == "hidden":
            d[name.group(1)] = html.unescape(val.group(1)) if val else ""
    return d


def _tm_cards(text):
    out = []
    for seg in text.split("HdnJobCode")[1:]:
        code = re.search(r'value="(\d+)"', seg)
        title = re.search(r'font-size: 13px;">\s*(.*?)\s*</div>', seg, re.S)
        info = re.search(r"<b>Skill Set </b>:\s*(.*?)\s*<br\s*/?>\s*<b>Experience</b>\s*:\s*(.*?)\s*"
                         r"<br\s*/?>\s*<b>Location</b>\s*:\s*(.*?)\s*</p>", seg, re.S)
        if not code or not title or not info:
            continue
        skill, exp, loc = (_strip(x) for x in info.groups())
        if not _target_loc(loc, india_known=True):
            continue
        out.append({"title": _strip(title.group(1)), "location": loc,
                    "url": f"{TM_PAGE}?JobCode={code.group(1)}",
                    "description": _strip(f"Skill Set: {skill}; Experience: {exp}"),
                    "posted_at": None})
    return out


def _techmahindra(c):
    s = _session()
    home = _get(s, TM_PAGE, timeout=45).text
    base = _tm_form(home)
    if not base.get("__VIEWSTATE"):
        raise ValueError("CurrentOpportunity.aspx returned no ASP.NET form state")
    deadline = time.time() + c.get("deadline_s", 120)
    out = []
    for q in (c.get("queries") or TM_QUERIES):
        if time.time() > deadline:
            log.warning("Tech Mahindra feed hit its %ss deadline; returning %d jobs",
                        c.get("deadline_s", 120), len(out))
            return out
        form = dict(base)
        form[TM_TEXT] = q
        form[TM_BTN] = "Search"
        form["__EVENTTARGET"] = ""
        form["__EVENTARGUMENT"] = ""
        form["__ASYNCPOST"] = "true"
        form[TM_SM] = f"ctl00$ContentPlaceHolder1$up|{TM_BTN}"
        r = _post(s, TM_PAGE, data=form, timeout=45)
        out += _tm_cards(r.text)
        time.sleep(float(c.get("delay_s", 0)))  # politeness between search terms
    return _dedupe(out)


# ------------------------------------------------------------------------------------------------ a company
def _ibm(c):
    s = _session()
    deadline = time.time() + c.get("deadline_s", 120)
    out = []
    for q in (c.get("queries") or IBM_QUERIES):
        off = 0
        for _ in range(int(c.get("max_pages", 4))):  # size is capped at 100 by the API
            if time.time() > deadline:
                log.warning("IBM feed hit its %ss deadline; returning %d jobs", c.get("deadline_s", 120), len(out))
                return out
            body = {"appId": "careers", "scopes": ["careers2"],
                    "query": {"bool": {"filter": [{"term": {"field_keyword_05": "India"}}],
                                       "must": [{"multi_match": {"query": q, "fields": ["title"]}}]}},
                    "from": off, "size": 100, "_source": IBM_FIELDS}
            d = _post(s, IBM_API, json=body, timeout=45).json()
            hits = ((d.get("hits") or {}).get("hits") or [])
            total = int(((d.get("hits") or {}).get("total") or {}).get("value") or 0)
            for h in hits:
                src = h.get("_source") or {}
                loc = _strip(src.get("field_keyword_19"))
                if not _target_loc(loc, india_known=True):
                    continue
                url = src.get("url")
                if not url:
                    continue
                out.append({"title": _strip(src.get("title")), "location": loc, "url": url,
                            "description": _strip(src.get("description")), "posted_at": None})
            off += 100
            if off >= total or not hits:
                break
        time.sleep(float(c.get("delay_s", 0)))  # politeness between search terms
    return _dedupe(out)


def infosys(c):
    return _safe("Infosys", _infosys, c)


def lnt(c):
    return _safe("Larsen & Toubro", _lnt, c)


def techmahindra(c):
    return _safe("Tech Mahindra", _techmahindra, c)


def ibm(c):
    return _safe("IBM", _ibm, c)


FETCHERS = {"infosys": infosys, "lnt": lnt, "techmahindra": techmahindra, "ibm": ibm}
