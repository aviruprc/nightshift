# Troubleshooting

Start every investigation with `python hunt.py doctor`, then the newest lines of `data/live_run_A.log` and
`data/guardian.log`. "The process is running" is not proof of progress: check that a log line is recent.

| Symptom | Check / fix |
|---|---|
| `doctor`: `facts.yaml` FAIL | `cv/facts.yaml` is missing or invalid YAML. Copy it from `cv/facts.example.yaml` (or run `python hunt.py setup`). Indent with spaces, never tabs. |
| `doctor`: AI / Claude FAIL | Expected if you use no AI. Otherwise: `python hunt.py llm-test`, then `llm-key --provider <name>`. A provider "resting" after a quota error is retried by itself; `data/provider_cooldown.json` says until when. |
| `doctor`: keyring FAIL | No OS credential store is available. On Windows it is Credential Manager. Elsewhere install a keyring backend, or use `JOBHUNT_KEY_<NAME>` variables for AI keys. |
| `doctor`: Chrome FAIL | Install Google Chrome. Playwright drives the installed Chrome (`channel="chrome"`). |
| Dashboard does not open | `python hunt.py dashboard`, then http://127.0.0.1:8799. Port 8799 must be free. |
| Nothing is applied ("0 attempted") | `python _scratch/funnel.py` shows where the queue empties (age, location, score bar, caps). Typical causes: fit bar too high (`settings.json min_score`), `home_location_regex` too narrow, no AI provider (jobs are never scored), or discovery has not run (`python hunt.py discover`). |
| Lots of `skipped: location` | Your `preferences.home_location_regex` does not match how postings write your city. Add spellings. |
| Many jobs `needs_you` with questions | Answer them once on the dashboard (they are saved to `answers.yaml`), or add the keys listed in CONFIGURING_FACTS.md. |
| Sign-in loops / "manual reset" | `python hunt.py accounts-todo`. Reset that portal's password once in a normal browser, then `python hunt.py set-password --provider <host>`. |
| No verification codes arrive | `python hunt.py gmail-test`. The app password may be revoked, or IMAP may be disabled in Gmail settings. Codes older than 10 minutes are refused on purpose. |
| `unconfirmed` never becomes `applied` | Receipts are matched by `python hunt.py proof` (the guardian runs it every 15 minutes). Some employers send no receipt. The job then stays `unconfirmed`, which is the honest state. Never promote it by hand from a screenshot. |
| Naukri / LinkedIn "signed out" alert | `python hunt.py login` and sign in again in the bot's own browser window. |
| "Chrome profile in use" | Only one process may use `data/browser_profile`. Stop the bot, or close a stray Chrome window opened on that profile. |
| Workday feeds fail on a weekend | Workday's weekly maintenance page. It is reported as transient and retried. |
| A run freezes | The heartbeat watchdog kills a worker after 10-12 minutes without progress, records the job, and the worker restarts. Check the last `acting:` line in the log for the call that hung. |
| Wrong value submitted | Stop immediately (`stop_all.ps1`). Find the source of the value: the log line names it (`answers.yaml <key>`, `skill_years`, a rule). Fix the fact or the rule, add a test, back up the database, then restart. |
| Scripts blocked ("running scripts is disabled") | Run with `powershell -ExecutionPolicy Bypass -File start_all.ps1`, or do the steps by hand (OPERATING.md). |
| Tests fail on a fresh clone | Run them serially without `-n` if RAM is low. Browser tests need Chrome. Tests that need the private `data/failures` corpus skip themselves. |
