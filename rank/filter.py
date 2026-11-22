"""Deterministic relevance filter: role family, seniority, location, experience, dealbreakers.

Returnship / career-restart postings are kept when they are plausibly HER kind of
work, even when the title is not one of the analyst families ("Returnship Programme 2026" has no
family word at all). Their marker is the stored `family` value "returnship": it is a plain string in
the existing column (no schema change), appears in the dashboard's existing by-family breakdown, and
rank/score.py is untouched. The real detected family is recomputed deterministically by family(title)
(both title and description are searched), and every other rule (location, experience, title excludes)
still applies unchanged.
"""
import re

from discover.returnships import RETURN_RE

RETURNSHIP_FAMILY = "returnship"

FAMILIES = {
    "data_bi": r"data analyst|business analyst|\bbi\b|business intelligence|power ?bi|tableau|qlik|reporting analyst|\bmis\b|insights? analyst|analytics analyst|data quality|data analytics|reporting (specialist|executive)|dashboard|analyst[- ]data|data reporting",
    "risk_reg": r"risk|regulatory|compliance (analyst|data)|data governance|governance analyst|\baml\b|fraud|credit (analyst|policy)|model (validation|risk)|basel|stress test|financial crime",
    "finance": r"financial analyst|finance analyst|fp&a|fpa\b|business finance|financial planning|treasury|financial reporting|mi reporting|management information|controllership|finance data",
    "etl_sas": r"\bsas\b|informatica|\betl\b|sql developer|database analyst|data warehouse|datawarehouse|data migration",
}


def _custom_families() -> None:
    """facts.yaml preferences.job_families {name: regex} adds role families (the kinds of job you want); with
    preferences.replace_job_families: true they REPLACE the built-in ones above (which target data / BI / risk / finance roles)."""
    try:
        from core import profile
        pr = profile.preferences()
    except Exception:  # noqa: BLE001
        return
    extra = pr.get("job_families") or {}
    if isinstance(extra, dict) and extra:
        if pr.get("replace_job_families"):
            FAMILIES.clear()
        FAMILIES.update({str(k): str(v) for k, v in extra.items() if v})


_custom_families()
FAMILY_RE = {k: re.compile(v, re.I) for k, v in FAMILIES.items()}
GENERIC_ANALYST = re.compile(r"\banalyst\b", re.I)
#  audit of 6,907 rejected Bengaluru/remote listings: "Analytics Consultant" (a company), "KYC Representative", "Senior Accountant",
# "Senior Internal Auditor", "Data Management Consultant", "Billing & Invoicing SME" were dropped only because the title has no word "analyst".
ENTRY_TITLE = re.compile(r"\b(associate|junior|jr\.?|graduate|executive|officer|specialist|coordinator|assistant|fresher|entry[- ]level|trainee analyst|level i|"
                         r"consultant|representative|accountant|auditor|advisor|controller|sme|processor|"
                         #  audit of 20,894 rejected Bengaluru listings on company sites: "Expert - Reg Reporting",
                         # "Compliance Professional", "Specialist, Product Control", "Lending Operations Associate" had no grade word or no domain word.
                         r"expert|professional)\b", re.I)

# A returnship posting is kept only when it is plausibly her kind of work: a data/BI/finance/risk/
# analytics/business word in it, or NO domain word at all (a generic "Returnship Programme 2026").
# A returnship for a clearly different domain is still off_target.
HER_DOMAIN = re.compile(
    r"data|business intelligence|\bbi\b|analytics?|reporting|\bmis\b|insight|dashboard|"
    r"financ|fp&a|bank|treasury|investment|\bcredit\b|risk|regulat|compliance|governance|audit|fraud|\baml\b|\bkyc\b|"
    r"business|\bsql\b|\bsas\b|tableau|power ?bi|\betl\b|informatica|quant|"
    # finance-operations / accounting / reporting vocabulary. The 16,374-listing audit showed Bengaluru roles such as
    # "Associate, Fund Accounting", "Associate, Investor Services", "Associate, Travel & Expense Management" rejected only because no domain word matched.
    r"account(?:ing|s)\b|accounts (?:payable|receivable)|\bgeneral ledger\b|\bgl\b|fund (?:accounting|services|administration|operations)|investor services|"
    r"reconcil|settlement|custody|trade (?:support|operations|processing|capture)|middle office|back office|payroll|billing|revenue assurance|\btax\b|statutory|"
    r"cash (?:management|operations)|order to cash|procure to pay|travel (?:&|and) expense|expense management|client onboarding|reference data|"
    r"(?:finance|financial|banking|investment|treasury|loan|credit|payment|transaction) (?:operations|processing|services|monitoring)|"
    r"product control|\blending\b|loan (?:servicing|setup|operations|processing)|mortgage|collections|securities|capital markets|\boperations\b|operational (?:performance|excellence|risk)", re.I)
OTHER_DOMAIN = re.compile(
    r"mechanical|electrical|electronics|civil|chemical|instrumentation|manufactur|industrial|"
    r"automobile|automotive|aerospace|aviation|nurs|medical|clinical|pharma|healthcare|"
    r"legal|counsel|marketing|brand|sales|recruit|human resources|\bhr\b|supply chain|logistics|procurement|"
    r"construction|software engineer|full[- ]?stack|frontend|front[- ]end|back[- ]end|\bjava\b|\.net|"
    r"embedded|firmware|hardware|network engineer|devops|designer|\bux\b|\bui\b|graphic|teacher|professor|chef|"
    r"engineer|developer|business development|relationship manag|\bbanker\b|transformation|business partner|teradata|datastage|\bsap\b|\bazure\b|\biot\b|"
    r"customer service|security|\btrains?\b|field operations|people (?:operations|experience)|platform|infrastructure|learning|\baws\b|adobe", re.I)

TITLE_EXCLUDE = re.compile(
    r"\b(intern|internship|trainee|(?<!assistant )(?<!associate )(?<!deputy )(?<!asst )(?<!asst\. )manager|director|head|principal|architect|vp|vice president|chief|"
    r"software engineer|sde|developer advocate|data scientist|machine learning|ml engineer|ai engineer|"
    # "Sales Operations Analyst", "Sales MIS Executive" are reporting jobs, not selling; "Data Analyst Lead I", "Senior Lead Analyst" are analyst grades.
    r"sales(?! (?:operations|ops|analyst|analytics|mis|reporting|data|insights?|finance|planning)\b)|recruiter|devops|sre|java|\.net|react|frontend|backend|"
    r"full[- ]?stack|(?<!analyst )lead(?! analyst)|staff|"
    r"data engineer|analytics engineer|qa|tester|testing|security analyst|soc analyst|cyber)\b", re.I)
try:   # facts.yaml preferences.title_exclude_regex REPLACES the built-in exclusions above (tuned for analyst roles: it drops engineers, managers...)
    from core import profile as _profile
    if _profile.preferences().get("title_exclude_regex"):
        TITLE_EXCLUDE = re.compile(str(_profile.preferences()["title_exclude_regex"]), re.I)
except Exception:  # noqa: BLE001
    pass
AVP = re.compile(r"assistant vice president", re.I)

def _home_location_rx() -> str:
    """facts.yaml preferences.home_location_regex (e.g. "bengaluru|bangalore|karnataka"), else the recorded city. Matches nothing when
    neither is recorded (then only remote jobs pass and `python hunt.py doctor` warns)."""
    try:
        from core import profile
        rx = str(profile.preferences().get("home_location_regex") or "").strip()
        return rx or (re.escape(profile.city()) if profile.city() else r"(?!x)x")
    except Exception:  # noqa: BLE001
        return r"(?!x)x"


LOC_OK = re.compile(_home_location_rx(), re.I)
REMOTE = re.compile(r"remote|work from home|wfh|anywhere", re.I)
REMOTE_BAD = re.compile(r"\b(us|usa|u\.s\.|united states|canada|europe|emea|uk|united kingdom|latam|germany|australia|"
                        r"philippines|mexico|brazil|poland|singapore|dubai|uae)\b", re.I)
INDIA_HINT = re.compile(r"india|apac|asia|anywhere|worldwide|global", re.I)

# KB-63: a card found by a REMOTE search is not proof that the posting is remote. LinkedIn's
# "India" remote search returned on-site Pune/Mumbai/Hyderabad jobs, each marked `_remote=True`, and INDIA_HINT
# matched the "India" in "Pune Division, Maharashtra, India" - 147 such rows reached the apply queue (a company).
# So a named non-Bengaluru Indian city/state REFUTES a search-time remote flag; only the posting's own wording
# ("Remote", "Work from home") or a location with no other city (just "India") may stand in for it.
OTHER_CITY = re.compile(
    r"\b(pune|pimpri|chinchwad|mumbai|thane|navi mumbai|new delhi|delhi|gurugram|gurgaon|faridabad|noida|greater noida|"
    r"ghaziabad|hyderabad|secunderabad|chennai|kolkata|howrah|ahmedabad|gandhinagar|jaipur|jodhpur|udaipur|"
    r"kochi|cochin|coimbatore|madurai|indore|bhopal|chandigarh|mohali|panchkula|mysuru|mysore|mangaluru|hubli|"
    r"nagpur|nashik|aurangabad|trivandrum|thiruvananthapuram|bhubaneswar|visakhapatnam|vijayawada|goa|dehradun|"
    r"surat|vadodara|baroda|lucknow|kanpur|patna|ranchi|guwahati|raipur|kozhikode|calicut|trichy|tiruchirappalli)\b", re.I)
OTHER_STATE = re.compile(
    r"\b(maharashtra|telangana|tamil nadu|uttar pradesh|haryana|gujarat|rajasthan|kerala|west bengal|"
    r"madhya pradesh|punjab|andhra pradesh|odisha|uttarakhand|bihar|jharkhand|assam|chhattisgarh)\b", re.I)

PYTHON_MUST = re.compile(r"(must|required|mandatory|strong|proficien\w*|expert\w*|hands[- ]on)[^.]{0,40}\bpython\b|\bpython\b[^.]{0,30}(required|mandatory|must)", re.I)
YEARS = re.compile(r"(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–to]+\s*(\d{1,2})\s*)?(?:\+\s*)?(?:years|yrs)", re.I)


# In Indian banks and GCCs "Manager, Risk Reporting", "Manager - MI Analyst" and "Lead Data Analyst" are individual-contributor grades at 4-8 years.
# They are kept when the title is plainly one of HER four families; the experience filter and the fit score still judge the posting.
# Senior / programme / project / product managers and tech or team leads stay out.
_PEOPLE_OR_SENIOR = re.compile(r"\b(senior|sr\.?|general|program(?:me)?|project|product|delivery|engineering|account|sales|branch|relationship|store|area|regional)\s+"
                               r"(manager|mgr)\b|\b(tech(nical)?|team|practice|squad|chapter)\s+lead\b|\blead\s+(engineer|developer|architect|consultant)\b|"
                               r"manager\s+of\b|people manager", re.I)
_IC_WORD = re.compile(r"\b(manager|lead)\b", re.I)
_GRADE_PREFIX = re.compile(r"^\s*(?:am\s*/\s*)?(?:(?:deputy|assistant)\s+)?manager\s*[,\-–:_/]\s*|^\s*lead\s+(?=\S)", re.I)


#  audit of data/rejected_sample.jsonl (25,052 rows, 8,233 distinct): what the filter still drops is almost all senior, technical or other-domain.
# Two exclusion words fire inside titles that ARE her field: "QA" in "KYC QA Expert - Associate" / "Senior Analyst KYC QA" (quality assurance of KYC
# files - a bank-operations role) and "Sales" in "Reporting Analyst - Liability Sales, Salary & Trust" (a reporting job FOR the sales business).
_QA_IN_HER_FIELD = re.compile(r"\b(?:kyc|aml|cdd|edd|fincrime|financial crime|sanctions?|onboarding|compliance|risk)\b", re.I)
_ANALYST_OF_SALES = re.compile(r"\b(?:reporting|mis|data|business|credit|risk|financial|finance|operations) analyst\b|\bmis (?:analyst|executive|officer)\b", re.I)


def _analyst_role_misread(hit: str, title: str) -> bool:
    """True when the excluded word is part of an analyst title in her own field (see the audit note above)."""
    h = (hit or "").strip().lower()
    if h == "qa":
        return bool(_QA_IN_HER_FIELD.search(title)) and not re.search(r"software|automation|selenium|test", title, re.I)
    if h == "sales":
        return bool(_ANALYST_OF_SALES.search(title)) and not re.search(r"account executive|sales (?:manager|executive|officer|representative)|business development", title, re.I)
    return False


def _ic_title(t: str) -> str:
    """The title with a leading GRADE word removed when it is an individual-contributor grade in her own field: "Manager, Risk Reporting",
    "Manager - MI Analyst", "Lead Data Analyst". A trailing one ("Data Analytics Manager", "FP&A Manager") is a function head and stays excluded."""
    if _PEOPLE_OR_SENIOR.search(t):
        return t
    rest = _GRADE_PREFIX.sub(" ", t, count=1)
    if rest == t or _IC_WORD.search(rest):
        return t
    return rest if (any(rx.search(rest) for rx in FAMILY_RE.values()) or GENERIC_ANALYST.search(rest)) else t


def family(title: str):
    for k, rx in FAMILY_RE.items():
        if rx.search(title):
            return k
    if GENERIC_ANALYST.search(title):
        return "generic"
    # junior / associate titles that do not say "analyst" but are clearly in HER domain
    # (data, reporting, MIS, finance, risk...). The seniority filter and the fit score still apply afterwards.
    if ENTRY_TITLE.search(title) and HER_DOMAIN.search(title) and not OTHER_DOMAIN.search(title):
        return "generic"
    return None


def returnship_family(title: str, description: str = ""):
    """None if this is not a returnship posting; else the real analyst family ('generic' when the
    posting names no domain at all) or 'off_target' when it is a returnship in another domain."""
    desc = (description or "")[:4000]
    if not (RETURN_RE.search(title) or RETURN_RE.search(desc)):
        return None
    fam = family(title)
    if fam:
        return fam
    if OTHER_DOMAIN.search(title):  # "Mechanical Engineering Returnship" is never her work
        return "off_target"
    fam = family(desc)              # "Returnship Programme 2026" + a JD that names the role
    if fam:
        return fam
    if HER_DOMAIN.search(desc[:1500]):
        return "generic"
    if OTHER_DOMAIN.search(desc[:1500]):
        return "off_target"
    return "generic"                # a generic programme posting: considered, never auto-dropped


_REMOTE_NEUTRAL = re.compile(r"remote|work from home|wfh|anywhere|hybrid|flexible|full[- ]?time|part[- ]?time|permanent|contract|only|job|based|"
                             r"location|all areas?|\band\b|\bor\b|\bin\b|\bhq\b", re.I)


def remote_elsewhere(loc: str) -> bool:
    """True when a 'remote' location also names a place that is not Bengaluru / India / worldwide: "San Francisco (Remote)", "Remote - Ireland",
    "London (Remote)". Such a job is remote for residents of THAT place.: REMOTE_BAD only knew a list of country names, so a bare city
    slipped through and six applications went to US/UK roles she cannot take."""
    loc = loc or ""
    if LOC_OK.search(loc) or INDIA_HINT.search(loc):
        return False
    return len(re.sub(r"[^A-Za-z]+", " ", _REMOTE_NEUTRAL.sub(" ", loc)).strip()) >= 2


def location_ok(loc: str, remote_flag: bool = False) -> bool:
    """Bengaluru (or remote-India) only. A remote SEARCH flag cannot rescue a posting that names another
    Indian city or state - see OTHER_CITY/OTHER_STATE (KB-63, Bengaluru is her preference)."""
    loc = loc or ""
    if LOC_OK.search(loc):
        return True
    if REMOTE.search(loc):                     # the posting itself says remote / WFH
        if remote_elsewhere(loc):
            return False
        return not REMOTE_BAD.search(loc) or INDIA_HINT.search(loc) is not None
    if remote_flag and not (OTHER_CITY.search(loc) or OTHER_STATE.search(loc)):
        return not REMOTE_BAD.search(loc) or INDIA_HINT.search(loc) is not None
    return False


def location_conflict(loc: str) -> bool:
    """True when a STORED location names a non-Bengaluru Indian city/state and never mentions Bengaluru or
    remote. The row has lost its discovery-time remote flag, so this is the text-only test used at queue time
    (KB-63): `flt.check(..., skip_location=True)` must not let a Pune row through just because it was once found
    by a remote search."""
    loc = loc or ""
    if LOC_OK.search(loc):
        return False
    if REMOTE.search(loc):
        return remote_elsewhere(loc) and not (OTHER_CITY.search(loc) or OTHER_STATE.search(loc)) or False
    return bool(OTHER_CITY.search(loc) or OTHER_STATE.search(loc))


def min_years(text: str):
    """Smallest 'N years' requirement mentioned near experience wording; None if absent."""
    vals = []
    for m in YEARS.finditer(text or ""):
        ctx = text[max(0, m.start() - 60): m.end() + 40].lower()
        if "experience" in ctx or "exp" in ctx:
            vals.append(int(m.group(1)))
    return min(vals) if vals else None


def _returnship_title(t: str) -> bool:
    from discover import returnships
    return returnships.matches(t)


def _default_max_min_years() -> int:
    """facts.yaml preferences.max_stated_min_years N -> reject a stated minimum of N+ years (i.e. > N-1). 0/unset -> no cap."""
    try:
        from rank.score import max_stated_min_years
        n = max_stated_min_years()
    except Exception:  # noqa: BLE001
        n = 0
    return n - 1 if n > 0 else 99


def check(job: dict, max_min_years: int | None = None, skip_location: bool = False):
    """Returns (family|None, reason_if_rejected|None). skip_location: re-checks after discovery must not redo the location test
    (the stored row has lost the remote flag that the first pass used, which wrongly rejected remote-India jobs)."""
    t = AVP.sub("avp", job["title"])  # AVP is acceptable; plain VP / Vice President is not
    # "PMO Intern (Returnship Program)" at SISA and Keka was thrown out by the
    # word "intern". A returnship is often advertised as an internship or traineeship for returners; those two words do not exclude a returnship.
    tx = re.sub(r"\b(intern(ship)?|trainee)\b", " ", t, flags=re.I) if _returnship_title(t) else t
    m_ex = TITLE_EXCLUDE.search(_ic_title(tx))
    if m_ex and not _analyst_role_misread(m_ex.group(0), tx):
        return None, "title_excluded"
    rf = returnship_family(t, job.get("description", ""))
    if rf == "off_target":
        return None, "off_target"
    fam = RETURNSHIP_FAMILY if rf else family(t)
    if not fam:
        return None, "off_target"
    if not skip_location and not location_ok(job.get("location", ""), job.get("_remote", False)):
        return None, "location"
    desc = job.get("description", "")
    # skip roles whose stated MINIMUM is N+ years (facts.yaml preferences.max_stated_min_years). The reader is rank.score.stated_minimum_years:
    # a range reads its lower end, and ceilings ('up to 6 years'), the employer's own history ('our 50 years of experience') and
    # preferred figures are not minimums. Returnships keep the older 7-year limit (their wording describes the career before the break).
    from rank.score import stated_minimum_years
    if max_min_years is None:
        max_min_years = _default_max_min_years()
    my = stated_minimum_years(desc)
    if my is not None and my > (max(max_min_years, 7) if fam == RETURNSHIP_FAMILY else max_min_years):   # returnships keep the old 7-year limit
        return None, f"experience_{my}y"
    # Python in the JD is NOT a reason to skip. It only lowers the fit score (see rank/score.py).
    return fam, None
