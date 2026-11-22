"""LinkedIn discovery without login (guest endpoints) + optional logged-in Playwright route.

Public API
    fetch_linkedin(queries=None, hours=168, max_per_query=100, log=print, ...) -> list[job dict]
    fetch_description(url_or_id) -> str (plain-text description, '' on failure)
    fetch_job_detail(url_or_id) -> dict (description, applicants, easy_apply, criteria...)
    fetch_linkedin_browser(...) -> list[job dict] (logged-in UI via real Chrome profile)
    login_helper -> opens headed Chrome so YOU sign in manually

CLI: python -m discover.linkedin [login|browser|guest]

Rate-limit findings (see NOTES_linkedin.md): ~1 req/s trips 429 after ~80 requests; ~2.2 s/req with
jitter sustained 60+ requests with zero 429. 429 clears within about a minute.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from discover import returnships

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "data" / "browser_profile"
CACHE_FILE = ROOT / "data" / "linkedin_cache.json"

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
BENGALURU = "Bengaluru, Karnataka, India"
BENGALURU_GEOID = "105214831"

QUERIES = returnships.extend(
    ["Data Analyst", "Senior Data Analyst", "Business Intelligence Analyst", "Power BI Developer",
     "Tableau Developer", "MIS Reporting Analyst", "Risk Analyst", "Credit Risk Analyst",
     "Regulatory Reporting Analyst", "Data Governance Analyst", "Data Quality Analyst",
     "Financial Analyst", "FP&A Analyst", "SAS Analyst", "Informatica ETL Developer",
     "SQL Developer", "Business Analyst Banking", "AML Analytics"])
try:   # facts.yaml preferences.search_queries overrides this list (see docs/CONFIGURING_FACTS.md)
    from core import profile as _profile
    QUERIES = _profile.search_queries(QUERIES, lower=False)
except Exception:  # noqa: BLE001
    pass

MIN_INTERVAL = 2.2      # seconds between any two requests (+ jitter); 1.0 s tripped 429 at ~80 req
JITTER = (0.0, 1.0)
PAGE = 10               # guest search returns 10 cards per page (start=0,10,20...)
MAX_START = 990         # guest results stop around here anyway
UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
]
ANALYST_RE = re.compile(r"analy|bi\b|power ?bi|tableau|mis\b|sas\b|sql|etl|informatica|reporting|risk|regulat|"
                        r"governance|data quality|fp&a|financ|aml|data engineer|data scien", re.I)


class _Client:
    """Shared, thread-safe, rate-limited requests wrapper with 429 backoff and session rotation."""

    def __init__(self, log=print, deadline=None):
        self.log = log
        self.deadline = deadline
        self.gave_up = 0
        self.lock = threading.Lock()
        self.last = 0.0
        self.n_requests = 0
        self.n429 = 0
        self.consec429 = 0
        self.session = None
        self._new_session()

    def _new_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": random.choice(UAS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9",
            "Referer": "https://www.linkedin.com/jobs/search",
        })
        try:  # warm-up gets bcookie/lidc/JSESSIONID like a real visitor
            s.get("https://www.linkedin.com/jobs/search?keywords=analyst&location=Bengaluru", timeout=20)
        except requests.RequestException:
            pass
        self.session = s

    def _wait(self):
        with self.lock:
            gap = MIN_INTERVAL + random.uniform(*JITTER) - (time.time() - self.last)
            if gap > 0:
                time.sleep(gap)
            self.last = time.time()
            self.n_requests += 1

    def expired(self):
        return bool(self.deadline and time.time() > self.deadline)

    def get(self, url, params=None, max_retries=2):
        """Returns response or None if we gave up (persistent 429 / network failure)."""
        for attempt in range(max_retries + 1):
            if self.expired():
                return None
            self._wait()
            try:
                r = self.session.get(url, params=params, timeout=25)
            except requests.RequestException as e:
                self.log(f"  network error {type(e).__name__}; retry")
                time.sleep(3 + random.random() * 3)
                continue
            if r.status_code == 200:
                self.consec429 = 0
                return r
            if r.status_code in (429, 999, 503):
                self.n429 += 1
                self.consec429 += 1
                wait = float(r.headers.get("Retry-After") or 0) or min(30 * 2 ** attempt, 90)
                wait = wait * random.uniform(0.9, 1.4)
                self.log(f"  HTTP {r.status_code} (#{self.n429}); backing off {wait:.0f}s")
                with self.lock:  # all threads pause together
                    time.sleep(wait)
                    self.last = time.time()
                if attempt >= 1:
                    self._new_session()  # fresh cookies / UA
                continue
            if r.status_code == 404:
                return r
            self.log(f"  HTTP {r.status_code}")
            return None
        self.gave_up += 1
        return None


# ---------------------------------------------------------------- parsing
def _job_id(url_or_id: str) -> str | None:
    m = re.search(r"(\d{8,})(?:[/?#]|$)", str(url_or_id))
    return m.group(1) if m else None


def _parse_cards(html: str):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li"):
        div = li.select_one("div.base-card")
        if not div:
            continue
        urn = div.get("data-entity-urn", "")
        jid = urn.rsplit(":", 1)[-1] if urn else None
        if not jid or not jid.isdigit():
            continue
        t = li.select_one(".base-search-card__title")
        c = li.select_one(".base-search-card__subtitle")
        loc = li.select_one(".job-search-card__location")
        tm = li.select_one("time")
        txt = li.get_text(" ", strip=True).lower()
        out.append({
            "id": jid,
            "title": t.get_text(" ", strip=True) if t else "",
            "company": c.get_text(" ", strip=True) if c else "Unknown",
            "location": loc.get_text(" ", strip=True) if loc else "",
            "posted_at": tm.get("datetime") if tm and tm.get("datetime") else None,
            "actively_hiring": "actively" in txt,
        })
    return out


def _parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    d = {"ok": False}
    desc = soup.select_one(".show-more-less-html__markup")
    if desc:
        for br in desc.find_all("br"):
            br.replace_with("\n")
        for li in desc.find_all("li"):
            li.insert_before("\n- ")
        d["description"] = re.sub(r"\n{3,}", "\n\n", desc.get_text(" ").replace(" \n", "\n")).strip()
        d["ok"] = True
    ap = soup.select_one(".num-applicants__caption, .num-applicants__figure")
    if ap:
        m = re.search(r"(\d+)", ap.get_text())
        if m:
            d["applicants"] = int(m.group(1))
            d["applicants_over"] = "over" in ap.get_text().lower()
    # Apply button: modal outlet name reveals onsite (Easy Apply) vs offsite (external ATS)
    if "public_jobs_apply-link-onsite" in html:
        d["easy_apply"] = True
    elif "public_jobs_apply-link-offsite" in html:
        d["easy_apply"] = False
    crit = {}
    for item in soup.select("li.description__job-criteria-item"):
        h, v = item.select_one("h3"), item.select_one("span")
        if h and v:
            crit[h.get_text(strip=True)] = v.get_text(strip=True)
    d["criteria"] = crit
    ext = re.search(r'(?:externalApplyUrl|applyUrl)"?\s*[:=]\s*"?(https?://[^"\s<]+)', html)
    if ext:
        d["apply_url"] = ext.group(1).replace("&amp;", "&")
    return d


# ---------------------------------------------------------------- cache
def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_cache(c):
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(c), encoding="utf8")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- public: detail
_default_client = None


def _client(log=print):
    global _default_client
    if _default_client is None:
        _default_client = _Client(log)
    return _default_client


def fetch_job_detail(url_or_id, client=None) -> dict:
    jid = _job_id(url_or_id)
    if not jid:
        return {"ok": False}
    r = (client or _client()).get(DETAIL_URL.format(jid))
    if r is None or r.status_code != 200:
        return {"ok": False}
    return _parse_detail(r.text)


def fetch_description(url) -> str:
    """Full plain-text job description from the guest endpoint ('' if unavailable)."""
    return fetch_job_detail(url).get("description", "")


# ---------------------------------------------------------------- public: search
def _search_params(q, hours, start, location, exp, remote):
    p = {"keywords": q, "start": start, "f_TPR": f"r{int(hours * 3600)}", "sortBy": "DD"}
    if location == BENGALURU:
        p["location"] = BENGALURU
        p["geoId"] = BENGALURU_GEOID
    else:
        p["location"] = location
    if exp:
        p["f_E"] = exp          # 1 intern,2 entry,3 associate,4 mid-senior,5 director,6 exec
    if remote:
        p["f_WT"] = "2"         # on-site=1, remote=2, hybrid=3
    return p


def fetch_linkedin(queries=None, hours=72, max_per_query=100, log=print, details=True, max_details=150,
                   remote_india=True, experience_shards=None, strict_location=True, max_seconds=480,
                   resume_hours=4):
    """Guest (no-login) LinkedIn discovery. Returns common-shape job dicts (deduped by job id).

    details: fetch per-job detail page (description, applicants, Easy Apply flag). Cached on disk so repeat runs
             only pay for new jobs. max_details caps requests per run.
    experience_shards: e.g. ["2","3","4"] re-runs each query per experience level to get past the per-query ceiling.
    """
    t0 = time.time()
    cl = _Client(log, deadline=t0 + max_seconds)
    queries = queries or QUERIES
    shards = experience_shards or [None]
    searches = [(q, BENGALURU, False) for q in queries]
    if remote_india:
        searches += [(q, "India", True) for q in queries]
    cache = _load_cache()
    done = cache.setdefault("_done", {})      # search key -> {ts, ids}: lets an interrupted run resume
    cardstore = cache.setdefault("_cards", {})
    now = time.time()
    for k in [k for k, v in done.items() if now - v["ts"] > resume_hours * 3600]:
        del done[k]
    seen, jobs = {}, []
    stop = False
    for q, loc, remote in searches:
        if stop:
            break
        for exp in shards:
            key = f"{q}|{loc}|{exp}|{hours}"
            if key in done:
                for i in done[key]["ids"]:
                    if i in cardstore:
                        seen.setdefault(i, dict(cardstore[i], _remote=remote))
                log(f"[li] {q!r} {loc}: resumed from cache")
                continue
            if cl.expired() or cl.gave_up >= 2:
                log("[li] stopping early (time budget / persistent 429); returning partial results")
                stop = True
                break
            completed = True
            got_q = 0
            empty = 0
            qids = []
            for start in range(0, min(max_per_query, MAX_START + PAGE), PAGE):
                r = cl.get(SEARCH_URL, _search_params(q, hours, start, loc, exp, remote))
                if r is None or r.status_code != 200:
                    log(f"[li] {q!r} {loc} start={start}: giving up this query")
                    completed = False
                    break
                cards = _parse_cards(r.text)
                if not cards:
                    break
                new = 0
                for c in cards:
                    if c["id"] in seen:
                        continue
                    seen[c["id"]] = c
                    cardstore[c["id"]] = {k: v for k, v in c.items() if k != "_remote"}
                    qids.append(c["id"])
                    c["_remote"] = remote
                    new += 1
                    got_q += 1
                empty = empty + 1 if new == 0 else 0
                if empty >= 2 or len(cards) < PAGE - 3:
                    break
            if completed:
                done[key] = {"ts": time.time(), "ids": qids}
                _save_cache(cache)
            log(f"[li] {q!r} {'remote-India' if remote else 'Bengaluru'} exp={exp}: +{got_q} (total {len(seen)}, "
                f"req {cl.n_requests}, 429s {cl.n429})")

    cards = list(seen.values())
    if strict_location:
        cards = [c for c in cards if c["_remote"] or re.search(r"bengaluru|bangalore", c["location"], re.I)
                 or re.search(r"remote", c["location"], re.I)]
    todo = [c for c in cards if c["id"] not in cache
            and (ANALYST_RE.search(c["title"]) or returnships.matches(c["title"]))]
    todo.sort(key=lambda c: (c["posted_at"] or ""), reverse=True)
    cl.deadline = time.time() + max(120, 0.4 * max_seconds)   # details get their own budget
    cl.gave_up = 0
    todo = todo[:max_details] if details else []
    for i, c in enumerate(todo):
        r = cl.get(DETAIL_URL.format(c["id"]))
        if r is None or r.status_code != 200:
            if cl.consec429 >= 3 or cl.gave_up >= 2 or cl.expired():
                log("[li] detail fetching aborted (persistent rate limit)")
                break
            continue
        d = _parse_detail(r.text)
        if d["ok"] or "easy_apply" in d:
            cache[c["id"]] = {k: d[k] for k in ("description", "applicants", "applicants_over", "easy_apply",
                                                 "criteria", "apply_url") if k in d}
        if i % 25 == 24:
            _save_cache(cache)
            log(f"[li] details {i + 1}/{len(todo)}")
    _save_cache(cache)

    for c in cards:
        d = cache.get(c["id"], {})
        url = f"https://www.linkedin.com/jobs/view/{c['id']}"
        job = {"title": c["title"], "location": c["location"], "url": url,
               "apply_url": d.get("apply_url") or url, "description": d.get("description", ""),
               "posted_at": c["posted_at"], "company": c["company"], "source": "board:linkedin",
               "ats": "board", "easy_apply": d.get("easy_apply", False)}
        if "applicants" in d:
            job["applicants"] = d["applicants"]
        if "easy_apply" not in d:
            job["easy_apply_known"] = False
        if d.get("criteria"):
            job["criteria"] = d["criteria"]
        if c["_remote"]:
            job["_remote"] = True
        jobs.append(job)
    log(f"[li] done: {len(jobs)} unique jobs, {cl.n_requests} requests, {cl.n429} x 429, {time.time() - t0:.0f}s")
    fetch_linkedin.stats = {"jobs": len(jobs), "requests": cl.n_requests, "n429": cl.n429,
                            "seconds": round(time.time() - t0)}
    return jobs


# ---------------------------------------------------------------- logged-in browser route
def login_helper():
    """Open real Chrome with the persistent profile so YOU can sign in manually. No credentials are handled here."""
    from playwright.sync_api import sync_playwright
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), channel="chrome", headless=False,
                                                   viewport=None, args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.linkedin.com/login")
        print("Sign in to LinkedIn in the opened window (complete any 2FA/CAPTCHA yourself).")
        print("When you see your feed, come back here and press Enter; the session is saved in", PROFILE_DIR)
        input()
        ctx.close()


def fetch_linkedin_browser(queries=None, hours=168, max_per_query=100, log=print, headless=False,
                           descriptions=True):
    """Scrape the logged-in jobs search UI. Needs login_helper done once. Slower, human-paced, but richer:
    Easy Apply badge, applicant counts, 'Actively recruiting', external apply URL. Selectors are best-effort
    (LinkedIn changes them); anything missing is simply omitted."""
    from playwright.sync_api import sync_playwright
    queries = queries or QUERIES
    secs = int(hours * 3600)
    seen, jobs = set(), []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), channel="chrome", headless=headless,
                                                   viewport={"width": 1400, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for q in queries:
            for start in range(0, max_per_query, 25):
                url = (f"https://www.linkedin.com/jobs/search/?keywords={quote(q)}&location={quote(BENGALURU)}"
                       f"&geoId={BENGALURU_GEOID}&f_TPR=r{secs}&sortBy=DD&start={start}")
                page.goto(url, wait_until="domcontentloaded")
                time.sleep(random.uniform(2.5, 4.5))
                if "/login" in page.url or "authwall" in page.url or "checkpoint" in page.url:
                    log("[li-browser] not logged in / checkpoint: run login_helper() and solve it manually")
                    ctx.close()
                    return jobs
                cards = page.locator("li[data-occludable-job-id]")
                n = cards.count()
                if n == 0:
                    break
                new_here = 0
                for i in range(n):
                    card = cards.nth(i)
                    card.scroll_into_view_if_needed()
                    jid = card.get_attribute("data-occludable-job-id")
                    if not jid or jid in seen:
                        continue
                    seen.add(jid)
                    new_here += 1
                    time.sleep(random.uniform(0.4, 0.9))
                    txt = card.inner_text()
                    lines = [l.strip() for l in txt.split("\n") if l.strip()]
                    job = {"title": lines[0] if lines else "", "company": lines[1] if len(lines) > 1 else "Unknown",
                           "location": lines[2] if len(lines) > 2 else "",
                           "url": f"https://www.linkedin.com/jobs/view/{jid}", "apply_url": "",
                           "description": "", "posted_at": None, "source": "board:linkedin", "ats": "board",
                           "easy_apply": "easy apply" in txt.lower(),
                           "actively_recruiting": "actively" in txt.lower()}
                    if descriptions:
                        try:
                            card.click()
                            time.sleep(random.uniform(1.0, 2.0))
                            dtxt = page.locator("#job-details, .jobs-description__content").first.inner_text(timeout=4000)
                            job["description"] = dtxt
                            top = page.locator(".job-details-jobs-unified-top-card__tertiary-description-container, "
                                               ".jobs-unified-top-card__primary-description").first.inner_text(timeout=2000)
                            m = re.search(r"(\d+)\s+(?:applicants|people clicked apply)", top)
                            if m:
                                job["applicants"] = int(m.group(1))
                            tm = page.locator("time").first.get_attribute("datetime", timeout=1000)
                            job["posted_at"] = tm
                        except Exception:  # noqa: BLE001
                            pass
                    jobs.append(job)
                log(f"[li-browser] {q!r} start={start}: +{new_here} (total {len(jobs)})")
                if new_here == 0:
                    break
            time.sleep(random.uniform(3, 6))
        ctx.close()
    return jobs


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "guest"
    if mode == "login":
        login_helper()
    elif mode == "browser":
        res = fetch_linkedin_browser()
        print(len(res), "jobs")
    else:
        res = fetch_linkedin()
        print(len(res), "jobs")
