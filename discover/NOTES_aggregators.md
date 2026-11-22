# Aggregator sources: what works (tested /03, Bengaluru, from this PC)

Entry point: `discover/aggregators.py` -> `fetch_aggregators(queries=None, log=print, sources=None) -> (jobs, errors)`.
CLI: `python discover/aggregators.py [source ...]`. Per-source stats of the last run are in `aggregators.LAST_STATS`.

Full run (16 default queries, all working sources): **266 s, 1945 jobs kept** (Bengaluru/remote-India, deduped on URL and
title+company across sources), 0 errors. "Unique Bengaluru analyst" = Bengaluru location AND analyst/BI/risk/finance/data
title regex AND unique on (title, company) within that source. Raw counts include duplicates across queries; counts are for
one run and will drift daily.

| Source | Works? | Raw | Unique BLR analyst | Time | Method |
|---|---|---|---|---|---|
| Shine | yes | 461 | 410 | 33 s | public JSON `shine.com/api/v2/search/simple/?q=&loc=bengaluru&page=` (20/page, 2 pages/query). Full description, date, experience band (`_exp`). Loose relevance: many sub-4-yr / small-agency jobs, filter by `_exp`. |
| Foundit (Monster India) | yes, browser only | 346 | 310 | 27 s | Plain requests get a company "Access Denied". Real Chrome (headless OK once the `HeadlessChrome` UA token is removed) loads foundit.in, then `fetch('/middleware/jobsearch?...')` runs inside the page. Gives title, company, skills, exp, ms timestamp, apply URL (often a LinkedIn/company link). Description = skills line only (set `max_detail` in `foundit_pw` to also call `/middleware/jobdetail/<id>` for full text). |
| Hirist | yes | 419 | 181 | 30 s | Undocumented JSON the site uses: `gladiator.hirist.tech/job/search?query=&loc=3` (3=Bangalore, 132=Remote), 50/page. No description, so skills tags + experience are the description. Tech-leaning (BI/data/ETL roles, little pure finance/risk). |
| Instahyre | yes, partial | 201 | 119 | 43 s | Public `instahyre.com/api/v1/job_search?skills=<skill>`: only `skills` and `job_functions` filter (title/location are ignored), so it pages a handful of skills and filters Bangalore/WFH client-side. **Hard rate limit** (HTTP 429 after ~20 rapid calls), so 3 s spacing + stop on repeated 429. No description/date. Tech-skewed. |
| Apna | yes | 236 | 156 | 15 s | Server-rendered HTML job cards (`apna.co/jobs/<slug>-jobs-in-bengaluru?page=N`, 25/card page). Title, company, location, min experience only; no date. Many are low-pay or telecaller-style jobs. Slugs that 404 are skipped. |
| Glassdoor (jobspy) | yes | 191 | 165 | 29 s | `scrape_jobs(site_name=["glassdoor"], country_indeed="India")`. The glassdoor.co.in web page is 403 for plain requests, but jobspy's endpoint works. Not in `boards.py` today, so this is new. |
| Google Jobs | yes, throttled | 28 | 25 | 58 s | Real Chrome, headless, `google.com/search?udm=8&q=<query> jobs in Bengaluru` (jobspy's Google scraper stays dead: "no job data returned" with both `google_search_term` and `search_term` variants). Cards are scraped, each card is clicked and the detail pane is matched to the card's title/company (guards against a stale pane); apply links are resolved from `/goto?url=` via the 302 `Location`, preferring a non-aggregator ("Apply directly on Company") link over LinkedIn/Indeed copies. Posted date is converted from "3 days ago". **Google serves the "unusual traffic" page after about 1-2 queries within a few minutes**; the code stops at that point and keeps what it has (no CAPTCHA solving, no retries). Expect ~25-50 jobs/run, run it once or twice per day. Many results are LinkedIn/Naukri copies, but a good share are employer-career-page links (e.g. careers.hitachi.com) that other feeds miss. Needs the headless UA fix (default `HeadlessChrome` UA gets the block page immediately). Headed Chrome also works. |
| Remote boards: Himalayas | yes | ~200 | n/a (remote) | | `himalayas.app/jobs/api/search?q=&country=IN`. India-eligible remote jobs. Most valuable remote source. |
| Remote boards: Remotive, RemoteOK, Jobicy, WorkingNomads, WeWorkRemotely | work, tiny yield | 0 / 3 / 7 / 0 / 8 | n/a | | After keeping only analyst-type titles that are open to India/APAC/worldwide: near zero. WWR only serves `remote-jobs.rss` (category RSS feeds 301-loop). All 6 together ~29 s. Arbeitnow (Europe) is fetchable but irrelevant, so it is not included. |
| jobspy Bayt | works but useless | | | | Returns Gulf postings regardless of location; not wired in. |
| Adzuna India | needs key | | | | coded (`adzuna`), runs only if env vars are set. Untested (no key). |
| Jooble | needs key | | | | coded (`jooble`). Web search is Cloudflare-blocked; the API is the route. Untested. |
| JSearch (RapidAPI) | needs key | | | | coded (`jsearch`). It is Google-for-Jobs data behind an API. Untested. |
| SerpAPI Google Jobs | needs key | | | | coded (`serpapi`). Untested. |

## Sources that do NOT work without login/CAPTCHA (not built)
- **Wellfound**: Cloudflare Turnstile on first request.
- **Jooble website**: Cloudflare "Just a moment" (use the API key instead).
- **Adzuna website**: 403 (use the API key instead).
- **Cutshort**: job listings need an account (`/backend-api/webpage/jobs/...` returns 400, auth redirect).
- **Careerjet website**: JS stub page, no listings without running a challenge. Their partner API (below) is the legit route.
- **TimesJobs**: `www.timesjobs.com` fails TLS certificate verification from here; not worth bypassing.
- **Naukri raw API**: needs an app-id header; already covered through jobspy (`naukri`), not duplicated here.
- **jobspy `google`**: dead in 1.4.1 for every query form tried. The `"google"` entry in `discover/boards.py` `SITES` is dead weight (it only logs an error per query) and could be removed; I did not touch that file.
- Skipped on purpose: LinkedIn (other agent), Internshala, ZipRecruiter (no India), HN "Who is hiring" (irrelevant for Bengaluru analyst roles), PSU/government bank careers (most are paper/portal-form notices, better served by the existing `companies_banks.yaml` ATS list).

## Optional API keys (free tiers exist; check each page for the current limits)
Set as environment variables (or in your shell profile); the matching source then switches on automatically.

| Env var(s) | Service | Sign up | Why |
|---|---|---|---|
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Adzuna API (`/jobs/in/`) | https://developer.adzuna.com/signup | Real Indian job index with dates and descriptions. |
| `JOOBLE_KEY` | Jooble API | https://jooble.org/api/about | Large India aggregator that the website blocks. |
| `RAPIDAPI_KEY` | JSearch on RapidAPI | https://rapidapi.com (search "JSearch") | Google Jobs data via a clean API; avoids the Google throttle. |
| `SERPAPI_KEY` | SerpAPI `google_jobs` engine | https://serpapi.com/users/sign_up | Same Google Jobs data, official, low monthly quota on the free plan. |
| (not coded) Careerjet partner API | Careerjet | https://www.careerjet.com/partners/ (API docs under Partners) | Needs a partner key and the caller's IP/user-agent; wire it in if a key is obtained. |

## Operational notes
- Politeness: 0.6-3 s sleeps between calls per source, 25 s between Google queries, Instahyre 3 s with backoff. One browser launch is shared by Foundit and Google.
- Browser sources need real Chrome installed (`channel="chrome"`, same as `hunt.py`). If Playwright or Chrome fails, those two are skipped and the error is returned, other sources still run.
- Output keys beyond the common shape: `_exp` (experience band text, Shine/Hirist/Apna/Foundit), `_via` / `_via_listed` (Google: where the chosen apply link points / which site Google listed first).
- Output is already location-filtered (Bengaluru / remote / India-wide) and deduped, but NOT title-filtered (except the generic feeds Instahyre, Apna's slugs, and the remote boards), so the existing ranker should still screen out unrelated roles and the 0-3 yr entries from Shine/Apna.
- Everything here is an unofficial endpoint (Shine, Hirist, Instahyre, Foundit middleware, Google markup via CSS classes `tNxQIb` etc.); any of them can change without notice. The Google selectors are the most fragile.
