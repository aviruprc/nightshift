"""Fetchers for employers with custom careers backends (no standard ATS adapter).

Each fetcher takes the company dict and returns list[dict] (title, location, url, description, posted_at,
apply_url, company). Only Bengaluru-relevant (or, where the API cannot filter, India) listings are returned.
Verified live; see companies_custom.yaml.
"""
import json
import re
import time
from datetime import datetime, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "application/json"}
BLR = re.compile(r"bengaluru|bangalore|blr", re.I)
ANALYST_Q = ["data analyst", "business analyst", "analytics", "reporting", "risk analyst", "business intelligence",
             "data scientist", "data engineer"]


def _strip(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", s.replace("&amp;", "&").replace("&nbsp;", " ")).strip()


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def _req(method, url, retries=4, **kw):
    """requests with backoff on 429/5xx."""
    for i in range(retries):
        r = requests.request(method, url, headers={**UA, **kw.pop("headers", {})}, timeout=30, **kw)
        if r.status_code in (429, 500, 502, 503):
            time.sleep(6 * (i + 1))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


# ---------------------------------------------------------------- Amazon
def amazon(c):
    out, seen = [], set()
    for q in ["", "data analyst", "business analyst", "business intelligence", "data scientist", "data engineer"]:
        off = 0
        while off < 600:
            d = _req("GET", "https://www.amazon.jobs/en/search.json",
                     params={"base_query": q, "loc_query": "Bengaluru", "country": "IND", "result_limit": 100,
                             "offset": off, "sort": "recent"}).json()
            jobs = d.get("jobs", [])
            for j in jobs:
                if j["id_icims"] in seen:
                    continue
                seen.add(j["id_icims"])
                url = "https://www.amazon.jobs" + j["job_path"]
                out.append({"title": j["title"], "location": j.get("normalized_location") or j.get("location", ""),
                            "url": url, "apply_url": j.get("url_next_step") or url, "company": c["name"],
                            "description": _strip(" ".join([j.get("description", ""), j.get("basic_qualifications", ""),
                                                            j.get("preferred_qualifications", "")])),
                            "posted_at": _parse_amz(j.get("posted_date"))})
            off += 100
            if len(jobs) < 100 or off >= d.get("hits", 0):
                break
    return out


def _parse_amz(s):
    try:
        return datetime.strptime(s, "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- a company (GraphQL gateway)
_GS_Q = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: $searchQueryInput) "
         "{ totalCount items { roleId corporateTitle jobTitle jobFunction division locations { primary state country city } "
         "status externalSource { sourceId } } } }")


def goldman(c):
    # The server-side location filter returns 0 for every spelling; pull all roles and filter by city client-side.
    out, page, got = [], 0, 0
    while True:
        v = {"searchQueryInput": {"page": {"pageSize": 100, "pageNumber": page},
                                  "sort": {"sortStrategy": "RELEVANCE", "sortOrder": "DESC"}, "filters": [],
                                  "experiences": ["EARLY_CAREER", "PROFESSIONAL"], "searchTerm": ""}}
        d = _req("POST", "https://api-higher.gs.com/gateway/api/v1/graphql",
                 json={"operationName": "GetRoles", "variables": v, "query": _GS_Q},
                 headers={"Origin": "https://higher.gs.com", "Referer": "https://higher.gs.com/"}).json()["data"]["roleSearch"]
        items = d["items"]
        got += len(items)
        for j in items:
            locs = j.get("locations") or []
            if not any(BLR.search(l.get("city") or "") for l in locs):
                continue
            sid = (j.get("externalSource") or {}).get("sourceId") or j["roleId"].split("_")[0]
            url = f"https://higher.gs.com/roles/{sid}"
            out.append({"title": j["jobTitle"].strip(), "location": ", ".join(f"{l['city']}, {l['country']}" for l in locs if l.get("city")),
                        "url": url, "apply_url": url, "company": c["name"],
                        "description": f"{j.get('division') or ''} | {j.get('jobFunction') or ''} | {j.get('corporateTitle') or ''}",
                        "posted_at": None})
        page += 1
        if not items or got >= d["totalCount"]:
            break
    return out


# ---------------------------------------------------------------- Google (server-rendered AF_initDataCallback)
def google(c):
    out, seen = [], set()
    dec = json.JSONDecoder()
    page = 1
    while page <= 15:
        t = _req("GET", "https://www.google.com/about/careers/applications/jobs/results/",
                 params={"location": "Bengaluru, Karnataka, India", "page": page}, headers={"Accept": "text/html"}).text
        i = t.find("AF_initDataCallback({key: 'ds:1'")
        if i < 0:
            break
        s = t[i:i + 4_000_000]
        data = dec.raw_decode(s[s.find("data:") + 5:])[0]
        jobs = data[0] or []
        for j in jobs:
            if j[0] in seen:
                continue
            seen.add(j[0])
            locs = ", ".join(x[0] for x in (j[9] or []))
            slug = re.sub(r"[^a-z0-9]+", "-", j[1].lower()).strip("-")
            url = f"https://www.google.com/about/careers/applications/jobs/results/{j[0]}-{slug}"
            desc = _strip(" ".join((j[k][1] if j[k] and len(j[k]) > 1 else "") or "" for k in (10, 3, 4)))
            out.append({"title": j[1], "location": locs, "url": url, "apply_url": url, "company": c["name"],
                        "description": desc, "posted_at": _iso(j[12][0]) if len(j) > 12 and j[12] else None})
        total = data[2] if len(data) > 2 else 0
        if not jobs or len(seen) >= total:
            break
        page += 1
    return out


# ---------------------------------------------------------------- Microsoft (apply.careers.microsoft.com PCSX, Eightfold-based)
def microsoft(c):
    out, seen = [], set()
    for q in ["data analyst", "business analyst", "data scientist", "business intelligence"]:
        start = 0
        while start < 100:
            try:
                d = _req("GET", "https://apply.careers.microsoft.com/api/pcsx/search", retries=5,
                         params={"domain": "microsoft.com", "query": q, "location": "Bengaluru", "start": start}).json()
            except requests.RequestException:
                if out:  # aggressive rate limiter: keep partial results rather than failing
                    return out
                raise
            pos = (d.get("data") or {}).get("positions", [])
            for p in pos:
                if p["id"] in seen:
                    continue
                seen.add(p["id"])
                locs = p.get("standardizedLocations") or p.get("locations") or []
                if not any(BLR.search(l) for l in locs):
                    continue
                url = "https://apply.careers.microsoft.com" + p["positionUrl"]
                out.append({"title": p["name"], "location": "; ".join(locs), "url": url, "apply_url": url,
                            "company": c["name"], "description": p.get("department") or "", "posted_at": _iso(p.get("postedTs"))})
            if len(pos) < 10:
                break
            start += len(pos)
            time.sleep(3)
        time.sleep(3)
    return out


# ---------------------------------------------------------------- Oracle HCM Candidate Experience (shared helper)
def _oracle(c, host, site, base_url, keywords=("",)):
    out, seen = [], set()
    for kw in keywords:
        off = 0
        while True:
            kwp = f',keyword="{kw}"' if kw else ""
            fin = f"findReqs;siteNumber={site},limit=50,offset={off}{kwp},sortBy=POSTING_DATES_DESC"
            d = _req("GET", f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
                     params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": fin}).json()["items"][0]
            reqs = d.get("requisitionList") or []
            for j in reqs:
                if j["Id"] in seen:
                    continue
                seen.add(j["Id"])
                locs = [j.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in j.get("secondaryLocations") or []]
                if not (any(BLR.search(l) for l in locs) or j.get("PrimaryLocationCountry") == "IN"):
                    continue
                url = f"{base_url}/job/{j['Id']}"
                out.append({"title": j["Title"], "location": "; ".join(l for l in locs if l), "url": url, "apply_url": url,
                            "company": c["name"], "description": _strip(j.get("ShortDescriptionStr") or ""),
                            "posted_at": j.get("PostedDate")})
            off += 50
            if not reqs or off >= d.get("TotalJobsCount", 0):
                break
    return out


def amex(c):
    return _oracle(c, "egug.fa.us2.oraclecloud.com", "CX_1", "https://careers.americanexpress.com/en/sites/CX_1")


def kotak(c):
    return _oracle(c, "hcbt.fa.em2.oraclecloud.com", "CX_4001",
                   "https://hcbt.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_4001")


def dell(c):
    return _oracle(c, "enterpriseplatform.dell.com", "CX_1001", "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers")


# ---------------------------------------------------------------- a company (RippleHire)
def axis(c):
    out, seen = [], set()
    tok = "WIXhCuz0XRZ7H0GZCwjJ"
    for q in ANALYST_Q + ["manager", "MIS", "credit", "technology", "audit", "compliance", "product"]:
        page = 0
        while page < 20:
            p = {"page": page, "search": q, "token": tok, "source": "CAREERSITE", "pagesize": 50}
            d = _req("POST", "https://axisbank.ripplehire.com/candidate/candidatejobsearch",
                     data={"careerSiteUrlParams": json.dumps(p), "lang": "en"}).json()
            jobs = d.get("jobVoList") or []
            for j in jobs:
                if j["jobSeq"] in seen:
                    continue
                seen.add(j["jobSeq"])
                loc = j.get("locations") or j.get("jobLocation") or ""
                if not BLR.search(loc):
                    continue
                url = f"https://axisbank.ripplehire.com/candidate/?token={tok}&source=CAREERSITE#detail/job/{j['jobSeq']}"
                out.append({"title": j["jobTitle"], "location": loc, "url": url, "apply_url": url, "company": c["name"],
                            "description": _strip(j.get("jobDesc") or "") or (j.get("jobReqExp") or ""),
                            "posted_at": None})
            if len(jobs) < 50:
                break
            page += 1
    return out


CUSTOM_FETCHERS = {"amazon": amazon, "goldman": goldman, "google": google, "microsoft": microsoft,
                   "amex": amex, "kotak": kotak, "dell": dell, "axis": axis}
