"""Maps a form label to the user's answer (regex rules + answers she saved). Unknown or untrue-risk -> None (job is parked for her)."""
import re
from datetime import datetime

from core import profile
from cv import vocab

# Only genuine terms / privacy / data / accuracy declarations are ticked. Qualification attestations ("I confirm I have 5+ years...") are NOT.
CONSENT = re.compile(r"\b(terms|privacy|data processing|personal data|processing of my|i agree|i accept|read and (agree|understood)|"
                     r"information (provided )?(is|are) (true|accurate|correct|complete)|accurate|cookie)\b", re.I)
MARKETING = re.compile(r"newsletter|marketing|promotional|promotions|job alerts?|send me|keep me|updates? (about|from)|offers|"
                       r"communications? (about|from)|subscribe|talent (community|network)|follow", re.I)
DECLINE = re.compile(r"decline|prefer not|do not wish|not to (say|disclose|answer)|choose not", re.I)
# An explicit consent / agreement statement about processing HER application. Answered Yes (applying is the consent), never a marketing opt-in.
CONSENT_YES_RX = r"\bi (?:consent|agree|accept)\b|\bexplicit consent\b|\bconsent to (?:that|the|this|such)\b|\bread and (?:agree|accept|understood)\b"
FOREIGN = re.compile(r"\b(us|u\.s\.|usa|united states|uk|united kingdom|canada|europe|eu|uae|dubai|singapore|australia|germany|ireland|"
                     r"netherlands|japan|saudi|qatar|new zealand)\b", re.I)
AUTH = re.compile(r"authori[sz]ed|work authori|right to work|sponsor|visa|eligible to work|legally", re.I)
# KB-56: "a place" is never the answer to a yes/no question, and a sentence that merely contains the word "country" is not a
# country field. `answer_for("Are you legally entitled to work in the country where the job is located?")` used to return "India" - the
# bare `country -> India` rule matched the sentence - which on a free-text form would have written a false work-authorisation answer.
YESNO_Q = re.compile(r"^\s*(are|is|am|was|were|do|does|did|have|has|had|will|would|can|could|should|may)\b", re.I)
def _location_values() -> set:
    """The place answers (city / state / country and spellings) recorded in answers.yaml: never an answer to a yes/no question (KB-56)."""
    try:
        A = profile.answers()
    except Exception:  # noqa: BLE001
        return set()
    vals = {A.get("current_city"), A.get("current_state"), A.get("current_country")}
    vals |= set(A.get("city_aliases") or [])
    return {str(v) for v in vals if v}


LOCATION_VALUES = _location_values()
_LOC_CACHE: dict = {}


def _job_location(job_id) -> str:
    """The posting's location from the live DB ('' when unknown). Read-only and cached: answer_for is called per field.
    Used only to tell an India work-authorisation question from a foreign one; a read failure is simply 'unknown'."""
    if not job_id:
        return ""
    if job_id in _LOC_CACHE:
        return _LOC_CACHE[job_id]
    loc = ""
    try:
        from core import db as dbm
        d = dbm.DB()
        row = d.c.execute("SELECT location FROM jobs WHERE id=?", (job_id,)).fetchone()
        loc = (row[0] or "") if row else ""
    except Exception:  # noqa: BLE001 (a lookup must never crash an application)
        loc = ""
    _LOC_CACHE[job_id] = loc
    return loc
YEARS_Q = re.compile(r"experience|years|yrs", re.I)
GENERIC_EXP = re.compile(r"\b(total|overall|relevant|professional|work|industry|similar|related|this role|the field|banking|financial|analytics|"
                         r"data analysis|data research|data analytics|data analyst|reporting|business analysis|bfsi|your career)\b", re.I)
MOTIVATIONAL = re.compile(r"why|motivat|interest(ed)? in|tell us about|about yourself|cover letter|what (excites|attracts)", re.I)
# Questions that are HER decision, never a rule's or an LLM's guess: commercial preferences and skill-specific experience.
# (the user, accuracy first: a submitted form can never be corrected. The old rule answered third-party payroll "Yes"
#  and VBA Macros "4" without a recorded fact; both are gone.)
ASK_ALWAYS = re.compile(r"third[- ]party payroll|contract[- ]to[- ]hire|contract(ual)? (role|position|basis)|"
                        r"quick commerce|\bcall report\b|fr[- ]?y[- ]?9c|\bccar\b|\btic\b|"
                        r"\bvba\b|\bmacros?\b|program coordinator|regulatory reports?|data research|"
                        # personal identifiers (a company: a National ID number is in none of her records). Nobody may invent one - the
                        # job parks with the question, whether it arrives as a drawer question or as a Workday validation banner.
                        r"national id|aadhaar|aadhar|\bpan\s*(?:number|card|no\.?)\b|social security number|\bssn\b|passport number|"
                        r"(ok|open|willing|fine|comfortable).{0,40}(contract|payroll|night|rotational|shift|weekend|travel|interview)|"
                        # "Your Experience - Quality Analyst (BPO) or Customer Support?" (a company): WHICH kind of experience she
                        # has is a fact only she can state; neither option is in her CV, and a model picked her languages bubble instead.
                        r"^\s*(?:your\s+)?(?:work\s+|relevant\s+|prior\s+)?experience\s*[-:–]|"
                        # a SELF-RATING is her own judgement (a company: "rate yourself out of 10 in SQL" was answered '3-4 years', a cached
                        # model answer that was the previous question's chip). Nobody may invent her score.
                        r"\brate yourself\b|\bon a scale of\b|\bout of (?:5|10)\b", re.I)
# Career-level phrases a recorded total/relevant figure CAN answer. Everything else after "experience in/with X" is skill-specific.
GENERIC_YEARS = re.compile(r"^(?:total|overall|professional|work|relevant|industry|domain|financial services|banking|bfsi|"
                           r"analytics|data analysis|data analytics|data analyst)(?:\s+(?:experience|exp))?$", re.I)
# "Which industry experience you are having (i.e Banking, healthcare, Ecommerce, Retail?)" (Naukri/IT consulting) is a
# question about HER experience in sectors - the old rules only recognised "experience in/with X" and filed it as our bug.
INDUSTRY_Q = re.compile(r"\b(?:which|what) (?:industry|industries|domain|sector|verticals?)\b|"
                        r"\b(?:industry|domain|sector)[- ](?:experience|background)\b", re.I)

# "Have you ever been issued a <Company> Employee ID / <Company> email address?" and its cousins (interns, co-ops, contractors,
# former employee, ex-employee). No is truthful for every company EXCEPT one she has worked for, so answer_for checks the
# employer guard (EMPLOYERS below) before this rule can speak. See the comment where the rule is used in build_rules.
FORMER_EMPLOYEE_RX = (
    r"(?:ever\s+)?(?:been\s+)?issued\s+(?:a\s+|an\s+)?[^?]{0,60}?employee\s+(?:id|number|badge)|"
    r"employee\s+id\s+or[^?]{0,50}email|"
    r"(?:were|been|served)\s+(?:an?\s+)?(?:interns?|co-?ops?|contractors?|temporar\w+|consultants?)[^?]{0,60}?\bat\b|"
    r"(?:previously|ever|formerly|before|prior).{0,25}(?:employed|worked|been (?:an )?employee)|previously (?:worked|been employed)|"
    r"former (?:employee|worker)|worked (?:for|at) (?:this|our|the) (?:organi[sz]ation|company|firm)|"
    r"ex-?employee|are you a current .*employee|candidate ?is ?previous ?worker"
)


def employer_words() -> set:
    """Every company on her CV, as plain lowercase words ("example bank", "sample research", "demo consulting"). A former-employee question that
    names one of these is NOT answerable with a blanket No: the user's own history decides (they may apply to a former employer's roles)."""
    out = set()
    try:
        for e in profile.facts().get("experience") or []:
            c = re.sub(r"\b(group|ltd|limited|pvt|private|inc|llc|india)\b\.?", " ", (e.get("company") or "").lower())
            c = re.sub(r"[^a-z0-9]+", " ", c).strip()
            if len(c) >= 3:
                out.add(c)
    except Exception:  # noqa: BLE001
        return set()
    return out


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:60]


def _city_rx(A) -> str:
    """Regex alternation of the recorded city/state spellings ('' when none recorded)."""
    names = [A.get("current_city"), A.get("current_state")] + list(A.get("city_aliases") or [])
    names = sorted({str(n).strip().lower() for n in names if n}, key=len, reverse=True)
    return "|".join(re.escape(n) for n in names)


def build_rules():
    """Form label -> answer. EVERY personal value comes from data/answers.yaml or cv/facts.yaml. A value that is not recorded is
    None, which answer_for reports as unknown: the job parks with the exact question (nothing is ever invented)."""
    A, F = profile.answers(), profile.facts()
    p = F["personal"]

    def g(k):
        return str(A[k]) if A.get(k) not in (None, "") else None

    def yes_if(k, val):
        return val if str(A.get(k) or "").strip().lower() in ("yes", "true") else None

    try:
        dob = datetime.strptime(A["date_of_birth"], "%d/%m/%Y")
    except Exception:  # noqa: BLE001
        dob = None
    try:
        lwd = datetime.strptime(A["last_working_day"], "%d %b %Y")
    except Exception:  # noqa: BLE001
        lwd = None
    city_rx = _city_rx(A)
    edu = (F.get("education") or [{}])[0]
    phone = profile.phone_digits() or None
    github = g("github") or (("https://" + str(p["github"]).removeprefix("https://")) if p.get("github") else None)
    immediate = bool(yes_if("available_immediately", 1))
    rules = [  # order matters: first match wins
        (r"\bgpa\b|\bcgpa\b|overall result|grade point|percentage|marks", "SKIP"),  # never guess a score (scores are not required by default)
        # A CONSENT question is answered Yes whatever else its wording mentions - but never a marketing opt-in (the guard in answer_for).
        # "Where applicable law requires my explicit consent ... as described in the Privacy Notice" was answered with
        # the NOTICE PERIOD ('Immediate') by the bare `notice` rule below, i.e. an unrecorded fact typed into a Yes/No dropdown.
        (CONSENT_YES_RX, "Yes"),
        (r"do you have (a |any )?notice period|serving (a )?notice", g("has_notice_period")),
        (r"available to (start|join|begin)|when are you available|available from", "ASAP" if immediate else g("available_to_join")),
        (r"artificial intelligence|\bai\b.{0,40}(match|screen|assess|tool|process)|(faster|efficient).{0,60}(hiring|recruit)|automated (decision|screen)", "Opt Out"),
    ]
    if city_rx:
        # Yes/NO questions only (KB-56): "Which country are you currently located in?" asks for a PLACE; the old unanchored rule
        # matched 'located in' and answered "No" - a place question answered with a yes/no value.
        rules.append((rf"^\s*(are|do|is)\b.{{0,60}}(residing|living|located|based|staying) (in|at|near)[^?]{{0,40}}({city_rx})|"
                      rf"({city_rx}).{{0,30}}(resident|reside|based|located)", "Yes"))
    rules += [
        # compound "residing in X OR willing to relocate": the willingness half is recorded (answers.yaml willing_to_relocate)
        (r"or willing to relocate|willing to relocate", g("willing_to_relocate")),
        # any OTHER city: the user lives in the recorded city (only when one is recorded)
        (r"^\s*(are|do|is)\b.{0,60}(currently )?(residing|living|located|based|staying) (in|at|near)", "No" if city_rx else None),
        # third-party payroll / contract-to-hire is a COMMERCIAL preference: ASK_ALWAYS routes it to the user (was wrongly "Yes")
        (r"(require|need).{0,30}sponsorship|sponsorship.{0,30}(require|need)", g("requires_sponsorship")),
        (r"(legally |lawfully )?(authori[sz]ed|eligible|permitted|right) to work", g("work_authorization_home") or g("work_authorization_india")),
        # A former-employee question must be asked BEFORE the plain email rule below: a company asks "Have you ever been issued a company
        # Employee ID or a company email address? This includes individuals who were Interns, Co-Ops, Temporary, and Contractors at a company."
        # which matched `e-?mail` and would have answered an email address to a Yes/No radio (four jobs).
        # No is truthful because the CV lists every employer; answer_for SKIPS this rule when the question names one of them.
        (FORMER_EMPLOYEE_RX, "No"),
        # A national ID (PAN / Aadhaar / SSN) is a PERSONAL FACT: it is never invented (two jobs blocked on "National ID
        # number is required"). If recorded in answers.yaml as national_id_number the recorded value is used; otherwise this rule
        # matches with None, which answer_for reports as unknown and the job asks the user the exact question.
        (r"national id|aadhaar|aadhar|\bpan\b(?: number| card)?|tax identification|social security number|\bssn\b|passport number",
         A.get("national_id_number") or None),
        (r"first name|given name|forename", profile.first_name() or None),
        (r"last name|surname|family name", profile.last_name() or None),
        (r"full name|^name\b|your name|candidate name|legal name", p.get("name") or None),
        (r"e-?mail", p.get("email") or None),
        (r"phone device type|device type|phone type", "Mobile"),
        (r"extension|address line ?[23]\b|^address 2|apartment|suite", "SKIP"),  # leave optional extra lines empty
        # knows no director/employee at any company (answers.yaml knows_no_insider), no other citizenship (other_citizenship)
        (r"(relative|family member|related to|friend).{0,40}(work|employ|director|board)|know anyone|do you know (any|someone)|"
         r"referr(ed|al)|employee referral|connected to (a|any) (director|employee)|director.{0,30}(related|relative)", yes_if("knows_no_insider", "No")),
        (r"other citizenship|dual (citizen|national)|another (citizenship|nationality)|second nationality", g("other_citizenship")),
        # common compliance questions: answered No ONLY when the user confirmed none apply (answers.yaml no_compliance_flags: 'Yes')
        (r"(agreement|obligation|non-?compete|non-?solicit|restrictive covenant).{0,80}(restrict|prevent|prohibit|limit)|"
         r"restrict(s|ed)? your ability to work", yes_if("no_compliance_flags", "No")),
        (r"(related to|relative of|family member of|close associate of).{0,40}(government|public) official|politically exposed|\bpep\b",
         yes_if("no_compliance_flags", "No")),
        (r"(18|eighteen) years( of age)? or older|at least 18|over the age of 18|legal age", g("over_18")),
        (r"criminal (record|conviction)|convicted of|pending (charges|criminal)", yes_if("no_compliance_flags", "No")),
        (r"conflict of interest", yes_if("no_compliance_flags", "No")),
        (r"(immediate family|family member|relative|spouse|partner|parent|sibling).{0,120}(government|regulat|state[- ]owned|public (sector|official)|politically)|"
         r"(government|regulat|state[- ]owned).{0,80}(immediate family|family member|relative)", yes_if("no_compliance_flags", "No")),
        (r"sanction|debarred|disqualified|bankruptcy|terminated for cause|dismissed (from|for)|asked to resign|disciplinary", yes_if("no_compliance_flags", "No")),
        (r"(gift|entertainment|donation).{0,40}(government|public official)|anti-?(bribery|corruption)", yes_if("no_compliance_flags", "No")),
        (r"country code|phone code|dial", profile.phone_country_code() or None),
        (r"phone|mobile|contact (no|number)|whatsapp|telephone", phone),
        (r"linkedin", g("linkedin")),
        (r"github|portfolio|website|personal (site|url)|blog", github),
        # education: the highest/latest degree (first entry of facts.yaml education) is the default
        (r"(name of (your )?|which |what is your )?(college|university|institut(e|ion)|school)( name)?\b|alma mater|where did you (study|graduate)",
         g("institution") or edu.get("institution")),
        (r"highest (level of )?(education|qualification|degree)|(level|degree) of education", g("degree_level_option") or g("highest_degree")),
        (r"(field|area|branch) of study|\bmajor\b|specialization|specialisation|stream", g("field_of_study") or edu.get("field")),
        (r"year of (graduation|passing|completion)|graduation (year|date)|passing year", g("graduation_year") or (str(edu["year"]) if edu.get("year") else None)),
        # a CURRENT employer field must not claim a past job as current (: a Lever form did): answers.yaml current_employer_answer
        (r"\b(current|present) (employer|company|organi[sz]ation)\b", g("current_employer_answer")),
        (r"(current|present|last|recent|previous) (employer|company|organi[sz]ation)|employer name", g("last_employer")),
        (r"(current|present|last|recent|previous) (job )?(title|designation|role|position)|^designation", g("last_title")),
        (r"relevant.*(experience|exp)", g("relevant_experience_years")),
        (r"total.*(experience|exp)|years? of (work )?experience|overall experience|experience.*years", g("total_experience_years")),
        # monthly figures come from answers.yaml expected_monthly / current_monthly; a number of notice days is 0 only for an immediate joiner
        (r"(monthly|per month).{0,40}(expected|desired)|(expected|desired).{0,40}(monthly|per month)", g("expected_monthly")),
        (r"(monthly|per month).{0,40}(current|last|present)|(current|last|present).{0,40}(monthly|per month)", g("current_monthly")),
        (r"notice.{0,40}(number|days)|(number|days).{0,40}notice", "0" if immediate else None),
        (r"citizenship status|immigration status|visa status|work permit|status of citizenship", g("citizenship_status")),
        # Only a NOTICE PERIOD question may use the notice period: the bare word `notice` matched "Privacy Notice for Applicants" and typed
        # the notice period into a consent dropdown.
        (r"\bnotice\s*period\b|\bperiod of notice\b|\bhow (much|long) notice\b|\bnotice\s+(?:required|to be served|to serve)", g("notice_period")),
        (r"(expected|desired|asking).*(ctc|salary|compensation|package|pay)|salary expectation", g("expected_ctc_lpa")),
        (r"fixed.*(ctc|salary|pay)", g("last_ctc_fixed_lpa")),
        (r"variable", g("last_ctc_variable_lpa")),
        (r"(current|last|present|previous).*(ctc|salary|compensation|package|pay)", g("last_ctc_lpa")),
        (r"date of birth|\bdob\b|birth ?date", dob),
        (r"nationality|citizenship", g("nationality")),
        # Gender IDENTITY / transgender questions are not the recorded gender: decline (: "same as your sex?" was answered No).
        (r"gender identity|transgender|same as (your )?(sex|gender)", "DECLINE"),
        (r"gender|\bsex\b", g("gender")),
        (r"veteran|disabilit|race|ethnic|hispanic|lgbt|sexual orientation", "DECLINE"),
        (r"pin ?code|postal|zip", g("pincode")),
        (r"current (city|location)|^city|city of residence|^location", g("current_city")),
        (r"\bstate\b|province", g("current_state")),
        # A real country FIELD only (KB-56): a bare `country` matched any sentence, so "Are you legally entitled to work in the country
        # where the job is located?" was answered with a country instead of Yes.
        (r"^\s*(country|country\/region|country of (residence|origin|birth))\b"
         r"|(which|what) country (are|do|did) you|country (where|in which) you (live|reside|are based|are located)", g("current_country")),
        (r"address", g("address")),
        (r"sponsor", g("requires_sponsorship")),
        (r"authori[sz]ed to work|work authori[sz]ation|legally (authori[sz]ed|eligible|entitled)|right to work|eligible to work|"
         r"entitled to work|permitted to work", g("work_authorization_home") or g("work_authorization_india")),
        (r"relocat", g("willing_to_relocate")),
        (r"hybrid|work from office|on-?site|in[- ]office", g("open_to_hybrid")),
        (r"how did you (hear|find)|where did you (hear|find)", g("how_did_you_hear")),
        (r"(career|employment|resume|work) gap|gap in|break in (your )?career|career break", g("career_gap_reason")),
        (r"last working day|last day (of|at)|relieving date", lwd),
        (r"reason.*(leav|chang|quit)|why.*leav", g("reason_for_leaving_last_job")),
        # "Do you have a bachelor's degree or higher qualification?" is a Yes/No question, not a request for the degree's name:
        # answering the degree's name matched no chip and the job was lost. answers.yaml holds_degree.
        (r"\bdo you (have|hold|possess)\b[^?]{0,40}\b(bachelor'?s?|master'?s?|graduate|postgraduate)\b[^?]{0,20}\bdegree\b|"
         r"\bdo you (have|hold|possess)\b[^?]{0,30}\b(a|any) degree\b|degree or (higher|above)|higher qualification", g("holds_degree")),
        (r"currently (employed|working)|are you (currently )?(employed|working)", g("currently_employed")),
        (r"highest (degree|qualification|education)|education level|qualification", g("highest_degree")),
        (r"start date|earliest|when can you (join|start)|joining date|date of joining", "ASAP" if immediate else g("available_to_join")),
        (r"immediate|join (immediately|within)|available to (join|start)", g("available_immediately")),
    ]
    return rules


_RULES = None


def _skill_phrase(low: str) -> str | None:
    """"How many years of experience do you have in VBA Macros?" -> "vba macros"."""
    m = re.search(r"experience[^?]{0,40}?\b(?:in|with|on|using)\s+([^?]+)", low) or \
        re.search(r"(?:years?|yrs?)\s+(?:\w+\s+){0,3}?(?:in|with|on|using)\s+([^?]+)", low)
    if not m:
        return None
    p = re.split(r"\b(?:do you|have you|did you|are you|you have|you had)\b", m.group(1))[0]
    return p.strip(" ,;:.?") or None


def _clean_phrase(p: str) -> str:
    p = re.sub(r"\([^)]*\)", " ", p or "")                  # "dashboards (years)"
    p = re.sub(r"\b(?:years?|yrs?|experience|exp)\b", " ", p, flags=re.I)
    return re.sub(r"[^a-z0-9 ]+", " ", p.lower()).strip()



# skill phrase (regex) -> whole years the CV supports. There is NO built-in table: the figures are personal facts and live in
# data/answers.yaml `skill_years` (see data/answers.example.yaml), used only once `skill_years_confirmed: true` is set.
DEFAULT_SKILL_YEARS: list = []


def skill_years(phrase: str):
    """Whole years for an owned skill, '2' etc., else None. A phrase naming ANY skill she lacks (Python, Snowflake...) is None even if it also names an owned one."""
    low = _clean_phrase(phrase)
    if not low or any(rx.search(low) for rx in vocab.LACKED_RE.values()):
        return None
    # a different ROLE on the same tool is not her experience: she DEVELOPED with Informatica, she never administered it (a company "Informatica Administration")
    if re.search(r"administrat|\badmin\b|architect|\bmdm\b|\bcloud\b|iics|\bidq\b|\bmanager\b|\blead\b|\bdba\b|devops|migration|upgrade|support engineer", low):
        return None
    if not profile.answers().get("skill_years_confirmed"):  # OFF until the user has confirmed the exact figures (answers.yaml: skill_years_confirmed: true)
        return None
    table = profile.answers().get("skill_years") or {}
    rules = [(k, str(v)) for k, v in table.items()] if isinstance(table, dict) and table else DEFAULT_SKILL_YEARS
    hits = [v for rx, v in rules if re.search(rx, low, re.I)]
    return min(hits, key=int) if hits else None   # several owned skills in one question: the most conservative figure

def canonical_question(label: str) -> str:
    """A SHORT, stable form of the portal question: no counters, no doubled spaces, always ends with '?'.
    Identical questions across jobs produce the SAME string, so the dashboard shows one entry and one answer is reused."""
    q = re.sub(r"^\s*(?:q(?:uestion)?\s*)?\d{1,2}\s*[\).:\-]\s*", "", str(label or ""), flags=re.I)
    q = re.sub(r"\s+", " ", q).strip().strip(" :;|.-")
    q = re.sub(r"[\s?*]+$", "", q)  # trailing '?' and the portal's required-marker '*'
    return (q + "?") if q else ""


def needs_her(label: str) -> bool:
    """True when the right answer is the user's decision, not a rule or an LLM guess (skill years, skill experience,
    commercial preference). Reading failures return False: those are OUR bug, not a question for her."""
    low = re.sub(r"^\s*unanswered required:\s*", "", str(label or ""), flags=re.I).lower().strip()
    if not low:
        return False
    if re.match(r"^(?:unreadable|required field|could not read|question text was not legible|no visible)", low):
        return False
    if ASK_ALWAYS.search(low):
        return True
    if INDUSTRY_Q.search(low):
        return True
    if YEARS_Q.search(low):
        phrase = _skill_phrase(low)
        return bool(phrase) and not GENERIC_YEARS.match(_clean_phrase(phrase))
    return bool(re.search(r"\bexperience\b[^?]{0,30}\b(?:in|with|of|using)\b", low))


def source_of(label: str, ans) -> str:
    """Which recorded fact an answer came from (goes in the log line; accuracy is auditable)."""
    low = (label or "").lower()
    if re.search(r"residing|living|located|based|staying", low):
        return f"answers.yaml current_location = '{profile.answers().get('current_location', '')}'"
    if re.search(r"relocat", low):
        return f"answers.yaml willing_to_relocate = '{profile.answers().get('willing_to_relocate', '')}'"
    if AUTH.search(low) and not (FOREIGN.search(low) and "india" not in low):
        return "answers.yaml work_authorization_home"
    if "notice" in low:
        return f"answers.yaml notice_period = '{profile.answers().get('notice_period', '')}'"
    if re.search(r"ctc|salary|compensation|package|pay", low):
        return "answers.yaml current/expected CTC"
    if re.search(r"years|experience", low):
        ph = _skill_phrase(low)
        if ph and not GENERIC_YEARS.match(_clean_phrase(ph)) and skill_years(ph) is not None:   # the log must name the REAL source of a submitted figure
            return f"skill_years (answers.yaml, confirmed by the user) = {skill_years(ph)} years for '{_clean_phrase(ph)[:30]}'"
        return f"answers.yaml total/relevant experience = {profile.total_years_text()} years"
    return "answers_map rule"


def answer_for(label: str, job_id: str | None = None):
    """Returns str | datetime | 'DECLINE' | 'ASAP' | None. None means: do not guess, park the job."""
    global _RULES
    low = (label or "").lower().strip()
    if not low:
        return None
    answers = profile.answers()
    s = slug(label)
    if job_id and f"{s}@{job_id}" in answers:  # motivational answers are saved per job, never reused across companies
        return answers[f"{s}@{job_id}"]
    # an answer the user already gave for THIS question (the dashboard 'Save and retry' writes answers.yaml) always wins,
    # including a skill-years figure: it is her recorded fact now, not a guess (reuse rule).
    canon = slug(canonical_question(label))
    if s in answers:
        return answers[s]
    if canon and canon in answers:
        return answers[canon]
    if YEARS_Q.search(low):
        if any(rx.search(low) for rx in vocab.LACKED_RE.values()):
            return None
        phrase = _skill_phrase(low)
        # NEVER guess a skill-specific figure. Only career-level phrases may use the recorded total.
        if phrase and not GENERIC_YEARS.match(_clean_phrase(phrase)):
            # the user chose "from CV dates": a skill she OWNS gets the whole years her CV supports (data/answers.yaml `skill_years`,
            # editable). Anything not in that table (unowned, unknown, SAP...) is still never guessed.
            yrs = skill_years(phrase)
            return yrs if yrs is not None else None
    # work authorisation for any country other than the user's home country (answers.yaml current_country) is answered truthfully:
    # not authorised there -> "No", needs sponsorship there -> "Yes". Only when the home country is recorded; set
    # `authorized_in_other_countries: true` in answers.yaml to switch this off (the question is then parked or answered from answers.yaml).
    # The posting's own location counts as "foreign" too (KB-56): a question that says only "the country where the job is located"
    # must be answered from the job, not assumed to be the home country.
    home = str(answers.get("current_country") or "").strip().lower()
    if AUTH.search(low) and home and not answers.get("authorized_in_other_countries"):
        loc = _job_location(job_id)
        foreign_job = bool(loc) and home not in loc.lower()
        if (FOREIGN.search(low) or foreign_job) and home not in low:
            return "Yes" if re.search(r"sponsor|visa", low) else "No"
    if _RULES is None:
        _RULES = build_rules()
    names_own_employer = any(n in low for n in employer_words())
    for rx, val in _RULES:
        if not re.search(rx, low):
            continue
        if YESNO_Q.search(low) and val in (LOCATION_VALUES or _location_values()):
            continue   # KB-56: 'India'/'Bengaluru' is an answer to a place question, never to 'Are you…?'
        if rx == FORMER_EMPLOYEE_RX and names_own_employer:
            continue  # the user HAS worked there (a former employer): never a blanket No; let her history answer or park it
        if rx == CONSENT_YES_RX and MARKETING.search(low):
            continue  # "I agree to receive marketing communications" is an OPT-IN nobody authorised: park it, never Yes
        return val
    if not MOTIVATIONAL.search(low) and len(low.split()) >= 3:  # fuzzy reuse only for factual, multi-word questions (one-word labels collide)
        hit = profile.lookup(label, threshold=88)
        if hit:
            return hit[1]
    return None


def optional_filler(label: str):
    """A required field our rules normally leave empty (address line 2, extension...): a true, harmless value so the form can be submitted."""
    low = (label or "").lower()
    A = profile.answers()
    if re.search(r"address line ?[23]|^address 2|apartment|suite|landmark", low):
        return str(A.get("area") or A.get("address_line_2") or "") or None
    if re.search(r"extension", low):
        return "0"
    return None


def fmt_date(d: datetime, fmt_hint: str = "") -> str:
    if "yyyy-mm-dd" in fmt_hint or fmt_hint == "iso":
        return d.strftime("%Y-%m-%d")
    if re.search(r"mm/dd", fmt_hint, re.I):
        return d.strftime("%m/%d/%Y")
    return d.strftime("%d/%m/%Y")
