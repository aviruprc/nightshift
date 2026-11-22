"""Naukri direct apply. Naukri applies with the profile resume, so for 'always tailored' we first upload the tailored CV to the profile.
Dry run never clicks Apply (one click = real submission)."""
import re
from datetime import datetime

from apply import engine

PROFILE = "https://www.naukri.com/mnjuser/profile"
DRAWER = ".chatbot_Drawer"  # the recruiter-question panel (".nI-gNb-drawer" is the header notifications drawer)

BUTTONISH = re.compile(r"^\s*(save|skip( this question)?|next|submit|apply|close|back|send|done)\s*$", re.I)
TEXT_FIELDS = ("input:not([type=file]):not([type=hidden]):not([type=submit]):not([type=button]):"
               "not([type=checkbox]):not([type=radio]):not([type=password]):visible, textarea:visible, "
               "[contenteditable=true]:visible")
QUESTIONISH = re.compile(r"^(are|do|does|did|have|has|how|what|when|where|which|who|why|will|would|can|could|is|should|may|"
                          r"please|kindly|enter|select|choose|provide|mention|tell)\b", re.I)



def _name_rx():
    try:
        from core import profile
        words = [w for w in profile.full_name().split() if len(w) >= 2]
    except Exception:  # noqa: BLE001
        words = []
    return re.compile(r"(?i)" + "|".join(r"\b" + re.escape(w) + r"\b" for w in words)) if words else None


_NAME_RX = _name_rx()   # the user's own name (cv/facts.yaml): a bubble carrying it is a greeting, never an answer chip

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (s or "").lower())).strip()


def _not_the_question(q: str, options: list[str]) -> list[str]:
    """Drop the question itself from the option list (KB-72).

    a company 17:31: `pick_option` answered "Do you have a bachelor's degree or higher qualification?" with an option of that same
    text - the drawer's own question bubble had been collected as a clickable answer by `_visible_options`, and the degree rule
    (`bachelor`, `qualification`) matched it. Clicking the question can never answer it: the panel did not advance."""
    qn = _norm(q)
    if not qn:
        return options
    out = []
    for o in options:
        on = _norm(o)
        if on == qn or (len(qn) >= 15 and qn in on):
            continue
        out.append(o)
    return out


def pick_option(q: str, options: list[str], ans):
    """Choose the option for a Naukri screening question. Notice period 'Immediate' = Naukri's 'Available now'."""
    options = _not_the_question(q, options)
    ql = q.lower()
    if "notice" in ql:
        for o in options:
            if re.search(r"available now|immediate|serving|0 ?days?|no notice", o, re.I):
                return o
    if ans is None or (isinstance(ans, str) and not ans.strip()):
        return None   # a blank answer must never fall through to the first chip (that would be a fabricated answer)
    # A degree-LEVEL dropdown ("Bachelor's Degree / Master's Degree / Doctorate") takes her real level, not the Yes that
    # answers the same question when the drawer offers Yes/No chips (a company). Her facts: BE + MBA.
    if re.search(r"bachelor|degree or higher|higher qualification|qualification", ql):
        for o in options:
            if re.search(r"master|post[- ]?grad|\bmba\b|\bm\.?\s?(tech|sc|com)\b", o, re.I):
                return o
        # Nothing master-level: the user HOLDS a bachelor's (cv/facts.yaml education), so a bachelor-level chip is still a true
        # statement - 'Bachelor of Engineering' / 'B.E/B.Tech' / 'Any Graduate' / 'Bachelor's Degree'. Never a doctorate, and never a
        # degree she does not hold (B.Com, B.Sc), which would be a false claim on a submitted form.
        for o in options:
            if re.search(r"bachelor(?:'s)? degree|undergrad|any graduate|^graduate$|\bb\.?\s?e\.?\b|\bb\.?tech\b|bachelor of engineering|^engineering\b",
                         o, re.I) and not re.search(r"doctor|ph\.?\s?d", o, re.I):
                return o
    a = str(ans).lower()
    # exact first, then whole-word ('Yes' must not match the concatenated 'YesNo' that inner_text can produce),
    # then prefix ('Immediate' -> 'Immediately available'). Order matters: DOM options follow the text lines.
    for o in options:
        if o.lower() == a:
            return o
    for o in options:
        if re.search(r"\b" + re.escape(a) + r"\b", o, re.I):
            return o
    for o in options:
        if o.lower().startswith(a):
            return o
    # a whole-number answer against RANGE chips ("0-1 years", "1 to 3", "3-5 yrs", "5+", "Less than 1"): the chip whose range holds it (a company Power BI,:
    # the answer 2 was right but no chip said "2", so a true answer was reported as 'needs the user'). On a shared edge (3 in "1-3" and "3-5") the LOWER range
    # wins: never claim more experience than her CV supports.
    if re.fullmatch(r"\d{1,2}", a.strip()):
        n = int(a.strip())
        for o in options:
            ol = o.lower().replace("years", " ").replace("yrs", " ").replace("year", " ").replace("yr", " ")
            m = re.search(r"(\d+)\s*(?:-|to|\u2013)\s*(\d+)", ol)
            if m and int(m.group(1)) <= n <= int(m.group(2)):
                return o
            m = re.search(r"(?:less than|under|below|<)\s*(\d+)", ol)
            if m and n < int(m.group(1)):
                return o
            m = re.search(r"(\d+)\s*\+|(?:more than|over|above|>)\s*(\d+)", ol)
            if m and n >= int(m.group(1) or m.group(2)):
                return o
    # Location chips are shorter than her recorded location: the answer is "Bengaluru, Karnataka, India" while the drawer
    # offers "Bengaluru" or "Bangalore" (a company were lost on this). Only her own city, from
    # answers.yaml, and only when the question is about where she is.
    if re.search(r"location|city|residing|based|located", ql):
        try:
            from core import profile
            city = (profile.answers().get("current_location") or "").split(",")[0].strip()
        except Exception:  # noqa: BLE001
            city = ""
        try:
            aliases = [str(x).lower() for x in (profile.answers().get("city_aliases") or [])]
        except Exception:  # noqa: BLE001
            aliases = []
        # a fixed order: iterating a SET made the chip chosen depend on the process's hash seed (two spellings of one city)
        for token in dict.fromkeys(t for t in [city.lower()] + aliases if t):
            for o in options:
                if re.search(r"\b" + re.escape(token) + r"\b", o, re.I):
                    return o
    return None


ATS_HOST_RX = re.compile(r"(myworkdayjobs|workday|successfactors|oraclecloud|greenhouse|lever\.co|smartrecruiters|icims|taleo|"
                         r"phenom|avature|jobvite|brassring|eightfold|workable|ashby)", re.I)


HANDOFF_LINKS_JS = """() => {
  const out = [];
  const add = u => { if (u && /^https?:/i.test(u) && !out.includes(u)) out.push(u); };
  for (const e of document.querySelectorAll('a[href], button, [role=button], [data-url], [data-href], [data-apply-url]')) {
    const t = (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!/apply on (the )?company('s)? site/i.test(t)) continue;
    const a = e.closest('a[href]');
    if (a) add(a.href);
    for (const k of ['data-url', 'data-href', 'data-apply-url', 'data-redirect', 'href', 'onclick']) {
      const m = (e.getAttribute(k) || '').match(/https?:\\/\\/[^'"\\s)<>]+/);
      if (m) add(m[0]);
    }
  }
  return out;
}"""


def company_site_target(pg):
    """(full_url | None, host | None) for the hand-off CONTROL itself ('Apply on company site').

    Only the control's OWN link counts. Any other external anchor on a job page is a recommendation, an ad or a social link:
    the old fallback named the first one, and two live rows were recorded as "Naukri sends this to the company site
    (www.ambitionbox.com)" - a guess presented as fact (KB-23). The full URL is kept only for a known ATS host so the
    Workday / Oracle / SuccessFactors adapter can route it; another non-Naukri host is named but never stored as an apply target."""
    try:
        links = pg.evaluate(HANDOFF_LINKS_JS) or []
    except Exception:  # noqa: BLE001
        return None, None
    host_of = lambda h: re.sub(r"^https?://([^/?#]+).*", r"\1", h).lower()  # noqa: E731
    for h in links:
        if ATS_HOST_RX.search(h):
            return h, host_of(h)
    for h in links:
        if "naukri.com" not in h.lower():
            return None, host_of(h)
    return None, None



def company_site_click_target(pg):
    """(full_url | None, host | None) read by CLICKING 'Apply on company site' and looking at the page it opens (: 242 jobs sat in `deferred_board` because that
    control is a JS button with no href, so the static read above never saw the target). Only opens the employer's page and closes it again: nothing is filled or sent."""
    try:
        btn = pg.get_by_text(re.compile(r"^\s*apply on (the )?company('s)? (site|website)\s*$", re.I)).first
        if not btn.count():
            return None, None
        with pg.context.expect_page(timeout=12000) as pi:
            btn.click(timeout=5000)
        np = pi.value
        try:
            np.wait_for_load_state("domcontentloaded", timeout=15000)
            np.wait_for_timeout(2500)
            u = np.url
        finally:
            try:
                np.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        return None, None
    if not re.match(r"^https?://", u or "", re.I) or "naukri.com" in u.lower():
        return None, None
    host = re.sub(r"^https?://([^/?#]+).*", r"\1", u).lower()
    # the page the control ITSELF opened is the employer's apply target, ATS or not (118 rows sat in
    # `deferred_board` because only known ATS hosts were kept). Aggregators and social sites are still never stored as a target.
    if ATS_HOST_RX.search(u) or not AGGREGATOR_RX.search(host or ""):
        return u, host
    return None, host


AGGREGATOR_RX = re.compile(r"ambitionbox|linkedin|indeed|glassdoor|monster|shine\.com|foundit|timesjobs|instahyre|cutshort|facebook|twitter|instagram|youtube|google\.", re.I)


def drop_history_chips(lines: list, qi: int, options: list) -> list:
    """Her EARLIER answers stay in the chat as bubbles (`[class*=ans]` matches them), so the DOM scan returns them as chips.
    a company (and): the 'English, Tamil' bubble of the languages question was offered as an
    option of 'Your Experience - Quality Analyst (BPO) or Customer Support?', the model chose it, the answer cache kept it, and the
    bot clicked the old bubble twice. A text that appears only ABOVE the current question is history, never a choice."""
    before, after = set(lines[:qi]), set(lines[qi + 1:])
    return [o for o in options if not (o in before and o not in after)]


def _visible_options(drawer) -> list[str]:
    """Every visible clickable answer in the drawer. The text-line parse misses custom chips, so ask the DOM too."""
    out: list[str] = []
    for sel in ("button:visible", "[role=button]:visible", "[role=option]:visible", "a:visible", "li:visible",
                "[class*=option]:visible", "[class*=chip]:visible", "[class*=ans]:visible"):
        try:
            for el in drawer.locator(sel).all():
                t = (el.inner_text() or "").strip()
                if (t and len(t) <= 260 and not BUTTONISH.match(t) and not re.search(r"^thank", t, re.I)
                        and not re.search(r"^(view profile|log ?out|sign out|help|privacy|terms)\b", t, re.I) and t not in out):
                    out.append(t)
        except Exception:  # noqa: BLE001
            continue
    return out


def _field_label(drawer) -> str:
    """Name the visible field. A required question we cannot read must still tell the user WHICH field (not 'could not read the question')."""
    try:
        for el in drawer.locator(TEXT_FIELDS).all():
            for attr in ("aria-label", "placeholder", "name", "id"):
                v = (el.get_attribute(attr) or "").strip()
                if v and not re.search(r"^(input|textarea|text|number|field|answer)$", v, re.I):
                    return v[:80]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _skip_offer(drawer) -> bool:
    """The PORTAL's own Skip: clicking it is the site's permitted way to leave an optional question, not our decision."""
    try:
        loc = drawer.get_by_text(re.compile(r"^\s*skip( this question)?\s*$", re.I))
        if loc.count() and loc.first.is_visible():
            loc.first.click(timeout=4000)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _canon(q: str) -> str:
    from apply import answers_map as am
    return am.canonical_question(q)


def _drawer_evidence(pg, drawer, tag: str) -> str:
    """Keep the question panel itself when a screening question cannot be answered (KB-59).

    Three rows were `failed` with "Naukri showed no option matching the recorded answer 'No'/'Yes'" - and the recorded
    detail was cut before the options list, the screenshot shows only that the drawer existed, and no DOM was kept, so the
    next occurrence could not be diagnosed. The panel is small: save its text and HTML under data/failures/."""
    try:
        d = engine.SHOTS.parent / "failures"
        d.mkdir(parents=True, exist_ok=True)
        job = engine.CURRENT_JOB or {}
        stem = (f"{datetime.now().strftime('%m%d_%H%M%S')}_"
                f"{(job.get('company') or 'naukri')[:12].replace(' ', '')}_{tag}")
        try:
            text = drawer.inner_text()
        except Exception:  # noqa: BLE001
            text = ""
        try:
            html = drawer.inner_html()
        except Exception:  # noqa: BLE001
            try:
                html = pg.content()
            except Exception:  # noqa: BLE001
                html = ""
        (d / f"{stem}.txt").write_text((text or "")[:40000], encoding="utf-8")
        (d / f"{stem}.html").write_text((html or "")[:400000], encoding="utf-8")
        return str(d / f"{stem}.txt")
    except Exception:  # noqa: BLE001
        return ""


def _no_option_message(q: str, ans, options: list[str]) -> str:
    """The options must survive truncation: 'no option matching No' without the list is not diagnosable (KB-59).

    The tail is built first and the QUESTION is what gets shortened, so a long question can never push the options out of
    the 300-character detail the runner stores."""
    seen = ", ".join((o or "").strip()[:45] for o in options[:4]) or "none"
    tail = f" (recorded answer '{ans}' matched none of: {seen})"
    head = _canon(q)[:max(0, 300 - len(tail))]
    return (head + tail)[:300]


DOB_BOXES = ("input[name='day']", "input[name='month']", "input[name='year']")


def dob_widget(drawer) -> bool:
    """True when the panel's current answer is Naukri's Date of Birth widget: three number boxes (day/month/year).

    a company: the drawer asked 'Date of Birth' as `input[type=number][name=day|month|year]` with a
    DISABLED Save, and `answer_chat` had no path for it - the generic text-field branch filled the DAY box (a number input)
    with the whole answer or with a city name and the panel never advanced, so the job parked as 'unanswered required:
    Current Location?' (the previous, already-answered question) with the real question invisible in the detail."""
    try:
        return all(drawer.locator(sel).count() for sel in DOB_BOXES)
    except Exception:  # noqa: BLE001
        return False


def fill_dob_widget(drawer) -> tuple[bool, list[str]]:
    """Type her recorded date of birth into the three boxes and verify each one. Never invents or guesses a part.

    Returns (complete, notes); complete is True only when all three boxes read back exactly what was typed."""
    from core import profile
    notes: list[str] = []
    try:
        dob = datetime.strptime(str(profile.answers().get("date_of_birth") or ""), "%d/%m/%Y")
    except Exception:  # noqa: BLE001
        return False, ["date of birth is not recorded in answers.yaml - not guessed"]
    ok = True
    for sel, val in zip(DOB_BOXES, (dob.day, dob.month, dob.year)):
        try:
            box = drawer.locator(sel).first
            box.fill(str(val), timeout=4000)
            # a react-style date widget enables its Save on CHANGE, not on input: commit the box the way a person leaving it does
            box.evaluate("e => { for (const t of ['input', 'change', 'blur']) "
                         "e.dispatchEvent(new Event(t, {bubbles: true})); }")
            shown = (box.input_value(timeout=3000) or "").strip()
        except Exception as e:  # noqa: BLE001
            notes.append(f"{sel}: could not be filled ({type(e).__name__})")
            ok = False
            continue
        if shown != str(val):
            notes.append(f"{sel}: shows '{shown}' after typing '{val}'")
            ok = False
        else:
            notes.append(f"{sel}: set to {val}")
    return ok, notes


def answer_chat(pg, job_id: str):
    """Naukri's recruiter-question panel: answer each question from saved answers.
    Returns (done, unanswered_question). An unreadable OPTIONAL question is skipped; a required one is parked
    naming its field; a question that is her decision is parked with its exact wording (never answered by a guess)."""
    from core import activity
    from apply import answers_map as am
    drawer = pg.locator(DRAWER).first
    last_q = None
    dob_done = False   # the DOB widget is answered ONCE; seeing it again means the panel is stuck (KB-72)
    last_clicked = ""   # what the previous turn actually clicked, so 'the question did not advance' names it
    for _ in range(12):
        pg.wait_for_timeout(1800)
        text = drawer.inner_text() if drawer.count() else ""
        if re.search(r"successfully applied|applied to|you have applied", text, re.I) or not drawer.count() or not drawer.is_visible():
            return True, None
        lines = [x.strip() for x in text.split("\n") if x.strip()]
        if dob_widget(drawer):
            # KB-69: the panel's Date of Birth is day/month/year boxes with a disabled Save - not a chip and not one text box.
            # KB-72: the widget stays in the panel's DOM, so it must be handled ONCE: refilling it every turn burned all 12 turns
            # and reported "too many questions" without naming anything (a company). A second sighting means the panel did
            # not advance -> keep the evidence and park naming the question.
            if dob_done:
                _drawer_evidence(pg, drawer, "dob_stuck")
                return False, ("unanswered required: Date of Birth (the panel did not advance after day/month/year were filled; "
                               "the panel is saved in data/failures)")[:300]
            ok_dob, dob_notes = fill_dob_widget(drawer)
            activity.log("portal", f"Naukri: date of birth widget - {'; '.join(dob_notes)[:160]}", portal="Naukri")
            if not ok_dob:
                _drawer_evidence(pg, drawer, "dob")
                return False, ("unanswered required: Date of Birth (Naukri's day/month/year boxes did not take the value: "
                               + "; ".join(dob_notes)[:150] + ")")[:300]
            pg.wait_for_timeout(800)
            try:
                sv = drawer.get_by_text("Save", exact=True)
                if sv.count():
                    sv.first.click(timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            dob_done = True
            pg.wait_for_timeout(1200)
            continue
        if re.search(r"thank you for your responses?", text, re.I):  # a company: every answer is taken; only Save is left
            sv = drawer.get_by_text("Save", exact=True)
            try:
                if sv.count():
                    sv.first.click(timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            pg.wait_for_timeout(1500)
            return True, None
        qs = [i for i, x in enumerate(lines) if x.endswith("?") or QUESTIONISH.match(x)]
        if not qs:  # a label without "?" (e.g. "Current CTC (in LPA)"): the prompt opens the trailing block
            cand = [i for i, x in enumerate(lines) if len(x) >= 8 and not BUTTONISH.match(x) and not re.search(r"^(hi|hello|thank)", x, re.I)]
            if not cand:
                # unreadable block (short lines, no prompt). Do not block the whole application on an OPTIONAL question: the
                # portal's own Skip is used when offered. If it is required, name the field so the reason is actionable.
                if _skip_offer(drawer):
                    job = engine.CURRENT_JOB
                    activity.log("portal", f"Naukri: skipped an optional question whose text the bot could not read ({job.get('company', '')} - {job.get('title', '')})", portal="Naukri")
                    pg.wait_for_timeout(800)
                    continue
                field = _field_label(drawer) or "unlabelled field"
                activity.log("portal", f"Naukri: required field '{field}' has illegible question text", portal="Naukri")
                _drawer_evidence(pg, drawer, "illegible")
                return False, f"required field '{field}' (Naukri's question text was not legible; the panel is saved in data/failures)"
            qs = [cand[-6:][0]]  # question opens the trailing block (a company: long sentence options broke "longest line")
        qi = qs[-1]
        q = lines[qi]
        if q == last_q:
            # nothing advanced after an answer: report the exact question, not the whole drawer dump
            activity.log("portal", f"Naukri: question did not advance; drawer showed: {' / '.join(lines)[:240]}", portal="Naukri")
            # A bare question says nothing about WHY it did not advance: name what was clicked and what the drawer still shows.
            _drawer_evidence(pg, drawer, "stuck")
            tail = " / ".join(lines[-6:])[:110] or "nothing readable"
            why = f" (after clicking '{last_clicked}', the drawer did not advance; it still shows: {tail})" if last_clicked else ""
            return False, (_canon(q) + why)[:300]
        last_q = q
        # lines AFTER the question are the options (history above is not); the DOM scan is appended because inner_text
        # can glue adjacent flex chips into one line ("YesNo"). Exact matching in pick_option keeps the chips distinct.
        options = [x for x in lines[qi + 1:] if not BUTTONISH.match(x) and len(x) < 260 and not re.search(r"^thank you", x, re.I)]
        options += [o for o in _visible_options(drawer) if o not in options]
        options = drop_history_chips(lines, qi, options)
        options = [o for o in options if not (_NAME_RX and _NAME_RX.search(o)) and not re.search(r"(?i)^\W*(hi|hello|hey|dear|welcome)\b|thank you for|thanks for", o.strip())]   # the bot's greeting / thanks bubbles are chat text, never chips (: "Hi <name>, thank..." counted as an option; a chip never carries the user's name)
        options = [o for o in options if not o.rstrip().endswith("?")]   # an EARLIER question in the chat history is never a chip (a company: 'Are you ready to join immediately...?' was counted)
        options = _not_the_question(q, options)   # the drawer's own question bubble is not a chip: with it counted, a plain number field never got filled (a company: answer '2', chips=[the question itself])
        field = drawer.locator(TEXT_FIELDS)
        ans = am.answer_for(q, job_id)
        if isinstance(ans, str) and re.fullmatch(r"\d{1,2}", ans.strip()) and options and field.count():
            # A number answer with a visible text field: bare-number "chips" are the chat's echo of my EARLIER answers (a company: answering SQL = 3 saw chips=['2'],
            # the Power BI answer just typed) and word bubbles are history ('Yes'). Only range/descriptive chips ("1-3 years") are real choices.
            options = [o for o in options if re.search(r"\d\s*(-|to|\+)|year|yr|less|more|above|below|under|over", o, re.I) and not re.fullmatch(r"\d{1,2}", o.strip())]
        if ans is None or (options and pick_option(q, options, ans) is None):
            if am.needs_her(q):
                # her recorded facts do not answer this: park it as ONE stable question. Never let a model invent a
                # skill-years figure or a commercial preference.
                job = engine.CURRENT_JOB
                activity.log("portal", f"Naukri: needs the user's answer for {job.get('company', '')} - {job.get('title', '')}: {_canon(q)}", portal="Naukri")
                # evidence: was it NO answer, or an answer that matched no chip? The two need different fixes.
                activity.log("portal", f"Naukri: evidence for that park: answer={ans!r}; chips={[o[:24] for o in options][:8]}", portal="Naukri")
                return False, _canon(q)
            from apply import llm_answer
            # a company: with a hidden text field present the LLM got no options and answered in its own sentence
            got = llm_answer.answer([{"q": q, "options": options if options else None}], {**engine.CURRENT_JOB, "id": job_id})
            ans = got.get(q, ans)
        if hasattr(ans, "strftime"):
            ans = ans.strftime("%d/%m/%Y")
        # A drawer can show BOTH a text box and stray option-ish lines (chat history above/below): the text box is still the
        # answer target when none of the options matches the recorded answer (a company: a Date of Birth typed as
        # a recorded date was parked because `options` was non-empty, so the field was never filled).
        if field.count() and not pick_option(q, options, ans):
            if ans is None:
                if _skip_offer(drawer):
                    continue
                return False, _canon(q)
            field.first.fill(str(ans))
            activity.log("portal", f"Naukri: answered '{q[:80]}' = '{ans}' ({am.source_of(q, ans)})", portal="Naukri")
            if not drawer.get_by_text("Save", exact=True).count():  # a company: the chat box only accepts the answer on Enter
                field.first.press("Enter")
        else:
            opt = pick_option(q, options, ans)
            if opt is not None and ans is not None:
                try:
                    if not drawer.get_by_text(opt, exact=True).count():  # gluing/prose: pick the real clickable chip instead
                        # inner_text glues adjacent chips into one line ('Bachelor's DegreeMaster's DegreeDoctorate'), so the chosen text
                        # is not clickable. Re-run the chooser ONCE over the real chips: ranking preserved (Master's before Bachelor's),
                        # unlike re-running it per option, where the first chip won by position (a company).
                        vis = _visible_options(drawer)
                        best = pick_option(q, vis, ans) if vis else None
                        if best:
                            opt = best
                        else:
                            want = re.sub(r"\s+", " ", opt).strip().lower()
                            for o in vis:
                                oo = re.sub(r"\s+", " ", o).strip().lower()
                                if oo == want or (want and want in oo):
                                    opt = o
                                    break
                except Exception:  # noqa: BLE001
                    pass
            if opt is None and ans is not None:  # an answer whose chip text the drawer renders differently
                try:
                    loc = drawer.get_by_text(str(ans), exact=True)
                    if loc.count() and loc.last.is_visible():
                        opt = str(ans)
                except Exception:  # noqa: BLE001
                    pass
            if opt is None:
                if _skip_offer(drawer):
                    continue
                if ans is not None:
                    # name the options: "no option matching 'Yes'" hid whether the drawer offered Yes/No or a degree-level
                    # dropdown (a company). The next occurrence is then diagnosable from the row itself.
                    # KB-59: the answer and the options come FIRST so a long question cannot truncate them, and the panel is saved.
                    _drawer_evidence(pg, drawer, "nomatch")
                    return False, _no_option_message(q, ans, options)
                _drawer_evidence(pg, drawer, "noanswer")
                return False, _canon(q)
            activity.log("portal", f"Naukri: answered '{q[:80]}' = '{opt[:40]}' ({am.source_of(q, ans)})", portal="Naukri")
            last_clicked = opt[:60]
            try:
                drawer.get_by_text(opt, exact=True).last.click(timeout=4000)  # .last: chat history also contains old "Yes" answers (a company)
            except Exception:  # noqa: BLE001 (a company: a single-choice answer closed the drawer, so the click timed out)
                continue  # the next loop turn re-reads the drawer: gone or "applied" -> done, otherwise reported properly
        pg.wait_for_timeout(500)
        save = drawer.get_by_text("Save", exact=True)
        try:
            if save.count():
                save.first.click(timeout=4000)
        except Exception:  # noqa: BLE001
            continue
    # KB-72: "too many questions" named nothing, so a looping panel could not be diagnosed. Save the panel and name the last
    # question that was actually asked.
    _drawer_evidence(pg, drawer, "toomany")
    return False, (f"unanswered required: too many questions ({12} turns); last asked: {_canon(last_q) if last_q else 'unknown'}")[:300]


def upload_profile_resume(ctx, pdf, tries: int = 2) -> bool:
    """Upload the tailored CV to the Naukri profile; confirmed only by the page's own "Uploaded on <today>" line.
    a company (first jobs after the machine woke): ONE slow page load returned False and the job was parked as `needs_you`
    ("could not upload") although the very next job uploaded fine. One failed load is a page state, not a verdict: try again."""
    for attempt in range(max(1, tries)):
        pg = ctx.new_page()
        try:
            pg.goto(PROFILE, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(4500)
            pg.locator("input[type=file]").first.set_input_files(str(pdf))
            pg.wait_for_timeout(8000)
            if f"Uploaded on {datetime.now().strftime('%b %d, %Y')}" in pg.inner_text("body"):
                return True
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                pg.close()
            except Exception:  # noqa: BLE001
                pass
    return False


def walk_in_detail(body: str, texts) -> str:
    """'' unless the page is a walk-in interview listing (in-person, dated venue, 'I am interested' instead of Apply)."""
    low = (body or "")[:3000].lower()
    if "apply" in texts or not re.search(r"\bwalk[- ]?in\b", low):
        return ""
    if "time and venue" not in low and "i am interested" not in texts:
        return ""
    m = re.search(r"time and venue\s*(.{0,160})", re.sub(r"\s+", " ", body or ""), re.I)
    when = (m.group(1).strip() if m else "")[:140]
    return (f"walk-in interview listing (in person{': ' + when if when else ''}): Naukri offers only 'I am interested', no apply flow - "
            f"attending is her decision")[:280]


def apply_naukri(ctx, url: str, pdf, dry_run: bool, job_id: str) -> dict:
    res = {"status": "failed", "detail": "", "unknown": [], "filled": [], "screenshot": None}
    shot = engine.SHOTS / datetime.now().strftime("%Y-%m-%d") / f"{job_id}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    pg = ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(4000)
        body = pg.inner_text("body")[:6000]
        if re.search(r"no longer (accepting|available|open)|job (has )?expired|job you are looking for", body, re.I):
            res.update(status="closed", detail="Naukri listing closed")
            return res
        texts = {}
        for el in pg.locator("button:visible, a:visible").all()[:120]:
            try:
                texts[(el.inner_text() or "").strip().lower()] = el
            except Exception:  # noqa: BLE001
                continue
        applied_badge = pg.get_by_text("Applied", exact=True)
        if "applied" in texts or (applied_badge.count() and applied_badge.first.is_visible() and "apply" not in texts):
            res.update(status="already_applied", detail="already applied on Naukri")
            return res
        if "apply on company site" in texts and "apply" not in texts:
            # KB-23: 151 jobs stopped here with no idea where they went. A signed-out page shows no apply control at all,
            # so the only place the hand-off target can be read is this signed-in context: record it (host always, full
            # URL when it points at a known ATS) so the next step stops being a guess.
            url, host = company_site_target(pg)
            if not url:   # no static link: click the control and read where it goes
                url2, host2 = company_site_click_target(pg)
                url, host = url2 or url, host2 or host
            detail = "Naukri sends this to the company site" + (f" ({host})" if host else "")
            if url:
                detail += f" -> {url}"
            res.update(status="board_direct", detail=detail[:280])
            if url:  # KB-23 step 2: a known ATS target is stored and routed on the next pass instead of dying here
                res["apply_url"] = url
            return res
        btn = texts.get("apply") or (pg.locator("#apply-button").first if pg.locator("#apply-button").count() else None)
        if btn is None and walk_in_detail(body, texts):
            # a company: "Walk-in || Administrative Support Specialist ...": the page offers "I am interested" and a
            # Time and Venue block (21 November, -, an address). It is an in-person interview, not an application flow, so
            # "no Apply button" was neither a layout change nor retryable. Attending is HER decision: park it with the facts.
            pg.screenshot(path=str(shot), full_page=True)
            res.update(status="needs_you", screenshot=str(shot), detail=walk_in_detail(body, texts))
            return res
        if btn is None:
            # this used to park the job as needs_you on the guess "(signed out or layout changed)" and saved NO evidence, so the
            # two causes could not be told apart afterwards. A signed-out session is OURS to fix (sign in again, retry); only a signed-in
            # page with no Apply control is a job/layout question for her - and either way the page is now saved.
            pg.screenshot(path=str(shot), full_page=True)
            signed_in = bool(pg.locator('a[href*="/mnjuser"], a[href*="nlogin/logout"], [class*="user-name"]').count()) or "my naukri" in body.lower()
            if not signed_in:
                res.update(status="failed", screenshot=str(shot),
                           detail="Naukri page is signed out (no Apply button and no signed-in header); signing in again on the next attempt. "
                                  "Not a job problem")
                return res
            res.update(status="needs_you", screenshot=str(shot),
                       detail="no Apply button on a signed-in Naukri page (layout change or the listing moved); page saved as evidence")
            return res
        if dry_run:
            pg.screenshot(path=str(shot), full_page=True)
            res.update(status="dry_run_ok", detail="Naukri Apply button found; not clicked", screenshot=str(shot))
            return res
        from core import activity
        activity.log("portal", "Naukri: uploading the tailored CV to the profile", portal="Naukri")
        if not upload_profile_resume(ctx, pdf):
            res.update(status="failed", detail="the Naukri profile page did not confirm the CV upload after 2 tries (page or network slow); retried later")
            return res
        btn.click()
        pg.wait_for_timeout(4500)
        activity.log("portal", "Naukri: clicked Apply", portal="Naukri")
        if pg.locator(DRAWER).count() and pg.locator(DRAWER).first.is_visible():  # recruiter questions: answer them automatically
            activity.log("portal", "Naukri: answering the recruiter's questions", portal="Naukri")
            done, q = answer_chat(pg, job_id)
            if not done:
                pg.screenshot(path=str(shot), full_page=True)
                res.update(status="needs_you", detail=f"unanswered required: {q}", screenshot=str(shot))
                try:  # KB-59: keep the page + element snapshot for EVERY unanswered Naukri question, not just the drawer text
                    from apply import agent as _agent  # lazy: agent imports engine
                    _agent._save_failure(pg, engine.CURRENT_JOB or {}, res)
                except Exception:  # noqa: BLE001
                    pass
                return res
            pg.wait_for_timeout(2500)
        pg.screenshot(path=str(shot), full_page=True)
        res["screenshot"] = str(shot)
        body = pg.inner_text("body")[:6000]
        applied_btn = any((e.inner_text() or "").strip().lower() == "applied" for e in pg.locator("button:visible").all()[:80])
        if re.search(r"successfully applied|you have successfully applied|applied to", body, re.I) or applied_btn:
            res.update(status="applied", detail="Naukri confirmed the application",
                       proof="PAGE: Naukri shows 'Applied' after clicking Apply" if applied_btn else "PAGE: Naukri 'successfully applied' message",
                       proof_shot=engine.proof_shot(pg, job_id))
        elif pg.locator("[class*=chatbot], [class*=Drawer], [class*=questionnaire]").count():
            res.update(status="needs_you", detail="unanswered required: Naukri asks extra questions; open the job to answer them")
        else:
            res.update(status="unconfirmed", detail="clicked Apply; no confirmation seen")
    except Exception as e:  # noqa: BLE001
        res.update(status="failed", detail=f"{type(e).__name__}: {str(e)[:100]}")
    finally:
        try:
            pg.close()
        except Exception:  # noqa: BLE001
            pass
    return res
