# Safety and accuracy

Nightshift submits job applications in your name, and **a submitted application cannot be edited, withdrawn or re-sent.**
Every rule below exists because a real application went wrong, or nearly did. Fewer correct applications beat more
doubtful ones.

## The rules (they do not change)

1. **Never state anything untrue about you.** Facts come only from `cv/facts.yaml` and `data/answers.yaml`. If a form needs a
   fact you have not recorded, the job is parked as `needs_you`. It is never guessed, rounded up or "close enough".
2. **Never claim a skill in `not_claimable`**, or any skill not in your `skills`. A tool you have not used is answered 0 / No.
3. **Never solve or bypass a CAPTCHA.** The job is parked for you. With the hand-off tool you solve it yourself in a
   visible window.
4. **`applied` needs proof tied to that job:** the employer's receipt that names the job, or the portal's own record. A
   thank-you page is `unconfirmed`. Verification-code mails and "you applied for N jobs" digests are never receipts.
   Do not loosen `apply/proof.py`, `engine.SUCCESS` or `agent._confirmed` to raise a count.
5. **One role, one application.** The same company + title + city is never sent twice (`runner.duplicate_role`).
6. **Secrets live only in the OS credential store** (Windows Credential Manager via `keyring`). Never in chat, code, logs,
   `facts.yaml`, `answers.yaml` or `llm_providers.json`. The optional `data/accounts.json` mirror is off by default and
   is not encrypted.
7. **Back up before you repair data** (`python hunt.py backup`). Never edit a `.py` file or toggle `data/STOP` while the
   test suite runs.
8. **Every fix gets a test.** Engineering lessons are recorded in [ENGINEERING_LESSONS.md](ENGINEERING_LESSONS.md).
9. **Decisions with consequences beyond one form are yours:** sending any email in your name (outreach sending is off
   unless `outreach_send: true`), and the do-not-apply list. Note that the navigator **does create career-site
   accounts** with your email when a portal requires one. If you do not want that, stop the bot on portals where you would
   rather register yourself.

A request like "apply to everything" changes **which jobs** to go for. It never changes rules 1 to 9.

## How the code enforces accuracy

- **Rules first, then the AI, within limits.** `apply/answers_map.py` answers from your files. The AI answers only what the
  rules cannot, is given only your facts, and must answer `null` rather than invent. Skill-specific years, self-ratings,
  IDs and commercial preferences always come to you.
- **The page is checked, not trusted.** Before Next, Save or Submit, visible answers are compared with your facts (work
  authorisation, CTC, notice period, employers, institutions, education dates, phone, name). A wrong pre-filled answer
  blocks the final Submit.
- **No fuzzy matching for facts.** A picker must contain the distinctive word of your recorded school or employer. A
  look-alike ("Larkmore Cyber University" for "Larkmoor University") is refused.
- **Dates are whole or empty.** A split date of birth is completed only from your record. A month you did not record is
  never guessed.
- **Recipes carry routes, never answers.** Remembered page flows store clicks, never which option was chosen, so a past
  mistake cannot be replayed.
- **Negative signals are double-checked.** "Closed", "no Apply button" or "page blank" is confirmed on a second look or
  through the site's own API before a job is written off.

## Your responsibilities

- Keep `facts.yaml` and `answers.yaml` true and current. Update them the day something changes (a new job, a new notice
  period).
- Do dry runs first, and spot-check sent applications every day.
- Stop the bot (`stop_all.ps1`) the moment you see a wrong submission. Fix the cause, add a test, then restart.

## Legal and terms-of-service caution

- **Website terms.** Many job sites, LinkedIn in particular, forbid automated access or automated use of an account in their
  terms of service. Breaking them can get your account restricted or banned. Nightshift reads LinkedIn through public guest
  pages, uses Easy Apply only as a last resort, and caps the daily volume. Whether you use a given site with automation is
  your decision and your responsibility.
- **Consumer chat websites** (ChatGPT, DeepSeek and similar web apps) must not be automated as AI providers: it breaks their
  terms, and accounts have been suspended for it. Use official APIs.
- **Employer expectations.** Some employers ask candidates to confirm that they completed the application personally, or
  forbid automated tools. Answer such declarations honestly, or apply to those by hand.
- **Personal data.** You process your own data on your own machine. Outreach emails go only to addresses that companies
  publish on their own websites, and are capped per day. Respect anti-spam law where you live.
- **No warranty.** The software is provided as is (see LICENSE). It can and will make mistakes on websites it has not seen
  before. You are responsible for what is submitted in your name.

## Never invent facts: a checklist before going live

- [ ] Every value in `cv/facts.yaml` and `data/answers.yaml` is mine and true. No example values remain.
- [ ] `not_claimable` lists every relevant tool I do not know.
- [ ] Education start and end months match my certificates.
- [ ] Pay figures use the unit the forms expect (for example LPA), and I am comfortable stating them.
- [ ] Declarations (`no_compliance_flags`, `knows_no_insider`, `over_18`) are set only because they are true.
- [ ] Three dry runs filled correctly, and I checked every field.
