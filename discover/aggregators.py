"""Extra job-discovery sources (no login, no CAPTCHA solving). See NOTES_aggregators.md.

    from discover.aggregators import fetch_aggregators
    jobs, errors = fetch_aggregators # everything that works unauthenticated
    jobs, errors = fetch_aggregators(sources=["shine"]) # subset

Every job: {title, location, url, apply_url, description, posted_at, company, source:'board:<name>', ats:'board'}
(plus a few private '_' keys such as _exp). Sources needing a key (adzuna, jooble, jsearch, serpapi) run only when
the env var is set. Per-source stats from the last run are in LAST_STATS.
"""
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from discover import returnships

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
H = {"User-Agent": UA, "Accept": "application/json,text/html,*/*"}

QUERIES = returnships.extend(
    ["Data Analyst", "Senior Data Analyst", "Business Intelligence Analyst", "Power BI Developer",
     "Tableau Developer", "MIS Reporting Analyst", "Risk Analyst", "Credit Risk Analyst",
     "Regulatory Reporting Analyst", "Data Governance Analyst", "Data Quality Analyst",
     "Financial Analyst", "FP&A Analyst", "SAS Analyst", "Business Analyst Banking", "AML Analytics"])
# Google throttles after ~2-3 queries/run, so only ONE returnship query is added here.
GOOGLE_QUERIES = ["Data Analyst", "Business Intelligence Analyst", "Risk Analyst", "Financial Analyst", "Returnship"]
GOOGLE_MAX_CARDS = 30
INSTAHYRE_SKILLS = ["SQL", "Power BI", "Tableau", "Data Analysis", "Business Intelligence", "SAS"]
APNA_SLUGS = ["data-analyst", "business-analyst", "risk-analyst", "financial-analyst", "mis-executive",
              "power-bi-developer", "reporting-analyst"]

TITLE_RE = re.compile(
    r"analy|\bbi\b|business intelligence|power ?bi|tableau|\bsas\b|\bsql\b|\bmis\b|reporting|\brisk|compliance|"
    r"regulat|\baml\b|\bkyc\b|financ|fp&a|fpna|data (quality|governance|steward|engineer|scientist|management|model)|"
    r"\betl\b|informatica|insight|credit|audit|valuation|treasury", re.I)
BLR_RE = re.compile(r"bengal|bangal", re.I)
REMOTE_RE = re.compile(r"remote|work from home|wfh|anywhere|work-from-home|pan india|india \(|multiple locations", re.I)
# jobs from remote-only feeds: keep when open to India / worldwide
REMOTE_OK_RE = re.compile(r"india|worldwide|anywhere|apac|asia|global|^\s*$|earth|world", re.I)
AGGREGATOR_VIA = {"linkedin", "indeed", "naukri", "glassdoor", "ziprecruiter", "jooble", "jobrapido", "jobrapido.com",
                  "bebee", "shine", "foundit", "workindia", "myinternships.in", "talent.com", "learn4good", "adzuna",
                  "simplyhired", "whatjobs", "careerjet", "monster", "jobsora", "recruit.net", "jobilize", "freshersworld",
                  "instahyre", "apna", "apna jobs", "timesjobs", "cutshort", "hirist", "wellfound", "sociallyhire",
                  "trabajo.org", "jobleads", "expertini", "jobtensor", "ihireaccounting", "jobget"}


# ----------------------------------------------------------------------------------------------- helpers
def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def _job(source, title, company, location, url, desc="", posted=None, apply_url=None, **extra):
    d = {"source": f"board:{source}", "ats": "board", "title": (title or "").strip(),
         "company": (company or "").strip() or "Unknown", "location": (location or "").strip(),
         "url": url or "", "apply_url": apply_url or url or "", "description": (desc or "")[:15000],
         "posted_at": posted}
    d.update(extra)
    return d


def _iso_ms(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _iso_s(s):
    try:
        return datetime.fromtimestamp(int(s), tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def _rel_to_iso(txt):
    """'6 hours ago' / '3 days ago' / '30+ days ago' / 'a month ago' -> ISO date (None if unknown)."""
    t = (txt or "").lower()
    m = re.search(r"(\d+)\+?\s*(minute|hour|day|week|month)", t)
    if not m:
        return datetime.now(timezone.utc).isoformat() if "just" in t or "today" in t else None
    n, u = int(m.group(1)), m.group(2)
    delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n), "day": timedelta(days=n),
             "week": timedelta(weeks=n), "month": timedelta(days=30 * n)}[u]
    return (datetime.now(timezone.utc) - delta).isoformat()


def _get(url, retries=1, **kw):
    for i in range(retries + 1):
        r = requests.get(url, headers=kw.pop("headers", H), timeout=25, **kw)
        if r.status_code == 429 and i < retries:
            time.sleep(15)
            continue
        r.raise_for_status()
        return r
    return r


def _clean_url(u):
    try:
        p = urlparse(u)
        q = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
        return urlunparse(p._replace(query=urlencode(q)))
    except Exception:  # noqa: BLE001
        return u


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _key(j):
    return (_norm(j["title"]), _norm(j["company"]))


def _loc_ok(loc):
    return bool(BLR_RE.search(loc) or REMOTE_RE.search(loc))


# ----------------------------------------------------------------------------------------------- plain-HTTP sources
def shine(queries, log, pages=2):
    out, seen = [], set()
    for q in queries:
        for page in range(1, pages + 1):
            try:
                d = _get("https://www.shine.com/api/v2/search/simple/", params={"q": q, "loc": "bengaluru", "page": page}).json()
            except Exception as e:  # noqa: BLE001
                log(f"  shine {q} p{page}: {str(e)[:60]}")
                break
            for j in d.get("results", []):
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                locs = ", ".join(j.get("jLoc") or [])
                out.append(_job("shine", j.get("jJT"), j.get("jCName"), locs,
                                f"https://www.shine.com/jobs/{j['jSlug']}", j.get("jJD") or j.get("jJDT"),
                                (j.get("jPDate") or "") + ("+05:30" if j.get("jPDate") else "") or None,
                                _exp=j.get("jExp")))
            time.sleep(0.8)
            if page >= d.get("num_pages", 1):
                break
    return out


def hirist(queries, log):
    out, seen = [], set()
    for q in queries:
        for loc in (3, 132):  # Bangalore, Remote
            try:
                d = _get("https://gladiator.hirist.tech/job/search", params={"query": q, "loc": loc}).json()
            except Exception as e:  # noqa: BLE001
                log(f"  hirist {q} loc{loc}: {str(e)[:60]}")
                continue
            for j in d.get("data", []):
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                locs = ", ".join(x.get("name", "") for x in j.get("locations") or [] if isinstance(x, dict))
                if j.get("workFromHome"):
                    locs += ", Remote"
                tags = ", ".join(t.get("name", "") for t in j.get("tags") or [] if isinstance(t, dict))
                desc = f"{j.get('title','')}. Skills: {tags}. Experience: {j.get('min')}-{j.get('max')} yrs."
                out.append(_job("hirist", j.get("title"), (j.get("companyData") or {}).get("companyName"), locs,
                                j.get("jobDetailUrl"), desc, _iso_ms(j.get("createdTimeMs")),
                                _exp=f"{j.get('min')}-{j.get('max')} yrs"))
            time.sleep(0.6)
    return out


def instahyre(queries, log):
    """Public job_search feed. `skills=` filters; location/title params are ignored, so filter client-side.
    Rate limited hard (429 after ~20 rapid calls) -> 3s spacing + one backoff."""
    out, seen = [], set()
    for sk in INSTAHYRE_SKILLS:
        for off in (0, 35):
            try:
                r = requests.get("https://www.instahyre.com/api/v1/job_search", headers=H, timeout=25,
                                 params={"skills": sk, "limit": 35, "offset": off})
                if r.status_code == 429:
                    log("  instahyre 429, backing off 25s")
                    time.sleep(25)
                    r = requests.get("https://www.instahyre.com/api/v1/job_search", headers=H, timeout=25,
                                     params={"skills": sk, "limit": 35, "offset": off})
                r.raise_for_status()
                objs = r.json().get("objects", [])
            except Exception as e:  # noqa: BLE001
                log(f"  instahyre {sk}: {str(e)[:60]}")
                if "429" in str(e):
                    return out
                break
            for o in objs:
                if o["id"] in seen:
                    continue
                seen.add(o["id"])
                loc = (o.get("locations") or "").replace(",", ", ")
                if not _loc_ok(loc):
                    continue
                out.append(_job("instahyre", o.get("title"), (o.get("employer") or {}).get("company_name"), loc,
                                o.get("public_url"), f"{o.get('title','')}. Keywords: {', '.join(o.get('keywords') or [])}",
                                None))
            time.sleep(3.0)
    return out


APNA_EXTERNAL = re.compile(r'\\?"external_?[jJ]ob_?[uU]rl\\?"\s*:\s*\\?"(https?://(?:[^"\\]|\\+u0026|\\+/){8,400})')


def page_jobposting(html_text: str) -> str:
    """The description of a schema.org JobPosting embedded in a job page (Apna, Hirist, Instahyre all publish one), tags stripped. "" if none."""
    best = ""
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html_text or "", re.S):
        try:
            j = json.loads(m.group(1))
        except Exception:  # noqa: BLE001
            continue
        for x in (j if isinstance(j, list) else [j]):
            if isinstance(x, dict) and x.get("@type") == "JobPosting":
                d = strip_html(html.unescape(str(x.get("description") or "")))
                if len(d) > len(best):
                    best = d
    return best


def apna_detail(url: str) -> tuple:
    """(company-site apply URL or "", description) for one Apna job page.

    212 Apna jobs had been found and none applied to, filed as
    "a job board with no applier". Most Apna listings for her roles are not applied to ON Apna at all: the page's button is "Apply on company
    website" and the page data carries `external_job_url` (a company -> its Oracle career site). With that URL the job is an ordinary company-site
    application; no Apna account is involved. A listing without it can only be applied to inside Apna (sign-in needed) and keeps its Apna URL."""
    try:
        t = _get(url).text
    except Exception:  # noqa: BLE001
        return "", ""
    m = APNA_EXTERNAL.search(t)
    ext = html.unescape(re.sub(r"\\+/", "/", re.sub(r"\\+u0026", "&", m.group(1)))) if m else ""
    if ext and re.search(r"(^|\.)apna\.co(/|$)", ext.split("//", 1)[-1]):
        ext = ""
    return ext, page_jobposting(t)


def apna(queries, log, pages=2):
    out, seen = [], set()
    for slug in APNA_SLUGS:
        for page in range(1, pages + 1):
            try:
                t = _get(f"https://apna.co/jobs/{slug}-jobs-in-bengaluru", params={"page": page} if page > 1 else None).text
            except Exception as e:  # noqa: BLE001
                log(f"  apna {slug} p{page}: {str(e)[:60]}")
                break
            cards = re.split(r'<a data-testid="job-card"', t)[1:]
            for c in cards:
                href = re.search(r'href="(/job/\d+)"', c)
                c = re.sub(r"<style.*?</style>", "", c.split("</a>")[0], flags=re.S)
                parts = [html.unescape(p).strip() for p in re.sub(r"<[^>]+>", "|", c).split("|") if p.strip()]
                parts = [p for p in parts if not p.startswith("class=")]
                if not href or len(parts) < 3 or href.group(1) in seen:
                    continue
                seen.add(href.group(1))
                exp = next((p for p in parts if re.match(r"(?i)min\.|fresher|\d+", p) and "year" in p.lower()), "")
                url = "https://apna.co" + href.group(1)
                if not TITLE_RE.search(parts[0]):
                    continue                      # the detail page is fetched only for a title that can be hers
                ext, desc = apna_detail(url)
                time.sleep(0.4)
                out.append(_job("apna", parts[0], parts[1], parts[2], url,
                                desc if len(desc) >= 200 else f"{parts[0]} at {parts[1]}. " + " | ".join(parts[2:]), None,
                                apply_url=ext or None, _exp=exp))
            time.sleep(0.8)
            if len(cards) < 20:
                break
    return out


# ----------------------------------------------------------------------------------------------- remote boards
def _remote_filter(title, loc):
    return bool(TITLE_RE.search(title)) and bool(REMOTE_OK_RE.search(loc or ""))


def remote_boards(queries, log):
    out = []

    def attempt(name, fn):
        try:
            got = fn()
            log(f"  {name}: {len(got)}")
            out.extend(got)
        except Exception as e:  # noqa: BLE001
            log(f"  {name} failed: {str(e)[:80]}")
        time.sleep(0.8)

    def remotive():
        res = []
        for s in ("data analyst", "business analyst", "financial analyst", "risk"):
            for j in _get("https://remotive.com/api/remote-jobs", params={"search": s, "limit": 100}).json().get("jobs", []):
                loc = j.get("candidate_required_location", "")
                if _remote_filter(j["title"], loc):
                    res.append(_job("remotive", j["title"], j["company_name"], f"Remote ({loc})", j["url"],
                                    strip_html(j.get("description")), j.get("publication_date")))
            time.sleep(1)
        return res

    def himalayas():
        res = []
        for s in ("data analyst", "business analyst", "financial analyst", "risk analyst", "business intelligence"):
            for page in (1, 2):
                d = _get("https://himalayas.app/jobs/api/search", params={"q": s, "country": "IN", "page": page}).json()
                for j in d.get("jobs", []):
                    lr = j.get("locationRestrictions") or []
                    if lr and not any(re.search(r"india|worldwide|apac|asia", x, re.I) for x in lr):
                        continue
                    res.append(_job("himalayas", j["title"], j["companyName"], "Remote (" + (", ".join(lr) or "Worldwide") + ")",
                                    j.get("applicationLink") or j.get("guid"), strip_html(j.get("description")),
                                    _iso_s(j.get("pubDate"))))
                time.sleep(1)
        return res

    def remoteok():
        res = []
        for j in _get("https://remoteok.com/api").json()[1:]:
            title = j.get("position") or ""
            if _remote_filter(title, j.get("location", "")):
                res.append(_job("remoteok", title, j.get("company"), f"Remote ({j.get('location') or 'Worldwide'})",
                                j.get("url"), strip_html(j.get("description")), j.get("date"), apply_url=j.get("apply_url")))
        return res

    def jobicy():
        res = []
        for geo in ("anywhere", "apac"):
            for j in _get("https://jobicy.com/api/v2/remote-jobs", params={"count": 50, "tag": "analyst", "geo": geo}).json().get("jobs", []):
                if TITLE_RE.search(j["jobTitle"]):
                    res.append(_job("jobicy", html.unescape(j["jobTitle"]), j["companyName"], f"Remote ({j.get('jobGeo')})",
                                    j["url"], strip_html(j.get("jobDescription")), j.get("pubDate")))
            time.sleep(1)
        return res

    def workingnomads():
        res = []
        for j in _get("https://www.workingnomads.com/api/exposed_jobs/").json():
            if _remote_filter(j["title"], j.get("location", "")):
                res.append(_job("workingnomads", j["title"], j["company_name"], f"Remote ({j.get('location')})", j["url"],
                                strip_html(j.get("description")), j.get("pub_date")))
        return res

    def wwr():
        res = []
        root = ET.fromstring(_get("https://weworkremotely.com/remote-jobs.rss").content)
        for it in root.iter("item"):
            full = it.findtext("title") or ""
            comp, _, title = full.partition(": ")
            region = it.findtext("region") or ""
            if _remote_filter(title, region):
                res.append(_job("weworkremotely", title, comp, f"Remote ({region})", it.findtext("link"),
                                strip_html(it.findtext("description")), None))
        return res

    for n, f in (("remotive", remotive), ("himalayas", himalayas), ("remoteok", remoteok), ("jobicy", jobicy),
                 ("workingnomads", workingnomads), ("weworkremotely", wwr)):
        attempt(n, f)
    return out


# ----------------------------------------------------------------------------------------------- jobspy (glassdoor / bayt)
def glassdoor(queries, log):
    from jobspy import scrape_jobs
    out, seen = [], set()
    # the jobspy route is slow: keep the first analyst terms AND add two returnship terms
    qlist = list(dict.fromkeys(list(queries[:8]) + returnships.RETURNSHIP_QUERIES[:2]))
    for q in qlist:
        try:
            df = scrape_jobs(site_name=["glassdoor"], search_term=q, location="Bengaluru, India", results_wanted=30,
                             hours_old=24 * 14, country_indeed="India", verbose=0)
        except Exception as e:  # noqa: BLE001
            log(f"  glassdoor {q}: {str(e)[:70]}")
            continue
        for r in df.to_dict("records"):
            url = r.get("job_url") or ""
            if url in seen:
                continue
            seen.add(url)
            dp = r.get("date_posted")
            out.append(_job("glassdoor", str(r.get("title") or ""), str(r.get("company") or ""), str(r.get("location") or ""),
                            url, str(r.get("description") or ""), str(dp) if dp is not None and str(dp) != "NaT" else None,
                            apply_url=str(r.get("job_url_direct") or url)))
        time.sleep(1.5)
    return out


# ----------------------------------------------------------------------------------------------- browser sources
_PW_UA = None


def _pw_context(p):
    """Real Chrome, headless, with the HeadlessChrome token removed from the UA (Google/a company block the default)."""
    b = p.chromium.launch(channel="chrome", headless=True, args=["--disable-blink-features=AutomationControlled"])
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{b.version} Safari/537.36"
    return b, b.new_context(locale="en-IN", viewport={"width": 1366, "height": 900}, user_agent=ua)


def foundit_pw(ctx, queries, log, max_detail=0):
    pg = ctx.new_page()
    pg.goto("https://www.foundit.in/", wait_until="domcontentloaded", timeout=45000)
    pg.wait_for_timeout(3000)
    if "denied" in pg.title().lower():
        raise RuntimeError("foundit: Access Denied (Akamai) page")
    fetch_js = ("async(u)=>{const r=await fetch(u,{headers:{accept:'application/json'}});"
                "return [r.status, await r.text()]}")
    out, seen = [], set()
    for q in queries:
        for start in (0, 50):
            url = f"/middleware/jobsearch?sort=1&limit=50&start={start}&query={requests.utils.quote(q)}&locations=Bengaluru"
            st, txt = pg.evaluate(fetch_js, url)
            if st != 200:
                log(f"  foundit {q}: HTTP {st}")
                break
            data = json.loads(txt).get("jobSearchResponse", {}).get("data", [])
            new = 0
            for j in data:
                if not j.get("title") or j["jobId"] in seen:
                    continue
                seen.add(j["jobId"])
                new += 1
                link = "https://www.foundit.in" + (j.get("seoJdUrl") or j.get("jdUrl") or "")
                red = j.get("applyUrl") or j.get("redirectUrl") or link
                out.append(_job("foundit", j["title"], j.get("companyName") or j.get("recruiterName"), j.get("locations"),
                                link, f"{j['title']}. Skills: {j.get('skills','')}. Experience: {j.get('exp','')}",
                                _iso_ms(j.get("createdAt")), apply_url=red, _exp=j.get("exp")))
            if new < 5:
                break
            time.sleep(0.7)
        time.sleep(0.7)
    for j in out[:max_detail]:  # optional full descriptions
        try:
            jid = j["url"].rsplit("-", 1)[-1]
            st, txt = pg.evaluate(fetch_js, f"/middleware/jobdetail/{jid}")
            if st == 200:
                j["description"] = strip_html(json.loads(txt)["jobDetailResponse"].get("description"))[:15000]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
    pg.close()
    return out


_CARDS_JS = r"""() => {
  const out = [];
  for (const t of document.querySelectorAll('div.tNxQIb')) {
    let n = t;
    for (let i = 0; i < 8 && n.parentElement; i++) {
      if (n.parentElement.querySelectorAll('div.tNxQIb').length !== 1) break;
      n = n.parentElement;
    }
    out.push({title: t.innerText.trim(), lines: (n.innerText || '').split(String.fromCharCode(10)).map(s => s.trim()).filter(Boolean)});
  }
  return out;
}"""

_PANE_JS = r"""([title, company]) => {
  const vis = e => e.getClientRects().length > 0 && e.getBoundingClientRect().width > 0;
  const heads = Array.from(document.querySelectorAll('div,span,h2,h3')).filter(
    e => e.children.length === 0 && vis(e) && e.textContent.trim() === 'Job description');
  for (const h of heads) {
    let n = h;
    for (let i = 0; i < 10 && n.parentElement; i++) {
      n = n.parentElement;
      const t = n.innerText || '';
      const aps = Array.from(n.querySelectorAll('a[title^="Apply"]')).filter(vis);
      if (t.indexOf(title) >= 0 && t.indexOf(company) >= 0 && aps.length > 0) {
        const di = t.indexOf('Job description');
        return {ap: aps.map(a => [a.title, a.href]), desc: di >= 0 ? t.slice(di + 15) : t};
      }
    }
  }
  return null;
}"""


def _resolve_goto(href):
    try:
        r = requests.get(href, headers={"User-Agent": UA}, allow_redirects=False, timeout=15)
        return _clean_url(r.headers.get("location") or href)
    except Exception:  # noqa: BLE001
        return href


def google_pw(ctx, queries, log, max_cards=GOOGLE_MAX_CARDS):
    """Google Jobs (udm=8) via real Chrome. Aborts for the run if Google serves the 'unusual traffic' page."""
    pg = ctx.new_page()
    out, seen = [], set()
    for q in queries:
        pg.goto("https://www.google.com/search?hl=en&gl=in&udm=8&q=" + requests.utils.quote(f"{q} jobs in Bengaluru"),
                wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(2500)
        if "unusual traffic" in pg.inner_text("body")[:1500].lower():
            if out:  # keep what we already collected from earlier queries
                log(f"  google: 'unusual traffic' page after {len(out)} jobs (query '{q}'); stopping Google for this run")
                break
            raise RuntimeError("google: 'unusual traffic' page served; stopped (no CAPTCHA solving)")
        for _ in range(4):  # scroll to load more cards
            if len(pg.query_selector_all("div.tNxQIb")) >= max_cards:
                break
            pg.mouse.wheel(0, 4000)
            pg.wait_for_timeout(1200)
        cards = pg.evaluate(_CARDS_JS)[:max_cards]
        for i, c in enumerate(cards):
            lines = c["lines"]
            try:
                ti = lines.index(c["title"])
            except ValueError:
                continue
            company = lines[ti + 1] if len(lines) > ti + 1 else ""
            locvia = lines[ti + 2] if len(lines) > ti + 2 else ""
            posted = next((x for x in lines if " ago" in x), "")
            loc, _, via = locvia.partition("via")
            loc = loc.replace("•", "").replace("•", "").replace("∙", "").strip()
            if (c["title"], company) in seen:
                continue
            pane = None
            try:
                pg.query_selector_all("div.tNxQIb")[i].click()
                for _ in range(10):  # wait until the pane shows THIS card (avoids stale pane)
                    pg.wait_for_timeout(300)
                    pane = pg.evaluate(_PANE_JS, [c["title"], company])
                    if pane:
                        break
            except Exception as e:  # noqa: BLE001
                log(f"  google click {i}: {str(e)[:50]}")
            if not pane:
                continue
            seen.add((c["title"], company))
            ap = pane["ap"]
            nm = lambda t: re.sub(r"^Apply (directly )?on ", "", t).strip()  # noqa: E731
            pick = next((a for a in ap if nm(a[0]).lower() not in AGGREGATOR_VIA), ap[0])
            link = _resolve_goto(pick[1])
            out.append(_job("google", c["title"], company, loc, link, pane["desc"].strip(), _rel_to_iso(posted),
                            _via=nm(pick[0]), _via_listed=via.strip()))
        log(f"  google {q}: {len(out)} jobs so far")
        time.sleep(25)
    pg.close()
    return out


# ----------------------------------------------------------------------------------------------- keyed APIs (env vars)
def adzuna(queries, log):
    aid, key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not (aid and key):
        return None
    out, seen = [], set()
    for q in queries:
        d = _get("https://api.adzuna.com/v1/api/jobs/in/search/1",
                 params={"app_id": aid, "app_key": key, "results_per_page": 50, "what": q, "where": "Bengaluru",
                         "max_days_old": 14, "content-type": "application/json"}).json()
        for j in d.get("results", []):
            if j["id"] in seen:
                continue
            seen.add(j["id"])
            out.append(_job("adzuna", html.unescape(j.get("title", "")), (j.get("company") or {}).get("display_name"),
                            (j.get("location") or {}).get("display_name"), j.get("redirect_url"), strip_html(j.get("description")),
                            j.get("created")))
        time.sleep(0.5)
    return out


def jooble(queries, log):
    key = os.getenv("JOOBLE_KEY")
    if not key:
        return None
    out, seen = [], set()
    for q in queries:
        r = requests.post(f"https://jooble.org/api/{key}", json={"keywords": q, "location": "Bengaluru", "page": 1},
                          headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            if j["link"] in seen:
                continue
            seen.add(j["link"])
            out.append(_job("jooble", j.get("title"), j.get("company"), j.get("location"), j["link"],
                            strip_html(j.get("snippet")), j.get("updated")))
        time.sleep(0.5)
    return out


def jsearch(queries, log):
    key = os.getenv("RAPIDAPI_KEY")
    if not key:
        return None
    out, seen = [], set()
    for q in queries[:6]:
        d = requests.get("https://jsearch.p.rapidapi.com/search", timeout=30,
                         headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"},
                         params={"query": f"{q} in Bengaluru, India", "country": "in", "date_posted": "month", "num_pages": 1}).json()
        for j in d.get("data", []):
            if j["job_id"] in seen:
                continue
            seen.add(j["job_id"])
            loc = ", ".join(x for x in (j.get("job_city"), j.get("job_state")) if x) + (" (Remote)" if j.get("job_is_remote") else "")
            out.append(_job("jsearch", j.get("job_title"), j.get("employer_name"), loc, j.get("job_google_link") or j.get("job_apply_link"),
                            j.get("job_description"), j.get("job_posted_at_datetime_utc"), apply_url=j.get("job_apply_link")))
        time.sleep(1)
    return out


def serpapi(queries, log):
    key = os.getenv("SERPAPI_KEY")
    if not key:
        return None
    out, seen = [], set()
    for q in queries[:6]:
        d = requests.get("https://serpapi.com/search.json", timeout=40,
                         params={"engine": "google_jobs", "q": f"{q} jobs", "location": "Bengaluru, Karnataka, India",
                                 "hl": "en", "gl": "in", "api_key": key}).json()
        for j in d.get("jobs_results", []):
            ao = j.get("apply_options") or [{}]
            link = ao[0].get("link") or j.get("share_link")
            if link in seen:
                continue
            seen.add(link)
            out.append(_job("serpapi", j.get("title"), j.get("company_name"), j.get("location"), link, j.get("description"),
                            _rel_to_iso((j.get("detected_extensions") or {}).get("posted_at"))))
        time.sleep(1)
    return out


# ----------------------------------------------------------------------------------------------- orchestration
LAST_STATS = {}
HTTP_SOURCES = [("shine", shine), ("hirist", hirist), ("instahyre", instahyre), ("apna", apna),
                ("remote_boards", remote_boards), ("glassdoor", glassdoor),
                ("adzuna", adzuna), ("jooble", jooble), ("jsearch", jsearch), ("serpapi", serpapi)]
BROWSER_SOURCES = ["foundit", "google"]


def _postprocess(jobs):
    """Keep Bengaluru / remote jobs; drop empties; dedupe on url and (title, company)."""
    seen_u, seen_k, out = set(), set(), []
    for j in jobs:
        if not j["title"] or not j["url"]:
            continue
        loc = j["location"]
        if loc and not (_loc_ok(loc) or re.search(r"india", loc, re.I)):
            continue
        k = _key(j)
        if j["url"] in seen_u or k in seen_k:
            continue
        seen_u.add(j["url"])
        seen_k.add(k)
        out.append(j)
    return out


def fetch_aggregators(queries=None, log=print, sources=None):
    """Run every unauthenticated aggregator. Returns (jobs, errors). `sources` = subset of names
    (shine, hirist, instahyre, apna, remote_boards, glassdoor, foundit, google, adzuna, jooble, jsearch, serpapi)."""
    queries = queries or QUERIES
    want = (lambda n: sources is None or n in sources)
    jobs, errors = [], []
    LAST_STATS.clear()

    def record(name, got, t0):
        got = got or []
        blr = [j for j in got if BLR_RE.search(j["location"]) and TITLE_RE.search(j["title"])]
        LAST_STATS[name] = {"raw": len(got), "blr_analyst_unique": len({_key(j) for j in blr}),
                            "seconds": round(time.time() - t0, 1)}
        jobs.extend(got)
        log(f"[agg] {name}: {len(got)} raw, {LAST_STATS[name]['blr_analyst_unique']} unique Bengaluru analyst, "
            f"{LAST_STATS[name]['seconds']}s")

    for name, fn in HTTP_SOURCES:
        if not want(name):
            continue
        t0 = time.time()
        try:
            got = fn(queries, log)
            if got is None:
                continue  # key not configured
            record(name, got, t0)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {str(e)[:120]}")
            log(f"[agg] {name} FAILED: {str(e)[:120]}")

    if any(want(n) for n in BROWSER_SOURCES):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b, ctx = _pw_context(p)
                try:
                    for name, fn, qs in (("foundit", foundit_pw, queries), ("google", google_pw, [q for q in GOOGLE_QUERIES])):
                        if not want(name):
                            continue
                        t0 = time.time()
                        try:
                            record(name, fn(ctx, qs, log), t0)
                        except Exception as e:  # noqa: BLE001
                            errors.append(f"{name}: {str(e)[:120]}")
                            log(f"[agg] {name} FAILED: {str(e)[:120]}")
                finally:
                    b.close()
        except Exception as e:  # noqa: BLE001
            errors.append(f"browser sources skipped: {str(e)[:120]}")
            log(f"[agg] browser sources skipped: {str(e)[:120]}")

    return _postprocess(jobs), errors


if __name__ == "__main__":
    import sys
    t = time.time()
    srcs = sys.argv[1:] or None
    res, errs = fetch_aggregators(sources=srcs)
    print(f"\nTOTAL kept {len(res)} jobs in {time.time()-t:.0f}s; errors: {errs}")
    for n, s in LAST_STATS.items():
        print(f"  {n:15s} {s}")
