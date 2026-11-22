# Operating Nightshift

## The moving parts

| Process | Started by | Job |
|---|---|---|
| **Dashboard** `track/live.py` | `start_all.ps1`, `python hunt.py dashboard`, or the guardian | Local web page at http://127.0.0.1:8799 (the full dashboard), `/simple` (a four-block summary), `/ops` (machine health, failures, AI providers). Buttons call a local action API that accepts requests only from 127.0.0.1. |
| **Guardian** `track/guardian.py` | `start_all.ps1` | Runs forever. Every 3 minutes it: keeps the supervisor and workers alive; kills a worker whose heartbeat is older than 12 minutes; restarts the dashboard; runs `hunt.py proof` every 15 minutes while jobs await receipts; reads employer replies every 30 minutes; runs a full `hunt.py discover` daily and whenever the queue is low (at most once an hour); runs startup outreach daily if you set it up; and alerts when the Naukri or LinkedIn session is signed out. Log: `data/guardian.log` (STATUS / ALERT / HEAL lines), `data/health.json`. |
| **Supervisor** `supervise.ps1` | the guardian (or the dashboard's Start button) | Starts N workers (default 1) and holds `data/supervisor.lock`, so two supervisors never run at once. |
| **Worker** `worker.ps1` | the supervisor | Loops `python hunt.py run --live ...` until `data/STOP` exists. Worker 2+ uses its own copy of the browser profile and skips Naukri. Logs: `data/live_run_<A..>.log`. |

**One applier per browser profile.** Chrome locks a profile to one process. On a typical 8 GB machine, run one worker.

## Start and stop

```powershell
powershell -ExecutionPolicy Bypass -File start_all.ps1   # dashboard + guardian; the guardian starts LIVE applying within a minute
powershell -ExecutionPolicy Bypass -File stop_all.ps1    # writes data/STOP + data/GUARDIAN_STOP, then ends the processes
```

The scripts work from any folder (they use their own location). `start_all.ps1` starts only what is not already running and
removes old stop markers. If your system blocks scripts, run the same steps by hand: create `data\STOP` and
`data\GUARDIAN_STOP` to stop; delete both and `data\supervisor.lock`, then `python track\guardian.py`, to start.

A stop is graceful: the worker finishes the job it is on, then exits. Pages already open are left alone.

## One-off commands (`python hunt.py <command>`)

| Command | What it does |
|---|---|
| `setup` | Guided first-run configuration (see SETUP.md). |
| `doctor` | Checks facts, answers, database, AI, credential store, Chrome. |
| `discover [--no-boards]` | Fetches career sites (+ boards, LinkedIn, aggregators), filters, stores candidates, then runs `prepare`. |
| `naukri` | Signed-in Naukri search only. |
| `prepare [--limit N]` / `score [--limit N]` | AI-scores candidates that have a description. |
| `pool` | What is waiting, by family and source, best fit first. |
| `run [--target N] [--max-attempts N] [--only TEXT] [--show]` | **Dry run** (fills, never submits). |
| `run --live ...` | Real submissions. `--only naukri.com` limits it to URLs containing the text. |
| `status` | Count of jobs per status. |
| `learn` | Per-portal success rates and the "fix next" list (what blocks the most jobs). |
| `proof` | Matches receipt emails to sent applications (promotes `unconfirmed` to `applied`). |
| `replies [--days N]` | Reads employer replies (rejections, interviews, assessments, offers) into the tracker. |
| `accounts-todo` | Portals waiting for one manual password reset by you, with the jobs behind each. |
| `set-password --provider <host>` | Stores a portal password you typed (hidden) and marks the account as working. |
| `gmail-setup` / `gmail-test` | Stores and tests the mailbox app password. |
| `llm-key --provider <name>` / `llm-test [--only <name>]` | Stores and tests AI keys. |
| `login` | Opens the bot's Chrome profile so you can sign in by hand. |
| `assist` | The older CAPTCHA helper: opens parked CAPTCHA jobs in a visible window for you. |
| `report` | Writes `data/tracker.xlsx` and a static dashboard. |
| `history` | The last durable status changes (`data/applications.jsonl`). |
| `backup` / `restore [--file F]` | Database snapshot and restore (a safety copy is taken first). |
| `import-old` | Imports jobs you already applied to elsewhere (see SETUP.md). |

Two helpers live in `_scratch/`: `python _scratch/funnel.py` (queue health: why jobs are or are not in the queue) and
`python _scratch/one.py <job id> --dry --show` (one job through the full pipeline in a visible window, for diagnosing a failure; **without `--dry` it submits for real**).

## Daily routine (about 10 minutes)

1. Open the dashboard. Read the one-line "what the bot is doing / why it is idle".
2. **Needs you**: answer parked questions once (they are saved to `answers.yaml` and reused everywhere), do any listed
   one-time password resets (`python hunt.py accounts-todo`), and finish CAPTCHA jobs if you have time (below).
3. Spot-check two or three of yesterday's `applied` jobs: open the proof screenshot and the receipt. If anything submitted is
   wrong, stop the bot (`stop_all.ps1`), fix the fact or the rule, and add a test.
4. Glance at `python hunt.py learn`: the portal at the top of "fix next" blocks the most jobs.

## Job statuses

| Status | Meaning |
|---|---|
| `candidate` | Found, passed the filters, waiting (scored or not). |
| `skipped` | Filtered out, with the reason (location, ghost/stale, duplicate role, do-not-apply company, ...). |
| `dry_run_ok` | A dry run filled the form successfully (nothing was sent). |
| `unconfirmed` | Submitted, but no proof yet (a thank-you page is not proof). Waiting for the receipt email or the portal record. |
| `applied` | Submitted **and** proven for this job: a receipt that names it, or the portal's own record. |
| `unverified` | A send claim that could not be checked against the evidence (kept apart from `applied`). |
| `needs_you` | Parked for you, with the exact reason (see below). |
| `failed` | An attempt failed. Its `fail_class` decides what happens next (below). |
| `closed` | The posting is closed, confirmed on the page itself or through the site's own API. A missing Apply button alone never closes a job. |
| `deferred_captcha`, `deferred_board`, `deferred_easy` | Held back on purpose: a CAPTCHA site, a board with no applier yet, or LinkedIn Easy Apply (used only as a last resort). |

**Failure classes** (`apply/errors.py`): `transient` (timeouts, maintenance pages: retried after 5/20/60 minutes),
`lockout` (rate limits: retried after 35 minutes), `permanent` (closed, already applied: no retry), `captcha` (handed to
you), `personal` (a fact only you know: answer it once), and `logic` (a bug in the bot: shown under "What is failing" and
retried after a fix).

### What `needs_you` means

The bot stopped because continuing would mean guessing or deciding something only you may decide:

- A **question with no recorded answer** (skill-specific years, a self-rating, a national ID, a contract or shift
  preference). Answer it on the dashboard and it is saved and reused.
- A **CAPTCHA** or "verify you are human" check.
- A **sign-in that needs one manual password reset** (the saved password was rejected and the site's one automatic reset was
  already used, or the account is locked).
- A **form value that disagrees with your facts** and could not be corrected safely (for example a pre-filled "No" on work
  authorisation). Final Submit is refused while a wrong answer is visible.

`needs_you` is never a verdict that the job is closed or impossible. Answer and retry.

## CAPTCHA hand-off

CAPTCHAs are never solved or bypassed. When you are at the computer:

```powershell
python -m apply.handoff <job id> [<job id> ...]
```

Each job runs through the normal pipeline (tailored CV, every accuracy gate, proof) in a **visible** Chrome window on a copy
of the signed-in profile, so the unattended worker keeps running. At the CAPTCHA the bot stops, brings the window forward
and waits (up to 30 minutes) while you solve it. Then it continues and submits. Some sites reject CAPTCHA answers from an
automated browser; apply to those by hand. Job ids are shown on the dashboard.

## Health checks

- `python hunt.py doctor`. Then check that the last line of `data/live_run_A.log` and the `STATUS` lines in
  `data/guardian.log` are recent. A process being alive is not the same as it making progress.
- The heartbeat (`data/run_state.json`) is refreshed only by the applier's main thread, so a frozen browser call is noticed
  within 10 minutes and the worker is relaunched.
