"""Haiku fit scoring in batches. Skills she lacks (facts.not_claimable) lower the score."""
import json
import re

from core import llm, profile

PROMPT = """You score whether a candidate is ELIGIBLE for a job and could credibly do it. Be realistic, not strict: the candidate wants to apply widely.

CANDIDATE: {profile}
SKILLS THE CANDIDATE LACKS (do not count as a match, penalise if mandatory): {lacks}

The candidate IS eligible for (score 55+ unless a mandatory skill is missing): {eligible}
An operations or associate role in the candidate's field is NOT a mismatch just because its title differs.
A contract, fixed-term, temporary or contract-to-hire role is acceptable: never lower the score for that.

For each job give:
 score 0-100 (>=75 the candidate's core work, 55-74 eligible and credible, 35-54 a stretch they could still apply to, <35 not eligible),
 python_or_lacking_mandatory: true if a mandatory requirement is a skill the candidate lacks,
 reason: max 15 words.
Score below 35 only for: a field unrelated to the candidate's background ({unrelated}){years_rule},
people-management of a team, or a mandatory licence/qualification the candidate does not have ({quals}).
Entry-level / junior roles are ACCEPTABLE: do not penalise for being junior or for pay.

JOBS:
{jobs}

Return a JSON list: [{{"id": "...", "score": 0, "python_or_lacking_mandatory": false, "reason": "..."}}]"""


def max_stated_min_years() -> int:
    """facts.yaml preferences.max_stated_min_years: a posting whose stated MINIMUM is this many years or more is skipped (0 = no cap)."""
    try:
        return int(profile.preferences().get("max_stated_min_years", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


def render_prompt(jobs: list[dict]) -> str:
    """The scoring prompt filled ONLY from cv/facts.yaml (preferences + summary + not_claimable)."""
    f = profile.facts()
    pr = f.get("preferences") or {}
    n = max_stated_min_years()
    years_rule = (f", a stated minimum of {n}+ years (the candidate's real total is {her_total_years(f)})" if n else "")
    return PROMPT.format(profile=profile.candidate_summary() or "(no profile recorded)",
                         lacks=", ".join(f.get("not_claimable") or []) or "none recorded",
                         eligible=" ".join(str(pr.get("eligible_roles") or "roles matching the profile above").split()),
                         unrelated=" ".join(str(pr.get("unrelated_fields") or "fields unrelated to the profile").split()),
                         years_rule=years_rule,
                         quals=" ".join(str(pr.get("missing_qualifications") or "none recorded").split()),
                         jobs=json.dumps([_compact(j) for j in jobs], ensure_ascii=False))


def _compact(j: dict) -> dict:
    return {"id": j["id"], "title": j["title"], "company": j["company"], "location": j["location"],
            "jd": (j.get("description") or "")[:900]}


def score_batch(jobs: list[dict]) -> dict:
    out = llm.ask_json(render_prompt(jobs), "score",
                       purpose=f"fit-scoring {len(jobs)} jobs")
    return {r["id"]: r for r in out}


LACKS_RX = re.compile(
    r"\b(?:requires?|required|requiring|mandatory|must[- ]have|needs?)\b[^.]{0,80}\black(?:s|ing)?\b|\black(?:s|ing)?\b[^.]{0,60}\bmandatory\b"
    #  (coordinator, the user's rule "fewer correct applications"): a company 'Salesforce Business Analyst' = 42 with the reason
    # "Salesforce platform experience missing" was applied to. A reason naming a required item as missing / not held caps too.
    r"|\b(?:experience|skills?|knowledge|expertise|qualification|certification|background)\b[^.]{0,20}\bmissing\b"
    r"|\bmissing\b[^.]{0,40}\b(?:experience|skills?|qualification|certification|platform)\b"
    r"|\b(?:she\s+)?(?:does\s+not|doesn'?t)\s+have\b|\bno\s+(?:hands-on\s+|prior\s+|direct\s+)?experience\s+(?:in|with|of)\b", re.I)
OPTIONAL_RX = re.compile(r"not mandatory|non[- ]mandatory|optional|nice[- ]to[- ]have|preferred|a plus\b|bonus", re.I)
NOT_ELIGIBLE = 25   # below every bar in settings.json
_PYTHONISH = re.compile(r"\b(?:python|pandas|numpy|r|pyspark)\b", re.I)
_FILLER = re.compile(r"\b(?:requires?|required|requiring|mandatory|must|have|needs?|skills?|she|lacks?|lacking|missing|does|not|doesn't|no|"
                     r"experience|hands-on|prior|direct|with|of|and|or|both|the|a|is|in|her|list|which|tools?|plus|scope|senior|lead)\b", re.I)
_CEILING = re.compile(r"(?:up\s*to|upto|maximum(?:\s+of)?|max\.?|less\s+than|under|below|not\s+more\s+than)\s+\d+(?:\.\d+)?\s*\+?\s*(?:-\s*\d+\s*)?(?:years?|yrs?)", re.I)
YEARS_SLACK = 1.0   # a stated minimum more than this above her real total is "clearly above hers"


def _only_python_missing(reason: str) -> bool:
    """True when Python (or Python/R) is the ONLY missing item named by the reason."""
    if not re.search(r"python", reason or "", re.I):
        return False
    rest = _FILLER.sub(" ", _PYTHONISH.sub(" ", reason or ""))
    return not re.search(r"[A-Za-z]{3,}", rest)


_MONTHS = {m: n for n, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def her_total_years(facts: dict | None = None) -> float:
    """The user's real total from cv/facts.yaml experience dates (for example Sep 2022 - Jan 2025 + Jan 2021 - Mar 2021 + Oct 2018 - Jul 2020
    = 4.5 years; an end month counts in full). Not a rounded figure from the summary."""
    if facts is None:
        from core import profile
        facts = profile.facts()
    months = 0
    for e in facts.get("experience") or []:
        try:
            sm, sy = str(e["start"]).split()
            em, ey = str(e["end"]).split()
            months += (int(ey) * 12 + _MONTHS[em[:3].lower()]) - (int(sy) * 12 + _MONTHS[sm[:3].lower()]) + 1
        except Exception:  # noqa: BLE001 ('Present' or an unreadable date: not counted, so the total can only be LOWER, never inflated)
            continue
    return round(months / 12, 2)


_ORG_HISTORY = re.compile(r"\b(?:we|our|us|company|firm|serving|clients?|legacy|founded|since|history|heritage|track record of)\b", re.I)
_SOFT = re.compile(r"\b(?:preferred|preferably|ideally|nice[- ]to[- ]have|desirable|desired|a plus|advantage|bonus)\b", re.I)


def stated_minimum_years(description: str):
    """The smallest experience figure the posting REQUIRES of the candidate, or None. Not read as a requirement (review):
    a ceiling ('up to 6 years'), the employer's own history ('our firm has over 50 years of experience', 'with 25 years of experience
    serving banks'; any figure of 15+), and a preferred figure ('Preferred: 6+ years')."""
    from rank import filter as flt
    text = _CEILING.sub(" ", description or "")
    vals = []
    for m in flt.YEARS.finditer(text):
        n = int(m.group(1))
        before, after = text[max(0, m.start() - 60): m.start()], text[m.end(): m.end() + 40]
        ctx = (before + " " + after).lower()
        if "experience" not in ctx and "exp" not in ctx:
            continue
        if n >= 15 or _ORG_HISTORY.search(before[-40:]) or _ORG_HISTORY.search(after[:30]):
            continue
        if _SOFT.search(before[-30:]) or _SOFT.search(after[:25]):
            continue
        vals.append(n)
    return min(vals) if vals else None


def years_cap(sc: int, description: str, total: float | None = None, title: str = "") -> int:
    """Cap below the bar when the posting's stated minimum is clearly above her real total (minimum > total + 1).
    The smallest required figure counts, so a range '3-5 years' reads 3 and is never capped. Returnships are never capped here:
    their experience wording describes the career before the break, and their eligibility is checked by hand (a company ReEntry lesson)."""
    try:
        from discover import returnships
        if returnships.matches(title or "") or returnships.matches((description or "")[:300]):
            return sc
    except Exception:  # noqa: BLE001
        pass
    my = stated_minimum_years(description)
    if my is None:
        return sc
    if total is None:
        total = her_total_years()
    return min(sc, NOT_ELIGIBLE) if my > total + YEARS_SLACK else sc


def capped_score(sc: int, flag: bool, reason: str, text: str) -> int:
    """A score whose own reason says a mandatory qualification/skill is missing must fall BELOW the bar (: a company 'requires CA
    qualification she lacks' = 40 and a company 'SAP S/4HANA/SAC mandatory skills she lacks' = 45 were both applied to, the bar being 35).
    Python-only gaps keep the old rule (applied, ranked lower); Python PLUS anything else missing is capped."""
    low = (text or "").lower()
    only_python = not any(re.search(rx, low) for rx in (r"\bsnowflake\b", r"\bdatabricks\b", r"\bspark\b", r"\bsap\b", r"\bsalesforce\b", r"\bazure\b", r"\baws\b", r"\bgcp\b"))
    says = bool(LACKS_RX.search(reason or "")) and not OPTIONAL_RX.search(reason or "")
    if says and not _only_python_missing(reason):
        return min(sc, NOT_ELIGIBLE)
    if flag:
        return min(sc, 64 if only_python else NOT_ELIGIBLE)
    return sc


def score_all(db, jobs: list[dict], batch: int = 20, log=print) -> int:
    n = 0
    for i in range(0, len(jobs), batch):
        chunk = jobs[i:i + batch]
        try:
            res = score_batch(chunk)
        except llm.UsageLimit:
            log("usage limit hit; stopping scoring (resume later)")
            break
        except Exception as e:  # noqa: BLE001
            log(f"batch failed: {e}")
            continue
        for j in chunk:
            r = res.get(j["id"])
            if not r:
                continue
            sc = int(r.get("score", 0))
            sc = capped_score(sc, bool(r.get("python_or_lacking_mandatory")), r.get("reason", ""),
                              (j.get("description") or "") + " " + (j.get("title") or ""))
            sc = years_cap(sc, j.get("description") or "", title=j.get("title") or "")
            db.c.execute("UPDATE jobs SET score=?, score_reason=? WHERE id=?", (sc, r.get("reason", ""), j["id"]))
            n += 1
        db.c.commit()
        log(f"scored {min(i + batch, len(jobs))}/{len(jobs)}")
    return n
