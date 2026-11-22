# LinkedIn discovery notes (measured, home IP, India)

## What works: public guest endpoints (no login) -- used by `fetch_linkedin`
- Search: `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=..&location=Bengaluru, Karnataka, India&geoId=105214831&f_TPR=r{seconds}&sortBy=DD&start=N`
  (NOTE: it is `seeMoreJobPostings`, not `seeMoreJobPosts` -- the latter returns 404.)
  Returns 10 cards per page as HTML: job id, title, company, location, `<time datetime>` posted date.
  Pagination of 100/query (10 pages) worked fine; `Senior Data Analyst` returns nearly the same set as `Data Analyst`
  (LinkedIn fuzzy-matches), so sharding by near-duplicate keyword adds little. `f_E` (experience) and `f_WT=2` (remote) filters work.
  Other reports: results end around start=1000.
- Detail: `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}` -> full description, applicant count
  ("Over 200 applicants" is capped at 200), criteria (seniority, employment type, function, industry).
- Easy Apply vs external: the apply button modal is tagged `public_jobs_apply-link-onsite` (= Easy Apply) or
  `public_jobs_apply-link-offsite` (= external ATS). Reliable; about 2% of analyst jobs were Easy Apply (41/1848 sample was on detail-less set, so see counts below).
- The external company URL is NOT exposed to guests (sits behind sign-in). `apply_url` therefore falls back to the LinkedIn job URL;
  the logged-in browser route is the only way to get it (click Apply -> new tab), not implemented/tested.
- Some detail pages return 200 but are an auth-wall stub with no description (e.g. a few ids); parser reports ok=False and they are not cached.

## Rate limits (single IP, one requests.Session, warm-up GET for cookies)
| pacing | result |
|---|---|
| ~1.0 s between requests | 429 at ~80th request (8 pages of one query) |
| ~2 s + jitter | 60/60 ok |
| 2.2 s + 0-1 s jitter (implemented) | 344 search requests, 0 x 429; 429 only appeared in the detail endpoint after ~500 cumulative requests (2 hits, ~30-40 s backoff each cleared it) |
- 429 clears within ~30-60 s; no Retry-After header. Backoff 30/60/90 s with jitter, fresh session after 2nd retry.
- Cost: ~3 s/request effective, so ~20 requests/minute. Budget accordingly: 36 searches x ~10 pages = 360 requests = ~18 min; this is why
  `fetch_linkedin` has `max_seconds` and shorter default hours (72h returns far fewer pages per query).

## Behaviour of fetch_linkedin
- Defaults: `hours=72`, `max_seconds=480` for searches (+40% extra for details), `max_details=150`. Searches Bengaluru (geoId) plus remote-India (`f_WT=2`).
- Resumable: finished (query, location, hours) searches and card data are cached in `data/linkedin_cache.json` for 4 h
  (`resume_hours`), and descriptions are cached by job id for good, so a re-run costs only new work.
- Stops cleanly with partial results on time-budget expiry or 2 abandoned requests (persistent 429); never loops forever.
- Descriptions are fetched only for analyst-ish titles, newest first, capped by `max_details`; others have `description=''`
  (call `fetch_description(url)` on demand). `easy_apply_known=False` is set when the detail wasn't fetched.

## Full-week baseline run (hours=168, 18 queries x Bengaluru + remote-India, 200 details)
1848 unique jobs (951 with Bengaluru location, ~1274 analyst-ish titles), 546 requests, 2 x 429, 1548 s (~26 min).

## Logged-in Playwright route (`login_helper`, `fetch_linkedin_browser`)
Written but NOT live-tested (needs the user to sign in once; profile at `data/browser_profile`, real Chrome, headed).
Expected extras: Easy Apply badge on cards, exact applicant counts, "Actively recruiting", external apply URL.
Risk: logged-in scraping can restrict the account; keep it human-paced and low-volume. Selectors may need adjusting.

## Other routes considered
- python-jobspy LinkedIn = same guest endpoint with fixed ~1 s pacing -> the 429s seen. Google Jobs/Indeed/Naukri via jobspy remain complementary.
- Official LinkedIn Jobs API: partner-only. 3rd-party scraper APIs (Apify etc.) are paid or limited free tiers; not used.
- Community reports: pagination ceiling ~1000, un-throttled scrapers 429 within ~15 requests, 8 s pacing recommended for cloud IPs.

## 72h on-demand run (max_seconds=400, details budget bug since fixed)
641 unique Bengaluru jobs (355 analyst-ish titles, 17 Easy Apply) from 149 requests, 0 x 429, 403 s; the search budget ran out
at the 15th of 36 searches (remote-India not reached) and details were starved -> details now get their own budget.
For full coverage in under ~10 min pass a shorter `queries` list (Analyst/BI/Risk/Regulatory/Finance core ~8) or accept partial results and re-run: it resumes from cache.
