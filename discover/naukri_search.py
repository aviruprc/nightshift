"""Naukri search in the signed-in browser (jobspy's Naukri scraper is blocked). Fresh jobs only (jobAge days), experience filter = 4 yrs,
Bengaluru + remote. Discovery only; applying is apply/naukri_apply.py."""
import re
from urllib.parse import quote

from discover import returnships

QUERIES = returnships.extend(
    ["data analyst", "senior data analyst", "business intelligence analyst", "power bi developer", "tableau developer",
     "mis analyst", "reporting analyst", "risk analyst", "credit risk analyst", "regulatory reporting", "data governance analyst",
     "data quality analyst", "financial analyst", "fp&a analyst", "sas developer", "sql developer", "business analyst banking",
     "aml analyst", "etl developer", "analytics consultant",
     #  widened: neighbouring titles she can honestly do, and entry/associate levels
     "business analyst", "data analytics", "mis executive", "mis reporting", "credit analyst", "kyc analyst", "fraud analyst", "financial reporting analyst",
     "financial planning analyst", "revenue analyst", "pricing analyst", "operations analyst", "product analyst", "sql analyst", "dashboard developer",
     "qlik sense developer", "informatica developer", "data migration analyst", "associate analyst", "junior data analyst", "data operations analyst",
     "risk and compliance analyst", "regulatory analyst", "model validation analyst", "analytics associate", "business reporting analyst",
     "data reconciliation analyst", "treasury analyst", "audit analyst data", "excel reporting analyst"])
try:   # facts.yaml preferences.search_queries overrides this list (see docs/CONFIGURING_FACTS.md)
    from core import profile as _profile
    QUERIES = _profile.search_queries(QUERIES, lower=True)
except Exception:  # noqa: BLE001
    pass
EXPERIENCES = (4, 2)   # the portal's experience filter returns roles whose range holds N: 4 = her level, 2 = entry/associate roles she asked to include

CARDS_JS = """() => [...document.querySelectorAll('.srp-jobtuple-wrapper')].map(c => ({
    id: c.getAttribute('data-job-id'), title: (c.querySelector('a.title')||{}).innerText || '', href: (c.querySelector('a.title')||{}).href || '',
    company: (c.querySelector('.comp-name, a.subTitle')||{}).innerText || '', location: (c.querySelector('.locWdth, .loc-wrap, .location')||{}).innerText || '',
    exp: (c.querySelector('.expwdth, .exp-wrap, .experience')||{}).innerText || '', age: (c.querySelector('.job-post-day')||{}).innerText || '',
    snippet: (c.querySelector('.job-desc, .job-description')||{}).innerText || ''}))"""


def _slug(q: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", q.lower()).strip("-")


def fetch_naukri(ctx, queries=None, pages=2, job_age=3, log=print) -> list[dict]:
    out, seen = [], set()
    pg = ctx.new_page()
    try:
        for q in queries or QUERIES:
          for exp in EXPERIENCES:
            for loc in ("bengaluru", "remote"):
                for p in range(1, pages + 1):
                    base = f"https://www.naukri.com/{_slug(q)}-jobs-in-{loc}" + (f"-{p}" if p > 1 else "")
                    url = f"{base}?k={quote(q)}&l={loc}&experience={exp}&jobAge={job_age}"
                    try:
                        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                        pg.wait_for_selector(".srp-jobtuple-wrapper", timeout=12000)
                        pg.wait_for_timeout(1200)
                        cards = pg.evaluate(CARDS_JS)
                    except Exception:  # noqa: BLE001 (no results page / timeout)
                        break
                    new = 0
                    for c in cards:
                        if not c["href"] or c["id"] in seen:
                            continue
                        seen.add(c["id"])
                        new += 1
                        out.append({"source": "board:naukri", "ats": "board", "company": c["company"].strip() or "Unknown",
                                    "title": c["title"].strip(), "location": c["location"].strip() or ("Remote" if loc == "remote" else "Bengaluru"),
                                    "url": c["href"].split("?")[0], "apply_url": c["href"].split("?")[0], "description": "",
                                    "posted_at": None, "_age": c["age"], "_exp": c["exp"], "_remote": loc == "remote"})
                    if len(cards) < 20 or not new:
                        break
            log(f"naukri '{q}': {len(out)} so far")
    finally:
        pg.close()
    return out
