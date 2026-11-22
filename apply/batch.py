"""One call per PAGE, not per field.

The navigator already asks the model once per page snapshot. This module resolves what her recorded answers decide BEFORE the model
is asked, so the model only has to work the unresolved fields:

  prefill(snap, job_id) -> (actions, resolved element numbers): fills of EMPTY short plain inputs / native selects whose label is
                                on the ALLOW-LIST below and which `answers_map.answer_for` answers with a definite short string.
  annotate(snap, resolved) -> the snapshot shown to the model: resolved elements carry `rule_answer` ("do not touch").
  merge(prefill, model_acts, resolved) -> prefill first, then the model's actions minus anything aimed at a resolved field.

Review (other session): answer_for's regexes are unanchored and were written for SHORT field labels. Run on a long question
they answer wrongly ('Please state why you want to join us' -> 'Karnataka', 'Describe a major project' -> 'Finance', 'CGPA' -> 'SKIP',
'What are you currently working on?' -> 'No'). So a rule answer is used only for an allow-listed short label, never a textarea,
never a sentinel (SKIP/DECLINE/ASAP/Opt Out), never a value already shown (a stuck page keeps every value visible to the model).

Prefill actions carry `_rule: True`: `agent._storable` never puts them in a recipe (KB-83: a route, never an answer).
Every accuracy gate stays where it was: question_mismatches, education_date_wrong, identity_mismatches, final-submit checks.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "nav_calls.jsonl"

_TEXT_TYPES = {"", "text", "number", "select-one", "select"}   # never a textarea: free text is the model's / the park rules' job
_FIELD_SHAPES = re.compile(
    r"^(?:(?:current|present|total|overall|official|serving)\s+)*"
    r"(?:notice\s+period|current\s+(?:city|location)|city|current\s+country|country|willing\s+to\s+relocate|nationality)$"
    r"|^(?:total\s+(?:work\s+)?experience|years\s+of\s+(?:total\s+)?experience)(?:\s*\((?:in\s+)?years?\))?$", re.I)
# CTC / salary: ONLY when the label itself names LPA/lakhs (then her LPA figure is the right unit). 'Current CTC (INR)' wants rupees, 'Current
# CTC' has no unit, 'Expected Salary' may be a USD/EUR form - those stay with the model (agent._ctc_fill infers the scale) or park.
_CTC_LPA = re.compile(r"^(?:current|present|expected)\s+(?:ctc|salary)\s*\((?:in\s+)?(?:lpa|lakhs?)\)$", re.I)
_SECTION = re.compile(r"experience|employ|education|school|universit|degree|college", re.I)
_SENTINELS = {"SKIP", "DECLINE", "ASAP", "OPT OUT", "NONE", "N/A"}
_NEVER_LABEL = re.compile(r"password|passcode|captcha|otp|verification|first name|last name|full name|e-?mail|phone|mobile|"
                          r"date of birth|\bdob\b|address|zip|postal|pin ?code|linkedin|salutation|title", re.I)
_PLACEHOLDER = re.compile(r"^(?:select|choose|please select|--+|)\s*(?:one|an option)?\.*$", re.I)


def _clean(label: str) -> str:
    return re.sub(r"\s*\*+\s*$", "", (label or "").strip())


def prefill(snap: dict, job_id: str | None = None) -> tuple[list, set]:
    """Actions for the empty fields her record decides. Returns ([], set) when nothing is certain."""
    from apply import answers_map
    acts, resolved = [], set()
    seen = {}
    for e in snap.get("els") or []:
        k = _clean(e.get("group") or e.get("label") or "").lower()
        seen[k] = seen.get(k, 0) + 1
    for e in snap.get("els") or []:
        if e.get("tag") not in ("input", "select") or e.get("i") is None:
            continue
        # repeating Work Experience / Education blocks ask City and Country PER employer / school: not her home city (review)
        if e.get("row") or seen.get(_clean(e.get("group") or e.get("label") or "").lower(), 0) > 1                 or _SECTION.search(str(e.get("rowctx") or "") + " " + str(e.get("group") or "")):
            continue
        typ = str(e.get("type") or "")
        if typ not in _TEXT_TYPES or e.get("disabled") or e.get("covered") or e.get("invalid"):
            continue
        shown = str(e.get("value") or "").strip()
        if shown and not _PLACEHOLDER.match(shown):
            continue
        lab = _clean(e.get("group") or e.get("label") or "")
        if not lab or len(lab.split()) > 6 or "?" in lab or _NEVER_LABEL.search(lab) or not (_FIELD_SHAPES.match(lab) or _CTC_LPA.match(lab)):
            continue
        if _CTC_LPA.match(lab) and "india" not in (answers_map._job_location(job_id) or "india").lower():
            continue   # a foreign posting's salary box is not in her LPA
        try:
            ans = answers_map.answer_for(lab, job_id)
        except Exception:  # noqa: BLE001 (a rule must never break the page)
            continue
        if not isinstance(ans, str) or not ans.strip() or ans.strip().upper() in _SENTINELS or len(ans.strip()) > 60:
            continue
        ans = ans.strip()
        if typ == "number" and not re.fullmatch(r"\d+(?:\.\d+)?", ans):
            continue
        if e.get("tag") == "select":
            opts = [str(o).strip() for o in (e.get("options") or [])]
            hit = next((o for o in opts if o.lower() == ans.lower()), None)
            if not hit:
                continue   # a list that does not contain her answer verbatim is the model's/park rules' problem, never a fuzzy pick (KB-57)
            acts.append({"do": "select", "i": e["i"], "value": hit, "_rule": True})
        else:
            acts.append({"do": "fill", "i": e["i"], "value": ans, "_rule": True})
        resolved.add(e["i"])
    return acts, resolved


def annotate(snap: dict, resolved: set, trim: bool = False) -> dict:
    """The page as the model sees it. `trim` is accepted for callers but never hides a value: a stuck page needs every value visible."""
    out = dict(snap)
    out["els"] = [dict(e, rule_answer="the tool fills this from her recorded answers - do not touch") if e.get("i") in resolved else e
                  for e in snap.get("els") or []]
    return out


def merge(pre: list, model_acts: list, resolved: set) -> list:
    keep = [a for a in model_acts if not (a.get("i") in resolved and a.get("do") in ("fill", "select", "check", "uncheck", "click"))]
    return list(pre) + keep


def log_call(job_id, host: str, step: int, sig: str, n_els: int, n_prefilled: int, repeat: int, replayed: bool, tokens_hint: int = 0) -> None:
    """One line per model call: calls per application and per page signature are measured from this file, not guessed."""
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 1), "job": job_id, "host": host, "step": step, "sig": sig, "els": n_els,
                                "prefilled": n_prefilled, "repeat": repeat, "replayed": replayed, "chars": tokens_hint}) + "\n")
    except Exception:  # noqa: BLE001
        pass
