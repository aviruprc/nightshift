"""Generic career-site reader for employers with no public jobs API (Empower, a company, ...).

Most career sites publish a sitemap of their job pages, and each job page carries schema.org `JobPosting` JSON-LD (title, description, location,
date posted, valid-through). That is enough to read ANY such site without a per-company adapter:

    - {name: Empower, ats: sitemap, host: jobs.empower.com, job_re: "/job/", tier: fintech}

Parameters (all optional except host):
    host site to read (robots.txt is checked for Sitemap: lines, then /sitemap.xml, /sitemap_index.xml)
    sitemaps explicit sitemap URLs when robots.txt names none
    job_re regex a job-page URL must match (default: a /job/ or /jobs/ path)
    city_re the city words that mark a Bengaluru job in a URL (default bengaluru|bangalore|blr)
    slug_re for URLs that name no city: only pages whose slug matches this are fetched (default: analyst-type words)
    max_pages cap on pages fetched per run (default 250; city URLs first)

A page is kept only when its own JSON-LD says Bengaluru / Karnataka / India / remote-India (the URL is only a hint to save fetches).
Public pages only; no login, no form, nothing is applied to. Fetched pages are cached for CACHE_DAYS so a repeat run costs only new URLs.
"""
from __future__ import annotations

import gzip
import html as htmllib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "sitemap_cache.json"
CACHE_DAYS = 3
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36", "Accept": "*/*"}
LOC_TAG = re.compile(r"<loc>\s*([^<\s][^<]*?)\s*</loc>", re.I)
JSONLD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
DEFAULT_JOB_RE = r"/jobs?/|/job-|/positions?/|/vacanc|/openings?/|/careers/[^/]+/job"
DEFAULT_CITY_RE = r"bengaluru|bangalore|blr"
DEFAULT_SLUG_RE = (r"analyst|associate|specialist|reporting|finance|financial|data|risk|audit|compliance|account|controller|operations|business|"
                   r"insight|intelligence|treasury|credit|fund|kyc|aml|returnship|return-to-work|career-restart")
INDIA = re.compile(r"bengaluru|bangalore|karnataka|india|remote", re.I)


def _get(url: str, **kw):
    kw.setdefault("timeout", 25)
    return requests.get(url, headers=UA, **kw)


def _text(resp) -> str:
    if resp.content[:2] == b"\x1f\x8b":
        return gzip.decompress(resp.content).decode("utf-8", "replace")
    return resp.text


def sitemap_locs(host: str, explicit: list[str] | None = None, max_files: int = 40) -> list[str]:
    """Every <loc> reachable from the site's sitemap(s); sitemap indexes are followed (bounded)."""
    queue = list(explicit or [])
    if not queue:
        try:
            robots = _get(f"https://{host}/robots.txt").text
            queue = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
        except requests.RequestException:
            queue = []
        if not queue:
            queue = [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml", f"https://{host}/sitemap-index.xml"]
    seen, out = set(), []
    while queue and len(seen) < max_files:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        try:
            r = _get(u)
            if r.status_code != 200:
                continue
            body = _text(r)
            locs = LOC_TAG.findall(body)
        except (requests.RequestException, OSError, ValueError):
            continue
        is_index = "<sitemapindex" in body[:2000].lower()   # an index lists sitemaps, whatever their file names
        for loc in locs:
            loc = loc.replace("&amp;", "&")
            if is_index or (re.search(r"\.xml(\.gz)?(\?|$)", loc) and "sitemap" in loc.lower()):
                queue.append(loc)
            else:
                out.append(loc)
        if out and not queue:
            break
    return list(dict.fromkeys(out))


def _strip(html: str) -> str:
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    s = re.sub(r"<(br|/p|/li|/div|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", s)).strip()


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def job_postings(html: str) -> list[dict]:
    out = []
    for block in JSONLD.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for node in _walk(data):
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                out.append(node)
    return out


def _addr(loc) -> str:
    parts = []
    for item in (loc if isinstance(loc, list) else [loc]):
        if isinstance(item, str):
            parts.append(item)
            continue
        a = (item or {}).get("address", item) if isinstance(item, dict) else {}
        if isinstance(a, str):
            parts.append(a)
        elif isinstance(a, dict):
            country = a.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            parts.append(", ".join(str(x) for x in (a.get("addressLocality"), a.get("addressRegion"), country) if x))
    remote = ""
    return ("; ".join(p for p in parts if p) + remote).strip()


def _expired(valid_through) -> bool:
    if not valid_through:
        return False
    try:
        d = datetime.fromisoformat(str(valid_through).replace("Z", "+00:00")[:25])
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d < datetime.now(timezone.utc)
    except ValueError:
        return False


def parse_job(url: str, html: str, city_re: str = DEFAULT_CITY_RE) -> dict | None:
    """One job page -> job dict, or None when the page is not a live Bengaluru / India / remote-India job."""
    posts = job_postings(html)
    if not posts:
        return parse_page(url, html)
    p = posts[0]
    if _expired(p.get("validThrough")):
        return None
    location = _addr(p.get("jobLocation"))
    if str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE" and not location:
        location = "Remote"
    if not location:
        m = re.search(city_re, url, re.I)
        location = m.group(0).title() if m else ""
    if not INDIA.search(location):
        return None
    org = p.get("hiringOrganization")
    return {"title": _strip(str(p.get("title") or "")), "location": location, "url": url, "apply_url": url, "description": _strip(str(p.get("description") or "")),
            "posted_at": p.get("datePosted"), "org": (org.get("name") if isinstance(org, dict) else org) or ""}


TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def parse_page(url: str, html: str) -> dict | None:
    """Fallback for job pages with no JSON-LD (a company): the <title> reads 'Role - City, Region, Country | Company Careers' and the body
    holds the description. Kept only when that title names Bengaluru / Karnataka / India; the description is the page text from 'Job Description' on."""
    m = TITLE_TAG.search(html or "")
    if not m:
        return parse_meta_page(url, html)
    head = htmllib.unescape(re.sub(r"\s+", " ", m.group(1))).split("|")[0].strip()
    if " - " not in head:
        return parse_meta_page(url, html)
    title, _, location = head.rpartition(" - ")
    # "Manager - Structures (Hotel Projects) - Bangalore, Bengaluru, Karnataka, India": the location is the last segment only when it names a place
    if not INDIA.search(location):
        return parse_meta_page(url, html)
    body = _strip(html)
    i = body.lower().find("job description")
    desc = body[i:i + 8000] if i >= 0 else body[:8000]
    if len(desc) < 200:
        return None
    return {"title": title.strip(), "location": location.strip(), "url": url, "apply_url": url, "description": desc, "posted_at": None, "org": ""}


META_DESC = re.compile(r"""<meta[^>]+name=["'](?:job-)?description["'][^>]+content=["'](.*?)["']\s*/?>""", re.I | re.S)
H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
PIPE_LOC = re.compile(r"\|\s*([A-Z][A-Za-z .'-]+(?:,\s*[A-Za-z .'-]+){0,2})\s*(?:\||\n)")


def parse_meta_page(url: str, html: str) -> dict | None:
    """Second fallback (a company's Next.js pages): no JSON-LD and no <title>; the job text sits in a job-description meta tag, the role in an <h1> or the first
    line of the page, and the place in a 'Title | City, Country | id' strip. A page whose place cannot be read from the page itself is dropped."""
    d = META_DESC.search(html or "")
    if not d:
        return None
    desc = _strip(htmllib.unescape(htmllib.unescape(d.group(1))))
    h = H1.search(html or "")
    body = _strip(html)
    title = _strip(h.group(1)) if h else ""
    if not title:
        title = next((ln.strip() for ln in body.splitlines() if 3 < len(ln.strip()) < 120 and "|" not in ln and not re.search(r"contact|apply|share|menu|search", ln, re.I)), "")
    loc = PIPE_LOC.search(body)
    location = loc.group(1).strip() if loc else ""
    if not title or not INDIA.search(location) or len(desc) < 200:
        return None
    return {"title": title, "location": location, "url": url, "apply_url": url, "description": desc[:8000], "posted_at": None, "org": ""}


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def pick_urls(locs: list[str], c: dict) -> list[str]:
    """Which job-page URLs are worth fetching: city-named ones first, then slugs that look like her kind of role; capped."""
    job_re = re.compile(c.get("job_re") or DEFAULT_JOB_RE, re.I)
    city_re = re.compile(c.get("city_re") or DEFAULT_CITY_RE, re.I)
    slug_re = re.compile(c.get("slug_re") or DEFAULT_SLUG_RE, re.I)
    jobs = [u for u in locs if job_re.search(u) and not re.search(r"/(?:category|categories|search|location|locations|results)/?$", u, re.I)]
    city = [u for u in jobs if city_re.search(u)]
    rest = [u for u in jobs if not city_re.search(u) and slug_re.search(u.rsplit("/", 2)[-2] if u.endswith("/") else u.rsplit("/", 1)[-1])]
    # a site whose URLs never name a city: the slug filter above is all we have, so cap the volume
    return (city + rest)[: int(c.get("max_pages") or 250)]


def fetch(c: dict) -> list[dict]:
    """ats_api FETCHERS entry: read a career site from its sitemap + JSON-LD job pages."""
    host = c["host"]
    locs = sitemap_locs(host, c.get("sitemaps"))
    urls = pick_urls(locs, c)
    cache = _load_cache()
    now = time.time()
    todo = [u for u in urls if u not in cache or now - cache[u].get("at", 0) > CACHE_DAYS * 86400]

    blocked = {"n": 0}

    def one(u):
        """(url, job | None, definitive). A rate limit / error is NOT an answer: it must not be cached as 'not a job' (Cushman answered 429 under load)."""
        if blocked["n"] >= 5:
            return u, None, False
        try:
            r = _get(u)
            if r.status_code in (429, 403, 503):
                blocked["n"] += 1
                return u, None, False
            if r.status_code != 200:
                return u, None, r.status_code in (404, 410)
            return u, parse_job(u, r.text, c.get("city_re") or DEFAULT_CITY_RE), True
        except requests.RequestException:
            return u, None, False

    with ThreadPoolExecutor(int(c.get("threads") or 4)) as ex:
        for u, job, definitive in ex.map(one, todo):
            if definitive:
                cache[u] = {"at": now, "job": job}
    if todo:
        _save_cache(cache)
    out = []
    for u in urls:
        job = (cache.get(u) or {}).get("job")
        if job:
            out.append({k: v for k, v in job.items() if k != "org"})
    return out
