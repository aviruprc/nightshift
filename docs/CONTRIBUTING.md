# Contributing

Thanks for helping. This tool submits irreversible applications in people's names, so the bar is **correctness first**.

## Ground rules

- Read [SAFETY_AND_ACCURACY.md](SAFETY_AND_ACCURACY.md) and [ENGINEERING_LESSONS.md](ENGINEERING_LESSONS.md) first.
- Never loosen `apply/proof.py`, `engine.SUCCESS`, `agent._confirmed`, `runner.duplicate_role`, or the accuracy gates in
  `apply/agent.py` to raise a count.
- Every behaviour change or fix comes with a test that fails before it and passes after it.
- No CAPTCHA solving or bypassing, ever. No automation of consumer chat websites.

## Privacy: no personal data in commits

- Never commit `data/` (except `*.example.*`), `cv/facts.yaml`, CVs, browser profiles, logs, `.env`, screenshots or saved
  failure pages. They contain names, emails, phone numbers and session cookies. `.gitignore` covers them. Check
  `git status` before every commit.
- Test fixtures use the invented person in `tests/fixtures/profile` ("Asha Example", `example.com` addresses,
  `+91 9000000001`). Saved third-party pages in `tests/fixtures/` must have every personal value replaced before they are
  added.
- Never put a real API key, password or one-time code in code, tests or issues.

## Development

```powershell
pip install -r requirements-dev.txt
$env:PYTHON_KEYRING_BACKEND = "keyring.backends.null.Keyring"   # keep tests away from your real credential store (conftest also sets it)
python -m pytest -q tests                  # full suite (about 1,000 tests; the browser tests need Google Chrome)
python -m pytest -q tests/test_rank.py     # one file
python -m pytest -q -n 3 tests             # parallel, if you have the RAM
```

- Tests never call an AI (`JOBHUNT_NO_LLM=1`), never touch `data/STOP` and related markers, and never read your real
  profile.
- Do not edit `.py` files while the suite runs: some tests inspect source text.
- Tests that need the private `data/failures` corpus skip when it is absent. A few source-inspection tests are marked
  `xfail` because they were already out of date before the public release. Fixing them is a welcome first contribution.
- Code style: keep comments that explain **why** (the incident that motivated a rule). Name the test that pins a fix in
  your commit message.

## Good first contributions

- Make the India-specific parts configurable (see CONFIGURING_FACTS.md "Regional assumptions"): discovery location
  parameters, the city lists in `rank/filter.py`, LPA parsing, and the browser locale.
- macOS/Linux supervision (a Python replacement for `supervise.ps1` / `worker.ps1` and the Windows-only process checks).
- An applier for a job board that currently has none.
