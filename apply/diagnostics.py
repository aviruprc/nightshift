"""Generic evidence reader: turn ONE saved page snapshot into a structured diagnosis.

Design goal: learn from every error and fix, so one generic solution fits many portals. A saved page is the ground truth;
this module reads it once and returns `Problem` records that any portal handler can act on, so a repair
no longer has to be written per site.

API
---
    diagnose(snapshot, page_url="", job=None) -> list[Problem]
    terminal(diagnosis, evidence_kinds=None) -> (status, detail)
    next_actions(diagnosis) -> list[str]
    fail_class(diagnosis) -> str | None (one key from apply.errors)

`snapshot` is either the raw `snap` dict saved in `data/failures/*.json`
(`url/title/heads/errs/text/els`) or the whole saved record `{"url", "job", "detail", "snap"}`; the
wrapper is unwrapped automatically. The functions are PURE: no browser, no network, no database, no
writes, no clock. `job` is accepted for call-site compatibility and is not needed for the reading.

Design rules learned from the failures (KB-10..KB-19, data/known_bugs.md)
-------------------------------------------------------------------------------
- A failed sign-in is AUTH_REJECTED only when the PAGE says so. A verification request
  ("An email has been sent to you ... your account may need verification") is AUTH_VERIFY_PENDING, and
  an empty sign-in shell is AUTH_REQUIRED -- never an invented "wrong password" (KB-18, a company).
- "Application Received" on a Workday candidate home is the status of an OLD application, not a
  confirmation. CONFIRMED reuses the same rules as `agent._confirmed`: success wording, no Submit
  control left, a page with a password field can never confirm, and a candidate-home page needs the
  post-submit MODAL wording (KB-13). It ALSO needs positive evidence that OUR final Submit was
  clicked (`final_click: true` or `final_step` on the snapshot); without that marker the wording is
  UNKNOWN, because an application-status page can describe an earlier attempt.
- Country is a fact, not a preference: any country picker showing something other than India is
  COUNTRY_MISMATCH and the form must never be submitted (KB-17, a company).
- A field named by a validation banner is FIELD_REQUIRED (KB-19, a company "Phone Device Type").
- When two readings are possible return UNKNOWN with the exact text that was seen -- never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlparse

from apply import answers_map as am
from apply import errors

# --------------------------------------------------------------------------- kinds

AUTH_REQUIRED = "AUTH_REQUIRED"
AUTH_REJECTED = "AUTH_REJECTED"
AUTH_VERIFY_PENDING = "AUTH_VERIFY_PENDING"
RESET_LINK_AVAILABLE = "RESET_LINK_AVAILABLE"
FIELD_REQUIRED = "FIELD_REQUIRED"
CONSENT_REQUIRED = "CONSENT_REQUIRED"
ELEMENT_NOT_ACTIONABLE = "ELEMENT_NOT_ACTIONABLE"
FORM_VALIDATION_ERRORS = "FORM_VALIDATION_ERRORS"
CONFIRMED = "CONFIRMED"
JOB_CLOSED = "JOB_CLOSED"
PDF_OR_NO_FORM = "PDF_OR_NO_FORM"
SIGNUP_WALL = "SIGNUP_WALL"
UNKNOWN = "UNKNOWN"
# Human-only facts (the ONLY two routes to `needs_you`, per the rule).
CAPTCHA = "CAPTCHA"
HUMAN_QUESTION = "HUMAN_QUESTION"
# Accuracy check added from KB-17 (a company): a country field holding a non-India value.
COUNTRY_MISMATCH = "COUNTRY_MISMATCH"

KINDS = frozenset({
    AUTH_REQUIRED, AUTH_REJECTED, AUTH_VERIFY_PENDING, RESET_LINK_AVAILABLE, FIELD_REQUIRED,
    CONSENT_REQUIRED, ELEMENT_NOT_ACTIONABLE, FORM_VALIDATION_ERRORS, CONFIRMED, JOB_CLOSED,
    PDF_OR_NO_FORM, SIGNUP_WALL, UNKNOWN, CAPTCHA, HUMAN_QUESTION, COUNTRY_MISMATCH,
})


@dataclass(frozen=True)
class Problem:
    """One thing the saved page proves. `evidence` is the exact text/element that proves it."""
    kind: str
    field: str = ""       # the label / element the problem is about, or ""
    evidence: str = ""    # exact page text, element label/value, or URL that proves the reading
    fixable: bool = False
    action: str = ""      # machine-readable hint: check:/select:/fill:/open_mail:/stop:, ...


# --------------------------------------------------------------------------- page regexes
# Closed wording, deliberately narrow: only explicit closure language counts (engine.CLOSED mirrors this).
CLOSED_RX = re.compile(
    r"no longer (?:accepting|available|open|active)"
    r"|position (?:has been |is )?(?:filled|closed)"
    r"|(?:this )?(?:job|posting|position|listing) (?:has been |is )?(?:closed|expired|removed|filled)"
    r"|not currently accepting"
    r"|applications? (?:are|is) (?:now )?closed",
    re.I)

# Same wording as agent.engine.SUCCESS: a success claim, not proof by itself.
SUCCESS_RX = re.compile(
    r"thank you for (?:applying|your application|submitting)"
    r"|application (?:has been |was )?(?:successfully )?(?:submitted|received|sent)"
    r"|successfully (?:applied|submitted)"
    r"|we(?:'ve| have) received your application"
    r"|application complete"
    r"|your application is in",
    re.I)
# The post-submit modal Workday shows ON the candidate home (agent.CONFIRM_MODAL).
CONFIRM_MODAL_RX = re.compile(
    r"application (?:has been )?(?:successfully )?submitted"
    r"|confirmation:?\s*your application"
    r"|your application has been (?:successfully )?(?:submitted|received)",
    re.I)
CANDIDATE_HOME_RX = re.compile(r"my applications|application status|job req|date submitted|candidate home|my tasks", re.I)
# Only these controls are a FINAL submit (agent.SUBMIT_BTN): a page that still has one is not a confirmation.
SUBMIT_BTN_RX = re.compile(
    r"^\s*(?:submit(?: (?:my )?application)?|send application|finish(?: application)?|complete application"
    r"|confirm(?: and submit)?|submit & apply)\s*$", re.I)

# Account verification wording (agent.VERIFY_PAGE). Never a rejected sign-in (KB-18).
VERIFY_RX = re.compile(
    r"an email has been sent to you"
    r"|verify your (?:candidate |e-?mail |user )?account"
    r"|(?:your )?account (?:may |might )?needs? verification"
    r"|resend (?:account )?verification"
    r"|account verification"
    r"|we(?:'ve| have) (?:sent|emailed) you (?:an? )?(?:email|link) to verify",
    re.I)
# A password reset mail/link was just offered; distinct from account verification.
RESET_SENT_RX = re.compile(
    r"(?:password )?reset (?:link|email|e-mail) (?:has been |was )?(?:sent|emailed)"
    r"|we(?:'ve| have) (?:sent|emailed) you (?:a |an )?(?:password )?reset"
    r"|check your (?:email|inbox) for (?:the |a )?(?:password )?reset"
    r"|reset link (?:is|was) (?:in|sent)",
    re.I)
# A REAL rejection (agent.SIGNIN_REJECT): only when the page says it.
REJECT_RX = re.compile(
    r"wrong\s+(?:email|e-mail|username|password|credentials)"
    r"|incorrect\s+(?:email|e-mail|username|password|credentials)"
    r"|invalid\s+(?:email|e-mail|username|password|credentials|login)"
    r"|account (?:might|may|has been|is)?\s*(?:be )?(?:locked|disabled|suspended)"
    r"|too many (?:sign-?in|log-?in|login) attempts"
    r"|does not match our records",
    re.I)
SIGNIN_RX = re.compile(r"^\s*(?:sign\s*(?:in|on)|log\s*in|login|signin)\b", re.I)
# Challenge wording only: the invisible reCAPTCHA badge in a footer ("This site is protected by
# reCAPTCHA") is NOT a CAPTCHA blocking the final step.
CAPTCHA_RX = re.compile(
    r"i(?:'m| am) not a robot"
    r"|verify (?:that )?you are (?:a )?human"
    r"|complete (?:the |this )?(?:re)?captcha"
    r"|solve (?:the |this )?(?:re)?captcha"
    r"|(?:re)?captcha (?:challenge|verification|check)"
    r"|hcaptcha challenge",
    re.I)
HUMAN_Q_RX = re.compile(r"unanswered required:\s*(.+)", re.I)

# Consent / marketing wording has ONE definition: apply/answers_map.py (the same regexes the
# deterministic fillers engine.py and workday.py use). They are re-exported here so the evidence
# reader and the fillers can never disagree about which checkbox is consent and which is marketing.
CONSENT_RX = am.CONSENT
MARKETING_RX = am.MARKETING
AGREE_ERR_RX = re.compile(r"(?:need|must|have) to (?:agree|accept|acknowledge)[^.!?]*", re.I)
FORWARD_RX = re.compile(
    r"^\s*(?:next|continue|save(?: and| &)? continue|proceed|review"
    r"|submit(?: (?:my )?application)?|send(?: application)?|finish(?: application)?|complete application)\b",
    re.I)

SIGNUP_URL_RX = re.compile(r"/(?:sign[-_]?up|register|join|talent/signup)(?:/|$|\?)", re.I)
SIGNUP_TITLE_RX = re.compile(r"\bsign[- ]?up\b|\bregister\b", re.I)
CREATE_ACCOUNT_RX = re.compile(r"\bcreate (?:an |your )?account\b|\bsign[- ]?up\b|\bregister\b", re.I)

COUNTRY_LABEL_RX = re.compile(r"\bcountry(?:\s*/\s*region)?\b|\bcountry of residence\b", re.I)
COUNTRY_EXCLUDE_RX = re.compile(r"\b(?:code|phone|dial)\b", re.I)
INDIA_RX = re.compile(r"\b(?:india|indian)\b|\+91", re.I)
NON_COUNTRY_VALUE_RX = re.compile(r"^\s*(?:select|select one|choose|--|n/?a|)$", re.I)

DOC_EXT_RX = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rtf)(?:$|[?#&])", re.I)
# Status toasts are not validation errors (a company: "Assoc Analyst ... page is loaded").
TOAST_RX = re.compile(r"\bpage is loaded\b|\bloading\b|please wait|successfully (?:saved|updated|sent)|has been saved", re.I)

# "The field X is required and must have a value." / "Error-X: ..." / "X is required."
FIELD_REQUIRED_RXS = (
    re.compile(r"the field\s+([^.:;]{2,60}?)\s+is required", re.I),
    re.compile(r"(?:error[-:\s]+)\s*([^.:;]{2,60}?)\s*:\s*the field[^.!?]*required", re.I),
)
FIELD_REQUIRED_ANCHORED = re.compile(r"^\s*([^.:;]{2,60}?)\s+is required(?:\.|!|$)", re.I)
POLITE_REQUIRED_RX = re.compile(r"please (?:enter|select|choose|provide)\s+(?:a |an |your )?([^.:;]{2,60})", re.I)
_NOT_A_FIELD_LABEL_RX = re.compile(r"[*]|\b(?:fields?|marked|this|that|it|value|required)\b", re.I)


# --------------------------------------------------------------------------- small helpers

def _clean(value, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _as_page(snapshot) -> dict:
    """Accept the raw `snap` dict or the whole saved record; return the page part."""
    if not isinstance(snapshot, dict):
        return {}
    inner = snapshot.get("snap")
    if isinstance(inner, dict):
        page = dict(inner)
        for key in ("final_click", "final_step", "after_final_click", "unanswered_required", "unanswered"):
            if key in snapshot and key not in page:
                page[key] = snapshot[key]
        return page
    return dict(snapshot)


def _els(page: dict) -> list[dict]:
    return [e for e in (page.get("els") or []) if isinstance(e, dict)]


def _errs(page: dict) -> list[str]:
    out: list[str] = []
    for raw in page.get("errs") or []:
        text = _clean(raw, 240)
        if text and text not in out:
            out.append(text)
    return out


def _el_label(el: dict) -> str:
    return _clean(el.get("label") or el.get("text") or "", 140)


def _el_text(el: dict) -> str:
    return _clean(el.get("text") or el.get("label") or "", 90)


def _el_shown(el: dict) -> str:
    return _clean(el.get("text") or el.get("value") or "", 90)


def _label_core(label: str) -> str:
    """'* Yes, I have read...' / 'Email Address*' -> 'Yes, I have read...' / 'Email Address'."""
    return _clean(re.sub(r"^[\s*•·\-•]+|[\s*]+$", "", str(label or "")), 120)


def _form_inputs(els: list[dict]) -> list[dict]:
    return [e for e in els
            if (e.get("tag") or "").lower() in ("input", "select", "textarea")
            and (e.get("type") or "").lower() != "hidden"]


def _sentence(text: str, start: int, end: int, pad: int = 90) -> str:
    """The exact sentence around a match, for evidence (left side bounded so a title bar never floods it)."""
    left = max(text.rfind(".", 0, start), text.rfind("!", 0, start), text.rfind("?", 0, start))
    left = 0 if left < 0 else left + 1
    left = max(left, start - 60)  # a long unpunctuated page header must not become the evidence
    if left and text[left - 1] not in ".!? \n":  # never start mid-word
        space = text.find(" ", left)
        if 0 <= space < start:
            left = space + 1
    ends = [i for i in (text.find(".", end), text.find("!", end), text.find("?", end)) if i != -1]
    right = min(ends) + 1 if ends else min(len(text), end + pad)
    return _clean(text[left:right], 260)


def _find(rx: re.Pattern, errs: list[str], text: str):
    """First error banner match wins (exact text), else the sentence around the body-text match."""
    for err in errs:
        if rx.search(err):
            return err
    match = rx.search(text or "")
    if match:
        return _sentence(text, match.start(), match.end())
    return ""


def _as_problems(diagnosis) -> list[Problem]:
    if isinstance(diagnosis, Problem):
        return [diagnosis]
    return [p for p in (diagnosis or []) if isinstance(p, Problem)]


# --------------------------------------------------------------------------- individual readings

def _is_candidate_home(url: str, text: str) -> bool:
    if re.search(r"candidatehome|userhome", url or "", re.I):
        return True
    return bool(re.search(r"my applications", text or "", re.I)
                and re.search(r"date submitted|application status|job req", text or "", re.I))


def _final_click_marker(page: dict):
    """True / False when the snapshot records whether OUR final Submit was clicked, else None.

    `final_step` may legitimately be 0, so compare with `is not None` (the project's `0 == False` lesson).
    """
    marker = page.get("final_click")
    if isinstance(marker, bool):
        return marker
    if page.get("final_step") is not None:
        return True
    if page.get("after_final_click") is True:
        return True
    return None


def _confirmation(page: dict, url: str, els: list[dict], text: str):
    """(CONFIRMED, None) or (None, ambiguous UNKNOWN) using agent._confirmed's rules (KB-13).

    A success message is not proof by itself, and an "Application Submitted" status page may describe an
    EARLIER attempt (a company, a saved failure page: the run
    used up its time budget with no final-click marker while the page showed a submission status).
    CONFIRMED therefore requires POSITIVE final-click evidence from the snapshot; without it the page is
    reported as UNKNOWN with the exact wording, never as applied.
    """
    if _final_click_marker(page) is False:
        return (None, None)
    if any((e.get("type") or "").lower() == "password" for e in els):
        return (None, None)  # a sign-in / reset page can never be the final submit
    if any(SUBMIT_BTN_RX.match(_el_text(e)) for e in els):
        return (None, None)  # a Submit control still on the page is not a confirmation (a company lesson)
    home = _is_candidate_home(url, text)
    modal = CONFIRM_MODAL_RX.search(text or "")
    if home and not modal:
        return (None, None)  # 'Application Received' in My Applications is an OLD application (KB-13)
    hit = SUCCESS_RX.search(text or "") or (modal if home else None)
    if not hit:
        return (None, None)
    phrase = _sentence(text, hit.start(), hit.end())
    if _final_click_marker(page) is True:
        return (Problem(CONFIRMED,
                        evidence=f"success wording after OUR final Submit, no Submit control left: '{phrase}'",
                        fixable=False, action="stop:applied"), None)
    return (None, Problem(
        UNKNOWN, field="",
        evidence=(f"success wording '{phrase}' but the snapshot does not show it followed OUR final Submit "
                  f"(an application-status page can describe an earlier attempt); not treated as a confirmation"),
        fixable=False, action="stop:transient"))


def _signup_wall(url: str, title: str, text: str, els: list[dict]) -> Problem | None:
    path = urlparse(url or "").path or ""
    titled = SIGNUP_URL_RX.search(path) or SIGNUP_TITLE_RX.search(title or "")
    if not titled:
        return None
    has_password = any((e.get("type") or "").lower() == "password" for e in els)
    has_create = any(CREATE_ACCOUNT_RX.search(_el_text(e)) for e in els) or bool(CREATE_ACCOUNT_RX.search(text or ""))
    if not (has_password or has_create):
        return None
    if not has_password and len(_form_inputs(els)) < 2:
        return None
    return Problem(
        SIGNUP_WALL,
        field="account",
        evidence=f"account creation required before applying: '{url}' / title '{title}' shows a sign-up form",
        fixable=False, action="create_account")


def _auth_required(page: dict, url: str, text: str, els: list[dict]) -> Problem | None:
    passwords = [e for e in els if (e.get("type") or "").lower() == "password"]
    if passwords:
        emails = [e for e in els
                  if (e.get("type") or "").lower() in ("email", "text") and "email" in _el_label(e).lower()]
        evidence = f"sign-in form with {len(passwords)} password field(s)"
        if len(passwords) > 1:
            evidence += " - the sign-in form rendered more than once (duplicate copies)"
        if emails:
            evidence += f"; email field '{_label_core(_el_label(emails[0]))}'"
        evidence += f"; URL {url}"
        field = _label_core(_el_label(emails[0])) if emails else "Sign In"
        return Problem(AUTH_REQUIRED, field=field, evidence=evidence, fixable=True, action=f"fill:{field}")
    # An empty sign-in shell: a Sign In control, no form fields at all, a short page on an apply/login URL.
    shell = any(SIGNIN_RX.match(_el_text(e)) for e in els)
    short = len(text or "") < 400
    apply_url = bool(re.search(r"/apply|/login|signin|sign-in|/auth", url or "", re.I))
    if shell and not _form_inputs(els) and short and apply_url:
        return Problem(
            AUTH_REQUIRED, field="Sign In",
            evidence=(f"empty sign-in shell: URL {url} shows a Sign In control but no email/password "
                      f"field was rendered (title '{_clean(page.get('title'), 80)}')"),
            fixable=False, action="stop:transient")
    return None


def _consent_from_error(errs: list[str], text: str) -> tuple[str, str] | None:
    """('label', exact error) when the page itself says a consent/terms box must be accepted."""
    error = _find(AGREE_ERR_RX, errs, text)
    if not error:
        return None
    label = ""
    match = re.search(r"(?:agree|accept|acknowledge)\s+(?:to\s+)?(?:the\s+)?([^.!?]{2,80})", error, re.I)
    if match:
        label = _label_core(match.group(1))
        # Prefer the on-page control phrase when it is visible (a company: 'I agree with the terms and conditions').
        if label:
            phrase = re.search(r"(?:i\s+)?(?:agree|accept|acknowledge|consent)[^.!?]{0,60}?" + re.escape(label), text or "", re.I)
            if phrase:
                label = _clean(phrase.group(0), 120)
    return (label or "terms and conditions", error)


def _consent_problems(errs: list[str], text: str, els: list[dict]) -> list[Problem]:
    problems: list[Problem] = []
    forward_disabled = [e for e in els if e.get("disabled") and FORWARD_RX.match(_el_text(e))]
    seen: set[str] = set()

    from_error = _consent_from_error(errs, text)
    if from_error:
        label, error = from_error
        key = _label_core(label).lower()
        if key not in seen:
            seen.add(key)
            problems.append(Problem(
                CONSENT_REQUIRED, field=label, evidence=f"the page requires consent: '{error}'",
                fixable=True, action=f"check:{label}"))

    for el in els:
        if (el.get("tag") or "").lower() != "input" or (el.get("type") or "").lower() != "checkbox":
            continue
        label = _label_core(_el_label(el))
        if not label or MARKETING_RX.search(label) or not CONSENT_RX.search(label):
            continue
        required = bool(el.get("req"))
        unchecked = el.get("checked") is False
        if not (required or (unchecked and forward_disabled)):
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        if forward_disabled:
            forward = _el_text(forward_disabled[0]) or f"#{forward_disabled[0].get('i')}"
            evidence = (f"'{label}' is required consent (snapshot checked={str(bool(el.get('checked'))).lower()}) "
                        f"while the forward control '{forward}' is disabled")
        else:
            evidence = f"'{label}' is required consent and is not checked"
        problems.append(Problem(CONSENT_REQUIRED, field=label, evidence=evidence,
                                fixable=True, action=f"check:{label}"))
    return problems


def _field_required_labels(errs: list[str], text: str) -> list[tuple[str, str, bool]]:
    """(label, exact evidence, came_from_an_error_banner) for every field a validation message names."""
    found: list[tuple[str, str, bool]] = []
    seen: set[str] = set()

    def add(label: str, evidence: str, from_err: bool):
        label = _label_core(label)
        if not label or len(label) < 2:
            return
        key = label.lower()
        if key in seen:
            return
        seen.add(key)
        found.append((label, _clean(evidence, 240), from_err))

    for err in errs:
        for rx in FIELD_REQUIRED_RXS:
            match = rx.search(err)
            if match:
                add(match.group(1), err, True)
        match = FIELD_REQUIRED_ANCHORED.search(err)
        if match and not _NOT_A_FIELD_LABEL_RX.search(match.group(1)):
            add(match.group(1), err, True)
        match = POLITE_REQUIRED_RX.search(err)
        if match and not _NOT_A_FIELD_LABEL_RX.search(match.group(1)):
            add(match.group(1), err, True)

    # Body text: a Workday-style banner lives in text, not always in errs (a company).
    for chunk in re.split(r"(?<=[.!?])\s+", text or ""):
        for rx in FIELD_REQUIRED_RXS:
            match = rx.search(chunk)
            if match:
                add(match.group(1), _sentence(chunk, match.start(), match.end()), False)
        match = FIELD_REQUIRED_ANCHORED.search(chunk)
        if match and not _NOT_A_FIELD_LABEL_RX.search(match.group(1)):
            add(match.group(1), _sentence(chunk, match.start(), match.end()), False)
    return found


def _action_for_field(label: str) -> str:
    """Dropdown-ish labels are selected, everything else is filled (widgets.fill routes pickers anyway)."""
    if re.search(r"\b(?:type|state|country|code|status|source|hear|experience level|qualification|degree)\b", label, re.I):
        return f"select:{label}"
    return f"fill:{label}"


def _country_problems(els: list[dict]) -> list[Problem]:
    problems: list[Problem] = []
    seen: set[str] = set()
    for el in els:
        label = _el_label(el)
        if not label or not COUNTRY_LABEL_RX.search(label) or COUNTRY_EXCLUDE_RX.search(label):
            continue
        shown = _el_shown(el)
        if not shown or NON_COUNTRY_VALUE_RX.match(shown) or INDIA_RX.search(shown):
            continue
        core = _label_core(label)
        key = core.lower()
        if key in seen:
            continue
        seen.add(key)
        problems.append(Problem(
            COUNTRY_MISMATCH, field=core,
            evidence=f"'{label}' shows '{shown}' (must be India; a country field is a fact, never submitted wrong)",
            fixable=True, action=f"select:{core}=India"))
    return problems


def _hidden_consent_control(errs: list[str], text: str, els: list[dict]) -> Problem | None:
    """a company/Oracle: the real native consent checkbox is not in the element list, so it can never be ticked."""
    from_error = _consent_from_error(errs, text)
    if not from_error:
        return None
    has_checkbox = any((e.get("tag") or "").lower() == "input" and (e.get("type") or "").lower() == "checkbox"
                       for e in els)
    if has_checkbox:
        return None
    label, error = from_error
    agree_btn = next((e for e in els if re.match(r"^\s*agree\s*$", _el_text(e), re.I)), None)
    evidence = f"the page asks for consent ('{error}') but no consent checkbox is in the element list"
    if agree_btn:
        evidence += " (only an 'AGREE' button is offered)"
    return Problem(ELEMENT_NOT_ACTIONABLE, field=label, evidence=evidence,
                   fixable=True, action=f"check:{label}")


# --------------------------------------------------------------------------- public API

def diagnose(snapshot, page_url: str = "", job=None) -> list[Problem]:
    """Read one saved page and return every Problem it proves, most decisive first.

    `snapshot` accepts the raw `snap` dict or the whole `data/failures/*.json` record. Pure and
    side-effect free. Returns [Problem(UNKNOWN)] when no reading is supported by the page.
    """
    page = _as_page(snapshot)
    url = _clean(page_url or page.get("url") or "", 300)
    title = _clean(page.get("title") or "", 160)
    text = _clean(page.get("text") or "", 6000)
    els = _els(page)
    errs = _errs(page)

    # 1. A document is not a form (a company apply URL resolved to a PDF asset).
    if DOC_EXT_RX.search(url) or (text or "").lstrip().lower().startswith("%pdf"):
        return [Problem(PDF_OR_NO_FORM, field="",
                        evidence=f"the apply URL is a document, not a form: {url}" if url else "the page content is a PDF document",
                        fixable=False, action="stop:transient")]

    # 2. Closed ONLY with explicit closed wording and no form to fill.
    if not _form_inputs(els):
        closed = _find(CLOSED_RX, errs, text)
        if closed:
            return [Problem(JOB_CLOSED, evidence=closed, fixable=False, action="stop:closed")]

    # 3. Confirmation (same rules as agent._confirmed). Without a recorded final click the wording is
    #    ambiguous (an application-status page can describe an earlier attempt), so it stays UNKNOWN.
    problems: list[Problem] = []
    confirmed, ambiguous = _confirmation(page, url, els, text)
    if confirmed:
        return [confirmed]
    if ambiguous:
        problems.append(ambiguous)
    consumed_errors: set[str] = set()

    # 4. Human-only blockers.
    captcha = _find(CAPTCHA_RX, errs, text)
    if captcha:
        problems.append(Problem(CAPTCHA, evidence=captcha, fixable=False, action="stop:needs_you"))
    human_questions: list[str] = []
    marker = page.get("unanswered_required") or page.get("unanswered")
    if isinstance(marker, str) and marker.strip():
        human_questions.append(_clean(marker, 160))
    elif isinstance(marker, (list, tuple)):
        human_questions.extend(_clean(q, 160) for q in marker if _clean(q, 160))
    match = HUMAN_Q_RX.search(text or "")
    if match:
        human_questions.append(_clean(match.group(1), 160))
    for question in dict.fromkeys(human_questions):
        problems.append(Problem(HUMAN_QUESTION, field=question, evidence=f"unanswered required: {question}",
                                fixable=False, action="stop:needs_you"))

    # 5. Authentication reading, in the order the page evidence supports it.
    verify = _find(VERIFY_RX, errs, text)
    reset_sent = "" if verify else _find(RESET_SENT_RX, errs, text)
    rejected = "" if verify else _find(REJECT_RX, errs, text)
    # A rejection claim needs an authentication context: a password box or a sign-in/forgot control.
    # 'Invalid email address' on an application form is a validation error, not a failed sign-in.
    auth_context = (any((e.get("type") or "").lower() == "password" for e in els)
                    or any(SIGNIN_RX.match(_el_text(e)) for e in els)
                    or "forgot your password" in (text or "").lower())
    if rejected and not auth_context:
        rejected = ""
    signup = None if (verify or reset_sent or rejected) else _signup_wall(url, title, text, els)
    if verify:
        for err in errs:
            if VERIFY_RX.search(err):
                consumed_errors.add(err)
        problems.append(Problem(AUTH_VERIFY_PENDING, evidence=verify, fixable=True, action="open_mail:verify"))
    elif reset_sent:
        for err in errs:
            if RESET_SENT_RX.search(err):
                consumed_errors.add(err)
        problems.append(Problem(RESET_LINK_AVAILABLE, evidence=reset_sent, fixable=True, action="open_mail:reset"))
    elif rejected:
        for err in errs:
            if REJECT_RX.search(err):
                consumed_errors.add(err)
        problems.append(Problem(AUTH_REJECTED, evidence=rejected, fixable=True, action="open_mail:reset"))
    elif signup:
        problems.append(signup)
    else:
        auth = _auth_required(page, url, text, els)
        if auth:
            problems.append(auth)

    # 6. Consent before a disabled forward control (a company).
    consent = _consent_problems(errs, text, els)
    problems.extend(consent)
    if _consent_from_error(errs, text):
        for err in errs:
            if AGREE_ERR_RX.search(err):
                consumed_errors.add(err)

    # 7. Country is a fact (a company / KB-17).
    problems.extend(_country_problems(els))

    # 8. Fields a validation banner names (a company / KB-19).
    field_required = _field_required_labels(errs, text)
    for label, evidence, from_err in field_required:
        problems.append(Problem(FIELD_REQUIRED, field=label, evidence=evidence,
                                fixable=True, action=_action_for_field(label)))
        if from_err:
            consumed_errors.add(evidence)

    # 9. Validation errors we could not attribute to one field still tell us the form is invalid.
    leftover = [e for e in errs if e not in consumed_errors and not TOAST_RX.search(e)]
    if leftover:
        problems.append(Problem(FORM_VALIDATION_ERRORS, field="",
                                evidence="; ".join(leftover), fixable=True, action="retry:form"))

    # 10. Disabled forward controls, and a consent control the snapshot cannot see.
    consent_action = next((p.action for p in consent if p.action), "")
    for el in els:
        if not (el.get("disabled") and FORWARD_RX.match(_el_text(el))):
            continue
        name = _el_text(el) or f"#{el.get('i')} {el.get('tag')}"
        evidence = f"the forward control '{name}' is disabled"
        if consent:
            evidence += " while a required consent box is unresolved"
        problems.append(Problem(ELEMENT_NOT_ACTIONABLE, field=name, evidence=evidence,
                                fixable=bool(consent_action), action=consent_action or "stop:transient"))
    hidden = _hidden_consent_control(errs, text, els)
    if hidden:
        problems.append(hidden)

    # 11. Nothing decisive: say exactly what was seen instead of guessing.
    if not problems:
        counts = f"{len(els)} control(s), {len(_form_inputs(els))} field(s)"
        first_error = f"; errors: {'; '.join(errs)}" if errs else "; no error banner"
        excerpt = _clean(text, 160)
        problems.append(Problem(
            UNKNOWN, field="",
            evidence=(f"no recognised signal: title '{title}', {counts}{first_error}; "
                      f"text: '{excerpt}'" if excerpt else f"no recognised signal: title '{title}', {counts}{first_error}"),
            fixable=False, action="stop:transient"))
    return problems


def _evidence_map(evidence_kinds) -> dict:
    """Normalise the caller's independent evidence: mapping {status_or_kind: detail} or an iterable."""
    if not evidence_kinds:
        return {}
    if isinstance(evidence_kinds, str):
        return {evidence_kinds.strip(): evidence_kinds.strip()}
    if isinstance(evidence_kinds, Mapping):
        return {str(k).strip(): _clean(v, 300) for k, v in evidence_kinds.items()}
    try:
        return {str(k).strip(): str(k).strip() for k in evidence_kinds}
    except TypeError:
        return {}


def _lookup(extra: dict, *names: str) -> str:
    for name in names:
        for variant in (name, name.upper(), name.lower()):
            if extra.get(variant):
                return extra[variant]
    return ""


def _transient_reason(problems: list[Problem]) -> str:
    """Name what the page showed. Never 'page stopped changing', never an invented cause."""
    suffix = " - retried later, not a closure"
    if not problems:
        return "transient: the saved page carried no recognised signal" + suffix
    parts = []
    for problem in problems[:4]:
        label = f"{problem.kind}({problem.field})" if problem.field else problem.kind
        parts.append(f"{label}: {_clean(problem.evidence, 110)}")
    body = _clean("transient page diagnosis: " + " | ".join(parts), 340 - len(suffix))
    return body + suffix


def terminal(diagnosis, evidence_kinds=None) -> tuple[str, str]:
    """ONE rule: a terminal status needs the evidence that justifies it.

    - `applied` only from a CONFIRMED Problem (or independent proof supplied by the caller).
    - `closed` only from explicit closed wording on the page (or an independent closure check).
    - `needs_you` only for a human-only fact: CAPTCHA or an unanswerable required question.
    - everything else is a TRANSIENT failure that names what was seen. It is never 'closed' and
      never a message like 'page stopped changing' (that is the caller's text, not a diagnosis).

    `evidence_kinds` is the caller's independent corroboration, as `{status_or_kind: detail}` or an
    iterable of kind names -- e.g. {"closed": "Workday CXS canApply=false"} or
    {"applied": "receipt 'Thank you for your application' R-01369315"}. It can escalate a page that
    cannot show the outcome; it never weakens a page that proves a failure (that is proof.py's rule).
    """
    problems = _as_problems(diagnosis)
    extra = _evidence_map(evidence_kinds)

    for problem in problems:
        if problem.kind == CONFIRMED:
            return ("applied", _clean(f"page confirmation: {problem.evidence}", 320))
    for problem in problems:
        if problem.kind == JOB_CLOSED:
            return ("closed", _clean(f"page says closed: {problem.evidence}", 320))

    independent = _lookup(extra, "applied", CONFIRMED)
    if independent:
        return ("applied", _clean(f"independent proof: {independent}", 320))
    independent = _lookup(extra, "closed", JOB_CLOSED)
    if independent:
        return ("closed", _clean(f"independent proof: {independent}", 320))

    for problem in problems:
        if problem.kind == CAPTCHA:
            return ("needs_you", _clean(f"CAPTCHA blocks the final step: {problem.evidence}", 320))
        if problem.kind == HUMAN_QUESTION:
            return ("needs_you", _clean(f"needs a human answer: {problem.evidence}", 320))
    independent = _lookup(extra, "needs_you", CAPTCHA, HUMAN_QUESTION)
    if independent:
        return ("needs_you", _clean(f"needs a human decision: {independent}", 320))

    return ("failed", _transient_reason(problems))


def fail_class(diagnosis) -> str | None:
    """The stable `fail_class` key for this diagnosis (one registry: apply/errors.py).

    A Problem kind is page EVIDENCE; a fail_class is what the ops report groups by. The mapping
    lives in `errors.KIND_TO_CLASS`, so adding a kind without a class fails
    tests/test_errors_taxonomy.py instead of silently becoming an unlabelled bucket.
    """
    return errors.for_problems(_as_problems(diagnosis))


def next_actions(diagnosis) -> list[str]:
    """Generic repair steps in priority order: one handler can repair many sites.

    Ordering is the lesson from the corpus:
      consent check before a disabled Next (a company), a named required field before a retry
      (a company), the verification mail before signing in again (a company), and a password reset
      only when the page itself rejected the saved password (a company) -- never merely because a sign-in
      form is visible (a reset is destructive and portals allow one).
    """
    problems = _as_problems(diagnosis)
    kinds = {problem.kind for problem in problems}
    if CONFIRMED in kinds:
        return ["stop:applied"]
    if JOB_CLOSED in kinds:
        return ["stop:closed"]
    if CAPTCHA in kinds or HUMAN_QUESTION in kinds:
        return ["stop:needs_you"]
    if PDF_OR_NO_FORM in kinds:
        return ["stop:transient"]

    steps: list[str] = []

    def add(*actions: str):
        for action in actions:
            action = _clean(action, 120)
            if action and action not in steps:
                steps.append(action)

    # 1. consent before anything that depends on it (a disabled Next).
    for problem in problems:
        if problem.kind == CONSENT_REQUIRED:
            add(problem.action or (f"check:{problem.field}" if problem.field else "check:consent"))

    # 2. mail recovery before signing in again.
    if AUTH_VERIFY_PENDING in kinds:
        add("open_mail:verify", "sign-in")
    if RESET_LINK_AVAILABLE in kinds:
        add("open_mail:reset", "set_password:new", "sign-in")
    if AUTH_REJECTED in kinds:
        # The page proved the saved password was rejected, so a reset is justified (one per company).
        add("open_mail:reset", "set_password:new", "sign-in")

    # 3. country is a fact: never submit while it is wrong.
    for problem in problems:
        if problem.kind == COUNTRY_MISMATCH:
            add(problem.action or (f"select:{problem.field}=India" if problem.field else "select:country=India"))

    # 4. named required fields before retrying the form.
    for problem in problems:
        if problem.kind == FIELD_REQUIRED:
            add(problem.action or (f"fill:{problem.field}" if problem.field else "fill:required"))

    # 5. an account session is needed: sign in first, create only as the fallback.
    if SIGNUP_WALL in kinds:
        add("create_account")
    for problem in problems:
        if problem.kind == AUTH_REQUIRED:
            if problem.fixable:
                email = problem.field if problem.field and problem.field.lower() != "sign in" else "Email Address"
                add(f"fill:{email}", "fill:Password", "sign-in", "create_account")
            else:
                add("reload")

    # 6. form-level validation we could not attribute to one field.
    if FORM_VALIDATION_ERRORS in kinds:
        add("recheck:required_fields")

    # 7. a disabled control with no other explanation: reload and retry it.
    for problem in problems:
        if problem.kind == ELEMENT_NOT_ACTIONABLE:
            if problem.fixable and problem.action:
                add(problem.action)
            elif not any(k in kinds for k in (CONSENT_REQUIRED, FIELD_REQUIRED, FORM_VALIDATION_ERRORS)):
                add("reload")

    if UNKNOWN in kinds:
        add("reload")
    add("retry")
    return steps
