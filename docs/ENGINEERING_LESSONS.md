# Engineering lessons (do not regress)

These lessons come from a real job search run with this code. Each was a real failure, and each is pinned by a test
under `tests/`. The `KB-nn` numbers match comments in the code. Read this before changing `apply/`.

## Doubt every signal, especially negative ones

- **A failure message is not a diagnosis.** Read the saved page (`data/failures/*.json`, `apply/diagnostics.py`) before
  classifying. "Page stopped changing" can hide a required field, and "closed" can be a slow page. **No saved page is
  itself a bug** (KB-54): `agent.navigate()` saves evidence through `_done(res)` on every exit, so never add a bare
  `return res` on a failure path.
- **Cross-check negatives** (closed, no Apply button, needs you, status light, summary count) with an independent source:
  the portal's JSON API, a second load, the real mailbox. If unsure, treat it as transient with a retry, never permanent.
- **Doubt a signal that overturns a closure as hard as the closure itself** (KB-60). Oracle serves an HTTP 200 page, with the
  role in `og:title`, for a removed requisition. Only the site's own API (`ats_oracle.req_live()`) decides.
- **A missing Apply control is never a closure** (KB-58). Only `ats_api.workday_open() is False` or
  `engine.page_state(pg, confirm=True)` may close a posting. `None` (for example a 403) means unknown.
- **A model's verdict is never an outcome** (KB-86). "The navigator says closed" is checked against the page. A closure on
  the model's word alone is transient.
- **A blank page is a page state, not a verdict** (KB-87): reload once, then report "never rendered" as transient. Never
  while `final_step` is set, because a blank page after Submit may be the send in progress.
- **A `blocked` reason that asserts a capability is a claim** (KB-45/46/47). "Cannot access the inbox" was false: the bot
  reads it over IMAP. "No interactive elements" was false on a page that had controls.
- **A claim in a failure message must be checkable from its own evidence** (KB-73): quote the page's own phrase.
- **"Already works" is about the past, not this attempt** (KB-70): a password rejected in this run parks the job for one
  manual reset.

## Accuracy

- **A place never answers a yes/no question** (KB-56). Country rules match real country fields only. Work authorisation is
  answered from the posting's own location.
- **Fuzzy matching is never used for facts** (KB-57). The institution picker requires the distinctive word of the recorded
  school (`widgets.institution_needle`). A look-alike school is refused, and the options are named in the error.
- **A split date is whole or empty** (KB-62). `widgets.fill_missing_dob_parts` fills only empty parts, verifies them, and
  never overwrites. The same applies to any grouped fact (phone code + number, address parts).
- **A recipe is a route, never an answer** (KB-83). Remembered actions never store a chosen option. Workday keeps answers
  server-side, so one stored mistake reappeared pre-filled.
- **A page that answers a question for you is checked, not trusted** (KB-84). Visible yes/no answers are compared with
  `answers_map.answer_for()`. Next and Save are refused while one is wrong, and the final Submit is refused outright.
- **A remembered plan may not submit a value it was never given** (KB-79): no replay on one-time-passcode pages.
- **A check with two kinds of evidence must credit the one that matched** (KB-82): `agent.confirmation_proof()`.
- **Read the whole page before saying "no confirmation"** (KB-76). The confirmation modal sat past the first 4,000
  characters.
- **Former-employer questions.** A blanket "No" is wrong for a company on your own CV (`answers_map.employer_words`).
- **Education dates.** Only recorded month + year. The day is the 1st of the month. An unrecorded month parks the job
  (`agent.education_date_wrong`).

## Robustness

- **One SVG icon must not make a page unreadable** (KB-65). Read `innerText` or `textContent`. `SVGElement` has no
  `innerText`.
- **A snapshot lists what exists, not what can be reached** (KB-85). Elements behind a declared modal dialog are marked
  `covered` and clicks on them are refused.
- **A widget that stays in the DOM is answered once** (KB-72), and an option may never be the question itself.
- **The step budget must fit the application, and a spent budget must say where it ran out** (KB-68): 26 steps / 600 s.
- **A heartbeat must be lit by the thing it claims is alive** (KB-80). Only the applier's main thread refreshes
  `run_state`. A probe thread must never keep a frozen worker looking healthy.
- **A hard exit owes the database a sentence** (KB-80): `runner._record_wedge()` writes the attempt before `os._exit`.
- **Log the intent before the call** (KB-81): `navigator step N: acting: ...` is written before each dispatch.
- **Zero is an age, not a missing value** (KB-75). Never write `x or default` on a number where 0 means something.
- **A "later" queue must be re-derivable** (KB-74). Anything held only in a run's memory is lost.
- **A verdict nested inside `done` is still a verdict** (KB-78). Normalise model output at one boundary
  (`agent._lift_nested_verdict`).
- **A model-specific outage is not a provider outage** (KB-64). Fall through to the provider's fallback model on 429 and 5xx.
- **A failure detail is a sentence, not a repr** (KB-71).
- **A field with no writer never fills itself, and a shown value may be uncommitted** (KB-66/67).
- **A remote SEARCH is not a remote job** (KB-63). A flag that comes from the search, not the posting, must be
  re-derivable from the stored row (`filter.location_conflict`).

## Test-suite rules

- Tests never touch the live control files (`data/STOP`, `data/GUARDIAN_STOP`, `data/supervisor.lock`).
  `tests/conftest.py` fails any test that does. Patch the module constant instead.
- Tests run against the invented person in `tests/fixtures/profile` (set by `conftest.py` before any import). Never point
  a test at real `cv/facts.yaml` or `data/answers.yaml`.
- `proof.reconcile()` opens `dbm.DB()` itself, so any test that calls it must monkeypatch `core.db.DB`.
- Never write a module-scoped Playwright fixture inside a sync session, and never edit a `.py` while pytest runs (the
  source-inspection tests read the file).
- A test may not depend on a pruned `data/failures` file. Embed the evidence, or skip when the file is absent.
- Use the editor, not shell heredocs, for Python containing backslashes (`tests/test_proof_and_retry.py` guards against
  control characters in sources).
