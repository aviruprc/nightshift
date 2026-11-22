"""Fetchers for Oracle Recruiting Cloud (HCM), Taleo and iCIMS career sites. Same job shape as ats_api.py.

Company fields:
  oracle_hcm: host, site (e.g. CX_1 / a company), optional location_id (Bengaluru geo id; auto-resolved if missing)
  taleo: host (e.g. foo.taleo.net), section (careersection id), portal (numeric), optional location_filter
  icims: sub (iCIMS subdomain e.g. ukcareers-msci), optional base_url
NOTE: ats_api.load_companies dedupes on (ats, slug or tenant): every entry here has a unique `tenant`.
"""
import html
import re
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}
QUERIES = ["data analyst", "business analyst", "reporting analyst", "risk analyst", "business intelligence",
           "power bi", "tableau", "sas", "financial analyst", "regulatory", "data quality", "analyst", "analytics"]
BLR = re.compile(r"bengal|bangal", re.I)


def _cutoff(c):
    return (datetime.now(timezone.utc) - timedelta(days=c.get("max_age_days", 14))).date().isoformat()


ORACLE_JOB_RX = re.compile(r"https?://([^/]+)/hcmUI/CandidateExperience/[^?#]*?/?sites/([^/]+)/job/(\d+)", re.I)


def req_live(url: str):
    """True / False / None: does the career site's OWN requisition API list this posting?

    KB-60 correction of KB-53. 'HTTP 200 and the role in og:title' was used to call three closures false, but
    an Oracle Candidate Experience page serves that shell for a REMOVED requisition too: a company 20282085 and a company
    30051451/30051453/30051454 all answered 200 with the role in the title, are absent from `ById` in
    recruitingCEJobRequisitionDetails (200 + empty items) and absent from the site's own list of 2,329 live requisitions -
    while a neighboring live req on the same tenant (a company 30043668) returns exactly one item. So an Oracle closure is
    cross-checked here instead of being trusted or overturned on the page alone."""
    m = ORACLE_JOB_RX.search(url or "")
    if not m:
        return None
    host, site, jid = m.group(1), m.group(2), m.group(3)
    try:
        r = requests.get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                         headers=UA, timeout=20,
                         params={"onlyData": "true", "expand": "all",
                                 "finder": f'ById;Id="{jid}",siteNumber={site}'})
        if r.status_code != 200:
            return None
        return bool(r.json().get("items"))
    except Exception:  # noqa: BLE001
        return None


def strip_html(s):
    s = html.unescape(s or "")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


# ---------------------------------------------------------------- Oracle HCM
def _ora_api(host):
    return f"https://{host}/hcmRestApi/resources/latest"


def _ora_find(c, keyword="", location_id=None, offset=0, limit=25):
    fin = f"findReqs;siteNumber={c['site']},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC"
    if keyword:
        fin += f",keyword='{keyword}'"
    if location_id:
        fin += f",locationId={location_id}"
    r = requests.get(f"{_ora_api(c['host'])}/recruitingCEJobRequisitions", headers=UA, timeout=20,
                     params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": fin})
    r.raise_for_status()
    items = r.json().get("items") or [{}]
    return items[0]


def _facet_blr(it):
    best = None
    for l in it.get("locationsFacet") or []:
        if BLR.search(l["Name"]) and (best is None or len(l["Name"]) > len(best["Name"])):
            best = l
    return best["Id"] if best else None


def oracle_location_id(c):
    """Return (location_id, client_filter). Facets list only the top ~10 places, so resolve India first, then
    look for Bengaluru inside India; if it is not listed fall back to the India filter + client-side filtering."""
    if c.get("location_id"):
        return c["location_id"], False
    it = _ora_find(c, limit=1)
    lid = _facet_blr(it)
    if lid:
        return lid, False
    india = next((l["Id"] for l in it.get("locationsFacet") or [] if l["Name"].strip().lower() == "india"), None)
    if not india:
        it = _ora_find(c, keyword="Bengaluru", limit=1)
        lid = _facet_blr(it)
        return (lid, False) if lid else (None, False)
    it = _ora_find(c, location_id=india, limit=1)
    lid = _facet_blr(it)
    if lid:
        return lid, False
    kar = next((l["Id"] for l in it.get("locationsFacet") or [] if re.match(r"karnataka", l["Name"], re.I)), None)
    if not kar:  # state not in the top facets: ask for it by keyword inside India
        it = _ora_find(c, keyword="Bengaluru", location_id=india, limit=1)
        kar = next((l["Id"] for l in it.get("locationsFacet") or [] if re.match(r"karnataka", l["Name"], re.I)), None)
    return (kar or india, True)


def oracle_hcm(c):
    """Bengaluru postings from the last c.get('max_age_days', 14) days, newest first; [] on any failure."""
    try:
        return _oracle_hcm(c)
    except Exception:  # noqa: BLE001
        return []


def _oracle_hcm(c):
    cutoff = _cutoff(c)
    lid, _client = oracle_location_id(c)
    if not lid:
        return []
    out, seen, off, stop = [], set(), 0, False
    deadline = time.time() + c.get("deadline_s", 90)
    while off < 1000 and not stop and time.time() < deadline:
        it = _ora_find(c, "", lid, off, 50)
        reqs = it.get("requisitionList") or []
        for j in reqs:
            jid = str(j["Id"])
            posted = j.get("PostedDate")
            if posted and posted < cutoff:
                stop = True  # sorted newest first
                break
            locs = [j.get("PrimaryLocation") or ""] + [x.get("Name", "") for x in j.get("secondaryLocations") or []]
            if jid in seen or not any(BLR.search(x) for x in locs):
                continue
            seen.add(jid)
            url = f"https://{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{jid}"
            out.append({"title": j["Title"], "location": "; ".join(x for x in locs if x), "url": url, "apply_url": url,
                        "description": strip_html(j.get("ShortDescriptionStr")), "posted_at": posted,
                        "_ora": {"host": c["host"], "site": c["site"], "id": jid}})
        off += 50
        if not reqs or off >= (it.get("TotalJobsCount") or 0):
            break
    return out


def oracle_description(job):
    o = job.get("_ora") or {}
    try:
        r = requests.get(f"{_ora_api(o['host'])}/recruitingCEJobRequisitionDetails", headers=UA, timeout=20,
                         params={"onlyData": "true", "expand": "all",
                                 "finder": f'ById;Id="{o["id"]}",siteNumber={o["site"]}'})
        d = r.json()["items"][0]
        return strip_html(" ".join(d.get(k) or "" for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                            "ExternalQualificationsStr")))
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- Taleo
def _taleo_body(kw="", page=1, loc_ids=()):
    def f(ids, sel=None):
        return [{"id": i, "selectedValues": list(sel) if (sel and i == "LOCATION") else []} for i in ids]
    return {"multilineEnabled": False, "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
            "fieldData": {"fields": {"KEYWORD": kw, "LOCATION": ""}, "valid": True},
            "filterSelectionParam": {"searchFilterSelections": f(
                ["POSTING_DATE", "LOCATION", "JOB_FIELD", "JOB_TYPE", "JOB_SCHEDULE", "JOB_SHIFT", "JOB_LEVEL", "EMPLOYEE_STATUS"], loc_ids)},
            "advancedSearchFiltersSelectionParam": {"searchFilterSelections": f(
                ["LOCATION", "JOB_FIELD", "JOB_NUMBER", "URGENT_JOB", "EMPLOYEE_STATUS", "JOB_SHIFT"])},
            "pageNo": page}


def _taleo_date(s):
    from datetime import datetime
    try:
        return datetime.strptime(s.strip(), "%b %d, %Y").date().isoformat()
    except Exception:  # noqa: BLE001
        return None


def taleo(c):
    try:
        return _taleo(c)
    except Exception:  # noqa: BLE001
        return []


def _taleo(c):
    """c: host (subdomain, e.g. 'daimler'), section (careersection id), portal (numeric id seen in the page's
    searchjobs XHR). Server-side filters by the Bengaluru LOCATION facet ids."""
    import json
    h, sec = c["host"].split(".")[0], c["section"]
    base = f"https://{h}.taleo.net/careersection"
    s = requests.Session()
    s.headers.update({**UA, "tz": "GMT+05:30", "tzname": "Asia/Calcutta", "X-Requested-With": "XMLHttpRequest",
                      "Accept": "application/json, text/javascript, */*; q=0.01", "Content-Type": "application/json"})
    s.get(f"{base}/{sec}/jobsearch.ftl?lang=en", timeout=20)
    s.headers["Referer"] = f"{base}/{sec}/jobsearch.ftl?lang=en"
    url = f"{base}/rest/jobboard/searchjobs?lang=en&portal={c['portal']}"

    def post(body):
        r = s.post(url, data=json.dumps(body), timeout=20)
        r.raise_for_status()
        return r.json()

    d0 = post(_taleo_body())
    ids = [f["id"] for fr in d0.get("facetResults") or [] if fr["id"] == "LOCATION"
           for f in fr["facetValueResults"] if BLR.search(f["text"])]
    cutoff, out, seen, stop = _cutoff(c), [], set(), False
    queries = [("", ids)] if ids else [("Bengaluru", ()), ("Bangalore", ())]
    for kw, loc_ids in queries:
        for page in range(1, 25):
            if stop:
                break
            d = post(_taleo_body(kw, page, loc_ids))
            reqs = d.get("requisitionList") or []
            for j in reqs:
                col = j.get("column") or []
                loc = re.sub(r'[\[\]"]', "", col[1]) if len(col) > 1 else ""
                if not loc_ids and not BLR.search(loc):
                    continue
                if j["jobId"] in seen:
                    continue
                pd = _taleo_date(col[2]) if len(col) > 2 else None
                if pd and pd < cutoff and not kw:
                    stop = True  # sorted newest first
                    break
                seen.add(j["jobId"])
                u = f"{base}/{sec}/jobdetail.ftl?job={j['contestNo']}&lang=en"
                out.append({"title": col[0] if col else "", "location": loc, "url": u,
                            "apply_url": f"{base}/{sec}/jobapply.ftl?job={j['contestNo']}&lang=en", "description": "",
                            "posted_at": _taleo_date(col[2]) if len(col) > 2 else None, "_taleo": {"url": u}})
            pg = d.get("pagingData") or {}
            if not reqs or page * (pg.get("pageSize") or 25) >= (pg.get("totalCount") or 0):
                break
    return out


def taleo_description(job):
    from urllib.parse import unquote
    try:
        t = requests.get(job["_taleo"]["url"], headers=UA, timeout=20).content.decode("utf-8", "replace")
        v = re.search(r'id="initialHistory" value="([^"]*)"', t).group(1)
        txt = unquote(html.unescape("".join(v.split("!*!")[1:])))
        return strip_html(txt).replace("\\:", ":")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- iCIMS
ICIMS_LOC = re.compile(r"bengal|bangal|\bIN-KA\b|IN-Bengaluru|IN-Bangalore", re.I)


def _icims_ts(title):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", title or "")
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def icims(c):
    try:
        return _icims(c)
    except Exception:  # noqa: BLE001
        return []


def _icims(c):
    """c: sub (iCIMS subdomain) or subs (list); optional loc_regex. Server search has no reliable location facet,
    so list all postings (paged) and keep Bengaluru ones."""
    subs = c.get("subs") or [c["sub"]]
    rx = re.compile(c["loc_regex"], re.I) if c.get("loc_regex") else ICIMS_LOC
    out, seen = [], set()
    for sub in subs:
        base = f"https://{sub}.icims.com"
        for pr in range(0, 40):
            r = requests.get(f"{base}/jobs/search?ss=1&pr={pr}&in_iframe=1", headers=UA, timeout=20)
            if r.status_code != 200:
                break
            blocks = r.text.split('<div class="col-xs-6 header left">')[1:]
            if not blocks:
                break
            for b in blocks:
                m = re.search(r'<a href="(https://[^"]+/jobs/(\d+)/[^"]*)"[^>]*>.*?<h3[^>]*>\s*(.*?)</h3>', b, re.S)
                if not m or m.group(2) in seen:
                    continue
                loc = re.search(r'Job Locations</span>\s*</dt>\s*<dd class="iCIMS_JobHeaderData"><span >\s*(.*?)\s*</span>', b, re.S)
                locs = strip_html(loc.group(1)) if loc else ""
                if not locs:  # layouts without the "Job Locations" label: join all header values
                    locs = " | ".join(strip_html(x) for x in re.findall(r'iCIMS_JobHeaderData"><span >\s*(.*?)\s*</span>', b, re.S))
                if not rx.search(locs):
                    continue
                posted = re.search(r'<span title="([^"]*)"', b)
                pdt = _icims_ts(posted.group(1) if posted else "")
                if pdt and pdt < _cutoff(c):
                    continue
                seen.add(m.group(2))
                url = m.group(1).split("?")[0] + "?in_iframe=1"
                out.append({"title": strip_html(m.group(3)), "location": locs, "url": url, "apply_url": url,
                            "description": "", "posted_at": pdt,
                            "_icims": {"url": url}})
            if "Page %d of" % (pr + 1) in r.text and re.search(r"Page (\d+) of (\d+)", r.text):
                pg = re.search(r"Page (\d+) of (\d+)", r.text)
                if pg.group(1) == pg.group(2):
                    break
    return out


def icims_description(job):
    try:
        import json
        t = requests.get(job["_icims"]["url"], headers=UA, timeout=20).content.decode("utf-8", "replace")
        m = re.search(r'<script type="application/ld\+json">(.*?)</script>', t, re.S)
        d = json.loads(m.group(1))
        job["posted_at"] = job.get("posted_at") or (d.get("datePosted") or "")[:10] or None
        return strip_html(d.get("description", ""))
    except Exception:  # noqa: BLE001
        return ""


def fetch_description(job):
    if job.get("_ora"):
        return oracle_description(job)
    if job.get("_taleo"):
        return taleo_description(job)
    if job.get("_icims"):
        return icims_description(job)
    return ""


FETCHERS = {"oracle_hcm": oracle_hcm, "taleo": taleo, "icims": icims}
