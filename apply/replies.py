"""Employer replies to her applications: rejections, closed positions, interview invites, assessments, offers.

.

Read from Gmail over IMAP with several SIMPLE phrase queries (a long OR query silently drops hits there), classified by wording, tied to a job by company +
role words + requisition id (the identity rules of apply/proof.py), and stored in the `replies` table. A job's `outcome` is its latest reply.

Accuracy rules (same spirit as the proof matcher):
  * a conditional sentence is not an outcome: "If you are shortlisted for an interview, we will contact you" sits in thousands of receipts;
  * a reply is tied to ONE job only when the company agrees AND the role (or requisition) singles it out; with several of her applications at one
    company and no role named, it is kept against the company, never guessed onto a job;
  * a reply cannot predate the application it answers.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from apply import proof
from core import db as dbm
from core import imap

# ---- classification ---------------------------------------------------------------------------------------------------------------------------
STRONG_REJECT = re.compile(
    r"regret to inform|(?:we |i )?(?:are |am )?(?:sorry|afraid|unable)[^.]{0,60}(?:not|unable)[^.]{0,40}(?:move|proceed|progress|go|offer|continu)|"
    r"\bnot (?:be )?(?:moving|move|progressing|progress|proceeding|proceed|going|go) forward|will not be (?:moving|progressing|proceeding|taking)|"
    r"decided (?:not to|to not|to (?:move|proceed|go) (?:forward )?with (?:other|another)|to pursue (?:other|another)|to (?:hire|select|go with) (?:other|another))|"
    r"other candidates|(?:not|n't) (?:been )?(?:selected|successful|shortlisted|chosen)|unsuccessful|"
    r"no longer (?:under consideration|being considered|in consideration)|(?:do not|don't|currently do not) have (?:any )?open roles?|"
    r"not (?:a )?(?:match|fit|suitable)|have chosen (?:to|another)|chosen to (?:proceed|move) with|not able to offer|"
    r"not (?:be )?(?:progressing|proceeding) (?:with )?your (?:application|candidacy)|will not be progressing", re.I)
CLOSED = re.compile(
    r"(?:position|job|role|requisition|vacancy|opening|posting)[^.]{0,30}(?:no longer (?:available|open|active)|(?:has been |is |was |have been )?(?:filled|closed|cancel+ed|withdrawn|removed))|"
    r"no longer (?:available|open|accepting)", re.I)
SOFT_REJECT = re.compile(r"unfortunately[^.]{0,140}(?:not (?:been )?(?:selected|successful|shortlisted|able to (?:move|proceed|offer|take)|moving|proceeding)|no longer|unsuccessful|will not|cannot (?:move|proceed|offer|take)|"
                         r"won't|(?:are|am|is) unable to (?:proceed|move|offer|take|continue|progress))|after (?:careful|further) (?:consideration|review)[^.]{0,200}(?:not|unable|other)", re.I)
# 'unfortunately ... we are unable to provide feedback' is a stock line of acknowledgements
BOILERPLATE_UNABLE = re.compile(r"unable to (?:provide|give|offer|share|send|respond|reply|answer)[^.]{0,30}(?:feedback|individual|personal|everyone|each|all)|high volume", re.I)
ATS_SENDER = re.compile(r"myworkday|workday|oraclecloud|oracle\.com|workflow\.(?:mail|email)|greenhouse|lever\.co|smartrecruiters|successfactors|sapsf|icims|ashbyhq|taleo|jobvite|talentkonnect|eightfold|phenom|avature|"
                        r"clinchtalent|workable|recruitee|bamboohr|darwinbox|keka|zohorecruit|ripplehire|joinsmart|teamtailor|pinpointhq|hirevue|cornerstone|brassring|"
                        r"mail\.amazon\.jobs|linkedin\.com", re.I)
OFFER = re.compile(r"pleased to (?:offer|extend)|offer letter|(?:delighted|happy|excited|thrilled) to (?:offer|extend)|extend(?:ing)? (?:you )?(?:an|the) offer|job offer|letter of (?:offer|intent)", re.I)
INTERVIEW = re.compile(
    r"\binterview\b|schedule (?:a|an|your|the) (?:call|meeting|time|chat|discussion|conversation)|(?:invite|invitation|invited)[^.]{0,50}(?:call|meet|chat|discussion|conversation|round)|"
    r"next round|phone screen|(?:speak|talk|chat|meet) with you|share your availability|your availability|recruiter call|shortlisted for", re.I)
ASSESSMENT = re.compile(
    r"\bassessments?\b|coding (?:test|challenge|round)|online (?:test|assessment)|do ?select|hackerrank|codility|aptitude|psychometric|take[- ]home|technical test|"
    r"(?:test|assignment|case study)[^.]{0,50}(?:link|complete|submit|within)|(?:complete|attempt|submit)[^.]{0,40}(?:test|assessment|assignment)", re.I)
# a sentence that only describes what MIGHT happen is not an outcome
CONDITIONAL = re.compile(
    r"^\s*(?:please note|note)?[:,]?\s*(?:if|should|in case|once|when|whether|unless)\b|\bif (?:you|your|we|a|there|the|this)\b|\bshould (?:you|your|we)\b|in the event|"
    r"will (?:contact|reach out|be (?:notified|contacted|informed))|may (?:be )?(?:contact|invite)|selected for (?:an? )?(?:interview|next)|"
    r"shortlisted (?:candidates|applicants)|(?:shortlisted|selected) (?:candidates|applicants) (?:who|that)|only (?:shortlisted|selected)|in due course|as soon as|"
    r"\b(?:might|may|could|can) (?:also )?(?:not|be)\b|reasons why|several reasons", re.I)
# an acknowledgement / notice, not an answer: it can only ever be a receipt
ACK_SUBJECT = re.compile(r"thank you for (?:your interest|applying|your application|submitting)|thanks for applying|application (?:received|submitted)|we(?:'ve| have) received your|"
                         r"has received your application|your application was sent|data privacy|privacy notice|welcome to|verify your|verification code|job alert|jobs? (?:you|for you)|"
                         r"newsletter|digest", re.I)
# senders that are never an employer's answer to one application
NOT_EMPLOYER = re.compile(r"google\.com|googlealerts|naukri\.com|camsonline|axis\.bank|axisbank|nptel|upgrad|nipponindia|openai|quora|medium\.com|glassdoor|talent500|tidycal|"
                          r"reddit|iimjobs|adplist|mycvcreator|micro1|xperteks|"
                          r"jobgether|dataannotation|nextraise|substack|testbook|unstop|internshala|monster|shine\.com|foundit|indeed\.com|ambitionbox|instahyre|wellfound|hirist|"
                          r"jobalerts-noreply|messages-noreply@linkedin|invitations@linkedin|newsletter|unsubscribe|digest@|community", re.I)
UNMATCHED_OK = re.compile(r"\byour (?:[\w&.]+ ){0,3}(?:application|candidacy|candidature|interview)|you (?:applied|have applied|recently applied)|applying (?:to|for|with)|for the (?:position|role) of|"
                          r"application (?:status|update)|status of your|schedule your|confirming your (?:rescheduled )?interview|interview (?:canceled|cancelled|rescheduled|reminder|invitation|with)", re.I)
def _own_rx():
    try:
        from core import profile
        e = profile.email()
    except Exception:  # noqa: BLE001
        e = ""
    return re.compile(re.escape(e) if e else r"(?!x)x", re.I)


OWN = _own_rx()   # the user's own address (cv/facts.yaml personal.email): a mail FROM it is never an employer's reply

KINDS = ("offer", "interview", "assessment", "rejected", "closed")
# when one job has several replies, the LATEST decides, except that an offer is never overwritten by an older-looking rejection of another stage
RANK = {"offer": 5, "interview": 4, "assessment": 3, "rejected": 2, "closed": 1}


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def _unconditional(text: str) -> str:
    return " ".join(s for s in _sentences(text) if not CONDITIONAL.search(s) and not BOILERPLATE_UNABLE.search(s))


def classify(subject: str, body: str) -> str | None:
    """'offer' | 'interview' | 'assessment' | 'rejected' | 'closed' | None (an acknowledgement, an alert or noise)."""
    text = f"{subject}. {body}"
    firm = _unconditional(text)   # receipts say "if you are not selected / shortlisted ..." too: only sentences that STATE something count
    if STRONG_REJECT.search(firm) or (SOFT_REJECT.search(firm) and not CLOSED.search(firm)):
        return "rejected"
    if CLOSED.search(firm):
        return "closed"
    if ACK_SUBJECT.search(subject or ""):
        return None   # 'Thank you for your interest in a company!', 'Thanks for applying': an acknowledgement, whatever else its boilerplate mentions
    if OFFER.search(firm):
        return "offer"
    if ASSESSMENT.search(firm):
        return "assessment"
    if INTERVIEW.search(firm):
        return "interview"
    return None


# ---- matching ---------------------------------------------------------------------------------------------------------------------------------
SENT = ("applied", "unconfirmed", "unverified", "already_applied", "closed", "failed", "needs_you", "skipped")


def _title_words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (title or "").lower()) if w not in proof.STOP_WORDS and w not in proof.GENERIC_TITLE}


NUM_ID = re.compile(r"(?:\bid|job (?:number|id|no\.?)|req(?:uisition)?(?: ?(?:id|no\.?|number))?|jreq|reference)[\s:#()\-]*(?:[a-z]{0,4})(\d{5,10})\b|#\s*(\d{5,10})\b", re.I)


def reply_ids(text: str) -> set[str]:
    return {a or b for a, b in NUM_ID.findall(text or "")}


def job_ids(job: dict) -> set[str]:
    return set(re.findall(r"(?<!\d)(\d{5,10})(?!\d)", f"{job.get('apply_url') or ''} {job.get('url') or ''} {job.get('title') or ''}"))


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _extra(company, head: str) -> str:
    """The company written WITHOUT spaces in the evidence ('JPMorganChase' for 'a company'). Only for names of 6+ characters: 'ey' sits inside
    'they', 'tp' inside 'http' (: every Amazon and a company reply was tied to the a company job)."""
    c = _compact(company)
    return f" {company}" if len(c) >= 6 and c in _compact(head) else ""


def _name(r: dict) -> str:
    """The sender's display name, or '' when the 'name' is just the address (no company is written in 'ecnf.fa.sender@workflow...oraclecloud.com')."""
    f = r.get("from") or ""
    return "" if "@" in f else f


def evidence(r: dict) -> str:
    """Where the COMPANY may be read from. An employer writes from its own name or domain; an application system (Workday, Oracle, Greenhouse, Amazon's
    mailer ...) writes for many employers, so only THEN may the subject and the opening of the mail name the company. A Reddit digest or a job board
    that merely has 'Amazon' in its subject is therefore never an answer from Amazon."""
    sender = f"{r.get('from', '')} {r.get('email', '')}"
    if ATS_SENDER.search(r.get("email") or ""):
        # the display name and the subject only: never the body ('market' / 'intelligence' appear in other companies' mail) and never the ADDRESS
        # (a company status mail is sent from ...oraclecloud.com, which made every one of them look like Oracle's)
        return f"{_name(r)} {r.get('subject', '')}"
    return sender


def company_from_subject(subject: str) -> str:
    """A readable company name for an unmatched reply written by an application system ('Your a company Application for R1568135', 'Equinix - Application Update')."""
    s = subject or ""
    for rx in (r"^(?:re:\s*)?your ([A-Z][\w&.' -]{1,30}?) application", r"^([A-Z][\w&.' -]{1,30}?)\s*[-–:|]\s*(?:application|update)", r"^([A-Z][\w&.' -]{1,30}?) application update",
               r"(?:application|candidacy) (?:with|to|at) ([A-Z][\w&.' -]{1,30}?)(?: for|\b$|[,.!])", r"\bat ([A-Z][\w&.' -]{1,30}?)$", r"^([A-Z][\w&.' -]{1,30}?)\s*[-–|]\s"):
        m = re.search(rx, s, re.I)
        if m and m.group(1).strip(" -")[:1].isupper():
            return m.group(1).strip(" -")
    return ""


def company_from_body(body: str) -> str:
    """'... applying for an opportunity at a company. After further review ...' -> 'a company' (mail from an application system with a generic subject)."""
    m = re.search(r"(?:opportunity|position|role|job|application|interest|applying) (?:at|with|in|to) ([A-Z][\w&.' -]{1,30}?)(?:[.,!(]| for | as | and | - )", (body or "")[:600])
    return m.group(1).strip() if m else ""


def match_job(r: dict, jobs: list[dict]) -> tuple[dict | None, int, bool]:
    """(job, score, ambiguous). `r` has from/email/subject/body/ts.

    The company must agree (proof.match_score with a neutral subject, so a generic subject such as 'Status update' can never veto a match) and must
    be named by the SENDER, the subject or the opening of the mail, never by words deep in the body. Among that company's jobs a requisition number the
    reply names, then the role words, single one out; a job whose own requisition number DIFFERS from the one named is excluded. If several remain
    equal the reply is AMBIGUOUS: the caller keeps it against the company only."""
    body = (r.get("body") or "")[:2500]
    head = evidence(r)
    hay = f"{r.get('subject', '')} {body}".lower()
    r_req = proof.req_ids(hay)
    r_num = reply_ids(f"{r.get('subject', '')} {body}")
    scored = []
    for j in jobs:
        if r.get("ts") and j.get("applied_at") and not str(j["applied_at"]).startswith("2026-01-01"):
            try:
                if r["ts"] < datetime.fromisoformat(j["applied_at"]).timestamp() - 3600:
                    continue   # an answer cannot come before the application
            except ValueError:
                pass
        extra = _extra(j.get("company"), head)   # 'JPMorganChase' names 'a company'
        probe = {"from": _name(r) if ATS_SENDER.search(r.get("email") or "") else r.get("from", ""), "email": "" if ATS_SENDER.search(r.get("email") or "") else r.get("email", ""), "subject": "Thank you for applying", "snippet": f"{head}{extra}"}
        s = proof.match_score(j, probe)
        if s <= 0:
            continue
        jn = job_ids(j)
        if r_num and jn and not (r_num & jn):
            continue   # the reply names another requisition than this job's own
        words = _title_words(j.get("title"))
        overlap = sum(1 for w in words if re.search(r"\b" + re.escape(w) + r"\b", hay))
        req = bool((r_req and (proof.job_req_ids(j) & r_req)) or (r_num and jn and (r_num & jn)))
        scored.append((req, overlap, s, j))
    if not scored:
        return None, 0, False
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    top = scored[0]
    ties = [x for x in scored if (x[0], x[1]) == (top[0], top[1])]
    if len({x[3]["id"] for x in ties}) > 1 and not top[0]:
        return None, top[2], True   # the company agrees, nothing singles one job out
    return top[3], top[2], False


# ---- the next step an employer asks for (deadline, link) -----------------------------------------------------------------------------------------
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_DMY = re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s\-/]+" + _MON + r"[a-z]*[\s,\-/]*(\d{4})?", re.I)
DATE_MDY = re.compile(_MON + r"[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?", re.I)
DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](20\d\d)(?!\d)")
REL = re.compile(r"within\s+(?:the next\s+)?(\d{1,2}|one|two|three|four|five|seven)\s*(hours?|days?|business days?|working days?|weeks?)", re.I)
DEADLINE_CUE = re.compile(r"(?:by|before|until|till|on or before|deadline[^.]{0,20}?|due(?: on| by)?|expires?(?: on)?|valid (?:until|till)|no later than|closes?(?: on)?)\s*[:\-]?\s*$", re.I)
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "seven": 7}
SCHEDULE_LINK = re.compile(r"assess|test|hackerrank|codility|hirevue|schedule|calendly|interview|invite|slot|select|candidate|portal|survey|exam|mettl|do-?select|cocubes|amcat|eightfold|zoom|teams|meet\.google", re.I)
SKIP_LINK = re.compile(r"unsubscribe|privacy|optout|opt-out|preferences|facebook|twitter|instagram|youtube|mailto:|\.(?:png|jpg|gif)(?:\?|$)", re.I)


def _mk_date(y: int, mo: int, d: int) -> str:
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def find_dates(text: str, base_ts: float) -> list[tuple[int, str]]:
    """(position, ISO date) for every date written in the text. A date with no year is read as the next occurrence after the mail's own date."""
    base = datetime.fromtimestamp(base_ts) if base_ts else datetime.now()
    out = []

    def settle(d: int, mo: int, y: str | None) -> str:
        if y:
            return _mk_date(int(y), mo, d)
        iso = _mk_date(base.year, mo, d)
        if iso and datetime.strptime(iso, "%Y-%m-%d") < base.replace(hour=0, minute=0, second=0, microsecond=0):
            iso = _mk_date(base.year + 1, mo, d)
        return iso

    for m in DATE_DMY.finditer(text):
        iso = settle(int(m.group(1)), MONTHS[m.group(2)[:3].lower()], m.group(3))
        if iso:
            out.append((m.start(), iso))
    for m in DATE_MDY.finditer(text):
        iso = settle(int(m.group(2)), MONTHS[m.group(1)[:3].lower()], m.group(3))
        if iso:
            out.append((m.start(), iso))
    for m in DATE_NUM.finditer(text):
        a, b = int(m.group(1)), int(m.group(2))
        iso = _mk_date(int(m.group(3)), b, a) if a <= 31 and b <= 12 else _mk_date(int(m.group(3)), a, b)
        if iso:
            out.append((m.start(), iso))
    return sorted(out)


def find_deadline(text: str, base_ts: float) -> str:
    """The date the employer sets: 'by 12 November', 'within 3 days' (counted from the mail), 'deadline: Nov 12'. '' when none is stated."""
    text = text or ""
    for pos, iso in find_dates(text, base_ts):
        if DEADLINE_CUE.search(text[max(0, pos - 40):pos]):
            return iso
    m = REL.search(text)
    if m:
        n = WORDNUM.get(m.group(1).lower()) or int(m.group(1))
        unit = m.group(2).lower()
        base = datetime.fromtimestamp(base_ts) if base_ts else datetime.now()
        delta = timedelta(hours=n) if unit.startswith("hour") else timedelta(weeks=n) if unit.startswith("week") else timedelta(days=n)
        return (base + delta).strftime("%Y-%m-%d")
    return ""


def pick_link(links: list[str] | None) -> str:
    cand = [u for u in (links or []) if u.lower().startswith("http") and not SKIP_LINK.search(u)]
    for u in cand:
        if SCHEDULE_LINK.search(u):
            return u[:600]
    return ""


NEXT_TEXT = {
    "offer": "Review the offer and reply to the recruiter",
    "interview": "Interview: reply to the invite and confirm a slot",
    "assessment": "Complete the assessment",
    "rejected": "No action - application rejected",
    "closed": "No action - position closed or filled",
}


def next_step(kind: str, subject: str, body: str, base_ts: float = 0.0, links: list[str] | None = None) -> dict:
    """What happens next for a reply: a plain-English step, the deadline/date the mail states, and the link to act on. Only facts written in the mail."""
    text = f"{subject}. {body or ''}"
    deadline = find_deadline(_unconditional(text) or text, base_ts) if kind in ("assessment", "interview", "offer") else ""
    link = pick_link(links) if kind in ("assessment", "interview", "offer") else ""
    step = NEXT_TEXT.get(kind, "")
    if kind == "interview":
        dates = find_dates(text, base_ts)
        if dates and not deadline:
            step = f"Interview on {dates[0][1]}: confirm and prepare"
            deadline = dates[0][1]
    if kind == "assessment" and deadline:
        step = f"Complete the assessment by {deadline}"
    return {"next_step": step, "deadline": deadline, "link": link}


# ---- reading the mailbox -------------------------------------------------------------------------------------------------------------------------
QUERIES = ['unfortunately', '"not moving forward"', '"not be moving forward"', '"regret to inform"', '"other candidates"', 'unsuccessful',
           '"after careful consideration"', '"not selected"', '"no longer available"', '"position has been filled"', '"not shortlisted"',
           '"do not have open roles"', '"decided not to"', '"decided to move forward with"', '"not be proceeding"', '"no longer under consideration"',
           'subject:"status update"', 'subject:"update on your application"', 'subject:"regarding your application"', 'subject:"regarding job application"',
           'subject:interview', 'subject:assessment', '"schedule a call"', '"next steps"', '"technical assessment"', '"online test"', '"offer letter"']


def read_mail(days: int = 60, per_query: int = 150, log=print) -> list[dict]:
    seen, out = set(), []
    for q in QUERIES:
        try:
            msgs = imap.search(f"newer_than:{days}d {q}", limit=per_query)
        except Exception as e:  # noqa: BLE001
            log(f"replies: query {q} failed: {type(e).__name__}")
            continue
        for m in msgs:
            key = (m.get("subject"), m.get("date"), m.get("email"))
            if key in seen:
                continue
            seen.add(key)
            m["ts"] = imap.msg_ts(m)
            out.append(m)
    return out


def msgkey(m: dict) -> str:
    return f"{m.get('email', '')}|{m.get('date', '')}|{(m.get('subject') or '')[:80]}"


def scan(days: int = 60, log=print, mails: list[dict] | None = None, d=None) -> dict:
    """Read, classify, match and store. Returns counts. Safe to run repeatedly (a reply is stored once)."""
    d = d or dbm.DB()
    mails = read_mail(days, log=log) if mails is None else mails
    jobs = d.rows("applied_at IS NOT NULL OR status IN ('applied','unconfirmed','unverified','already_applied') OR email_proof IS NOT NULL")
    by_company: dict[str, list[dict]] = {}
    for j in jobs:
        by_company.setdefault((j["company"] or "").lower(), []).append(j)
    stats = {"mails": len(mails), "stored": 0, "matched": 0, "company_only": 0, "unmatched": 0, "skipped": 0}
    for m in mails:
        if OWN.search(m.get("email") or "") or NOT_EMPLOYER.search(f"{m.get('email', '')} {m.get('from', '')}"):
            stats["skipped"] += 1
            continue
        kind = classify(m.get("subject") or "", m.get("body") or "")
        if not kind:
            stats["skipped"] += 1
            continue
        key = msgkey(m)
        step = next_step(kind, m.get("subject") or "", m.get("body") or "", m.get("ts") or 0.0, m.get("links"))
        if d.c.execute("SELECT 1 FROM replies WHERE msgkey=?", (key,)).fetchone():
            # a reply stored before the next-step columns existed: fill them in from the same mail (the reply itself is never stored twice)
            d.c.execute("UPDATE replies SET next_step=?, deadline=?, link=? WHERE msgkey=? AND next_step IS NULL", (step["next_step"], step["deadline"], step["link"], key))
            row = d.c.execute("SELECT job_id FROM replies WHERE msgkey=?", (key,)).fetchone()
            if row and not row[0]:   # a company-only reply: re-read the company name with the current rules (a recruiter's name is not a company)
                d.c.execute("UPDATE replies SET company=? WHERE msgkey=?", (_company_of(m, jobs) or display_company(m), key))
            continue
        job, score, ambiguous = match_job(m, jobs)
        company = (job["company"] if job else None) or _company_of(m, jobs)
        if not job and not company:
            # not one of her jobs: keep it only when it plainly answers an application (otherwise newsletters and recruiter spam fill the page)
            if not UNMATCHED_OK.search(f"{m.get('subject', '')} {(m.get('body') or '')[:800]}"):
                stats["skipped"] += 1
                continue
            company = display_company(m)
        snippet = (m.get("snippet") or "")[:300]
        when = datetime.fromtimestamp(m["ts"]).isoformat(timespec="seconds") if m.get("ts") else ""
        d.c.execute("INSERT OR IGNORE INTO replies(msgkey,job_id,company,kind,subject,sender,ts,received_at,snippet,score,created_at,next_step,deadline,link) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (key, job["id"] if job else None, company or (m.get("from") or ""), kind, (m.get("subject") or "")[:200], f"{m.get('from', '')} <{m.get('email', '')}>"[:160],
                     m.get("ts") or 0, when, snippet, score, dbm.now(), step["next_step"], step["deadline"], step["link"]))
        stats["stored"] += 1
        stats["matched" if job else ("company_only" if company and ambiguous else "unmatched")] += 1
    apply_outcomes(d)
    return stats


GENERIC_SENDER = re.compile(r"no-?reply|notification|workday|do ?not|careers?$|recruit|talent|hr$|human resources|@", re.I)


BAD_NAME = re.compile(r"interview|reminder|update|application|status|schedul|confirm|cancel|action|required|thank|follow|walk|drive|job|offer|assessment|test|recruit|notification|welcome", re.I)


def display_company(m: dict) -> str:
    """A readable company for a reply that matches none of her jobs: the sender's display name without 'Human Resources / Interviews / Careers',
    else the company the subject names, else the domain."""
    paren = re.search(r"\(([A-Za-z][\w&.\- ]{1,38}?)\s*(?:,|\))", m.get("from") or "")   # "Deepthi NS (a company, consultant)": the company is in the brackets
    if paren and not BAD_NAME.search(paren.group(1)):
        return paren.group(1).strip()
    name = re.sub(r"\s*(?:human resources|interviews?|careers?|recruiting|recruitment|talent acquisition|hr|team|notifications?)\s*$", "", (m.get("from") or "").strip(), flags=re.I).strip()
    if name and not GENERIC_SENDER.search(name) and not BAD_NAME.search(name) and len(name) <= 40:
        return name
    email = m.get("email") or ""
    dom = [x for x in email.split("@")[-1].split(".") if x not in ("com", "co", "in", "org", "net", "mail", "email", "notifications", "myworkday", "workflow", "careers", "jobs")]
    ALIAS = {"jpmchase": "JPMorgan Chase", "jpmorganchase": "JPMorgan Chase"}
    if dom and not ATS_SENDER.search(email) and not re.search(r"gmail|yahoo|outlook|hotmail|proton", email, re.I) \
            and not re.search(r"talent|culture|career|recruit|^hr|jobs|noreply|donotreply|notify|people|hire|apply|mail", dom[0], re.I):
        return ALIAS.get(dom[0].lower(), dom[0].title())   # the employer's own domain
    for named in (company_from_subject(m.get("subject") or ""), company_from_body(m.get("body") or "")):
        if named and not BAD_NAME.search(named):
            return named
    return (dom[0] if dom else name or "unknown").title()


def _company_of(m: dict, jobs: list[dict]) -> str | None:
    """The company a reply names when the role does not single out one job (ambiguous) - from the same identity rules."""
    head = evidence(m)
    best = None
    for j in jobs:
        extra = _extra(j.get("company"), head)
        probe = {"from": _name(m) if ATS_SENDER.search(m.get("email") or "") else m.get("from", ""), "email": "" if ATS_SENDER.search(m.get("email") or "") else m.get("email", ""), "subject": "Thank you for applying", "snippet": f"{head}{extra}"}
        s = proof.match_score(j, probe)
        if s > 0 and (best is None or s > best[0]):
            best = (s, j["company"])
    return best[1] if best else None


def apply_outcomes(d=None) -> int:
    """Set jobs.outcome / outcome_at from the LATEST reply of each job (an offer outranks everything that follows it)."""
    d = d or dbm.DB()
    rows = d.c.execute("SELECT job_id, kind, ts, received_at FROM replies WHERE job_id IS NOT NULL ORDER BY ts").fetchall()
    best: dict[str, tuple] = {}
    for r in rows:
        cur = best.get(r["job_id"])
        if cur is None or r["kind"] == "offer" or (cur[0] != "offer" and r["ts"] >= cur[2]):
            best[r["job_id"]] = (r["kind"], r["received_at"], r["ts"])
    n = 0
    for jid, (kind, at, _ts) in best.items():
        d.c.execute("UPDATE jobs SET outcome=?, outcome_at=? WHERE id=? AND (coalesce(outcome,'')<>? OR coalesce(outcome_at,'')<>?)", (kind, at, jid, kind, at))
        n += 1
    return n


def summary(d=None, since_days: int | None = None) -> dict:
    """Counts for the dashboard: replies by kind, jobs by outcome, applications still waiting, and the response rate."""
    d = d or dbm.DB()
    kinds = {k: 0 for k in KINDS}
    for r in d.c.execute("SELECT outcome, COUNT(*) n FROM jobs WHERE outcome IS NOT NULL GROUP BY outcome"):
        if r["outcome"] in kinds:
            kinds[r["outcome"]] = r["n"]
    sent = d.c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at IS NOT NULL AND applied_at NOT LIKE '2026-01-01%'").fetchone()[0]
    answered = d.c.execute("SELECT COUNT(*) FROM jobs WHERE outcome IS NOT NULL AND status IN ('applied','unconfirmed')").fetchone()[0]
    company_only = d.c.execute("SELECT COUNT(*) FROM replies WHERE job_id IS NULL").fetchone()[0]
    return {"by_outcome": kinds, "sent": sent, "answered": answered, "waiting": max(0, sent - answered), "company_only": company_only,
            "response_rate": round(100 * answered / sent) if sent else 0}


def recent(d=None, limit: int = 60) -> list[dict]:
    """Newest replies with the job they belong to (or the company only)."""
    d = d or dbm.DB()
    rows = d.c.execute("SELECT r.kind, r.subject, r.sender, r.received_at, r.snippet, r.company, r.job_id, j.title, j.status, j.apply_url, j.url "
                       "FROM replies r LEFT JOIN jobs j ON j.id = r.job_id ORDER BY r.ts DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def outcomes_by_job(d=None) -> dict[str, dict]:
    d = d or dbm.DB()
    return {r["id"]: {"kind": r["outcome"], "at": r["outcome_at"]} for r in d.c.execute("SELECT id, outcome, outcome_at FROM jobs WHERE outcome IS NOT NULL")}


if __name__ == "__main__":
    import sys
    print(scan(int(sys.argv[1]) if len(sys.argv) > 1 else 60))
