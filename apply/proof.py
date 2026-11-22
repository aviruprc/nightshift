"""Email proof: read application-receipt emails from Gmail (signed-in tab) and match them to our jobs.
A match marks the job email-confirmed; an 'unconfirmed' job with a receipt becomes 'applied'. Uses its own profile copy so it never
fights the appliers for the browser profile."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "data" / "browser_profile_proof"
RECEIPT = re.compile(r"application (has been )?(received|submitted|sent|confirmation|complete)|thank(s| you) for (applying|your (application|interest))|"
                     r"we('ve| have) received your application|your .{0,60}application|application\b.{0,40}(\bjr?-?\d+|\br\d{4,}|\(open\)|status)|"
                     r"successfully (applied|submitted)|application update|thank you for applying|applied (to|for)|you applied", re.I)
ATS_SENDERS = re.compile(r"myworkday\.com|workday\.com|oraclecloud|icims|greenhouse|lever\.co|smartrecruiters|successfactors|taleo|jobvite|ashbyhq|"
                         r"workable|talentbrew|phenom|eightfold|ripplehire|careers|talent|recruit|hiring|jobs@|noreply|no-reply", re.I)
STOP_WORDS = {"inc", "ltd", "llc", "pvt", "private", "limited", "technologies", "technology", "solutions", "services", "india", "global",
              "group", "the", "and", "company", "corporation", "corp", "software", "systems", "consulting", "bank", "centre", "center", "of",
              "care", "medical", "health", "healthcare", "capital", "financial", "digital", "data", "analytics", "business", "careers", "career",
              "talent", "acquisition", "recruiting", "jobs", "hiring", "international", "enterprise", "partners", "labs", "global"}
GENERIC_TITLE = {"analyst", "senior", "associate", "specialist", "manager", "engineer", "developer", "officer", "executive", "consultant", "assistant",
                 "reporting", "analytics", "operations", "finance", "financial", "regulatory", "business", "intelligence", "junior", "principal"}
ROWS_JS ="""() => [...document.querySelectorAll('tr.zA')].slice(0, 80).map(r => ({
    from: ((r.querySelector('.yW span[name], .yP, .zF') || {}).getAttribute ? (r.querySelector('.yW span[name], .yP, .zF').getAttribute('name') || r.querySelector('.yW span[name], .yP, .zF').innerText) : ''),
    email: ((r.querySelector('.yW span[email], .zF') || {}).getAttribute ? (r.querySelector('.yW span[email], .zF').getAttribute('email') || '') : ''),
    subject: (r.querySelector('.bog') || {}).innerText || '', snippet: (r.querySelector('.y2') || {}).innerText || '', text: r.innerText.slice(0, 300) }))"""


STRICT_FROM = "2026-11-04T16:40:00"  # when the confirmation check was tightened; earlier page proofs are not auto-promoted
NOISE = re.compile(r"job alert|new jobs?|jobs? for you|recommended|you may be interested|is hiring|people are viewing", re.I)


def is_receipt(r: dict) -> bool:
    """An application receipt (ATS 'thank you for applying', LinkedIn 'your application was sent'), not a job alert, recommendation,
    account-verification code or a Naukri 'you applied for 4 jobs' digest (which names no single job)."""
    subj = r.get("subject", "") or ""
    return bool((RECEIPT.search(f"{subj} {r.get('snippet', '')}") or re.search(r"your application was sent", subj, re.I))
                and not NOISE.search(subj) and not VERIFICATION.search(subj) and not NAUKRI_SUMMARY.search(subj))


REQ_ID = re.compile(r"(?<![a-z0-9])(?:r|jr|req|requisition)[-_ /]?(\d{4,})(?!\d)", re.I)
# Words that describe the ACT of applying, not the role: they never prove which job a receipt belongs to.
EMAIL_WORDS = {"thank", "thanks", "applying", "applied", "apply", "application", "applications", "your", "recent", "careers", "career", "team", "human",
               "resources", "notification", "received", "submitted", "confirmation", "confirming", "interest", "update", "open", "successfully", "dear",
               "hello", "regarding", "position", "role", "opportunity", "process", "jobs", "job", "workday", "portal", "account", "with", "from", "for",
               "and", "the", "you", "our", "this", "that", "have", "has", "was", "were", "will", "been", "see", "what", "results", "result", "code",
               "digit", "digits", "otp", "verification", "verify", "action", "needed", "online", "please", "click", "link", "below", "more", "information",
               "details", "regards", "employees", "their", "they", "yourself"}
try:   # the user's own name appears in every receipt ("Dear <name>"): it is never evidence of WHICH job a receipt belongs to
    from core import profile as _profile
    EMAIL_WORDS |= {w.lower() for w in _profile.full_name().split() if len(w) >= 3}
except Exception:  # noqa: BLE001
    pass
VERIFICATION = re.compile(r"verification code|one[- ]time (code|password)|\botp\b|\b\d[- ]?digit (code|pin)\b|security code", re.I)
NAUKRI_SUMMARY = re.compile(r"you applied for \d+ jobs?", re.I)


def req_ids(text: str) -> set[str]:
    """Requisition ids that a receipt (or job URL/title) names explicitly: Workday 'R161076', '..._R158179' (underscore is not a word boundary), iCIMS 'JR123456'."""
    return set(REQ_ID.findall(text or ""))


def job_req_ids(job: dict) -> set[str]:
    text = " ".join(str(job.get(k) or "") for k in ("apply_url", "url", "title"))
    ids = req_ids(text)
    # Workday/Ecolab-style ids are glued to a prefix in the URL ('...EUSR00298886EXTERNAL...'): no left word boundary, but an R + 5+ digits is specific
    for u in (job.get("apply_url"), job.get("url")):
        ids |= set(re.findall(r"(?i)r(\d{5,})", u or ""))
    return ids


WEAK_WORDS = {"express", "global", "digital", "capital", "solutions", "services", "systems", "group", "chain", "supply"}
# Ordinary English is not identity. An audit of all 56 receipts against all 2688 recent jobs found 7 pairs scoring > 0 on
# nothing but everyday words: 'Its All About People (ITAAP)' was matched by a company receipt through 'about'/'people', 'Applied
# Materials India' by a company receipt through 'applied', 'NEXT Ventures' through 'next', 'Team Ace HR LLP' through 'team', 'Excellent
# Opportunity Placements' through 'opportunity'. None had been credited (those jobs were not awaiting proof), but each could have been.
COMMON_COMPANY_WORDS = {"about", "people", "team", "next", "opportunity", "applied", "apply", "application", "all", "its", "first", "new",
                        "great", "excellent", "smart", "future", "star", "prime", "value", "one", "true", "bright", "care", "work", "works",
                        "house", "online", "direct", "open", "talent", "careers", "career", "recruit", "recruiting", "jobs", "hiring",
                        "staffing", "placement", "placements", "employer", "employment", "resource", "resources", "people's", "support", "opportunities", "careers", "positions", "intelligence", "analytics", "markets", "market", "research", "partners", "financial", "international", "insights"}


def _strong_token(tok: str, company: str) -> bool:
    """A company token precise enough to identify the company on its own: 7+ letters ('northern', 'mobineers') or an acronym ('DTCC', 'a company').
    Common business words ('express' in FedEx Express vs a company) are never strong on their own."""
    acronyms = {a.lower() for a in re.findall(r"\b[A-Z0-9]{2,}\b", company or "") if not a.isdigit() and a.lower() not in SHORT_NOT_IDENTITY}   # 2-letter names too (LG, a company):
    return tok in acronyms or (len(tok) >= 7 and tok not in WEAK_WORDS and tok not in COMMON_COMPANY_WORDS and tok not in EMAIL_WORDS)


def match_score(job: dict, r: dict) -> int:
    """0 = not this job. Whole-word company match is REQUIRED. An ATS receipt names the ROLE, so with 2+ distinctive title words at least 2 must
    appear (a company 'Analyst Data Privacy' is not our a company 'Trade and Transaction Reporting' job). LinkedIn receipts name only the company.

     a receipt that names a requisition id confirms ONLY that requisition ('R161076' must never confirm our R158179 job), and a
    multi-word company needs two tokens or one STRONG token: 'The Depository Trust & Clearing Corporation (DTCC)' must not match a 'a company'
    receipt through the shared word 'trust', and 'Mobineers Info Systems' must not match 'Argano' through 'info'."""
    hay = f"{r.get('from', '')} {r.get('email', '')} {r.get('subject', '')} {r.get('snippet', '')}".lower()
    if not hay.strip():
        return 0
    if VERIFICATION.search(r.get("subject") or "") or NAUKRI_SUMMARY.search(r.get("subject") or ""):
        return 0  # an OTP or a '4 jobs' digest is never proof of one specific application
    r_req = req_ids(f"{r.get('subject', '')} {r.get('snippet', '')}")
    req_hit = False
    if r_req:  # the strongest identity a receipt carries: if both sides name requisitions, they must agree
        j_req = job_req_ids(job)
        if not j_req or not (j_req & r_req):
            return 0
        req_hit = True  # the requisitions agree: nothing weaker may veto it (see the role-word rule below)
    ctoks = tokens(job["company"])
    hit_toks = [t for t in ctoks if re.search(r"\b" + re.escape(t) + r"\b", hay)]
    hits = len(hit_toks)
    if not hits:
        return 0
    # a company token must CARRY IDENTITY. If every agreeing word is ordinary English or the vocabulary of applying,
    # the only thing that can still prove the company is the whole name as one phrase ("its all about people" in the body), never a stray
    # 'about'/'applied'/'next' in a stock thank-you line. Being strict here can only delay a proof (the job stays unproven); being loose
    # would credit ANOTHER company's receipt to her job, which the proof rules exist to prevent.
    distinctive = [t for t in hit_toks if t not in COMMON_COMPANY_WORDS and t not in EMAIL_WORDS]
    if not distinctive:
        # 'Team Ace HR LLP' reduces to the single word 'team', which every recruiting email contains: a one-word company made of ordinary
        # English can never be identified by that word, so a multi-word name must appear as a phrase and a one-word name scores 0.
        if len(ctoks) < 2 or " ".join(ctoks) not in re.sub(r"[^a-z0-9]+", " ", hay):
            return 0
    if distinctive:
        hit_toks = distinctive
    if len(ctoks) > 1 and len(hit_toks) < 2 and not any(_strong_token(t, job["company"]) for t in hit_toks):
        return 0
    title_words = [w for w in re.findall(r"[a-z]{5,}", (job.get("title") or "").lower()) if w not in STOP_WORDS and w not in GENERIC_TITLE]
    overlap = sum(1 for w in title_words if re.search(r"\b" + w + r"\b", hay))
    from_linkedin = "linkedin" in hay and "application was sent" in hay
    # the role-word rule needed `req_hit` here. Live defect: our company is stored as 'a company' while the receipt says
    # 'a company Scientific', so receipt_role_words left {'scientific'}, no title word matched, and score 0 discarded TWO receipts that had
    # already matched their requisition exactly — reconcile matched 0 of 55 receipts. An agreed requisition outranks any role-word heuristic.
    if not from_linkedin and not req_hit:
        # Does the receipt NAME a role? Words like 'governance'/'privacy' do; 'thank you for applying' does not. A receipt that names a role must
        # share a word with our title (a company 'Data Privacy' is not our 'Trade and Transaction Reporting'), while a generic confirmation must not be
        # rejected just for being generic (a company send role-less receipts that are still real proof).
        role = receipt_role_words(r, job["company"])
        title_all = set(re.findall(r"[a-z]{4,}", (job.get("title") or "").lower()))
        if role and not (role & title_all):
            return 0
        if role and len(title_words) >= 2 and overlap < 2:
            return 0
    return 10 * hits + overlap


def receipt_role_words(r: dict, company: str) -> set[str]:
    """Role-describing words in a receipt subject: company names, apply-words and generic titles removed ('Thank you for applying' -> {})."""
    words = set(re.findall(r"[a-z]{4,}", (r.get("subject") or "").lower()))
    return {w for w in words - EMAIL_WORDS - set(tokens(company)) if w not in GENERIC_TITLE and w not in STOP_WORDS}


def names_this_job(job: dict, r: dict) -> bool:
    """The receipt names THIS job (its requisition or a role word), not merely the company. A generic 'job application submitted' email
    must never turn a failed/blocked attempt into 'applied': we cannot tell which of the company's roles it belongs to."""
    if req_ids(f"{r.get('subject', '')} {r.get('snippet', '')}") & job_req_ids(job):
        return True
    title_words = set(re.findall(r"[a-z]{4,}", (job.get("title") or "").lower()))
    return bool(receipt_role_words(r, job.get("company") or "") & title_words)


SHORT_NOT_IDENTITY = {"inc", "llc", "ltd", "pvt", "llp", "plc", "the", "and", "for", "of", "co", "us", "uk", "usa", "in", "an", "at", "by", "hr", "it", "bi", "ai", "bv", "ag", "sa", "gmbh"}


def tokens(company: str) -> list[str]:
    """Identity words of a company name. Words of 4+ letters, PLUS short all-capital names (LG, companies, 3M).

    the 4-letter minimum reduced 'LG Ad Solutions', 'a company', 'a company India', 'a company', 'a company Ltd', 'a company Business Solutions' and '3M India' to NOTHING
    (or only a stock word like 'solutions'), so no receipt could ever match their jobs: LG's 'Thanks for applying to LG Ad Solutions!' scored 0 and the
    job stayed unproven. A short name still has to be a whole word in the receipt, and every other rule (requisition, role words, send time) still applies."""
    base = [t for t in re.findall(r"[a-z0-9]{4,}", (company or "").lower()) if t not in STOP_WORDS]
    short = [a.lower() for a in re.findall(r"\b[A-Z0-9]{2,3}\b", company or "") if a.lower() not in SHORT_NOT_IDENTITY and not a.isdigit()]
    if not base and not short:   # a mixed-case short name on its own: 'a company India' -> 'pwc'
        first = (re.findall(r"[A-Za-z0-9]+", company or "") or [""])[0]
        if 2 <= len(first) <= 3 and first.lower() not in SHORT_NOT_IDENTITY and not first.isdigit():
            short = [first.lower()]
    return base + [s for s in short if s not in base]


def _profile_copy():
    # the Chrome backup profile holds a WORKING Gmail sign-in (the main bot profile's Gmail session was logged out by Google): prefer it
    llm = ROOT / "data" / "browser_profile_llm"
    main = llm if llm.exists() else ROOT / "data" / "browser_profile"
    subprocess.run(["robocopy", str(main), str(PROFILE), "/MIR", "/XD", "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
                    "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:0", "/W:0"], capture_output=True)


def read_receipts(days: int = 3) -> list[dict]:
    sys.path.insert(0, str(ROOT))
    from core import imap
    if imap.available():  # IMAP: reliable, fast, and not tied to a browser login that Google can invalidate
        # Several SIMPLE queries, merged: one long OR query silently dropped the newest a company receipts (: 27 hits, 0 a company, while the shorter query found them)
        qs = [f'newer_than:{days}d from:myworkday.com',
              f'newer_than:{days}d (subject:(application OR applying OR applied OR received OR submitted) OR subject:"thank you")',
              f'newer_than:{days}d ("thank you for applying" OR "received your application")',
              f'newer_than:{days}d (from:successfactors.com OR from:oraclecloud.com OR from:icims.com OR from:greenhouse.io OR from:lever.co OR from:smartrecruiters.com OR from:ripplehire.com OR from:darwinbox.in)']
        seen, rows = set(), []
        for q in qs:
            for m in imap.search(q, limit=60):
                key = (m["subject"], m["date"], m["email"])
                if key not in seen:
                    seen.add(key)
                    # WHEN it arrived matters as much as what it says: an AmbitionBox notice from was attached to a job sent at
                    #  (a company) purely because both said 'capco'. reconcile now refuses a receipt that predates the send.
                    rows.append({"from": m["from"], "email": m["email"], "subject": m["subject"], "snippet": m["snippet"], "text": m["snippet"],
                                 "ts": m.get("ts") or imap.msg_ts(m)})
        return [r for r in rows if is_receipt(r)]
    os.environ["JOBHUNT_PROFILE"] = str(PROFILE)
    from playwright.sync_api import sync_playwright

    from apply import browser
    _profile_copy()
    q = (f'newer_than:{days}d (subject:(application OR applying OR applied OR "thank you") OR "thank you for applying" OR "received your application" '
         f'OR from:myworkday.com OR from:workday.com OR from:oraclecloud.com OR from:icims.com OR from:greenhouse.io OR from:lever.co OR from:smartrecruiters.com)')
    out = []
    with sync_playwright() as pw:
        ctx = browser.launch(pw, headless=True)
        pg = ctx.new_page()
        pg.goto(f"https://mail.google.com/mail/u/0/#search/{quote(q, safe='')}", wait_until="domcontentloaded")
        pg.wait_for_timeout(6000)
        out = pg.evaluate(ROWS_JS)
        ctx.close()
    return [r for r in out if is_receipt(r)]


# ---------- Naukri Applied badge: LIVE context (KB-7) ----------
# A profile COPY is not signed in to Naukri (session cookies live in memory), so the copy-based check always met a login wall (0/13).
# These helpers read the badge from the browser context the worker already holds. Accuracy first: only an exact 'Applied' control on
# THAT job's page (or a 'already applied to this job' notice) counts; a timeout, a closed listing or a sign-in wall is 'unknown'.
NAUKRI_HOST = re.compile(r"https?://(?:[a-z0-9-]+\.)*naukri\.com/", re.I)
NAUKRI_JOB_ID = re.compile(r"-(\d{6,})(?:[?#/]|$)")  # Naukri job URLs end in '-<jobId>'
NAUKRI_LOGIN_URL = re.compile(r"nlogin/login|/login\b", re.I)
NAUKRI_APPLIED_NOTICE = re.compile(r"(you have already applied|already applied|you applied) to this job", re.I)
NAUKRI_WALL = re.compile(r"sign-in wall", re.I)

NK_STATE_JS = r"""() => {
  const txt = e => (e.innerText || e.textContent || '').replace(/\s+/g, ' ').trim();
  const vis = e => {
    try {
      const r = e.getBoundingClientRect();
      const s = getComputedStyle(e);
      return !!(r.width && r.height) && s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity || '1') > 0;
    } catch (err) { return false; }
  };
  const sels = ['#apply-button', '.apply-button', '[class*="apply-button"]', '[class*="applyButton"]', '[class*="applyBtn"]', 'button', 'a'];
  const seen = new Set();
  const cands = [];
  for (const sel of sels) {
    for (const e of document.querySelectorAll(sel)) {
      if (seen.has(e)) continue;
      seen.add(e);
      const t = txt(e);
      if (!t || t.length > 40) continue;
      if (!/^(applied|apply|apply on company site|apply on company website|already applied)$/i.test(t)) continue;
      const box = e.closest('#apply-button, .apply-button, [class*="apply-button"], [class*="applyButton"], [class*="applyBtn"]');
      cands.push({tag: e.tagName.toLowerCase(), id: e.id || '', cls: (e.className && String(e.className).slice(0, 60)) || '',
                  text: t, visible: vis(e), disabled: !!e.disabled || e.getAttribute('aria-disabled') === 'true',
                  in_apply: !!box || e.id === 'apply-button'});
    }
  }
  const body = (document.body && document.body.innerText) || '';
  const m = body.match(/[^\n]{0,60}(you have already applied|already applied|you applied) to this job[^\n]{0,60}/i);
  const w = body.match(/[^\n]{0,40}(login to apply|sign in to apply|login to continue|please login|please sign in)[^\n]{0,60}/i);
  const g = body.match(/[^\n]{0,50}(no longer accepting|no longer available|job has expired|job you are looking for)[^\n]{0,50}/i);
  const inView = e => {  // a login modal rendered off-screen is not a wall; the apply badge may legitimately sit below the fold
    try {
      const r = e.getBoundingClientRect();
      return r.bottom > 0 && r.right > 0 && r.top < (window.innerHeight || 1080) && r.left < (window.innerWidth || 1920);
    } catch (err) { return false; }
  };
  const pw = document.querySelector('input[type=password]');
  return {url: location.href, password: !!(pw && vis(pw) && inView(pw)),
          candidates: cands.slice(0, 20),
          notice: m ? m[0].replace(/\s+/g, ' ').trim().slice(0, 160) : '',
          wall: w ? w[0].replace(/\s+/g, ' ').trim().slice(0, 120) : '',
          gone: g ? g[0].replace(/\s+/g, ' ').trim().slice(0, 120) : ''};
}"""


def _naukri_verdict(state: dict, job_url: str = "") -> tuple[str, str]:
    """One page's verdict: ('applied'|'not_applied'|'unknown', evidence). Negative signals are never proof of anything by themselves,
    so only a visible exact 'Applied' control (or an explicit 'applied to this job' notice) returns 'applied'."""
    state = state or {}
    url = state.get("url") or ""
    if NAUKRI_LOGIN_URL.search(url):
        return "unknown", "sign-in wall: redirected to " + url[:100]
    if state.get("password"):
        return "unknown", "sign-in wall: a password field is visible"
    if state.get("wall"):
        return "unknown", "sign-in wall: " + str(state["wall"])[:100]
    want = NAUKRI_JOB_ID.search(job_url or "")
    if want and want.group(1) not in url:
        return "unknown", "the page moved away from this job (now " + url[:100] + ")"  # e.g. a company-site redirect: no Naukri badge to read
    cands = [c for c in (state.get("candidates") or []) if isinstance(c, dict) and c.get("visible")]
    applied = [c for c in cands if (c.get("text") or "").strip().lower() == "applied"]
    if applied:
        c = applied[0]
        return "applied", (f"Applied badge on this job page ({c.get('tag', '')}#{c.get('id') or '-'} "
                           f"class='{str(c.get('cls') or '')[:40]}', disabled={bool(c.get('disabled'))})")
    notice = str(state.get("notice") or "")
    if notice and NAUKRI_APPLIED_NOTICE.search(notice):
        return "applied", "this job page says: " + notice[:120]
    apply_btn = [c for c in cands if re.match(r"^apply$", (c.get("text") or "").strip(), re.I)]
    if apply_btn:
        return "not_applied", "the job page shows an Apply button and no Applied badge"
    if state.get("gone"):
        return "unknown", "listing closed/expired; badge not readable: " + str(state["gone"])[:100]
    if any("company" in (c.get("text") or "").lower() for c in cands):
        return "unknown", "Naukri routes this job to the company site; no badge to read"
    return "unknown", "no Apply control or Applied badge found"


def check_naukri_badges(ctx, jobs, log=print, page=None, settle_ms: int = 3000) -> dict:
    """Read the Naukri 'Applied' badge for `jobs` in an EXISTING, already-signed-in Playwright context (KB-7).

    Reuses `page` when given, otherwise opens exactly ONE tab and closes it (never launches a browser, never copies the profile,
    never touches the applier's own page). Each job's own Naukri URL is loaded and only that page's apply control is read.
    Timeouts / closed listings / a sign-in wall are 'unknown', never 'not applied'. On the first sign-in wall it stops and reports
    `signed_out` so the caller can back off, and the untouched jobs stay 'unknown'.

    Returns {"jobs": {job_id: {"verdict": "applied"|"not_applied"|"unknown", "evidence": str, "url": str}},
             "signed_out": bool, "wall": str}.
    """
    jobs = list(jobs or [])
    out = {"jobs": {}, "signed_out": False, "wall": ""}
    own_page = page is None
    try:
        pg = page or ctx.new_page()
    except Exception as e:  # noqa: BLE001 (cannot even open a tab: everything is unknown, nothing is written off)
        for i, j in enumerate(jobs):
            jid = str(j.get("id") or j.get("url") or j.get("apply_url") or i)
            out["jobs"][jid] = {"verdict": "unknown", "evidence": f"no page available: {type(e).__name__}: {str(e)[:80]}",
                                "url": j.get("apply_url") or j.get("url") or ""}
        return out
    try:
        for i, j in enumerate(jobs):
            jid = str(j.get("id") or j.get("url") or j.get("apply_url") or i)
            url = j.get("apply_url") or j.get("url") or ""
            if not NAUKRI_HOST.search(url):
                out["jobs"][jid] = {"verdict": "unknown", "evidence": "not a Naukri URL", "url": url}
                continue
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(settle_ms)
                state = pg.evaluate(NK_STATE_JS)
            except Exception as e:  # noqa: BLE001 (a slow/blocked page is UNKNOWN, never 'not applied')
                out["jobs"][jid] = {"verdict": "unknown", "evidence": f"{type(e).__name__}: {str(e)[:100]}", "url": url}
                continue
            verdict, evidence = _naukri_verdict(state, url)
            out["jobs"][jid] = {"verdict": verdict, "evidence": evidence, "url": url}
            if verdict == "unknown" and NAUKRI_WALL.search(evidence):
                out["signed_out"], out["wall"] = True, evidence
                for k in jobs[i + 1:]:  # signed out: do not load another page, and leave the rest explicitly unknown
                    k_id = str(k.get("id") or k.get("url") or k.get("apply_url") or "")
                    out["jobs"][k_id] = {"verdict": "unknown", "evidence": "not checked: signed out of Naukri",
                                         "url": k.get("apply_url") or k.get("url") or ""}
                break
    finally:
        if own_page:
            try:
                pg.close()
            except Exception:  # noqa: BLE001
                pass
    vals = list(out["jobs"].values())
    checked = [v for v in vals if v.get("evidence") != "not a Naukri URL"]
    log(f"naukri live badge check: {sum(1 for v in checked if v['verdict'] == 'applied')} applied, "
        f"{sum(1 for v in checked if v['verdict'] == 'not_applied')} not applied, "
        f"{sum(1 for v in checked if v['verdict'] == 'unknown')} unknown of {len(checked)}")
    return out


def verify_naukri(log=print) -> int:
    """Naukri sends no per-application email (your CV goes to the recruiter's Naukri inbox). The proof is the 'Applied' badge on the job page.

    KB-7: this runs in the background `hunt.py proof` process on a COPY of the profile, where Naukri usually shows a login wall (the
    session is not on disk). It now uses the same strict badge reader as check_naukri_badges and STOPS at the first wall instead of
    trying every job; a wall writes an hour-long back-off marker so later proof cycles skip the expensive copy instead of retrying,
    and the authoritative badge proof comes from check_naukri_badges in the worker's live context."""
    mark = ROOT / "data" / "naukri_proof_wall"
    try:  # signed out an hour ago: do not copy the profile and retry every cycle
        if mark.exists() and time.time() - mark.stat().st_mtime < 3600:
            log("naukri badge check: skipped (signed out within the last hour; live-context check handles it, KB-7)")
            return 0
    except Exception:  # noqa: BLE001
        pass
    sys.path.insert(0, str(ROOT))
    os.environ["JOBHUNT_PROFILE"] = str(ROOT / "data" / "browser_profile_proof_nk")  # must be set BEFORE apply.browser is imported (it reads the profile path at import)
    from playwright.sync_api import sync_playwright

    from apply import browser
    from core import db as dbm
    d = dbm.DB()
    jobs = d.rows("status IN ('applied','unconfirmed') AND email_proof IS NULL AND (source='board:naukri' OR apply_url LIKE '%naukri.com%')")
    ok = 0
    if not jobs:
        return 0
    # this check used the Gmail backup profile, which is NOT signed in to Naukri, so the Applied badge never showed ("0/16 confirmed").
    # Use a fresh COPY of the main bot profile (signed in to Naukri; the live worker keeps the original open).
    nk_profile = ROOT / "data" / "browser_profile_proof_nk"
    subprocess.run(["robocopy", str(ROOT / "data" / "browser_profile"), str(nk_profile), "/MIR", "/XD", "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
                    "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:0", "/W:0"], capture_output=True)
    os.environ["JOBHUNT_PROFILE"] = str(nk_profile)
    signed_out = False
    with sync_playwright() as pw:
        ctx = browser.launch(pw, headless=True)
        pg = ctx.new_page()
        for j in jobs:
            try:
                pg.goto(j["apply_url"], wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(4000)
                verdict, evidence = _naukri_verdict(pg.evaluate(NK_STATE_JS), j["apply_url"])
                if verdict == "applied":
                    cur = d.c.execute("UPDATE jobs SET email_proof=?, email_proof_at=? WHERE id=? AND email_proof IS NULL",
                                      (f"ACCOUNT: Naukri Applied badge {evidence}"[:200], dbm.now(), j["id"]))
                    if cur.rowcount:
                        ok += 1
                        if j["status"] == "unconfirmed":
                            d.set_status(j["id"], "applied", "confirmed: Naukri shows the Applied badge",
                                         applied_at=j["applied_at"] or dbm.now())
                elif verdict == "unknown" and NAUKRI_WALL.search(evidence):
                    signed_out = True
                    break  # KB-7: the proof profile is signed out; do not try every job
            except Exception:  # noqa: BLE001 (timeout: unknown, never 'not applied')
                pass
        pg.close()
        ctx.close()
    if signed_out:
        try:
            mark.write_text(str(time.time()))
        except Exception:  # noqa: BLE001
            pass
        from core import activity
        activity.log("info", "Naukri badge check (proof-profile copy): signed out (login wall); stopping and backing off for 1h. "
                             "The worker's LIVE context does this check after Naukri sends (KB-7).")
    else:
        mark.unlink(missing_ok=True)  # the copy had a real session this time: keep checking normally
    log(f"naukri badge check: {ok}/{len(jobs)} confirmed" + (" (signed out; stopped early)" if signed_out else ""))
    return ok


def _sent_epoch(job: dict) -> float | None:
    """When the application was actually sent, as an epoch. None when the row has no usable time (then the time gate is skipped)."""
    from datetime import datetime
    v = job.get("applied_at")
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19]).timestamp()
    except Exception:  # noqa: BLE001
        return None


def pick_by_time(cands: list, receipt: dict, window_s: int = 3600):
    """FIFO for a receipt that names no role: of the tied jobs, the EARLIEST one submitted before the receipt arrived (within window_s). Receipts of one portal arrive a few
    minutes after each send and in send order (a company: sends / /, receipts / /), so 'nearest' would credit the wrong job
    while FIFO keeps the unmatched one waiting for its own receipt. None when there is no timestamp or no job fits (never a guess)."""
    ts = receipt.get("ts")
    if ts is None:
        return None
    fits = [(sent, j) for j in cands for sent in [_sent_epoch(j)] if sent is not None and 0 <= float(ts) - sent <= window_s]
    return min(fits, key=lambda x: x[0])[1] if fits else None


def reconcile(days: int = 3, log=print) -> dict:
    sys.path.insert(0, str(ROOT))
    from core import activity
    from core import db as dbm
    d = dbm.DB()
    receipts = sorted(read_receipts(days), key=lambda r: float(r.get("ts") or 0))   # oldest first: FIFO against our send order
    jobs = d.rows("status IN ('applied','unconfirmed','needs_you','failed') AND (email_proof IS NULL)")
    matched, promoted, used = 0, 0, set()

    score = match_score

    for i, r in enumerate(receipts):  # each receipt confirms exactly ONE job: the best match
        # A receipt cannot prove a send that happened AFTER it: the AmbitionBox notice that arrived at was credited to the a company job
        # sent at because the only agreeing word was the company name, and a job board sends that notice for every apply, not this one.
        # 30 minutes of skew absorbs clock differences between us and the mail server.
        sent_ok = lambda j: (r.get("ts") is None or _sent_epoch(j) is None or float(r["ts"]) >= _sent_epoch(j) - 1800)  # noqa: E731
        best = max((j for j in jobs if sent_ok(j)), key=lambda j: score(j, r), default=None)
        if not best or score(best, r) == 0:
            continue
        # A receipt that names no role ("Thank You for Applying to a company") fits EVERY job at that company equally, and max above just takes the
        # first in table order. Decide by WHEN we submitted: the tied job sent shortly BEFORE the receipt (0..30 min, nearest). No such job -> leave the receipt unmatched.
        top = score(best, r)
        tied = [j for j in jobs if sent_ok(j) and score(j, r) == top]
        if len(tied) > 1 and r.get("ts") is not None:
            best = pick_by_time(tied, r)
            if best is None:
                continue
        if best["status"] in ("needs_you", "failed") and not names_this_job(best, r):
            continue  # a generic 'application submitted' email cannot overturn a blocked/failed attempt (which role was it?)
        # Name the requisition in the stored proof when the receipt carries one: two genuine a company receipts share the identical subject
        # 'Thank you for your interest in a company Scientific!', so without the id a later audit cannot tell them apart from one
        # receipt credited twice, and would have to re-read the mailbox to know which row each one proved.
        reqs = sorted(req_ids(f"{r.get('subject', '')} {r.get('snippet', '')}") & job_req_ids(best))
        tag = " ".join(f"R-{x}" for x in reqs[:2]) + " " if reqs else ""
        d.c.execute("UPDATE jobs SET email_proof=?, email_proof_at=? WHERE id=?", (f"{tag}{r['from']}: {r['subject']}"[:200], dbm.now(), best["id"]))
        jobs = [j for j in jobs if j["id"] != best["id"]]
        used.add(i)
        matched += 1
        if best["status"] in ("unconfirmed", "needs_you", "failed"):
            d.set_status(best["id"], "applied", "confirmed by the employer's receipt email", applied_at=best["applied_at"] or dbm.now(), needs_you_reason=None)  # keep the real submission time: "Today" must count when we applied, not when the receipt was matched
            promoted += 1
        activity.log("result", f"email proof: {r['subject'][:80]}", status="email_proof")
    # portals that never send receipts (RippleHire...): a post-submit confirmation page captured under the TIGHTENED check (success wording + no Submit
    # button left; after the a company false positive) with no email after 6h counts as applied, proof kind 'page'
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(hours=6)).isoformat(timespec="seconds")
    for j in d.rows("status='unconfirmed' AND page_proof IS NOT NULL AND email_proof IS NULL AND applied_at>=? AND applied_at<? AND page_proof NOT LIKE 'PAGE: Naukri%'",
                    (STRICT_FROM, cutoff)):
        d.c.execute("UPDATE jobs SET email_proof=?, email_proof_at=? WHERE id=?", (j["page_proof"][:150] + " (portal sends no receipt email)", dbm.now(), j["id"]))
        d.set_status(j["id"], "applied", "confirmation page verified; this portal sends no receipt email", applied_at=j["applied_at"], needs_you_reason=None)
        promoted += 1
    other = len(receipts) - len(used)
    nk = verify_naukri(log)
    res = {"receipts": len(receipts), "matched": matched, "promoted": promoted, "other_receipts": other, "naukri_badge_confirmed": nk}
    log(f"email proof: {res}")
    activity.log("info", f"email proof check: {len(receipts)} receipt emails, {matched} matched to our jobs, {promoted} promoted to sent, {other} from other applications")
    return res
