"""Loads facts (CV truth) and learned answers; fuzzy question matching.

Paths can be overridden with environment variables (used by the test suite, handy for a second profile):
  JOBHUNT_FACTS -> path to facts.yaml (default: cv/facts.yaml)
  JOBHUNT_ANSWERS -> path to answers.yaml (default: data/answers.yaml)
"""
import json
import os
import re
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process, utils

ROOT = Path(__file__).resolve().parent.parent
FACTS = Path(os.environ.get("JOBHUNT_FACTS") or ROOT / "cv" / "facts.yaml")
ANSWERS = Path(os.environ.get("JOBHUNT_ANSWERS") or ROOT / "data" / "answers.yaml")
SETTINGS = ROOT / "data" / "settings.json"


def facts() -> dict:
    return yaml.safe_load(FACTS.read_text(encoding="utf-8"))


def answers() -> dict:
    return yaml.safe_load(ANSWERS.read_text(encoding="utf-8")) or {}


def settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 (missing/broken settings = defaults)
        return {}


def save_answer(key: str, value: str):
    a = answers()
    a[key] = value
    ANSWERS.write_text(yaml.safe_dump(a, allow_unicode=True, sort_keys=False), encoding="utf-8")


def lookup(question: str, threshold: int = 70):
    """Fuzzy match a form question against answer keys. Returns (key, answer) or None."""
    a = answers()
    keys = {k: k.replace("_", " ") for k in a}
    m = process.extractOne(question, keys, scorer=fuzz.token_set_ratio, processor=utils.default_process)
    if m and m[1] >= threshold:
        return m[2], a[m[2]]
    return None


# ---------- safe accessors (never raise: a missing facts file must not break an import) ----------

def _safe_facts() -> dict:
    try:
        return facts() or {}
    except Exception:  # noqa: BLE001
        return {}


def _safe_answers() -> dict:
    try:
        return answers() or {}
    except Exception:  # noqa: BLE001
        return {}


def personal() -> dict:
    return _safe_facts().get("personal") or {}


def full_name() -> str:
    return str(personal().get("name") or "").strip()


def first_name() -> str:
    p = personal()
    return str(p.get("first_name") or (full_name().split() or [""])[0]).strip()


def last_name() -> str:
    p = personal()
    if p.get("last_name"):
        return str(p["last_name"]).strip()
    parts = full_name().split()
    return parts[-1] if len(parts) > 1 else ""


def email() -> str:
    return str(os.environ.get("JOBHUNT_EMAIL") or personal().get("email") or "").strip()


def phone_digits() -> str:
    """The national phone number without country code or separators ('+91-90000 00001' -> '9000000001')."""
    p = personal()
    raw = str(p.get("phone_national") or p.get("phone") or "")
    cc = re.sub(r"\D", "", str(p.get("phone_country_code") or ""))
    d = re.sub(r"\D", "", raw)
    if cc and d.startswith(cc) and len(d) > len(cc) + 6:
        d = d[len(cc):]
    return d


def phone_country_code() -> str:
    cc = str(personal().get("phone_country_code") or "").strip()
    return cc if not cc or cc.startswith("+") else "+" + cc


def city() -> str:
    return str(personal().get("city") or str(personal().get("location") or "").split(",")[0]).strip()


def preferences() -> dict:
    return _safe_facts().get("preferences") or {}


def resume_filename() -> str:
    """'Asha_Example_Resume.pdf' from the recorded name (no personal default)."""
    n = re.sub(r"[^A-Za-z0-9]+", "_", full_name()).strip("_")
    return f"{n or 'Candidate'}_Resume.pdf"


def total_years_text() -> str:
    """The recorded total experience as text ('4'), or '' when it is not recorded."""
    return str(_safe_answers().get("total_experience_years") or "").strip()


def candidate_summary() -> str:
    """One paragraph about the candidate for AI prompts: facts.yaml preferences.candidate_profile, else the CV summary."""
    pr = preferences()
    s = pr.get("candidate_profile") or _safe_facts().get("summary") or ""
    return " ".join(str(s).split())


def answer(key: str) -> str:
    """One recorded answer as text ('' when missing)."""
    v = _safe_answers().get(key)
    return "" if v is None else str(v).strip()


def phone_code_option() -> str:
    """Workday-style country phone code option, e.g. 'India (+91)' ('' unless both country and code are recorded)."""
    c, cc = answer("current_country"), phone_country_code()
    return f"{c} ({cc})" if c and cc else ""


def citizenship_options(nationality_question: bool) -> list:
    """Accepted option texts for a citizenship / nationality picker, from answers.yaml (never the first alphabetical option)."""
    nat, cit, c = answer("nationality"), answer("citizenship_status"), answer("current_country")
    vals = [nat, c, cit] if nationality_question else [cit, f"{c} (Citizen)" if c else "", c]
    return [v for v in dict.fromkeys(vals) if v]


def base_cv_pdf() -> Path:
    """Your own ready-made CV PDF (settings.json `base_cv_pdf`, relative to the project or absolute; default data/resumes/_latest/resume.pdf).
    Used for job-board profile uploads and outreach emails. Per-job CVs are generated from facts.yaml by cv/tailor.py."""
    v = str(settings().get("base_cv_pdf") or "").strip()
    q = Path(v).expanduser() if v else ROOT / "data" / "resumes" / "_latest" / "resume.pdf"
    return q if q.is_absolute() else ROOT / q


def search_queries(default: list, lower: bool = False) -> list:
    """facts.yaml preferences.search_queries (the job titles to search for) replaces the built-in data / BI / risk / finance list.
    Returnship and contract queries are added unless preferences.include_returnships is false."""
    pr = preferences()
    q = pr.get("search_queries")
    if not q:
        return default
    out = [str(x) for x in q if str(x).strip()]
    if pr.get("include_returnships", True):
        from discover import returnships
        out = returnships.extend(out)
    return [x.lower() for x in out] if lower else out
