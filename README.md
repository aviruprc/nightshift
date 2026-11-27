# Nightshift

**Nightshift: the job-application autopilot that works while you sleep, and never lies about you.**

Nightshift finds job postings (company career sites, ATS feeds, job boards, LinkedIn guest pages), scores each one against
*your* recorded background, tailors a one-page CV from *your* facts, and fills in the application forms in a real Chrome
browser. It submits only what you have recorded as true. Anything it cannot answer truthfully, it parks for you.

It grew out of real, daily use on Windows with Indian job portals (Naukri) and
global ATS platforms (Workday, Oracle HCM, SuccessFactors, Greenhouse, Lever, Ashby, SmartRecruiters and more). The
safety rules in it come from real mistakes that were caught and fixed. See [docs/ENGINEERING_LESSONS.md](docs/ENGINEERING_LESSONS.md).

> **Read this before you start: applications cannot be taken back.** A submitted application cannot be edited, withdrawn
> or re-sent. Nightshift is designed to submit fewer, correct applications rather than many doubtful ones. It still needs
> you to record true facts and to check its work. You are responsible for everything submitted in your name.

## What it does

- **Discovers** jobs from about 285 company career-site feeds (Workday, Oracle, Greenhouse, Lever, Ashby, SmartRecruiters,
  Eightfold and others), job boards (Indeed and Naukri via `python-jobspy`, plus a signed-in Naukri search), LinkedIn guest
  search, and returnship programmes.
- **Filters and scores** each job: your locations, role families, seniority and stated minimum experience, then an AI fit score
  that uses only your `facts.yaml`.
- **Tailors your CV** with no AI and no new facts: your bullets and skills are reordered to match the posting, then rendered to a
  one-page PDF.
- **Applies** in a persistent Chrome profile: it signs in or creates portal accounts, reads verification codes from your
  mailbox, fills forms from your recorded answers and uploads the CV. An AI "navigator" handles unfamiliar pages within
  strict accuracy gates.
- **Proves** each application: a job counts as `applied` only with proof tied to that job (the employer's receipt email or the
  portal's own record). A thank-you page alone stays `unconfirmed`.
- **Hands off to you** whatever it must not decide alone: CAPTCHAs, facts you have not recorded, one-time password resets, and
  new account sign-ups that need your decision.
- **Runs unattended** (Windows): a guardian process keeps the worker, dashboard, receipt checks and discovery running, and
  restarts what dies.
- **Dashboard** at http://127.0.0.1:8799: today's sends and proofs, what the bot is doing, what is failing, and what needs you.

## Honest limits

- **Irreversible.** Every live submission is final. Start with dry runs (the default) and read [docs/SAFETY_AND_ACCURACY.md](docs/SAFETY_AND_ACCURACY.md).
- **CAPTCHAs are never solved or bypassed.** Jobs behind a CAPTCHA are parked. You can finish them in a visible window with
  the hand-off tool (the bot fills the form and waits while you solve the CAPTCHA yourself).
- **Websites change.** Portal layouts, login flows and feeds change without notice, so some applications will fail and need
  fixes. The test suite pins every behaviour that has been fixed so far.
- **Terms of service.** Automating a website may break its terms of use. LinkedIn in particular forbids automated use of
  accounts, and some sites suspend accounts that automate them. Know the rules of each site you use. See
  [docs/SAFETY_AND_ACCURACY.md](docs/SAFETY_AND_ACCURACY.md).
- **Platform.** The unattended autopilot (guardian, supervisor, workers, start/stop scripts) is **Windows 10/11 only**: it
  uses PowerShell, Windows process queries, `robocopy` and Windows file locking. Single commands such as `hunt.py run`,
  `discover` and `dashboard` are plain Python and may work on macOS or Linux, but that is untested.
- **Regional focus.** Several parts are tuned for a job search based in India: Naukri, CTC in lakh per annum (LPA), the +91
  phone code, Indian city lists in the location filter, and discovery searches that ask for Bengaluru. Your home location,
  role families, search titles and every personal answer are configurable. Searching another country well needs code changes
  in `discover/` and `rank/filter.py`. See [docs/CONFIGURING_FACTS.md](docs/CONFIGURING_FACTS.md#regional-assumptions).
- **AI is optional but useful.** Without an AI provider, scoring and the form navigator do not run. Rule-based filling still
  works on simple forms. Free tiers (Gemini, Groq) are enough for a modest daily volume.

## Requirements

- Windows 10 or 11 for the full autopilot (see Platform above).
- Python 3.10 or newer.
- Google Chrome installed. Nightshift drives real Chrome (`channel="chrome"`) through Playwright.
- A mailbox reachable over IMAP with an app password (Gmail is the tested path). It is used to read verification codes,
  receipts and replies.
- Optional: an API key for at least one AI provider (Gemini, Groq, DeepSeek or another OpenAI-compatible API), or the Claude
  Code CLI.

## Quick start

```powershell
git clone https://github.com/aviruprc/nightshift.git
cd nightshift
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium

python hunt.py setup        # guided setup: disclaimers, your profile, mailbox, AI keys, browser sign-ins, settings
python hunt.py doctor       # checks everything setup configured
python hunt.py discover     # find and score jobs (no applications)
python hunt.py run --target 3 --show   # DRY RUN: fills 3 forms in a visible window, submits nothing
```

When the dry runs look right:

```powershell
python hunt.py run --live --target 5    # really submits, one batch
# or the unattended autopilot (Windows):
powershell -ExecutionPolicy Bypass -File start_all.ps1
powershell -ExecutionPolicy Bypass -File stop_all.ps1
# (or double-click start.cmd / stop.cmd; both work from any folder and use .venv if it exists)
```

`python hunt.py run --live` refuses to start while `cv/facts.yaml` or `data/answers.yaml` still holds the example person's values.

`start_all.ps1` starts the dashboard and the guardian, and **the guardian starts live applying** within a minute. Do not run it
until your dry runs are correct.

## Documentation

| Guide | What it covers |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Install, `hunt.py setup`, facts and answers, accounts and the credential store, mailbox app password, AI providers, first dry run |
| [docs/CONFIGURING_FACTS.md](docs/CONFIGURING_FACTS.md) | Every field of `facts.yaml`, `answers.yaml` and `settings.json`; `not_claimable` skills; job kinds, search titles, score bars |
| [docs/OPERATING.md](docs/OPERATING.md) | Start/stop, guardian, supervisor, workers, dashboard, daily routine, statuses, what `needs_you` means, CAPTCHA hand-off |
| [docs/SAFETY_AND_ACCURACY.md](docs/SAFETY_AND_ACCURACY.md) | The non-negotiable rules, legal and terms-of-service caution, never inventing facts |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common failures and what to check |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit together |
| [docs/ENGINEERING_LESSONS.md](docs/ENGINEERING_LESSONS.md) | The do-not-regress lessons behind the safety code |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | Tests, conventions, privacy rules for contributions |

## Privacy

Everything personal stays on your machine under `data/` and `cv/facts.yaml`. Both are git-ignored, and only `*.example.*`
templates are tracked. Passwords and API keys go in your OS credential store (Windows Credential Manager), never in files,
code or logs. The test suite runs against an invented person (`tests/fixtures/profile`), never your files.

## License

MIT. See [LICENSE](LICENSE).
