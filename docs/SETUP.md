# Setup

This guide takes you from a fresh clone to a correct dry run. Plan on about an hour, most of it spent writing your facts
carefully. Commands are run from the project folder.

## 1. Install

Requirements: Windows 10/11 (needed for the unattended autopilot), Python 3.10+, and Google Chrome.

```powershell
git clone https://github.com/aviruprc/nightshift.git
cd nightshift
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium     # optional: Nightshift drives your installed Google Chrome; this adds Playwright's own browser
```

On macOS/Linux you can install and run single commands (`discover`, `run`, `dashboard`), but the start/stop scripts, the
guardian and the supervisor are Windows-only. Desktop notifications (`win11toast`) are Windows-only and optional.

## 2. Run the guided setup

```powershell
python hunt.py setup
```

The setup tool asks before it writes anything, and it never overwrites an existing file unless you say so. It walks
through these steps (each can be skipped and re-run later):

1. **Disclaimers.** What the tool does, that submissions are final, terms-of-service caution, and that CAPTCHAs are never
   bypassed. You must type `I AGREE` to continue.
2. **Your profile.** It creates `cv/facts.yaml` and `data/answers.yaml` from the examples and fills in the basics you type
   (name, email, phone, city, LinkedIn, notice period, expected and current pay, work authorisation). Everything else you
   edit by hand afterwards (step 3 below).
3. **Job preferences.** Locations you accept, the kinds of job you want (search titles), the minimum fit score, and a cap
   on stated minimum experience.
4. **CV PDF.** The path to your own ready-made CV PDF (used for job-board profile uploads and outreach emails). Per-job CVs
   are generated from `facts.yaml`.
5. **Settings.** It creates `data/settings.json` (daily goal, score bars, per-company cap, do-not-apply companies, outreach
   sending on or off).
6. **Mailbox.** It stores an IMAP app password (see section 5) and tests the login.
7. **AI providers.** It creates `data/llm_providers.json`, then asks for an API key for each provider you have one for.
   It stores each key in the credential store and tests it (see section 6).
8. **Browser sign-ins.** It opens a visible Chrome window with the bot's own profile so you can sign in to your mailbox,
   Naukri, Indeed and LinkedIn (skip any you do not use).
9. **Check.** It runs `python hunt.py doctor`.

## 3. Write your facts (the most important step)

Open `cv/facts.yaml` and `data/answers.yaml` in an editor and replace **every** example value with your real one.
[CONFIGURING_FACTS.md](CONFIGURING_FACTS.md) explains each field. The rules:

- Only true things. Tailoring reorders and emphasises; it never adds. Anything you write here can be submitted.
- `not_claimable` lists skills you do NOT have. They are never claimed, and a job that requires one scores lower.
- A missing answer is safe: the job is parked as `needs_you` with the exact question, and you answer it once from the
  dashboard. A wrong answer is not safe.
- Leave out what you do not want to give (for example a national ID number). The bot never invents it.

If you prefer to skip the setup tool:

```powershell
copy cv\facts.example.yaml cv\facts.yaml
copy data\answers.example.yaml data\answers.yaml
copy data\settings.example.json data\settings.json
copy data\llm_providers.example.json data\llm_providers.json
```

## 4. Accounts and the credential store

- Career-site passwords are stored in your OS credential store (Windows Credential Manager, through the `keyring`
  package) under the service `nightshift-sites`, keyed by site host. On a portal that needs an account, the navigator signs in
  with the saved password, or creates an account with your `personal.email` and a generated password that it saves first.
- Saving a password you set yourself (for example after a manual reset):
  `python hunt.py set-password --provider <site host>` (input hidden). Despite its name, `--provider` here takes the site host.
- `python hunt.py accounts-todo` lists portals waiting for one manual password reset by you.
- Optional plain-file backup: set `"accounts_mirror": true` in `data/settings.json` to also keep a copy of portal passwords
  in `data/accounts.json`. That copy is base64, which is **not** encryption. It is off by default. Trade-off: the mirror
  also holds each portal's previous passwords, so with it off the bot cannot retry an older password after a reset that
  did not take effect. Such a login is parked for one manual reset instead.
- Never put passwords in `facts.yaml`, `answers.yaml`, code, logs or chat.

## 5. Mailbox: app password for codes, receipts and replies

The bot reads your mailbox over IMAP to type emailed verification codes, follow password-reset links, prove applications
from receipts, and record employer replies.

Gmail (the tested path):

1. Turn on 2-Step Verification for your Google account.
2. Create an app password at https://myaccount.google.com/apppasswords (name it `nightshift`).
3. Run `python hunt.py gmail-setup` and paste the 16 letters (input hidden). The password goes to the credential store
   (service `nightshift-gmail`) and the login is tested.
4. Check it any time: `python hunt.py gmail-test`.

Another provider: set `JOBHUNT_IMAP_HOST` (for example in `.env`, see `.env.example`) and use that provider's app password.
Some code paths use Gmail search syntax (`newer_than:2d`), so a non-Gmail server may find fewer codes and receipts.

The login address is `personal.email` from `facts.yaml`, or `JOBHUNT_EMAIL` if you set it.

## 6. AI providers and keys

`data/llm_providers.json` lists the providers in priority order. Each has `name`, `type` (`gemini`, `openai` for any
OpenAI-compatible API, or `codex` for a signed-in Codex CLI), `model`, optional `base_url` and `fallback_model`, and
`enabled`. Keys are **never** stored in this file: `llm-key` puts them in the credential store under the service `nightshift-llm`.

```powershell
python hunt.py llm-key --provider gemini    # free key: https://aistudio.google.com/apikey
python hunt.py llm-key --provider groq      # free key: https://console.groq.com/keys
python hunt.py llm-test                     # shows the chain and which providers are ready
python hunt.py llm-test --only gemini       # tests one provider
```

Without a credential store, a key can come from the environment instead: `JOBHUNT_KEY_<NAME>` with the provider name
upper-cased and non-letters replaced by underscores (for example `JOBHUNT_KEY_DEEPSEEK_API`).

Claude: if the Claude Code CLI (`claude`) is installed and signed in, it can serve as a provider. `settings.json`
`"claude_for_bot": false` keeps the bot from using it, and `"llm_primary": "backup"` uses the providers file first.
Do not configure consumer chat websites as providers: automating them breaks their terms and can get the account suspended.

## 7. First dry run

```powershell
python hunt.py doctor                 # every line should say OK (AI and Claude lines can fail if you use neither)
python hunt.py discover               # finds and scores jobs; prints what it kept and why it rejected the rest
python hunt.py pool                   # what is waiting, best fit first
python hunt.py run --target 3 --show  # dry run in a visible window: fills, uploads, never presses the final Submit
```

Watch the window, then check the dashboard (`python hunt.py dashboard`, then open http://127.0.0.1:8799). For each dry
run, check that every field it filled is true. Only then go live: `python hunt.py run --live --target 3`.

## 8. Optional

- `python hunt.py login` opens the bot's Chrome profile so you can sign in by hand to Gmail, Naukri, Indeed and LinkedIn.
- Startup outreach (`apply/outreach.py`) drafts honest emails to hiring addresses that startups publish on their own sites.
  It needs an `outreach` block in `facts.yaml` and your CV PDF. Sending is off unless `"outreach_send": true`.
- `python hunt.py import-old` imports a CSV (`data/tracker.csv`, or `JOBHUNT_OLD_TRACKER`) of jobs you already applied to
  elsewhere, so they are never applied to twice. Columns: `status, company, title, location, apply_url, ats, posted_at, applied_at`.
