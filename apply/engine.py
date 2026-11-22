"""Generic form-apply engine: fill from saved answers, upload tailored CV, consent (never marketing), park unknowns, confirm.
Statuses returned: applied | dry_run_ok | needs_you | unconfirmed | closed | captcha | login_required | no_form | failed."""
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from apply import answers_map as am
from apply import pagejs

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "screenshots"

# Trap wording, visibility, label resolution and the hidden-but-real control rule live in ONE place
# (apply/pagejs.py) and are shared with the navigator's snapshot. Building the JS by concatenation
# keeps it a single function for Playwright while removing the second copy of those rules.
EXTRACT_JS = "() => {\n" + pagejs.HELPERS_JS + r"""
  window.__jh = window.__jh || 0;
  const group = e => { const f = e.closest('fieldset,[role=radiogroup],[role=group]');
    if (f) { const l = f.querySelector('legend,label,[class*=label],h3,h4,p'); if (l && !l.contains(e)) return clean(l.innerText); }
    let p = e.parentElement; for (let i = 0; i < 4 && p; i++) { const t = p.innerText || ''; const first = t.split('\n')[0]; if (first && first.length > 8 && !p.querySelector('input[type=radio]') === false) return clean(first); p = p.parentElement; }
    return lab(e); };
  const out = [];
  document.querySelectorAll('input,select,textarea').forEach(e => {
    const type = (e.type || e.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
    const l = lab(e);
    // Robot traps are excluded by WORDING only (Oracle HCM's VISIBLE 184x38 input named 'honey-pot'):
    // never numbered, never filled, never asked about.
    if (isTrap(e)) return;
    const hidden = !vis(e);
    // A hidden control is kept only when it is a styled upload/checkbox/radio a human could click
    // (Oracle JET: input.input-row__hidden-control at 0x0, opacity 0). A hidden text box is noise.
    if (hidden && !keepHidden(e, type)) return;
    if (!e.dataset.jh) e.dataset.jh = 'jh' + (window.__jh++);
    const o = { id: e.dataset.jh, tag: e.tagName.toLowerCase(), type, label: l, name: e.name || '',
      required: e.required || e.getAttribute('aria-required') === 'true' || /\*\s*$/.test(l), value: e.value || '', checked: !!e.checked,
      hidden, placeholder: e.placeholder || '' };
    if (e.tagName === 'SELECT') o.options = [...e.options].map(x => x.text.trim());
    if (type === 'radio') { o.q = group(e); o.group = e.name; }
    out.push(o); });
  return out; }"""

CLOSED = re.compile(r"no longer (accepting|available|open|active)|position (has been |is )?(filled|closed)|job (is )?(closed|expired|no longer)|"
                    r"this (job|posting|position) (has )?(expired|closed)|page (you|could).{0,20}not (found|exist)|"
                    # KB-61: Workday's own answer for a withdrawn requisition is "The page you are looking for doesn't exist."
                    # Kept inside one sentence ([^.]) so ordinary prose that merely mentions a page cannot close a job.
                    r"(this |the )?page[^.]{0,60}(not (found|exist|available)|doesn['’]?t exist|no longer exist|"
                    r"has been (removed|deleted))|not currently accepting", re.I)
# Boards where clicking "Apply" IS the submission (profile resume, not our tailored one): never click these from the generic engine.
ONE_CLICK = re.compile(r"(naukri\.com|shine\.com|apna\.co|foundit\.in|monsterindia\.com|hirist\.(tech|com)|instahyre\.com|indeed\.com|"
                       r"glassdoor\.|workindia\.in|timesjobs\.com|linkedin\.com)", re.I)
CURRENT_JOB = {"id": None, "company": "", "title": "", "jd": ""}  # runner fills company/title/jd for the LLM fallback
SUCCESS = re.compile(r"thank you for (applying|your (job )?application|submitting)|application (has been |was )?(successfully )?(submitted|received|sent)|"
                     r"successfully (applied|submitted)|we('ve| have) received your application|"
                     r"application complete|your application is in", re.I)
FINAL_BTN = re.compile(r"^\s*(submit( (my )?application)?|apply( now)?|send application|finish|complete( application)?|confirm)\s*$", re.I)
NEXT_BTN = re.compile(r"^\s*(next|continue|save (and|&) continue|proceed|review)\s*$", re.I)
VERIFY_BTN = re.compile(r"^\s*(verify|validate|confirm( identity)?)\s*$", re.I)
# Oracle HCM / BrassRing ask for an emailed code in six one-character boxes. The engine used to park that as
# "unanswered required" (needs_you, a question for the user) although the code is in her inbox (KB-45/KB-49).
# Deliberately narrower than agent.CODE_PAGE: "Country Phone Code" is a Workday field, not a code page.
CODE_UNKNOWN = re.compile(r"(?:verification|security|one[- ]time)\s+(?:code|passcode)|\bcode\s+digit|\bpasscode\b", re.I)
APPLY_LINK = re.compile(r"^\s*(apply( now| for this job| to this job| here)?|i'?m interested|apply on company (site|website))\s*$", re.I)
_last_host: dict = {}


def pace(url: str, gap: float = 20.0):
    host = urlparse(url).netloc
    wait = gap - (time.time() - _last_host.get(host, 0))
    if wait > 0:
        time.sleep(min(wait, gap))
    _last_host[host] = time.time()


def _norm(s: str) -> str:
    """Case- and accent-insensitive form: Workday lists 'Karnātaka', 'Bihār'."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", str(s)) if not unicodedata.combining(c)).lower().strip()


def _pick(options: list[str], want) -> str | None:
    if want == "DECLINE":
        for o in options:
            if am.DECLINE.search(o):
                return o
        return None
    w = _norm(want)
    for o in options:  # exact, then startswith, then contains (all accent-insensitive)
        if _norm(o) == w:
            return o
    for o in options:
        n = _norm(o)
        if n.startswith(w) or (len(w) > 2 and w in n):
            return o
    for alt in (w[:-1] if w.endswith("n") else None, w[:-3] if w.endswith("ian") else None, w[:-2] if w.endswith("an") else None):
        if alt and len(alt) > 3:  # nationality vs country: 'indian' -> 'india'
            for o in options:
                if _norm(o) == alt or _norm(o).startswith(alt):
                    return o
    return None


LAST_CLOSED_HIT: dict = {"text": "", "url": ""}   # evidence for the ops report: WHICH phrase closed the job


def _oracle_live(url: str):
    """True/False/None from the site's own requisition API (KB-60): an Oracle SPA shell is not evidence either way."""
    try:
        from discover import ats_oracle
        return ats_oracle.req_live(url)
    except Exception:  # noqa: BLE001
        return None


def _closed_reason(pg, text: str) -> str:
    m = CLOSED.search(text or "")
    if m:
        return m.group(0)
    if re.search(r"\b404\b", pg.title() or ""):
        return "404 in the page title"
    return ""


def page_state(pg, confirm: bool = False) -> str:
    if re.search(r"greenhouse\.io/[^?]*\?error=true", pg.url or ""):
        return "closed"  # Greenhouse redirects a removed/closed job to its board with ?error=true
    try:
        text = pg.inner_text("body")[:6000]
    except Exception:  # noqa: BLE001
        return "ok"
    look = lambda t: _closed_reason(pg, t) and not pg.locator("input:visible,textarea:visible").count()  # noqa: E731
    if look(text):
        if confirm:
            # a company + a company 30051451/30051454 were closed as "closed page at …" while the postings were LIVE
            # (HTTP 200, og:title = the exact role). An SPA that has not rendered yet must not be declared closed on one look.
            try:
                pg.wait_for_timeout(5000)
                text = pg.inner_text("body")[:6000]
            except Exception:  # noqa: BLE001
                pass
            if not look(text):
                return "ok"
        LAST_CLOSED_HIT.update(text=_closed_reason(pg, text), url=pg.url or "")
        return "closed"
    for sel in ("iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='challenges.cloudflare']", ".g-recaptcha"):
        if pg.locator(sel).count() and pg.locator(sel).first.is_visible():
            if captcha_is_only_a_badge(pg, sel):
                continue   # an INVISIBLE reCAPTCHA / hCaptcha badge is on thousands of ordinary pages (a company, Okta listings were parked as "CAPTCHA" for it)
            return "captcha"
    if re.search(r"verify you are (a )?human|are you a robot|unusual traffic", text, re.I):
        return "captcha"
    return "ok"



def captcha_is_only_a_badge(pg, sel: str) -> bool:
    """True when the matched element is the small invisible-captcha badge, not a challenge the visitor must solve: the anchor iframe says size=invisible, or it sits in
    a .grecaptcha-badge box. A visible checkbox ('I'm not a robot'), a challenge popup (bframe) or an hCaptcha challenge are NOT badges."""
    try:
        el = pg.locator(sel).first
        src = (el.get_attribute("src") or "").lower()
        if "bframe" in src or "hcaptcha.com" in src and "invisible" not in src:
            return False
        if "size=invisible" in src or "badge" in src:
            return True
        # a company + a company: `.g-recaptcha` is a plain SUBMIT BUTTON ("Join Talent Community") wired to reCAPTCHA's invisible callback -
        # no iframe, no challenge. Only a container that actually holds a challenge iframe is a challenge.
        if bool(el.evaluate("e => ['BUTTON','INPUT','A'].includes(e.tagName) || (e.tagName !== 'IFRAME' && !e.querySelector('iframe') && e.getAttribute('data-size') === 'invisible')")):
            return True
        return bool(el.evaluate("e => !!e.closest('.grecaptcha-badge') || (e.getBoundingClientRect().width <= 80 && e.getBoundingClientRect().height <= 80)"))
    except Exception:  # noqa: BLE001
        return False

def looks_like_application(pg) -> bool:
    try:
        if pg.locator("input[type=file]").count():
            return True
        has_email = pg.locator("input[type=email]:visible, input[name*=mail i]:visible, input[id*=mail i]:visible").count()
        has_name = pg.locator("input[name*=name i]:visible, input[id*=name i]:visible, input[autocomplete*=name]:visible").count()
        return bool(has_email and has_name)
    except Exception:  # noqa: BLE001
        return False


def follow_embedded_form(pg):
    """Embedded ATS forms (Greenhouse/Lever/... in an iframe): open the frame's own URL so the form is in the main page."""
    if looks_like_application(pg):
        return pg
    for fr in pg.frames[1:]:
        u = fr.url or ""
        if u.startswith("http") and re.search(r"greenhouse|lever\.co|workable|smartrecruiters|ashbyhq|icims|jobvite|/apply|application", u, re.I):
            pg.goto(u, wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(2500)
            return pg
    return pg


def click_apply_if_needed(pg):
    """Landing pages show a job description with an Apply button; click it (follow a new tab if opened).
    Careers sites also have search/filter boxes, so 'already on a form' means APPLICATION fields: a CV upload, or email + name."""
    dismiss_cookies(pg)
    if looks_like_application(pg):
        return pg
    ctx = pg.context
    for role in ("button", "link"):
        for el in pg.get_by_role(role, name=APPLY_LINK).all()[:10]:  # filter by accessible name: works on long careers pages too
            try:
                if not el.is_visible():
                    continue
                before_url, before_pages = pg.url, set(id(p) for p in ctx.pages)
                el.click(timeout=8000)
                # careers sites open the application in a NEW tab, or navigate this one, sometimes after several seconds
                for _ in range(20):  # up to ~10s
                    pg.wait_for_timeout(500)
                    newer = [p for p in ctx.pages if id(p) not in before_pages]
                    if newer:
                        p2 = newer[-1]
                        try:
                            p2.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:  # noqa: BLE001
                            pass
                        p2.wait_for_timeout(1500)
                        dismiss_cookies(p2)
                        return p2
                    if pg.url != before_url:
                        pg.wait_for_timeout(1500)
                        dismiss_cookies(pg)
                        return pg
                return pg
            except Exception:  # noqa: BLE001
                continue
    return pg


def dismiss_cookies(pg):
    """Cookie banners (OneTrust and friends) sit on top of corporate careers pages and swallow clicks: accept/reject them first."""
    for sel in ("#onetrust-accept-btn-handler", "#onetrust-reject-all-handler", "button#truste-consent-button", "[data-automation-id=legalNoticeDeclineButton]"):
        try:
            loc = pg.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=2000)
                pg.wait_for_timeout(500)
                return
        except Exception:  # noqa: BLE001
            pass
    try:
        b = pg.get_by_role("button", name=re.compile(r"^(accept( all)?( cookies)?|reject all|i agree|got it|allow all|decline|confirm my choices)$", re.I))   # "Confirm My Choices" = SmartRecruiters/OneTrust preference centre left on its defaults (strictly necessary only); it covered a company's whole form
        if b.count() and b.first.is_visible():
            b.first.click(timeout=2000)
            pg.wait_for_timeout(500)
    except Exception:  # noqa: BLE001
        pass


def normalize_url(url: str) -> str:
    """Force English: text-based detection fails on /ja-JP/ (a company) or /fr-CA/ (a company) portals."""
    u = re.sub(r"(myworkdayjobs\.com)/[a-z]{2}-[A-Z]{2}/", r"\1/en-US/", url or "")
    return re.sub(r"(/CandidateExperience)/[a-z]{2}(-[A-Za-z]{2})?/", r"\1/en/", u)


# portals with no flow built yet: classified BEFORE any time is spent tailoring/launching; shown on the dashboard as a build list
BLOCKED_PORTALS = re.compile(r"successfactors|taleo\.net|jobs\.standardchartered|careers\.wipro|careers\.societegenerale|account\.amazon\.jobs|amazon\.jobs", re.I)


def proof_shot(pg, job_id: str) -> str | None:
    """Screenshot taken ONLY after the final Submit click, on the confirmation page. This (not a pre-submit screenshot) is the page proof."""
    try:
        p = SHOTS / datetime.now().strftime("%Y-%m-%d") / f"{job_id}_confirmed.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(p), full_page=True)
        return str(p)
    except Exception:  # noqa: BLE001
        return None


def _tick(pg, loc):
    """Tick a consent box even when the site hides the real <input> behind a custom widget (RippleHire, Phenom...)."""
    # a real click on the visible label first: frameworks (Oracle JET, React) only register user events; check(force) can flip
    # the hidden <input> without updating the form model ("You need to agree to the terms" while the box looks ticked)
    try:
        if loc.is_checked():
            return True
    except Exception:  # noqa: BLE001
        pass
    for how in ("label", "box", "check", "js"):
        try:
            if how == "label":
                lab = loc.evaluate_handle("e => (e.id && document.querySelector('label[for=\"' + CSS.escape(e.id) + '\"]')) || e.closest('label') || "
                                          "(e.nextElementSibling && e.nextElementSibling.tagName === 'LABEL' ? e.nextElementSibling : null)")
                el = lab.as_element()
                if not el:
                    continue
                el.click(timeout=3000)
            elif how == "box":
                if not loc.is_visible():
                    continue
                loc.click(timeout=3000)
            elif how == "check":
                loc.check(force=True, timeout=3000)
            else:
                loc.evaluate("e => { e.checked = true; e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); e.click && !e.checked && e.click(); }")
            if loc.is_checked():
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def fill_page(pg, resume: Path, unknown: list, filled: list):
    """Rules first (free). Whatever the rules can't answer goes to ONE batched LLM call (truth-bound); only then park."""
    pending_before = len(unknown)
    _fill_rules(pg, resume, unknown, filled)
    todo = list(dict.fromkeys(unknown[pending_before:]))
    if not todo or any("[" in u for u in todo) and len(todo) == sum("[" in u for u in todo):
        return
    fields = {(_f["q"] if _f["type"] == "radio" else _f["label"]): _f for _f in pg.evaluate(EXTRACT_JS)}
    qs = []
    for lab in todo:
        f = fields.get(lab)
        if not f or f["type"] in ("checkbox", "file"):
            continue  # attestation checkboxes stay parked: ticking them could be untrue
        if am.needs_her(lab):
            # the user (accuracy): a skill-specific question ('years of experience in VBA Macros') or a commercial preference is HER
            # decision. It used to go to the LLM, which could answer with her total career figure - an overclaim on a submitted form. It stays
            # parked and surfaces on the dashboard with the exact wording instead.
            continue
        opts = f.get("options") or ([x["label"] for x in fields.values() if x["type"] == "radio" and x.get("group") == f.get("group")] if f["type"] == "radio" else None)
        qs.append({"q": lab, "options": [o for o in (opts or []) if o and not re.match(r"^(select|choose|--)", o, re.I)] or None})
    from apply import llm_answer
    got = llm_answer.answer(qs, CURRENT_JOB)
    all_fields = pg.evaluate(EXTRACT_JS)
    for lab, ans in got.items():
        f = fields.get(lab)
        if not f:
            continue
        try:
            if f["type"] == "radio":
                group = [x for x in all_fields if x["type"] == "radio" and x["group"] == f["group"]]
                opt = _pick([x["label"] for x in group], ans)
                if opt is None:
                    continue
                pick = next(x for x in group if x["label"] == opt)
                _tick(pg, pg.locator(f'[data-jh="{pick["id"]}"]').first)
            elif f["tag"] == "select":
                opt = _pick([o for o in f["options"] if o], ans)
                if opt is None:
                    continue
                pg.locator(f'[data-jh="{f["id"]}"]').first.select_option(label=opt)
            else:
                pg.locator(f'[data-jh="{f["id"]}"]').first.fill(ans)
            filled.append((lab[:40] + " (AI)", ans[:30]))
            while lab in unknown:
                unknown.remove(lab)
        except Exception:  # noqa: BLE001
            continue


def _fill_rules(pg, resume: Path, unknown: list, filled: list):
    fields = pg.evaluate(EXTRACT_JS)
    seen_groups = set()
    n_files = sum(1 for x in fields if x["type"] == "file")
    jid = CURRENT_JOB["id"]
    for f in fields:
        sel = f'[data-jh="{f["id"]}"]'
        loc = pg.locator(sel).first
        t, label = f["type"], f["label"]
        try:
            if t == "file":
                lab = label + " " + f["name"]
                if f["value"] or re.search(r"cover|photo|picture|image|id proof|portfolio|certificate|transcript|marksheet", lab, re.I):
                    continue
                if re.search(r"resume|cv|curriculum", lab, re.I) or n_files == 1:
                    loc.set_input_files(str(resume))
                    filled.append(("resume", resume.name))
                continue
            if t == "checkbox":
                if am.MARKETING.search(label):
                    continue
                if am.CONSENT.search(label) and not f["checked"]:
                    _tick(pg, loc)
                    filled.append(("consent", label[:50]))
                elif f["required"] and not f["checked"] and not am.CONSENT.search(label):
                    unknown.append(label)
                continue
            if t == "radio":
                if f["group"] in seen_groups:
                    continue
                seen_groups.add(f["group"])
                ans = am.answer_for(f["q"], jid)
                group = [x for x in fields if x["type"] == "radio" and x["group"] == f["group"]]
                if ans is None:
                    if any(x["required"] for x in group):
                        unknown.append(f["q"])
                    continue
                opt = _pick([x["label"] for x in group], ans)
                if opt is None:
                    unknown.append(f["q"])
                    continue
                pick = next(x for x in group if x["label"] == opt)
                _tick(pg, pg.locator(f'[data-jh="{pick["id"]}"]').first)
                filled.append((f["q"][:40], opt))
                continue
            if t in ("text", "email", "tel", "number", "date", "url", "search", "textarea", "input", "password") or f["tag"] == "textarea":
                if f["value"].strip():
                    continue
                if t == "password":
                    continue
                ans = am.answer_for(label or f["placeholder"], jid)
                if t == "email" or re.search(r"@\w+\.\w+|e-?mail|mail id", f"{label} {f['placeholder']}", re.I):
                    ans = am.profile.facts()["personal"]["email"]  # an email box, even when its only label is a placeholder
                if ans == "SKIP":  # optional extras such as phone extension / address line 2: leave empty...
                    if f["required"]:  # ...unless the site REQUIRES it: then give a true, harmless value instead of parking the job
                        filler = am.optional_filler(label or f["name"])
                        if filler is not None:
                            loc.fill(filler)
                            filled.append((label or f["name"], filler))
                        else:
                            unknown.append(label or f["name"])
                    continue
                if ans == "ASAP":
                    ans = datetime.now() if t == "date" else "Immediately"
                if t == "date" and isinstance(ans, str):  # a date field must never receive 'Yes' or free text
                    ans = None
                if ans is None:
                    if f["required"]:
                        unknown.append(label or f["name"])
                    continue
                if hasattr(ans, "strftime"):
                    ans = am.fmt_date(ans, "iso" if t == "date" else (f["placeholder"] or ""))
                if ans == "DECLINE":
                    continue
                loc.fill(str(ans))
                filled.append((label[:40], str(ans)[:30]))
                continue
            if f["tag"] == "select":
                ans = am.answer_for(label, jid)
                if ans is None:
                    if f["required"] and (f["value"] in ("", None)):
                        unknown.append(label)
                    continue
                if hasattr(ans, "strftime"):
                    continue
                opt = _pick([o for o in f["options"] if o and not re.match(r"^(select|choose|--)", o, re.I)], ans)
                if opt is None:
                    unknown.append(label)
                    continue
                loc.select_option(label=opt)
                filled.append((label[:40], opt))
        except Exception as e:  # noqa: BLE001
            unknown.append(f"{label} [{type(e).__name__}]")


def find_button(pg, rx):
    for el in pg.locator("button:visible, input[type=submit]:visible, a[role=button]:visible").all():
        try:
            txt = (el.inner_text() or el.get_attribute("value") or "").strip()
            if rx.match(txt):
                return el
        except Exception:  # noqa: BLE001
            continue
    return None


def apply_to_url(pg, url: str, resume: Path, dry_run: bool = True, job_id: str = "job") -> dict:
    """Drive one application form. Never guesses: unknown required questions park the job."""
    res = {"status": "failed", "detail": "", "unknown": [], "filled": [], "screenshot": None}
    shot = SHOTS / datetime.now().strftime("%Y-%m-%d") / f"{job_id}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_JOB["id"] = job_id
    url = normalize_url(url)
    t_start = time.time()
    if ONE_CLICK.search(urlparse(url).netloc):  # decided BEFORE any click: on these boards 'Apply' submits immediately
        res.update(status="board_direct", detail="one-click job board; needs its own flow, nothing was clicked")
        return res
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(2500)
        st = page_state(pg, confirm=True)   # nothing is filled yet: one early look at a slow SPA is not proof of a closure
        if st == "closed":
            # KB-60: on Oracle, ask the site's own requisition API before believing the page - a REMOVED req serves the same
            # shell as a live one (HTTP 200, role in og:title), and a live req whose SPA has not rendered must not be dropped.
            live = _oracle_live(pg.url)
            if live is True:
                st = "ok"
            elif live is False:
                res.update(status="closed", detail="the career site's own requisition API does not list this posting "
                                                   f"({pg.url[:80]}); page also reads closed")
                return res
        if st != "ok":
            res.update(status=st, detail=f"{st} page at {pg.url[:80]}")
            return res
        pg = follow_embedded_form(pg)
        pg = click_apply_if_needed(pg)
        pg.wait_for_timeout(2000)
        pg = follow_embedded_form(pg)
        if re.search(r"myworkday(jobs|site)\.com", pg.url):  # (myworkdaysite.com is Workday too: a company) careers site handed off to Workday: let the dedicated Workday flow take over
            res.update(status="redirect", detail=pg.url)
            return res
        clicked_final: set = set()
        otp_tries = 0  # times the emailed-code rescue ran in this apply_to_url (KB-49, max 2)
        last_sig = None
        for step in range(8):
            if time.time() - t_start > 240:  # hard per-job time budget: never let one site eat minutes
                res.update(status="failed", detail="time budget exceeded (4 min) on this site")
                return res
            sig = (pg.url, pg.evaluate("""() => [...document.querySelectorAll('input,select,textarea')].filter(e => {const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0})
                                           .map(e => (e.id || e.name || e.type) + ':' + (e.type === 'file' ? '' : (e.value || ''))).join('|')"""))
            if sig == last_sig and step > 0:  # clicked Next but the page did not change: stuck, stop instead of looping
                res.update(status="failed", detail="page did not advance after clicking Next")
                return res
            last_sig = sig
            st = page_state(pg, confirm=(step == 0))   # a company: "closed at step 0" on a posting a fresh browser shows live; look twice before step 1
            if st == "closed" and _oracle_live(pg.url) is True:  # KB-60: the site's own API still lists it
                st = "ok"
            if st == "closed" and not re.search(r"greenhouse\.io/[^?]*\?error=true", pg.url or ""):
                # The posting page itself passed page_state(confirm=True) above, so a verdict reached AFTER the Apply click describes the page the
                # click led to (Nespresso + a company: both postings are live in a fresh browser, both were filed closed at step 0).
                why = (LAST_CLOSED_HIT.get("text") or "")[:90]
                res.update(status="failed", detail=f"the page reached by clicking Apply did not show the next step at step {step} ({pg.url[:70]}); the posting page itself was open - retried later")
                print(f"   . closure after the Apply click ignored at step {step}: '{why}' on {pg.url[:90]}")   # the phrase stays out of `detail`: classify would read it as permanent
                return res
            if st != "ok":
                res.update(status=st, detail=f"{st} at step {step}")
                return res
            if pg.locator("input[type=password]:visible").count() and pg.locator("input[type=email]:visible,input[type=text]:visible").count() <= 2:
                res.update(status="login_required", detail="sign-in/sign-up wall: " + pg.url[:80])
                return res
            before_n = len(res["filled"])
            fill_page(pg, resume, res["unknown"], res["filled"])
            from core import activity
            activity.log("fill", f"filled {len(res['filled']) - before_n} field(s) on {urlparse(pg.url).netloc} (page {step + 1})"
                         + (f"; {len(res['unknown'])} still unanswered" if res["unknown"] else ""))
            if res["unknown"]:
                joined = "; ".join(dict.fromkeys(res["unknown"]))
                if otp_tries < 2 and CODE_UNKNOWN.search(joined):
                    otp_tries += 1
                    from apply import agent as _agent  # lazy: agent imports engine
                    try:
                        body = pg.inner_text("body")[:6000]
                    except Exception:  # noqa: BLE001
                        body = ""
                    note, _since = _agent.otp_rescue(pg, pg.context, {"text": body, "els": []},
                                                     {"company": CURRENT_JOB.get("company") or ""}, wait_s=90)
                    activity.log("otp", f"{urlparse(pg.url).netloc}: {note}")
                    if note.startswith("the emailed code"):
                        # the boxes hold the code now: never let stale "unanswered" labels park the job on the next pass
                        res["unknown"] = [u for u in res["unknown"] if not CODE_UNKNOWN.search(u)]
                        res["filled"].append(("verification code", "typed"))
                        verify = find_button(pg, VERIFY_BTN)
                        if verify:
                            try:
                                verify.click(timeout=6000)
                            except Exception:  # noqa: BLE001
                                pass
                        pg.wait_for_timeout(2500)
                        last_sig = None
                        continue
                    res.update(status="failed",
                               detail=(f"no verification email could be used for this page ({note}); "
                                       f"retried later, not a closure")[:220])
                    return res
                res.update(status="needs_you", detail="unanswered required: " + joined[:300])
                return res
            if not res["filled"]:
                res.update(status="no_form", detail="no fillable form found at " + pg.url[:80])
                return res
            final, nxt = find_button(pg, FINAL_BTN), find_button(pg, NEXT_BTN)
            if final:
                # a real application form has a resume upload, or name + email + phone all filled (a lone search/alert box is not one)
                labels = " ".join(k for k, _ in res["filled"]).lower()
                is_application = any(k == "resume" for k, _ in res["filled"]) or all(re.search(p, labels) for p in ("name", "mail", "phone|mobile"))
                if not is_application:
                    res.update(status="no_form", detail="page has a button but no application form (no resume upload, no identity fields)")
                    return res
                key = (pg.url, (final.inner_text() or "").strip())
                if key in clicked_final:  # never submit the same form twice
                    res.update(status="unconfirmed", detail="submit was already clicked once on this page; not clicking again")
                    return res
                if dry_run:
                    pg.screenshot(path=str(shot), full_page=True)
                    res.update(status="dry_run_ok", detail=f"filled {len(res['filled'])} fields; not submitted", screenshot=str(shot))
                    return res
                clicked_final.add(key)
                url_before = pg.url
                before_hit = SUCCESS.search(pg.inner_text("body")[:5000])
                final.click()
                pg.wait_for_timeout(5000)
                pg.screenshot(path=str(shot), full_page=True)
                res["screenshot"] = str(shot)
                body = pg.inner_text("body")[:5000]
                hit = SUCCESS.search(body)
                if hit and not (before_hit and before_hit.group(0) == hit.group(0) and pg.url == url_before):
                    res.update(status="applied", detail="confirmation text seen", proof=f"PAGE: '{hit.group(0)}' shown after Submit",
                               proof_shot=proof_shot(pg, job_id))
                elif pg.locator("[aria-invalid=true]:visible, [role=alert]:visible").count():
                    res.update(status="needs_you", detail="validation errors after submit; see screenshot")
                elif pg.url != url_before and pg.locator("input:visible,textarea:visible,select:visible").count() >= 3 and step < 7:
                    continue  # moved to a NEW page that has more fields: the 'final' button was really a step
                else:
                    res.update(status="unconfirmed", detail="submitted but no confirmation seen; see screenshot")
                return res
            if nxt:
                nxt.click()
                pg.wait_for_timeout(2500)
                continue
            res.update(status="no_form", detail="no submit/next button found")
            return res
        res.update(status="failed", detail="too many steps")
    except Exception as e:  # noqa: BLE001
        res.update(status="failed", detail=f"{type(e).__name__}: {str(e)[:120]}")
    try:
        pg.screenshot(path=str(shot), full_page=True)
        res["screenshot"] = str(shot)
    except Exception:  # noqa: BLE001
        pass
    return res
