# Architecture

```
discover/  ──>  rank/  ──>  data/jobhunt.db (jobs)  ──>  apply/runner.py  ──>  apply/* (flows, navigator)  ──>  apply/proof.py
 feeds,          filter,                                  queue order,          rule engine, Workday,             receipts, portal
 boards,         ghost,                                   caps, gates           Naukri, Easy Apply, LLM           records
 LinkedIn        AI score                                                       navigator, widgets
                                                                  ^
cv/facts.yaml + data/answers.yaml  (core/profile.py)  ────────────┘   cv/tailor.py: per-job one-page CV (no AI)
track/: guardian (supervision), live.py (dashboard + action API), export, triage, lessons
core/:  db, llm (provider chain), imap (mailbox), cache (answers, recipes), activity/runstate (logs, heartbeat), backup, history
```

## Pipeline

1. **Discovery** (`hunt.py discover`): `discover/ats_api.py` fetches about 285 company feeds listed in
   `discover/companies_*.yaml` (Workday, Oracle, Greenhouse, Lever, Ashby, SmartRecruiters, Eightfold, custom APIs and
   sitemaps). `discover/boards.py` uses `python-jobspy` (Indeed, Naukri), `discover/linkedin.py` reads LinkedIn guest search,
   `discover/aggregators.py` covers other sources (some need free API keys), and `discover/returnships.py` covers returnship
   programmes. Each phase is stored as soon as it is read.
2. **Ingest** (`hunt.ingest`): a cheap title and location filter, then fetching missing descriptions, a full filter
   (`rank/filter.check`: role family, title exclusions, location, stated minimum years), de-duplication by fingerprint,
   already-applied check, and ghost/stale flags (`rank/ghost.py`).
3. **Prepare** (`hunt.py prepare`): AI fit scoring in batches (`rank/score.py`, prompt built from `facts.yaml`), with
   caps for missing mandatory skills and for stated minimum years.
4. **Queue** (`apply/runner.py`): freshness, location re-check, score bars from `settings.json`, per-company and per-board
   caps, duplicate-role guard, and an order learned from outcomes (`apply/learn.py`, a bandit over portals). Returnships come
   first. Easy Apply is a last resort.
5. **Apply**: `cv/tailor.py` builds the CV and `cv/ats_check.py` checks it (one page, no forbidden skill). Then a portal
   flow: `apply/engine.py` (rule-based form scanner), `apply/workday.py`, `apply/naukri_apply.py`, `apply/easy.py`, and
   `apply/agent.py` (the LLM navigator for anything else, with the accuracy gates). `apply/widgets.py` handles pickers,
   dates and dropdowns. `apply/pagejs.py` is the one shared page-snapshot definition.
6. **Answers**: `apply/answers_map.py` (rules over your answers), `apply/llm_answer.py` (AI fallback, truth-bound),
   `apply/batch.py` (one call per page), and `core/cache.py` (normalised question cache, recipes that store routes and never
   answers).
7. **Accounts and mail**: `apply/accounts.py` (credential store, one account per company), `core/imap.py` + `apply/mail.py`
   (codes, links, resets).
8. **Outcome**: `apply/errors.py` classifies every failure into a retry policy. `apply/proof.py` promotes `unconfirmed` to
   `applied` only on job-specific evidence. `apply/replies.py` reads rejections and interviews.
9. **Supervision**: `track/guardian.py` → `supervise.ps1` → `worker.ps1` → `hunt.py run --live`. Stop markers are
   `data/STOP` and `data/GUARDIAN_STOP`.

## Data

- `data/jobhunt.db` (SQLite): `jobs`, `events`, `answers_cache`, `recipes`, `portal_memory`, replies. Backed up before
  discovery and by `hunt.py backup` (`core/backup.py`).
- `data/history/`, `data/applications.jsonl`: durable append-only records that survive log rotation.
- `data/failures/`: saved page evidence for every failed or parked attempt.
- `data/browser_profile/`: the persistent Chrome profile (your sign-in sessions). Treat it like a password.

## AI

`core/llm.py` keeps a provider chain (`data/llm_providers.json`, in order) with per-provider cool-downs, a fallback model
per provider, a background prober that wakes rested providers, and optional Claude CLI use. `JOBHUNT_NO_LLM=1` turns all AI
off (the test suite does this).

## Configuration surface

`core/profile.py` is the one reader of `facts.yaml` and `answers.yaml` (paths overridable with `JOBHUNT_FACTS` /
`JOBHUNT_ANSWERS`), with helpers such as `first_name()`, `phone_digits()`, `preferences()`, `search_queries()` and
`base_cv_pdf()`. Several modules read the profile **at import time** (location regex, name filters, languages), so restart
the processes after editing your facts.
