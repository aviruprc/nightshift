"""Job boards via python-jobspy: LinkedIn, Indeed, Naukri, Google Jobs. Discovery only; applying is separate."""
from concurrent.futures import ThreadPoolExecutor

from discover import returnships

QUERIES = returnships.extend(
    ["Data Analyst", "Senior Data Analyst", "Business Intelligence Analyst", "Power BI Developer",
     "Tableau Developer", "MIS Reporting Analyst", "Risk Analyst", "Credit Risk Analyst", "Regulatory Reporting Analyst",
     "Data Governance Analyst", "Data Quality Analyst", "Financial Analyst", "FP&A Analyst", "SAS Analyst",
     "Informatica ETL Developer", "SQL Developer", "Business Analyst Banking", "AML Analytics"])
try:   # facts.yaml preferences.search_queries overrides this list (see docs/CONFIGURING_FACTS.md)
    from core import profile as _profile
    QUERIES = _profile.search_queries(QUERIES, lower=False)
except Exception:  # noqa: BLE001
    pass
SITES = ["indeed", "naukri"]  # LinkedIn -> discover/linkedin.py; Google + others -> discover/aggregators.py


def _s(v) -> str:
    """NaN-safe string: jobspy returns float('nan') for missing cells, which str turns into 'nan'."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "nat", "none") else s


def _one(args):
    from jobspy import scrape_jobs
    q, site = args
    try:
        kw = dict(site_name=[site], search_term=q, location="Bengaluru, India", results_wanted=40, hours_old=24 * 14,
                  country_indeed="India", verbose=0)
        if site == "google":
            kw["google_search_term"] = f"{q} jobs in Bengaluru since last 2 weeks"
        df = scrape_jobs(**kw)
    except Exception as e:  # noqa: BLE001
        return [], f"{site}/{q}: {str(e)[:80]}"
    out = []
    for r in df.to_dict("records"):
        url = _s(r.get("job_url"))
        if not url:
            continue
        out.append({"source": f"board:{site}", "ats": "board", "company": _s(r.get("company")) or "Unknown",
                    "title": _s(r.get("title")), "location": _s(r.get("location")), "url": url,
                    "apply_url": _s(r.get("job_url_direct")) or url,
                    "description": _s(r.get("description"))[:15000],
                    "posted_at": _s(r.get("date_posted")) or None,
                    "_remote": bool(r.get("is_remote"))})
    return out, None


def fetch_boards(queries=None, sites=None, workers=4):
    tasks = [(q, s) for q in (queries or QUERIES) for s in (sites or SITES)]
    jobs, errors = [], []
    with ThreadPoolExecutor(workers) as ex:
        for j, e in ex.map(_one, tasks):
            jobs += j
            if e:
                errors.append(e)
    return jobs, errors
