"""Capability toolkit: one robust, self-verifying tool per widget KIND, shared by every portal (Workday, Oracle, custom sites, the LLM
navigator). Fix a widget problem here once and every portal benefits — no per-portal testing.

  choose_searchable(pg, box, value) type-to-search pickers (school, skills, how-did-you-hear, country): tries several search terms,
                                      fuzzy-picks the best option, supports multi-value, verifies the selection registered
  search_terms(value) the search terms to try for a long value ("Larkmoor University, WSS Graduate School..." -> Larkmoor, WSS...)
"""
import re

from rapidfuzz import fuzz

GENERIC = {"university", "college", "institute", "school", "of", "and", "the", "graduate", "business", "technology", "research", "for",
           "management", "studies", "engineering", "science", "sciences", "advanced", "processes", "analysis", "development"}
OPTION_SEL = ("[data-automation-id=promptOption]:visible, [data-automation-id=menuItem]:visible, [role=option]:visible, "
              "li[role=option]:visible, [class*=option i]:visible")
SELECTED_SEL = "[data-automation-id=selectedItem], [data-automation-id=selectedItemList] *, [class*=selected i] [class*=chip i], [class*=multiValue i]"


def search_terms(value: str) -> list[str]:
    """Most specific first: the whole value, each comma part, acronyms (WSS, GSB), then distinctive words (Larkmoor, Brightwater)."""
    v = re.sub(r"\s+", " ", str(value or "")).strip()
    out = [v]
    parts = [p.strip() for p in re.split(r"[,;/()]", v) if p.strip()]
    out += parts
    out += [w for w in re.findall(r"\b[A-Z]{2,6}\b", v)]
    out += [w for w in re.findall(r"[A-Za-z][A-Za-z&.+-]{3,}", v) if w.lower() not in GENERIC]
    seen, res = set(), []
    for t in out:
        k = t.lower()
        if t and k not in seen and len(t) <= 60:
            seen.add(k)
            res.append(t)
    return res[:8]


def _best(options: list[str], value: str, term: str) -> tuple[str | None, int]:
    best, score = None, 0
    for o in options:
        s = max(fuzz.token_set_ratio(o.lower(), value.lower()), fuzz.partial_ratio(term.lower(), o.lower()) - 5)
        if s > score:
            best, score = o, s
    return best, score


def _pick_best(options: list[str], value: str, term: str) -> tuple[str | None, int]:
    """An EXACT option always wins: on the Workday phone-device list 'Mobile' also fuzzy-matches 'Mobile - BCM**', and the first of the two
    may be the wrong one (a company)."""
    want = str(value or "").strip().casefold()
    for o in options:
        if o.strip().casefold() == want:
            return o, 100
    return _best(options, value, term)


def _picked_shown(shown: str, pick: str) -> bool:
    """True when the widget really DISPLAYS the option we clicked (not merely 'something changed', which is how Landline could pass for
    Mobile). Portals shorten text, so the first words of the option are enough."""
    a = (shown or "").lower()
    p = (pick or "").lower().strip()
    if not p or not a:
        return False
    if p[:15] in a:
        return True
    if len(a) >= 3 and a in p:   # contains both ways: a widget may show 'India' where the option was 'India (+91)'
        return True
    words = re.findall(r"[a-z]{3,}", p)
    return bool(words) and all(w in a for w in words[:2])


def _selected_count(box) -> int:
    try:
        return box.locator(SELECTED_SEL).count()
    except Exception:  # noqa: BLE001
        return 0


def choose_searchable(pg, box, value: str, multi: bool = False, min_score: int = 72, fallback: str | None = None,
                      verify_wanted: bool = True) -> list[str]:
    """Pick `value` in a type-to-search picker inside `box`. multi=True: value may list several items ("SQL, Tableau, Power BI").
    Returns the option texts actually selected (verified by the selected-item count or the box text).

    verify_wanted (a company): the clicked option must BE the value we need. A picker that lands on the first suggestion
    ('Earth sciences' when we asked for 'Finance') is reported as unfilled, never accepted; only 'how did you hear' (verify_wanted=False)
    may take any plausible source because it is not a factual claim."""
    values = [x.strip() for x in re.split(r"[,;]", value) if x.strip()] if multi else [value]
    picked = []
    inp = box.locator("input:visible").first
    for val in values:
        done = False
        for term in search_terms(val) + ([fallback] if fallback else []):
            try:
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(250)
                before = _selected_count(box)
                inp.click(timeout=3000)
                inp.fill("")
                inp.type(term, delay=40)
                pg.keyboard.press("Enter")
                pg.wait_for_timeout(1600)
                for _ in range(3):  # nested categories: drill down until something is selected
                    opts = pg.locator(OPTION_SEL)
                    texts = [t.strip() for t in opts.all_inner_texts()[:60] if t.strip() and len(t.strip()) < 120]
                    if not texts:
                        break
                    pick, sc = _best(texts, val if term != fallback else fallback, term)
                    if not pick or (sc < min_score and term != fallback and len(texts) > 1):
                        break
                    opts.filter(has_text=re.compile(rf"^\s*{re.escape(pick)}\s*$")).first.click(timeout=3000, force=True)
                    pg.wait_for_timeout(1200)
                    shown_now = (box.inner_text() or "") + " " + (inp.input_value() or "")
                    want_now = fallback if (fallback and term == fallback) else val
                    if verify_wanted and not _picked_shown(shown_now, want_now):
                        break   # the option that was clicked is NOT the value we need: try the next term, never accept a suggestion
                    if _selected_count(box) > before or pick.lower()[:20] in shown_now.lower():
                        picked.append(pick)
                        done = True
                        break
                if done:
                    break
            except Exception:  # noqa: BLE001
                continue
        if not done and not multi:
            break
    try:
        pg.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    return picked


HEAR = re.compile(r"how did you (hear|find)|where did you (hear|find)|source|referral source|heard about", re.I)
HEAR_PREF = ["company career site", "careers", "company website", "corporate website", "career site", "website", "linkedin", "job board", "internet", "online", "other"]
LISTBOX_SEL = (OPTION_SEL + ", [role=listbox] li:visible, [role=listbox] [role=presentation]:visible, ul[class*=menu i] li:visible, "
               "[class*=dropdown i] li:visible, [class*=select__option i]:visible")


def _visible_options(pg, loc=None) -> list[str]:
    """Every visible option-ish text on the page. The read must survive ONE bad element: on the real a company page
    `all_inner_texts` returns a None for an unrelated `[class*=option]` element, and the old single try/except turned that into an
    EMPTY LIST - so the picker reported 'the list never opened' while the list was open on screen and the application died with
    'could not set Phone Device Type'."""
    raw: list = []
    if loc is not None:
        try:  # the list THIS control opened (aria-controls="<id>"): Workday's a company page also has LinkedIn 'option' widgets
            cid = loc.evaluate("e => e.getAttribute('aria-controls') || ''")
        except Exception:  # noqa: BLE001
            cid = ""
        if cid:
            try:
                raw = pg.locator(f'[id="{cid}"] [role=option]:visible, [id="{cid}"] li:visible').all_inner_texts()[:120]
            except Exception:  # noqa: BLE001
                raw = []
    if not raw:
        try:  # any open listbox next: page-wide text also matches unrelated widgets
            raw = pg.locator("[role=listbox]:visible [role=option]:visible, [role=listbox]:visible li:visible").all_inner_texts()[:120]
        except Exception:  # noqa: BLE001
            raw = []
    if not raw:
        try:
            raw = pg.locator(LISTBOX_SEL).all_inner_texts()[:120]
        except Exception:  # noqa: BLE001
            return []
    return [t.strip() for t in raw if t and len(t.strip()) < 140]


def _shown_value(loc) -> str:
    try:
        return loc.evaluate("e => (e.value || e.innerText || e.getAttribute('aria-label') || '') + ' ' + ((e.closest('[class*=field i],[class*=form i],div') || {}).innerText || '').slice(0, 120)")
    except Exception:  # noqa: BLE001
        return ""


PLACEHOLDER = re.compile(r"^\s*(select( one| an option| a value)?|choose( one| an option)?|please (select|choose)|none|-+.*-+)\s*$", re.I)

# Workday's My Information step marks these required but does not expose them to the normal field scan.
WORKDAY_REQUIRED = (
    (re.compile(r"State\b.{0,40}required", re.I),
     ('[data-automation-id="addressSection_countryRegion"]', '[data-automation-id*="countryRegion"]',
      '[data-automation-id="formField-countryRegion"] button[aria-haspopup="listbox"]',
      '[data-automation-id="formField-countryRegion"] button'), "{current_state}", "State"),
    (re.compile(r"Country Phone Code", re.I),
     ('[data-automation-id="countryPhoneCode"]', '[data-automation-id="formField-countryPhoneCode"] input',
      '[data-automation-id="formField-countryPhoneCode"] [data-automation-id="multiSelectContainer"] input',
      '[data-automation-id="formField-countryPhoneCode"] button'), "{phone_code}", "Country Phone Code"),
    # a company: the button lives inside `formField-phoneType` with the accessible name 'Phone Device Type Select One
    # Required' and its list starts with Landline / Landline - BCM**, so the old data-automation-id=phoneDeviceType lookup never found it.
    (re.compile(r"Phone Device Type", re.I),
     ('[data-automation-id="phoneDeviceType"]', '[data-automation-id*="phoneDevice"]',
      '[data-automation-id="formField-phoneType"] button[aria-haspopup="listbox"]',
      '[data-automation-id="formField-phoneType"] button', '[name="phoneType"][aria-haspopup="listbox"]',
      'button[aria-label^="Phone Device Type"]'), "Mobile", "Phone Device Type"),
)


def shown_text(loc) -> str:
    """The value a widget actually DISPLAYS (for a native <select>: the chosen option, not every option in the list)."""
    try:
        return (loc.evaluate("e => { if (e.tagName.toLowerCase() === 'select') { const o = e.selectedOptions && e.selectedOptions[0];"
                             " return o ? o.text : ''; } return e.value || e.innerText || ''; }") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH_FULL = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
DATE_LABEL = re.compile(r"\b(from|to|start|end|since|until|date|joined|joining|left|period|month)\b", re.I)


def month_year(text: str):
    """(month, year) a piece of text states, or None when it is not clearly ONE month of ONE year.
    '03/2024', '2024-03', '', 'Mar 2024', 'March 2024', '01/03/2024' is NOT accepted (day-month order is ambiguous)."""
    t = str(text or "").strip().lower()
    m = re.fullmatch(r"(\d{1,2})\s*[/.-]\s*((?:19|20)\d\d)", t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"((?:19|20)\d\d)\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*\d{1,2})?(?:[t ].*)?", t)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
        else:
            m = re.fullmatch(r"([a-z]{3,9})\.?,?\s+((?:19|20)\d\d)", t)
            if not m or m.group(1)[:3] not in _MONTH_NAMES:
                return None
            mo, y = _MONTH_NAMES.index(m.group(1)[:3]) + 1, int(m.group(2))
    return (mo, y) if 1 <= mo <= 12 else None


def fill_month_year(pg, loc, value: str, label: str = ""):
    """Type a month-and-year into a date box and VERIFY what the box then shows.

    None = not a month-year value for a date-like field (the caller's plain fill has been done here all the same).
    True = the box shows that month and year (or shows something this helper cannot read, in which case nothing is claimed).
    False = every usual format was tried and the box still shows a DIFFERENT month or year; the box is left empty.

     SmartRecruiters (experience entry, a month-year picker that reads YYYY-MM-DD): "06/2023" was typed, the widget stored January 2023,
    and the navigator typed "06/2023" again three times. A submitted application cannot be edited, so a wrong month must never be saved."""
    want = month_year(value)
    if not want or not DATE_LABEL.search(label or ""):
        loc.fill(value, timeout=5000)
        return None
    mo, y = want
    tries = [value, f"{y}-{mo:02d}-01", f"{y}-{mo:02d}", f"{mo:02d}/{y}", f"{_MONTH_FULL[mo - 1]} {y}", f"{_MONTH_FULL[mo - 1][:3]} {y}", f"{mo:02d}/01/{y}"]
    seen_other = False
    for v in dict.fromkeys(tries):
        try:
            loc.fill(v, timeout=5000)
            loc.press("Enter")
            pg.wait_for_timeout(250)
            loc.evaluate("e => e.blur()")
            pg.wait_for_timeout(350)
        except Exception:  # noqa: BLE001
            continue
        got = month_year(shown_text(loc))
        if got == want:
            return True
        if got is None and shown_text(loc):
            return True                      # shown in a form this helper cannot read (e.g. only "2023"): nothing is claimed either way
        seen_other = seen_other or got is not None
    if seen_other:
        try:
            loc.fill("", timeout=3000)       # never leave a wrong month behind
        except Exception:  # noqa: BLE001
            pass
        return False
    return True


def is_unset(loc) -> bool:
    """Empty OR showing a placeholder. a company: the State picker showed 'Select'; the old check saw non-empty text and skipped the fix,
    so the navigator looped and gave up with a false 'page stopped changing'."""
    shown = shown_text(loc)
    return not shown or bool(PLACEHOLDER.match(shown))


# A container is not a field. a company (Workday wd5): `[data-automation-id*="countryRegion"]` matched the WRAPPING
# `<div data-automation-id="formField-countryRegion">`, whose innerText ("State * Select One") is not a placeholder, so the helper
# believed State was already answered, reported nothing in `failed`, and four a company jobs died with "page stopped changing ".
CONTROL_SEL = ('[role="combobox"], [aria-haspopup="listbox"], select, textarea, '
               'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"])')
CONTAINER_TAGS = {"div", "span", "label", "section", "article", "li", "ul", "ol", "td", "th", "tr", "tbody", "table",
                  "fieldset", "form", "header", "footer", "p", "main", "body"}


def phone_device_value() -> str:
    """Her phone type as RECORDED in answers.yaml ('phone_device_type'), never guessed. a company's list offers only
    'Mobile/Android', 'Mobile/Apple iOS' and 'Mobile - Other (Non Iphone/Android)': fuzzy-picking one is a fabricated fact about her phone,
    and Workday may text a verification code to that device."""
    try:
        from core import profile
        a = profile.answers() or {}
    except Exception:  # noqa: BLE001
        return ""
    if str(a.get("phone_device_type") or "").strip():
        return str(a["phone_device_type"]).strip()
    for k, v in a.items():  # the dashboard keys an answer by the question text it showed, which also names the options
        if str(k).lower().startswith("phone_device_type") and str(v or "").strip():
            return str(v).strip()
    return ""


def phone_device_wants() -> list[str]:
    """Option texts to try (each matched EXACTLY) for the Phone Device Type list: her recorded value, plus the usual wordings of an iPhone / Android
    option, because tenants word them differently ('Mobile/Apple iOS', 'iPhone', 'Mobile - iOS'). Nothing is ever fuzzy-picked."""
    rec = phone_device_value()
    low = rec.lower()
    extra: list[str] = []
    if re.search(r"ios|iphone|apple", low):
        extra = ["Mobile/Apple iOS", "Apple iOS", "iOS", "iPhone", "Mobile - iOS", "Mobile iOS", "Mobile/iOS", "Mobile - iPhone"]
    elif "android" in low:
        extra = ["Mobile/Android", "Android", "Mobile - Android", "Mobile Android"]
    return list(dict.fromkeys(w for w in ([rec] if rec else []) + extra))


def field_shows_selection(loc, value: str = "") -> bool:
    """True when the FIELD around the control already shows a chosen value. Workday's multiselect keeps its chips OUTSIDE the input:
    the input stays empty while the field text reads '1 item selected, India (+91)' - reading only the input made an answered Country
    Phone Code look empty, so the helper retried and failed a field that was already correct (a company)."""
    try:
        txt = loc.evaluate("e => { const f = e.closest('[data-automation-id^=formField-]'); return f ? f.innerText : ''; }") or ""
    except Exception:  # noqa: BLE001
        return False
    low = " ".join(str(txt).split()).lower()
    if re.search(r"\b[1-9]\d* items? selected", low):
        return True
    return bool(value) and value.strip().lower() in low


def as_control(loc):
    """Return the control a selector actually addresses: the element itself when it IS one, else the first control inside its container."""
    try:
        tag = loc.evaluate("e => e.tagName.toLowerCase()")
    except Exception:  # noqa: BLE001
        return loc
    if tag not in CONTAINER_TAGS:
        return loc
    try:
        inner = loc.locator(CONTROL_SEL).first
        if inner.count():
            return inner
    except Exception:  # noqa: BLE001
        pass
    return loc


def fill_workday_required(pg, page_text: str, failed: list | None = None) -> list[str]:
    """Fill the required Workday fields the generic field scan cannot see, through the shared VERIFYING picker. Returns the labels filled.
    Only fields that are empty/placeholder are touched, and only fields the picker VERIFIED are reported.

    `failed` (optional out-list) receives the labels that were REQUIRED and EMPTY but could not be set - a company died with
    'Phone Device Type is required' while this helper silently reported nothing, so the navigator blamed 'page stopped changing'."""
    done = []
    from core import profile
    _subst = {"{current_state}": profile.answer("current_state"), "{phone_code}": profile.phone_code_option()}
    for trigger, selectors, value, label in WORKDAY_REQUIRED:
        value = _subst.get(value, value)
        if not value:
            continue   # the answer is not recorded in answers.yaml: never guessed
        if not trigger.search(page_text or ""):
            continue
        loc = None
        for sel in selectors:
            try:
                cand = pg.locator(sel).first
                if cand.count():
                    loc = cand
                    break
            except Exception:  # noqa: BLE001
                continue
        if loc is None:
            try:
                cand = pg.get_by_label(re.compile(r"^\s*" + re.escape(label), re.I)).first
                loc = cand if cand.count() else None
            except Exception:  # noqa: BLE001
                loc = None
        if loc is not None:
            loc = as_control(loc)      # a wrapping <div> matched by a contains-selector is not the picker itself (a company)
            try:
                if loc.evaluate("e => e.tagName.toLowerCase()") in CONTAINER_TAGS:
                    loc = None         # a container with no control inside is a MISS, not an answered field
            except Exception:  # noqa: BLE001
                pass
        if loc is None:
            if failed is not None:
                failed.append(label)   # the page says it is required, but the control could not even be found
            continue
        wants, exact = [value], False
        if label == "Phone Device Type":
            # 'Mobile' must never fuzzy-match 'Mobile/Android': use her recorded phone type (exactly) and the generic 'Mobile' (exactly),
            # otherwise the field stays empty and the job parks for her (a company).
            wants = list(dict.fromkeys(w for w in (phone_device_wants() + [value]) if w))
            exact = True
        try:
            if is_unset(loc) and field_shows_selection(loc, value):
                continue               # the chips live outside the input: answered, not a failure (a company Country Phone Code)
            if is_unset(loc) and any(pick_option(pg, loc, w, label, exact=exact) for w in wants):  # never overwrite a real answer
                done.append(label)
            elif not is_unset(loc):
                continue               # a real answer is already there: not a failure
            elif failed is not None:   # required, empty, and the picker could not verify the value: say WHY (it saw the list)
                failed.append(label + (f" ({LAST_PICK_ERROR[:90]})" if LAST_PICK_ERROR else ""))
        except Exception:  # noqa: BLE001
            if failed is not None:
                failed.append(label)
            continue
    return done


LAST_PICK_ERROR = ""   # why the last pick_option gave up: read by fill_workday_required so the failure names what it SAW

# A NEAR-MISS ON AN INSTITUTION IS A DIFFERENT FACT (a company, KB-57): the Workday picker searched "Larkmoor" and selected
# "Larkmore Cyber University" - her school is "Larkmoor University, Westfield School of Statistics (WSS)" - because the fuzzy score between
# "larkmoor" and "korea" is high enough (>=70). Nothing was submitted (the required-field check caught it), but a list that does not contain
# her institution must park the job, never write a look-alike name into an application that cannot be corrected.
INSTITUTION_Q = re.compile(r"school|universit|college|institut|academ", re.I)
_INST_GENERIC = {"university", "school", "college", "institute", "institution", "academy", "of", "and", "the", "for", "at",
                 "business", "graduate", "studies", "study", "technology", "technologies", "research", "science", "sciences",
                 "management", "engineering", "arts", "gsb", "campus"}


def institution_needle(value: str) -> str:
    """The one word that identifies HER institution: 'larkmoor' for Larkmoor University, 'brightwater' for Brightwater College of Technology.
    Empty when the value gives no distinctive word (then nothing stricter than the normal picker applies)."""
    for w in re.findall(r"[a-z0-9]+", str(value or "").lower()):
        if len(w) >= 4 and w not in _INST_GENERIC:
            return w
    return ""


def _matches_needle(option: str, needle: str) -> bool:
    return bool(needle) and needle in (option or "").casefold()


def pick_option(pg, loc, value: str, label: str = "", exact: bool = False) -> bool:
    """ONE tool for every dropdown flavour: native <select>, custom listbox, searchable combobox, multi-select chips.
    Opens it, searches with several terms, fuzzy-picks the best visible option (for 'how did you hear' any plausible source),
    clicks it, then VERIFIES the widget now shows something. Returns True only when verified.
    `exact=True` for a FACTUAL value whose variants are different facts ('Mobile' vs 'Mobile/Apple iOS'): only an option that equals
    the value may be clicked - fuzzy scoring picked 'Mobile - Other (Non Iphone/Android)' for a company on ."""
    global LAST_PICK_ERROR
    LAST_PICK_ERROR = ""
    value = str(value or "").strip()
    try:
        tag = loc.evaluate("e => e.tagName.toLowerCase()")
    except Exception:  # noqa: BLE001
        return False
    if tag == "select":
        try:
            opts = [o.strip() for o in loc.locator("option").all_inner_texts()]
            needle = institution_needle(value) if INSTITUTION_Q.search(label or "") else ""
            if needle and not any(_matches_needle(o, needle) for o in opts):
                LAST_PICK_ERROR = (f"the institution list has no option containing {needle!r}; list offered "
                                   + ", ".join(o[:24] for o in opts[:5]))
                return False
            if needle:
                opts = [o for o in opts if _matches_needle(o, needle)]
            if exact:
                hit = next((o for o in opts if o.casefold() == value.casefold()), None)
                if not hit:
                    LAST_PICK_ERROR = f"no option equals {value!r}; list offered " + ", ".join(o[:20] for o in opts[:5])
                    return False
                loc.select_option(label=hit, timeout=4000)
                return True
            best, sc = _best(opts, value, value)
            loc.select_option(label=best if best and sc >= 55 else value, timeout=4000)
            return True
        except Exception:  # noqa: BLE001
            return False
    is_hear = bool(HEAR.search(label or ""))
    # Workday-style searchable multi-select (nested categories, chips): the dedicated picker drills down and verifies by the selected-item count
    try:
        box = loc.locator("xpath=ancestor::*[@data-automation-id='multiSelectContainer' or @data-automation-id='promptIcon' or starts-with(@data-automation-id,'formField-')][1]")
        if box.count() and box.locator("[data-automation-id=multiSelectContainer], [data-automation-id=promptIcon]").count() +                 (1 if (box.first.get_attribute("data-automation-id") or "") in ("multiSelectContainer", "promptIcon") else 0):
            wants = [value] + ([p for p in HEAR_PREF] if is_hear else [])
            for w in wants:
                if choose_searchable(pg, box.first, w, multi=False, verify_wanted=not is_hear):
                    return True
            return False
    except Exception:  # noqa: BLE001
        pass
    before = _shown_value(loc)
    terms = [] if exact else search_terms(value) + (["career", "website", "linkedin"] if is_hear else [])
    for attempt, term in enumerate([None] + terms[:6]):
        try:
            pg.keyboard.press("Escape")
            pg.wait_for_timeout(200)
            loc.click(timeout=4000)
            pg.wait_for_timeout(500)
            if term:
                try:
                    inner = loc if loc.evaluate("e => e.tagName.toLowerCase() === 'input'") else pg.locator("input:focus").first
                    inner.fill("")
                    inner.type(term, delay=35)
                except Exception:  # noqa: BLE001
                    pass
                pg.wait_for_timeout(900)
            opts = _visible_options(pg, loc)
            for _ in range(3):  # Workday renders the option list a beat after the click: do not give up on the first empty read
                if opts:
                    break
                pg.wait_for_timeout(700)
                opts = _visible_options(pg, loc)
            if not opts:
                LAST_PICK_ERROR = "the list never opened"
                continue
            needle = institution_needle(value) if INSTITUTION_Q.search(label or "") else ""
            if needle:
                hits = [o for o in opts if _matches_needle(o, needle)]
                if not hits:  # 'Larkmore Cyber University' for 'Larkmoor University' is a DIFFERENT school, not a near-enough match (KB-57)
                    LAST_PICK_ERROR = (f"no option contains {needle!r} (her institution); the list offered "
                                       + ", ".join(o[:24] for o in opts[:5]))
                    continue
                opts = hits
            if exact:
                hit = next((o for o in opts if o.strip().casefold() == value.strip().casefold()), None)
                pick, sc = (hit, 100) if hit else (None, 0)
            else:
                pick, sc = _pick_best(opts, value, term or value)
            if is_hear and (not pick or sc < 70):  # any plausible 'source' option is fine: it is not a factual claim
                pick = next((o for pref in HEAR_PREF for o in opts if pref in o.lower()), opts[0])
            elif not pick or sc < 70:
                LAST_PICK_ERROR = (f"no option equals {value!r}; " if exact else "") + "list offered " + ", ".join(o[:20] for o in opts[:5])
                continue
            want_opt = re.compile(rf"^\s*{re.escape(pick)}\s*$")
            opt = pg.locator(LISTBOX_SEL).filter(has_text=want_opt).first
            try:  # click the option in the list THIS control opened, not the same text somewhere else on the page
                cid = loc.evaluate("e => e.getAttribute('aria-controls') || ''")
                if cid:
                    scoped = pg.locator(f'[id="{cid}"] [role=option], [id="{cid}"] li').filter(has_text=want_opt).first
                    if scoped.count():
                        opt = scoped
            except Exception:  # noqa: BLE001
                pass
            try:
                opt.scroll_into_view_if_needed(timeout=2000)   # long lists (device type, country) hide the wanted entry below the fold
            except Exception:  # noqa: BLE001
                pass
            opt.click(timeout=3000, force=True)
            pg.wait_for_timeout(700)
            after = _shown_value(loc)
            if is_hear:
                if after != before or _picked_shown(after, pick):
                    return True
            elif _picked_shown(after, pick):
                return True
            else:  # clicked the right option and the widget still does not show it: name both facts (a company)
                LAST_PICK_ERROR = f"clicked {pick[:24]!r} but the field still shows {(after or 'nothing')[:40]!r}"
        except Exception as e:  # noqa: BLE001
            # A swallowed timeout is why 'could not set Phone Device Type' carried no evidence (a company): the click on the
            # option never landed and nothing recorded it. Keep the first line of the Playwright error, it names the failing step.
            LAST_PICK_ERROR = f"{term or 'open'} failed: " + " ".join(str(e).split())[:70]
            continue
    if not is_hear:
        if not LAST_PICK_ERROR:
            LAST_PICK_ERROR = "no option list ever opened for this control"
        # NO blind ArrowDown+Enter for a factual value: the next option after 'Select One' is the FIRST alphabetically (Landline, Afghanistan).
        # Leaving the field EMPTY is recoverable; a wrong value on the form is not.
        return False
    try:  # last resort for 'how did you hear' only: any plausible source is acceptable, so a changed value is enough
        loc.click(timeout=3000)
        pg.keyboard.press("ArrowDown")
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(600)
        if _shown_value(loc) != before:
            return True
        LAST_PICK_ERROR = f"arrow-key fallback left the field at {(before or 'nothing')[:40]!r}"
        return False
    except Exception as e:  # noqa: BLE001
        LAST_PICK_ERROR = "arrow-key fallback failed: " + " ".join(str(e).split())[:70]
        return False


# An option whose words are SLOTTED into a web component (SmartRecruiters: <spl-select-option>India</spl-select-option> with an empty
# <div role=option><slot></slot></div> inside): the role=option element has no text of its own, so no text lookup finds "India".
SLOTTED_OPTION_JS = r"""(wants) => {
  const found = [];
  const walk = root => { for (const e of root.querySelectorAll('*')) {
      if (e.getAttribute('role') === 'option') found.push(e);
      if (e.shadowRoot) walk(e.shadowRoot); } };
  walk(document);
  const txt = n => ((n && (n.innerText != null ? n.innerText : n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const name = e => { let t = txt(e) || (e.getAttribute('aria-label') || '').trim();
    for (let h = e.getRootNode().host, i = 0; !t && h && i < 2; h = h.getRootNode().host, i++) t = txt(h) || (h.getAttribute('label') || '').trim();
    return t; };
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const want = wants.map(w => w.toLowerCase());
  return found.find(e => vis(e) && want.includes(name(e).toLowerCase())) || null;
}"""


def click_slotted_option(pg, wants: list) -> bool:
    """Click the visible role=option whose name (its own text, or its web-component host's) is exactly one of `wants`."""
    try:
        h = pg.evaluate_handle(SLOTTED_OPTION_JS, [w for w in wants if w])
        el = h.as_element()
        if el is None:
            return False
        el.click(timeout=3000)
        return True
    except Exception:  # noqa: BLE001
        return False


def fill_country(pg, loc, value: str = "India") -> bool:
    """Set a country-of-residence / nationality control to `value` through its REAL picker and VERIFY it.

    SmartRecruiters (a company): the alphabetical country list opens on its first entry, so an untouched picker sits on 'Afghanistan'
    (and the phone code picker showed an Afghanistan flag next to +91). This helper therefore:
      - never uses a blind ArrowDown+Enter (that selects whatever the list happens to start with),
      - clicks the option that names the wanted country, then verifies the control SHOWS it,
      - on failure clears the control (its own 'Clear ... country' control) so it stays EMPTY, never on a wrong first option,
      - returns True only on a verified visible match.
    Works for native <select>, custom autocomplete (SmartRecruiters spl-autocomplete), combobox and plain text inputs."""
    value = str(value or "India").strip()
    want = value.split("(")[0].strip()          # 'India (+91)' -> 'India'
    wants = [want, "Indian"] if want.lower() == "india" else ([want, "India"] if want.lower() == "indian" else [want])
    seen_re = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in wants) + r")\b", re.I)
    opt_re = re.compile(rf"^\s*(?:{'|'.join(re.escape(w) for w in wants)})(\s|\+|-|$)", re.I)
    try:
        tag = loc.evaluate("e => e.tagName.toLowerCase()")
    except Exception:  # noqa: BLE001
        return False
    if tag == "select":
        try:
            opts = [o.strip() for o in loc.locator("option").all_inner_texts()]
            pick = next((o for o in opts if opt_re.match(o)), None)
            if not pick:
                return False
            loc.select_option(label=pick, timeout=4000)
            return bool(seen_re.search(shown_text(loc)))
        except Exception:  # noqa: BLE001
            return False
    plain_typed = picked = offered = False
    try:
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
        loc.click(timeout=4000)
        pg.wait_for_timeout(400)
        inner = loc if tag == "input" else pg.locator("input:focus").first
        try:
            inner.fill("")
        except Exception:  # noqa: BLE001 (neither editable nor an input: leave it to the plain-fill attempt below)
            inner = None
        if inner is not None:
            inner.type(want, delay=50)
            plain_typed = True
            pg.wait_for_timeout(1400)
        opts = pg.locator(LISTBOX_SEL)
        texts = [t.strip() for t in opts.all_inner_texts()[:80] if t.strip() and len(t.strip()) < 80]
        pick = next((t for t in texts if opt_re.match(t)), None)
        if pick:
            opts.filter(has_text=re.compile(rf"^\s*{re.escape(pick)}\s*$")).first.click(timeout=3000, force=True)
            pg.wait_for_timeout(1000)
            picked = True
        elif click_slotted_option(pg, wants):
            pg.wait_for_timeout(1000)
            picked = True
        offered = opts.count() > 0
    except Exception:  # noqa: BLE001
        pass
    try:
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(200)
    except Exception:  # noqa: BLE001
        pass
    shown = (shown_text(loc) + " " + _shown_value(loc)).strip()
    #  a company (SmartRecruiters): "India" was TYPED into the Country/Region typeahead, no option was clicked, and the typed word alone
    # passed this check - the form then kept the field invalid and Next did nothing. A list that was offered and not picked from is not a selection.
    if seen_re.search(shown) and (picked or not offered):
        return True
    if not plain_typed:  # last resort for a plain (non-picker) field: set it directly, then verify again
        try:
            loc.fill(value, timeout=4000)
            return bool(seen_re.search(shown_text(loc)))
        except Exception:  # noqa: BLE001
            pass
    # A wrong alphabetical first option must not be left behind: use the field's own clear control so it stays EMPTY instead.
    try:
        for sel in ("[aria-label*='clear' i][aria-label*='country' i]", "[aria-label*='clear' i][aria-label*='region' i]",
                    "[title*='clear' i][title*='country' i]", "[title*='clear' i][title*='region' i]"):
            btn = pg.locator(sel).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=3000)
                pg.wait_for_timeout(500)
                break
    except Exception:  # noqa: BLE001
        pass
    return False


# --------------------------------------------------------------------------- controls the page hides
# a company: "How did you hear about us?" is a REQUIRED select inside a `class="... hidden"` wrapper. It is
# invisible, so it is not in the snapshot, so nothing filled it - and Next stayed disabled with every visible field
# correct. The navigator then blamed two OPTIONAL SMS checkboxes (aria-required="false"). A required control the page
# hides is still required: find it, answer it from a recorded fact, or park the job with the exact question.
HIDDEN_REQ_JS = r"""() => { const out = []; document.querySelectorAll('input,select,textarea').forEach(e => {
  if (!(e.required || e.getAttribute('aria-required') === 'true')) return;
  const isBox = e.type === 'checkbox' || e.type === 'radio';
  const val = isBox ? (e.checked ? 'on' : '') : String(e.value || '').trim();
  if (val) return;
  const r = e.getBoundingClientRect();
  if (r.width > 0 && r.height > 0) return;
  let label = e.getAttribute('aria-label') || e.getAttribute('title') || '';
  if (!label && e.id) { const l = document.querySelector('label[for="' + e.id + '"]'); if (l) label = l.innerText || ''; }
  if (!label) { const w = e.closest('div,fieldset,li'); if (w) { const l = w.querySelector('label,legend'); if (l) label = l.innerText || ''; } }
  label = String(label || e.name || e.id || '').replace(/\s+/g, ' ').trim().slice(0, 90);
  const options = e.tagName === 'SELECT' ? Array.from(e.options).map(o => String(o.text || '').trim()).filter(Boolean).slice(0, 8) : [];
  out.push({id: e.id || '', name: e.name || '', tag: e.tagName.toLowerCase(), type: e.type || '', label: label, options: options});
}); return out.slice(0, 6); }"""

SET_HIDDEN_JS = r"""(a) => { const e = a.id ? document.getElementById(a.id) : document.querySelector('[name="' + a.name + '"]');
  if (!e) return '';
  const want = String(a.value || '').trim().toLowerCase();
  if (e.tagName === 'SELECT') {
    const o = Array.from(e.options).find(o => String(o.text || '').trim().toLowerCase() === want || String(o.value || '').trim().toLowerCase() === want);
    if (!o) return '';
    e.value = o.value;
  } else if (e.type === 'checkbox' || e.type === 'radio') {
    e.checked = true;
  } else {
    e.value = a.value;
  }
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  e.blur();
  const sel = e.options && e.options[e.selectedIndex];
  return String(sel ? (sel.text || '') : (e.value || '')).trim(); }"""


def hidden_required(pg) -> list[dict]:
    """Required, EMPTY controls that the page hides from the generic scan ({id, name, tag, type, label, options}).

    An empty list is the normal case: a visible required field is already in the snapshot. This is for the
    'How did you hear about us?' kind - required by the form's validation, invisible to a human and to SNAP_JS."""
    try:
        items = pg.evaluate(HIDDEN_REQ_JS) or []
    except Exception:  # noqa: BLE001 (a page navigating mid-scan is not a finding)
        return []
    return [x for x in items if isinstance(x, dict) and (x.get("id") or x.get("name"))]


def set_hidden_value(pg, item: dict, value: str) -> bool:
    """Set a hidden control from a recorded fact, firing the events its framework listens for, and verify it stuck."""
    if not (item.get("id") or item.get("name")) or not str(value or "").strip():
        return False
    try:
        got = pg.evaluate(SET_HIDDEN_JS, {"id": item.get("id") or "", "name": item.get("name") or "", "value": str(value)})
    except Exception:  # noqa: BLE001
        return False
    got = str(got or "").strip()
    if (item.get("type") or "").lower() in ("checkbox", "radio"):
        return bool(got)
    g, w = got.lower(), str(value).strip().lower()
    return bool(g) and (g == w or w in g or g in w)


DOB_PART_RX = re.compile(r"^(?P<base>.*?)\b(?P<part>day|month|year)\b\s*\*?\s*$", re.I)
DOB_Q = re.compile(r"date of birth|\bdob\b|birth ?date", re.I)
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


AVAIL_Q = re.compile(r"earliest available|available date|availability date|date available|available from", re.I)


def fill_available_date_parts(pg, snap) -> list[str]:
    """Oracle's 'Earliest Available Date' split Day/Month/Year: she is available immediately (facts), so the date is TODAY. Eastern
    (Oracle HCM job 350896): the model typed only 'Month = 06', a date nobody recorded, and the page refused the half date. Only EMPTY parts are
    filled, each verified; 'Start date' of an experience row is never matched."""
    from datetime import date
    return fill_missing_dob_parts(pg, snap, dob=date.today(), qrx=AVAIL_Q)


def fill_missing_dob_parts(pg, snap, dob=None, qrx=None) -> list[str]:
    """Complete a SPLIT date of birth (separate Day/Month/Year controls) from her recorded date (KB-62).

    a company (Oracle CXS): the navigator filled 'Date of Birth Year' and left 'Date of Birth
    Month' empty, the page warned "You need to enter the whole date otherwise the date won't be saved", and the job failed
    as "page stopped changing". A half-date is never acceptable: it is either her real date or it stays empty and the job
    parks. Every part is VERIFIED by re-reading the control; a value that does not stick is reported, not assumed."""
    out: list[str] = []
    if dob is None:
        try:
            from apply import answers_map as am
            dob = am.answer_for("Date of Birth")
        except Exception:  # noqa: BLE001
            return out
    if dob is None or not hasattr(dob, "strftime"):
        return out
    want = {"day": dob.day, "month": dob.month, "year": dob.year}
    groups: dict[str, dict] = {}
    for e in (snap or {}).get("els") or []:
        lab = (e.get("label") or e.get("text") or "").strip()
        m = DOB_PART_RX.match(lab)
        if not m or not (qrx or DOB_Q).search(lab) or e.get("i") is None:
            continue
        groups.setdefault(m.group("base").strip().lower(), {})[m.group("part").lower()] = e
    for _base, parts in groups.items():
        for part, e in parts.items():
            shown = str(e.get("value") or "").strip()
            if shown:
                if not _part_matches(part, shown, want[part]):
                    out.append(f"{e.get('label')} shows {shown!r} (her real date is {dob.strftime('%d/%m/%Y')}); left as-is")
                continue
            try:
                loc = pg.locator(f'[data-ag="{e["i"]}"]').first
            except Exception:  # noqa: BLE001
                continue
            txt = _dob_part_text(part, want[part])
            ok = False
            try:
                loc.click(timeout=3000)
                try:
                    loc.fill("")
                except Exception:  # noqa: BLE001
                    pass
                loc.type(txt, delay=30)
                pg.wait_for_timeout(700)
                try:  # an Oracle/Workday combobox shows its choices in a grid/listbox: take the matching one
                    opt = pg.get_by_role("option", name=re.compile(rf"^\s*{re.escape(txt)}", re.I))
                    if opt.count():
                        opt.first.click(timeout=2000)
                except Exception:  # noqa: BLE001
                    pass
                pg.wait_for_timeout(400)
                shown = (loc.input_value() or "").strip()
                ok = _part_matches(part, shown, want[part])
            except Exception:  # noqa: BLE001
                ok = False
            out.append(f"{e.get('label')}: {'set to ' + txt if ok else 'could not be set (still empty)'}")
    return out


def fill_single_dob(pg, snap, dob=None) -> list[str]:
    """Set a SINGLE date-of-birth control (one text box or one native date input) from her recorded date (KB-66).

    a company Acceleration Center failed four times with "page stopped
    changing (Enter all mandatory fields: This field is required.)" while every other field on the page was filled: the
    required 'Date of Birth*' is a react-datepicker text box and stayed EMPTY in all four saved snapshots. Nothing in the
    applier knew how to commit a date into that widget, so the page never advanced.

    Safety: only an EMPTY field is touched; the value is VERIFIED by re-reading the control and is accepted only when it
    parses back to her exact recorded date; a value that cannot be verified is cleared again and reported. The
    month-first format is offered only when the day is > 12 (for example 17/09), so an ambiguous swap can never be written."""
    out: list[str] = []
    if dob is None:
        try:
            from apply import answers_map as am
            dob = am.answer_for("Date of Birth")
        except Exception:  # noqa: BLE001
            return out
    if dob is None or not hasattr(dob, "strftime"):
        return out
    targets = []
    for e in (snap or {}).get("els") or []:
        lab = (e.get("label") or e.get("text") or "").strip()
        if e.get("i") is None or not DOB_Q.search(lab) or DOB_PART_RX.match(lab):
            continue
        if str(e.get("value") or "").strip() or e.get("disabled"):
            continue
        targets.append(e)
    if not targets:
        return out
    e = targets[0]  # one date of birth per page; a duplicate listing is the same control
    try:
        loc = pg.locator(f'[data-ag="{e["i"]}"]').first
    except Exception:  # noqa: BLE001
        return out
    native = (e.get("type") or "").lower() == "date"
    tries = ["%Y-%m-%d"] if native else ["%d/%m/%Y", "%Y-%m-%d"]
    if not native and dob.day > 12:
        tries.append("%m/%d/%Y")   # only unambiguous: a day > 12 can never be read as a month
    ok, shown = False, ""
    for fmt in tries:
        txt = dob.strftime(fmt)
        try:
            loc.click(timeout=3000)
            if native:
                loc.fill(txt, timeout=4000)
            else:
                try:
                    loc.fill("", timeout=2000)
                except Exception:  # noqa: BLE001
                    pass
                loc.type(txt, delay=40, timeout=4000)   # a readonly/blocked control must cost 4 s, not Playwright's default 30
                pg.keyboard.press("Enter")     # react-datepicker commits on Enter/blur, not on typing alone
                pg.wait_for_timeout(600)
                pg.keyboard.press("Escape")    # close the calendar so the next snapshot is the form again
        except Exception:  # noqa: BLE001
            shown = ""
        try:
            shown = (loc.input_value() or "").strip()
        except Exception:  # noqa: BLE001
            shown = ""
        if shown and _date_value_matches(shown, dob):
            ok = True
            break
        try:  # a value we cannot verify is not ours to leave behind
            loc.fill("")
        except Exception:  # noqa: BLE001
            pass
        shown = ""
    out.append(f"{e.get('label') or 'Date of Birth'}: " + (f"set to {shown}" if ok else "could not be set (no verifiable date stuck)"))
    return out


FIELD_REQUIRED_ERR = re.compile(
    r"the field ([A-Za-z][A-Za-z /]{0,40}?) (?:is required|must have a value)|error-([a-z][a-z /]{0,40})", re.I)


def recommit_page_required(pg, snap, tried=None) -> list[str]:
    """Re-fire a field the page calls required although it already SHOWS a value (KB-67).

    a company `9d5e...` (Workday My Experience) failed with "Errors Found ... Error-URL The field URL is required and must
    have a value" while the snapshot listed `URL*` = her LinkedIn address: the page's own React state never received the
    typed value, so it validated an empty field. Workday commits such inputs on BLUR, which Playwright's fill/type does
    not always cause. The repair only re-fires events on the value that is already there (End, space, Backspace, Tab)
    and verifies the value is unchanged afterwards - it never invents or edits a fact."""
    out: list[str] = []
    s = snap or {}
    text = " ".join(s.get("heads") or []) + " " + (s.get("text") or "")
    if not re.search(r"errors? found|is required|must have a value", text, re.I):
        return out
    names = set()
    for m in FIELD_REQUIRED_ERR.finditer(text):
        name = (m.group(1) or m.group(2) or "").strip().lower()
        if len(name) >= 2:
            names.add(name)
    if not names:
        return out
    done = tried if tried is not None else set()
    for e in s.get("els") or []:
        if e.get("i") is None or e.get("i") in done:
            continue
        lab = re.sub(r"[\s*]+$", "", (e.get("label") or "")).strip().lower()
        if not lab or (e.get("value") or "").strip() == "":
            continue
        if not any(lab == n or lab.startswith(n) or n.startswith(lab) for n in names):
            continue
        done.add(e.get("i"))
        before = str(e.get("value") or "")
        try:
            loc = pg.locator(f'[data-ag="{e["i"]}"]').first
            loc.click(timeout=3000)
            pg.keyboard.press("End")
            pg.keyboard.type(" ")
            pg.keyboard.press("Backspace")
            pg.keyboard.press("Tab")
            pg.wait_for_timeout(300)
            after = (loc.input_value() or "").strip()
        except Exception:  # noqa: BLE001
            out.append(f"{e.get('label')}: could not re-commit (left as-is)")
            continue
        if after == before.strip():
            out.append(f"{e.get('label')}: re-committed (value unchanged)")
        else:
            out.append(f"{e.get('label')}: re-commit CHANGED the value ({before!r} -> {after!r}) - check the page")
    return out


def _date_value_matches(shown: str, dob) -> bool:
    """True when a control's shown value parses back to exactly her recorded date in one of the common orders."""
    from datetime import datetime as _dt
    s = (shown or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d %B %Y", "%d %b %Y"):
        try:
            if _dt.strptime(s, fmt).date() == dob.date():
                return True
        except ValueError:
            continue
    return False


def _dob_part_text(part: str, value: int) -> str:
    if part == "month":
        return MONTHS[value - 1]
    if part == "day":
        return f"{value:02d}"
    return str(value)


def _part_matches(part: str, shown: str, value: int) -> bool:
    s = (shown or "").strip().lower()
    if not s:
        return False
    if part == "month":
        name = MONTHS[value - 1].lower()
        return s == name or s.startswith(name[:3]) or s in {str(value), f"{value:02d}"}
    return s in {str(value), f"{value:02d}"} or s.startswith(str(value))


def upload_via_dialog(pg, loc, pdf) -> str:
    """Upload a file through a control that is not an <input type=file> (SuccessFactors "Upload a CV - Opens a dialogue", many custom portals).
    Two shapes are handled, in order: the click opens the browser's file chooser (set the file on it), or the click opens a dialog holding a
    file input (set the file, then press the dialog's own Attach / Upload / Save). Returns '' when neither happened (the caller falls back to
    the first <input type=file> on the page), else a short note of what was done. Reusable across portals; nothing is guessed about the file."""
    if loc is None:
        return ""
    try:
        with pg.expect_file_chooser(timeout=7000) as fc:
            loc.click(timeout=5000)
        fc.value.set_files(str(pdf))
        pg.wait_for_timeout(1500)
        return "file chooser"
    except Exception:  # noqa: BLE001
        pass
    try:   # the click may have opened a dialog holding a file input instead
        pg.wait_for_timeout(1200)
        box = pg.locator("[role=dialog] input[type=file], dialog input[type=file], .modal input[type=file]").first
        if box.count() == 0:
            return ""
        box.set_input_files(str(pdf))
        pg.wait_for_timeout(1200)
        btn = pg.locator("[role=dialog] button, dialog button, .modal button").filter(has_text=re.compile(r"^\s*(attach|upload|save|done|ok|add|submit)\b", re.I)).first
        if btn.count():
            btn.click(timeout=4000)
            pg.wait_for_timeout(1500)
        return "dialog"
    except Exception:  # noqa: BLE001
        return ""
