"""Failure taxonomy: ONE registry of stable `fail_class` keys. Every module that classifies an
outcome writes or reads these keys.

Two vocabularies existed before this registry:

  * the RETRY POLICY keys the runner acts on (`transient`, `lockout`, `permanent`, `captcha`,
    `personal`, `logic`, plus `llm_down` written directly by runner.py);
  * the EVIDENCE kinds `apply/diagnostics.py` proves from a saved page (`AUTH_REJECTED`,
    `FIELD_REQUIRED`, ...).

They are now two layers of one table. `classify` keeps returning a policy key so runner.py's
branching and every stored row stay valid; `for_problems`/`for_kinds` turn a diagnosis into the
granular key from the design (`auth_rejected`, `field_required`, ...); `retry_as` says which
policy key a granular class inherits, so `next_attempt` needs no second table. Aliases keep old
stored strings parseable (`data/jobhunt.db` already holds `transient`/`logic`/`captcha`/`llm_down`).

Policy semantics
----------------
  transient timeouts, navigation errors, Workday 'Something went wrong', browser crashes -> retry (backoff 5/20/60 min)
  lockout 'too many attempts', rate limits, temporary blocks -> retry after 35 min
  permanent closed / expired / not eligible / already applied -> no retry
  captcha a CAPTCHA blocks the final step -> hand off to the user (form verified complete)
  personal a personal fact nobody can infer (asked once, then persisted) -> 'Answer once' on the dashboard
  logic our own bug (stuck page, unknown widget, validation we could not fix) -> lessons.md; retried after the next fix
"""
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

RULES = [
    # a company: "Cloudflare bot verification block" was filed as our bug. A human check is hers to pass (never bypassed).
    ("captcha", r"captcha|i am not a robot|verify you are (a )?human|cloudflare (?:bot )?(?:verification|challenge|check)|bot verification|checking your browser|just a moment\.\.\.|\bcloudflare\b"),
    ("lockout", r"too many (attempts|requests)|try again (later|in \d+)|rate.?limit|temporarily (blocked|locked)|account (is )?locked|429"),
    ("permanent", r"\bclosed\b|no longer (accepting|available|open)|expired|job (has been )?removed|position (has been )?filled|already applied|"
                  r"not eligible|listing closed|blocked until"),
    ("transient", r"timeout|timed out|net::err|navigation|target (page|closed)|browser has been closed|connection (reset|refused|closed)|"
                  r"something went wrong|please refresh|503|502|504|ECONNRESET|execution context was destroyed|frame was detached|"
                  r"llm unavailable|claude -p failed|usagelimit|no verification email|no code arrived|email was delayed|"
                  r"sign-in did not complete|did not show the next step|time budget|never rendered|interface did not load|page did not load|"
                  r"currently unavailable|temporarily unavailable|service interruption|under maintenance|maintenance page|temporarily paused|deployment (?:is )?paused|"
                  # KB-58: a MISSING Apply control is a page state WE observed once, not a closure: Naukri's signed-out page
                  # carries no Apply button at all, and a Workday tenant whose API answers 403 gave us nothing to confirm with. Both were
                  # filed `closed` by the old wording. One fresh session, then it becomes our bug - never 'closed'.
                  r"did not confirm the cv upload|apply did not open an external page|no apply button|no 'apply'(?: or 'apply now')? button|apply button did not appear|could not set|"
                  # KB-80: a Playwright call has no timeout; the watchdog restarts a wedged worker and records the
                  # attempt as transient (retried with backoff, then parked as our bug) - never as a verdict about the job.
                  r"applier froze|no progress for \d+s"),
    ("personal", r"unanswered required: .*(passport|aadhaar|pan (card|number)|ssn|social security|national id|bank account)"),
    # a company: a LinkedIn job whose apply link is a Google Form behind accounts.google.com. Signing into HER Google account from the
    # bot risks a security challenge/lockout, so this is hers to finish - needs_you, never a "logic" failure retried forever.
    ("personal", r"accounts\.google\.com|google account challenge|sign in with google"),
    # a company: a Naukri WALK-IN listing has no apply flow (in-person interview, button "I am interested"): hers to decide.
    ("personal", r"walk-in interview listing"),
    # a portal whose sign-in texts a code to the user's PHONE (a new account there): only the person holding the phone can finish it.
    ("personal", r"sms (?:code|otp)|code (?:sent|texted) (?:by|via) sms|signs in with a code sent by sms"),
]
BACKOFF_MIN = [5, 20, 60]
MAX_TRANSIENT = 2  # a retry redoes the whole (LLM-driven) session: keep it to one


LLM_DOWN_RX = re.compile(r"llm unavailable|usage limit|rate limit reached|claude -p failed|usagelimit|runtimeerror: claude", re.I)


def is_llm_down(detail: str) -> bool:
    return bool(LLM_DOWN_RX.search(detail or ""))


def _asks_her(detail: str) -> bool:
    """True when the unanswered question is a decision only the user can make (skill-specific years, commercial preference).
    A READ failure stays 'logic' (our bug): answers_map.needs_her answers False for those."""
    try:
        from apply import answers_map as am
        return am.needs_her(detail)
    except Exception:  # noqa: BLE001 (a classification must never crash the runner)
        return False


# "the job is NOT closed", "it isn't closed", "never closed": a negated word must never make a job permanent.
# (: the a company picker message saying "it is not closed" was classified `permanent` -> closed, from `\bclosed\b`.)
NEGATED_PERMANENT = re.compile(r"\b(?:not|never|isn'?t|wasn'?t|aren'?t|won'?t be)\s+(?:been\s+)?(?:yet\s+)?"
                               r"(?:closed|expired|removed|filled)\b", re.I)


def classify(status: str, detail: str) -> str:
    if status == "captcha":
        return "captcha"
    if status in ("closed", "already_applied"):
        return "permanent"
    d = NEGATED_PERMANENT.sub("still open", (detail or "").lower())
    for cls, rx in RULES:
        if re.search(rx, d, re.I):
            return cls
    # a genuine form question (e.g. 'years of experience in Macros', 'ok with third-party payroll')
    # is HER decision, so the job is parked as needs_you with the question; rule/reading gaps remain our bug ('logic').
    if d.startswith("unanswered required:") and _asks_her(detail):
        return "personal"
    return "logic"


def next_attempt(cls: str, attempts: int, now: datetime | None = None) -> str | None:
    """When to retry (ISO time), or None for no automatic retry.

    Accepts a granular class too: it resolves through `retry_as`, so a caller never needs a
    second switch statement.
    """
    now = now or datetime.now()
    cls = retry_as(cls) or cls
    if cls == "lockout":
        return (now + timedelta(minutes=35)).isoformat(timespec="seconds")
    if cls == "transient" and attempts < MAX_TRANSIENT:
        return (now + timedelta(minutes=BACKOFF_MIN[min(attempts, len(BACKOFF_MIN) - 1)])).isoformat(timespec="seconds")
    return None


# --------------------------------------------------------------------------- one registry
# `retry_as` is one of the policy keys classify already returns (or None: no automatic retry).
# `aliases` are strings that may already sit in the DB / saved JSON; they must keep parsing.

@dataclass(frozen=True)
class FailClass:
    key: str
    retry_as: str | None
    label: str
    description: str
    aliases: tuple[str, ...] = ()


_FAIL_CLASS_LIST = (
    # --- the retry-policy keys the runner branches on (stored in jobs.fail_class today) ---
    FailClass("transient", "transient", "Transient",
              "timeout / navigation / site glitch; the job is not at fault"),
    FailClass("lockout", "lockout", "Lockout",
              "too many attempts / rate limited; one retry after 35 min"),
    FailClass("permanent", "permanent", "Permanent",
              "closed / expired / not eligible / already applied; never retried automatically"),
    FailClass("captcha", "captcha", "CAPTCHA", "a CAPTCHA blocks the final step; handed to the user"),
    FailClass("personal", "personal", "Needs her", "a personal fact nobody can infer"),
    FailClass("logic", None, "Our bug", "a defect in our toolchain; retried after the next fix"),
    # --- operational outcomes that used to be ad-hoc strings ---
    FailClass("llm_unavailable", "transient", "LLM unavailable", "the AI provider is down; not the job's fault",
              aliases=("llm_down",)),
    FailClass("timeout", "transient", "Timeout", "a page/job time budget expired"),
    # --- granular evidence classes from the GENERIC DESIGN (#6) ---
    FailClass("auth_required", "transient", "Sign-in required", "an account session is needed; automatable"),
    FailClass("auth_rejected", "lockout", "Password rejected",
              "the page itself rejected the saved password; one reset, then retry"),
    FailClass("auth_verify", "transient", "Verification pending",
              "the portal asked us to verify / reset by email; wait for the mail", aliases=("auth_verify_pending",)),
    FailClass("signup_wall", "transient", "Account creation", "the apply URL is a signup wall, not a closure"),
    FailClass("field_required", "logic", "Field required", "a banner named a required field we left empty"),
    FailClass("consent_required", "logic", "Consent required", "a required terms/privacy box was never ticked"),
    FailClass("element_not_actionable", "logic", "Control not actionable",
              "a forward control is disabled / covered / not in the snapshot"),
    FailClass("form_validation", "logic", "Form validation", "the form rejected our values without naming one field"),
    FailClass("wrong_environment", "transient", "Wrong environment",
              "the apply URL is not the form (PDF, wrong country, wrong page); reload it"),
    FailClass("proof_missing", "transient", "Awaiting proof", "a send has no receipt yet; re-check the mailbox"),
    FailClass("needs_fact", "personal", "Needs a fact", "a human-only answer (CAPTCHA or a personal question)"),
)

CLASSES: dict[str, FailClass] = {c.key: c for c in _FAIL_CLASS_LIST}
ALIASES: dict[str, str] = {alias: c.key for c in _FAIL_CLASS_LIST for alias in c.aliases}

# `apply/diagnostics.py` Problem kinds -> granular key. CONFIRMED maps to None: an applied row is
# not a failure and never gets a fail_class. tests/test_errors_taxonomy.py asserts this covers
# diagnostics.KINDS exactly, so the two modules cannot drift apart silently.
KIND_TO_CLASS: dict[str, str | None] = {
    "AUTH_REQUIRED": "auth_required",
    "AUTH_REJECTED": "auth_rejected",
    "AUTH_VERIFY_PENDING": "auth_verify",
    "RESET_LINK_AVAILABLE": "auth_verify",
    "FIELD_REQUIRED": "field_required",
    "CONSENT_REQUIRED": "consent_required",
    "ELEMENT_NOT_ACTIONABLE": "element_not_actionable",
    "FORM_VALIDATION_ERRORS": "form_validation",
    "CONFIRMED": None,
    "JOB_CLOSED": "permanent",
    "PDF_OR_NO_FORM": "wrong_environment",
    "SIGNUP_WALL": "signup_wall",
    "UNKNOWN": "transient",
    "CAPTCHA": "captcha",
    "HUMAN_QUESTION": "needs_fact",
    "COUNTRY_MISMATCH": "wrong_environment",
}


def canonical(key: str) -> str:
    """Normalise any stored / legacy class string to a registry key. Unknown strings pass through
    unchanged (an ops report must show what was stored, never invent a bucket)."""
    k = str(key or "").strip().lower()
    return ALIASES.get(k, k)


def fail_class(key: str) -> FailClass | None:
    """The registry entry for a stored key or alias, else None."""
    return CLASSES.get(canonical(key))


def retry_as(key: str) -> str | None:
    """The retry-policy key a (possibly granular) class inherits, or None for no automatic retry."""
    entry = fail_class(key)
    return entry.retry_as if entry else None


def for_kinds(kinds) -> str | None:
    """Granular fail_class for an ordered list of diagnostics Problem kinds.

    `diagnose` returns its most decisive Problem first, so the first kind with a mapping wins;
    CONFIRMED maps to None and is skipped (an applied row has no fail_class).
    """
    for kind in kinds:
        name = getattr(kind, "kind", kind)
        key = KIND_TO_CLASS.get(str(name or "").upper())
        if key:
            return key
    return None


def for_problems(problems) -> str | None:
    """Granular fail_class for `diagnostics.Problem` objects (duck-typed: only `.kind` is read)."""
    return for_kinds(problems)


# One class -> one job status. This is the mapping runner.handle_failure implements today; keeping
# it here as data means a new class can never silently acquire a different status in a second place.
_STATUS_BY_POLICY = {
    "transient": "candidate",   # retried later (next_attempt_at set)
    "lockout": "candidate",     # retried after 35 min
    "permanent": "closed",
    "captcha": "needs_you",
    "personal": "needs_you",
    "logic": "failed",
}


def status_for(key: str) -> str:
    """The job status a fail_class produces. Unknown keys fall back to 'failed' (our bug), never closed."""
    entry = fail_class(key)
    if entry is None:
        return "failed"
    return _STATUS_BY_POLICY.get(entry.retry_as or entry.key, "failed")
