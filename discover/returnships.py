"""Shared returnship / career-restart vocabulary.

One module so the discovery query builders (boards, linkedin, aggregators, naukri_search, ats_india)
and the relevance filter (rank/filter.py) agree on the same words: a term added here is BOTH searched
for and recognised after discovery.

Hidden / renamed programmes still surface through the plain vocabulary (returnship, return to work,
returner, career break, career restart, re-entry, relaunch, second innings, back to work, women
returning, mom returner) and through the named employer programmes listed in CANONICAL_TERMS below.

"Encore" alone is deliberately NOT matched: it is also an ordinary word / employer name
(e.g. Encore Capital), and tagging a normal analyst job as a returnship would be a false fact.
"""
import re

# Human-readable canonical vocabulary (also asserted in tests/test_returnships.py).
CANONICAL_TERMS = [
    "returnship", "return-to-work", "return to work", "returner", "career break",
    "career restart", "restart programme", "re-entry", "reentry", "relaunch", "encore",
    "second innings", "back to work", "women returning", "mom returner",
    "amazon returnship", "amazon rekindle", "ibm tech re-entry", "tcs rebegin", "infosys restart",
    "wipro wire", "begin again", "cognizant returnship",
    "deloitte encore", "ey reconnect", "pwc return to work", "barclays encore",
    "goldman sachs returnship", "salesforce pathfinder",
    "volvo excelher",
]

# Matches a returnship term inside a title or job description.
RETURN_RE = re.compile(
    r"\breturn-?ships?\b"
    r"|return[- ]?to[- ]?work"
    r"|\breturners?\b"
    r"|\bcareer[- ]?(break|restart|re-?entry|return)\b"
    r"|\brestarts?\b[- ]?(program(me)?|cohort|batch)"
    r"|\bre-?entry\b"
    r"|\brelaunch\b"
    r"|\bsecond innings\b"
    r"|\bback[- ]to[- ]work\b"
    r"|\bwomen returning\b|\breturning women\b|\bwomen returners?\b"
    r"|\bmom[- ]?returners?\b"
    r"|\bcareer comeback\b"
    r"|amazon (returnship|rekindle)|\brekindle\b"
    r"|ibm tech[- ]re-?entry"
    r"|\btcs rebegin\b|\brebegin\b"
    r"|infosys restart"
    r"|wipro w?ire\b|begin again"
    r"|cognizant returnship"
    r"|deloitte encore|barclays encore"
    r"|\bencore\b(?=[^.]{0,40}\b(returnships?|program(me)?|career|cohort|202[0-9])\b)"
    r"|\bey reconnect\b"
    r"|\bpwc return to work\b"
    r"|goldman sachs returnship"
    r"|salesforce pathfinder"
    r"|excelher\b",  # Volvo Group's India women-returner programme (seen on Shine)
    re.I)

# Free-text search queries for sources that accept one (jobspy boards, LinkedIn guest, aggregators,
# Naukri browser search). Location (Bengaluru, India) is passed by each source as before.
RETURNSHIP_QUERIES = [
    "Returnship",
    "Returnship Analyst",
    "Return to Work",
    "Return to Work Analyst",
    "Returner Analyst",
    "Career Restart",
    "Career Break",
    "Second Innings",
]

# Short single-term search strings for company feeds whose search matches titles with OR semantics
# (a company). Long queries would flood those feeds with every analyst job.
RETURNSHIP_SEARCH_STRINGS = [
    "returnship",
    "return to work",
    "career restart",
    "career break",
    "second innings",
]


# .
# A contract is a faster way back in after a career break. These are SEARCHED on every board next to the returnship terms, and a posting that
# says it is a contract goes right behind returnships in the apply queue (apply/runner.keep_sends_flowing).
CONTRACT_QUERIES = ["Contract Data Analyst", "Contract Business Analyst", "Contract Financial Analyst", "Analyst Contract to Hire"]
CONTRACT_RE = re.compile(
    r"\bcontract(?:ual|or)?\b(?! (?:management|manager|administration|negotiation|lifecycle|review|law|compliance|drafting|analyst\b(?! \()))"
    r"|\bc2h\b|\bcth\b|contract[- ]to[- ]hire|\bfixed[- ]term\b|\bftc\b|\btemporary\b|\btemp[- ]to[- ]perm\b|\bfreelanc\w+|"
    r"third[- ]party payroll|on (?:the )?payroll of|\b\d{1,2}[- ]?months?[- ](?:contract|assignment|engagement|project)", re.I)


def is_contract(title, description="") -> bool:
    """A posting that says it is contract / fixed-term / temporary work. The title decides; the description counts only in its opening part,
    where the employment type is stated (deeper text mentions "contracts" for many unrelated reasons)."""
    return bool(CONTRACT_RE.search(title or "") or CONTRACT_RE.search((description or "")[:600]))


def extend(base):
    """base queries + the returnship queries + the contract queries, case-insensitively deduped."""
    out = list(base)
    seen = {q.lower() for q in out}
    for q in RETURNSHIP_QUERIES + CONTRACT_QUERIES:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def matches(text) -> bool:
    return bool(RETURN_RE.search(text or ""))
