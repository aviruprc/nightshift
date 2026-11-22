"""Workday applications (myworkdayjobs.com). State machine keyed on Workday's data-automation-id attributes.
Steps: Apply -> Create account / Sign in (keyring, Gmail verification) -> My Information -> My Experience -> Questions -> Disclosures -> Review.
Dry run stops on Review. Unknown required answers or anything odd PARK the job (needs_you). Passwords only in keyring."""
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from apply import accounts, engine, mail
from apply import answers_map as am

EMAIL = accounts.EMAIL
ROOT_DATA = Path(__file__).resolve().parent.parent / "data"
JOB_SOURCE = {"src": ""}  # set per job so 'How did you hear about us' is answered truthfully (e.g. LinkedIn)
NEXT = "[data-automation-id=bottom-navigation-next-button]"

FIELDS_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const out = []; let n = 0;
  document.querySelectorAll('[data-automation-id^="formField-"]').forEach(f => {
    if (!vis(f)) return;
    if (!f.dataset.jh) f.dataset.jh = 'wf' + (window.__wf = (window.__wf || 0) + 1);
    const label = ((f.querySelector('label, legend') || {}).innerText || f.innerText.split('\\n')[0] || '').replace(/\\s+/g, ' ').trim();
    let kind = null;
    if (f.querySelector('[data-automation-id=file-upload-input-ref], input[type=file]')) kind = 'file';
    else if (f.querySelector('input[type=radio], [role=radiogroup]')) kind = 'radio';
    else if (f.querySelector('[data-automation-id=multiSelectContainer], [data-automation-id=promptIcon]')) kind = 'prompt';
    else if (f.querySelector('input[type=checkbox]')) kind = 'checkbox';
    else if (f.querySelector('button[aria-haspopup=listbox], [role=combobox]')) kind = 'dropdown';
    else if (f.querySelector('textarea')) kind = 'textarea';
    else if (f.querySelector('[data-automation-id^=dateSection]')) kind = 'date';
    else if (f.querySelector('input')) kind = 'text';
    if (!kind) return;
    const inp = f.querySelector('input:not([type=hidden]), textarea');
    const dd = f.querySelector('button[aria-haspopup=listbox]');
    out.push({ id: f.dataset.jh, auto: f.getAttribute('data-automation-id'), label, kind, required: /\\*\\s*$/.test(label) || !!f.querySelector('[aria-required=true]'),
               value: dd ? (dd.innerText || '').trim() : (inp ? (inp.value || '') : '') });
  });
  // newer Workday layout: Yes/No radio groups (e.g. candidateIsPreviousWorker) live OUTSIDE any formField-* wrapper
  document.querySelectorAll('[aria-labelledby]').forEach(g => {
    if (!vis(g) || g.closest('[data-automation-id^="formField-"]') || !g.querySelector(':scope input[type=radio]')) return;
    if (g.parentElement && g.parentElement.closest('[data-jh]') && !g.dataset.jh) return;
    if (!g.dataset.jh) g.dataset.jh = 'wf' + (window.__wf = (window.__wf || 0) + 1);
    const lab = g.getAttribute('aria-labelledby').split(' ').map(i => (document.getElementById(i) || {}).innerText || '').join(' ').replace(/\\s+/g, ' ').trim();
    out.push({ id: g.dataset.jh, auto: g.getAttribute('name') || 'radiogroup', label: lab || g.getAttribute('name') || '', kind: 'radio',
               required: g.getAttribute('aria-required') === 'true' || /\\*\\s*$/.test(lab), value: '' });
  });
  return out; }"""



def _first_employer_word() -> str:
    """A distinctive lowercase word of the most recent employer in cv/facts.yaml ('' when none): the truthfulness check that the
    CV's work history really landed on the Workday experience page."""
    try:
        from core import profile
        c = ((profile.facts().get("experience") or [{}])[0].get("company") or "").lower()
    except Exception:  # noqa: BLE001
        return ""
    words = [w for w in re.findall(r"[a-z0-9]+", c) if len(w) >= 3 and w not in ("group", "ltd", "limited", "pvt", "private", "inc", "llc", "the")]
    return words[0] if words else ""

def _click_submit_like(pg, button_sel: str):
    """Workday puts a transparent 'click_filter' overlay over its action buttons; try the real button, then the overlay."""
    try:
        pg.locator(button_sel).first.click(timeout=4000)
    except Exception:  # noqa: BLE001
        pg.locator("[data-automation-id=click_filter]").first.click(timeout=4000)


def _next(pg, final: bool = False) -> bool:
    """Click 'Save and Continue' / 'Next' (or 'Submit' on Review). Tenants name it differently, so try known ids then visible text."""
    pg.keyboard.press("Escape")  # close any picker list that could intercept the click
    pg.wait_for_timeout(400)
    for sel in ("[data-automation-id=pageFooterNextButton]", "[data-automation-id=bottom-navigation-next-button]",
                "[data-automation-id=wd-CommandButton_next]"):
        loc = pg.locator(f"{sel}:visible")
        if loc.count():
            try:
                loc.first.click(timeout=5000)
                return True
            except Exception:  # noqa: BLE001
                pass
    rx = r"^\s*(submit|submit application)\s*$" if final else r"^\s*(save and continue|next|continue)\s*$"
    btn = pg.get_by_role("button", name=re.compile(rx, re.I))
    if btn.count():
        btn.last.click(timeout=5000)
        return True
    return False


def _banner(pg) -> str:
    try:
        return re.sub(r"\s+", " ", pg.locator("[data-automation-id=errorMessage], [role=alert], [data-automation-id=errorBanner]").first.inner_text(timeout=1500))[:160]
    except Exception:  # noqa: BLE001
        return ""


def _active_step(pg) -> str:
    try:
        t = pg.locator("[data-automation-id=progressBarActiveStep]").first.inner_text(timeout=2000)
        return re.sub(r"\s+", " ", t).lower()
    except Exception:  # noqa: BLE001
        return ""


def wait_outcome(pg, seconds: int = 45, expect_bounce: bool = False) -> str:
    """Workday shows a spinner for a while after Create/Sign In. Poll until something decisive appears."""
    for _ in range(seconds // 2):
        pg.wait_for_timeout(2000)
        step = _active_step(pg)
        if pg.locator("[data-automation-id=signInContent]").count() == 0 and pg.locator("[data-automation-id=applyFlowPage]").count() \
                and step and not re.search(r"sign in|create account", step):
            return "in"  # signed in: whatever the next step is (My Information, or Autofill-with-Resume upload)
        try:
            body = re.sub(r"\s+", " ", pg.inner_text("body")).lower()
        except Exception:  # noqa: BLE001
            continue
        if re.search(r"already applied|you('ve| have) already (applied|submitted)|already submitted an application", body):
            return "applied"
        if re.search(r"wrong email address or password|incorrect (email|password|username)|invalid (email|password|credentials)|account might be locked|password.{0,20}(not correct|incorrect|invalid)", body):
            return "badpw"  # the ONLY signal that justifies a password reset
        if re.search(r"already (exists|have an account|registered)|account.*already", body):
            return "exists"
        # Workday hides whether an account exists: creating over an existing one just returns to a plain Sign In screen
        if expect_bounce and pg.locator("input[type=password]:visible").count() == 1 and not pg.locator("[data-automation-id=verifyPassword]").count():
            return "signin"  # after CREATE: two password boxes became one = Workday sent us back to the plain Sign In screen
        if re.search(r"verify your|check your email|confirmation email|verification|activate your", body):
            return "verify"
        if pg.locator("[data-automation-id=errorMessage], [role=alert]").count() and _banner(pg):
            return "error"
    return "timeout"


def _sign_in(pg, password: str) -> str:
    pg.locator("[data-automation-id=email]").first.fill(EMAIL)
    pg.locator("[data-automation-id=password]").first.fill(password)
    _click_submit_like(pg, "[data-automation-id=signInSubmitButton]")
    return wait_outcome(pg, 40)  # in | badpw | applied | timeout | ...


def _reset_log() -> dict:
    import json
    try:
        return json.loads((ROOT_DATA / "reset_log.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _reset_entry(log: dict, host: str):
    """(when, confirmed) of the last reset attempt. Plain-string entries were written by the code before the reset page was ever
    checked, so they are UNCONFIRMED - a guess must never block her account for 30 days (Target / a company)."""
    e = log.get(host)
    if not e:
        return None, False
    if isinstance(e, dict):
        confirmed = bool(e.get("confirmed"))
        raw = str(e.get("date") or "")[:10]
    else:
        confirmed, raw = False, str(e)[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d"), confirmed
    except ValueError:
        return None, confirmed


def _reset_allowed(host: str) -> bool:
    """A reset is never churn: one attempt per tenant per day, and after a CONFIRMED reset whose password still does not work, none
    for 30 days (that is a vault bug to fix, not something to repeat). An UNCONFIRMED attempt set nothing we can prove, so it may be
    retried on a later day - blocking forever on an unknown is the exact failure the standing rules forbid."""
    when, confirmed = _reset_entry(_reset_log(), host)
    if when is None:
        return True
    days = (datetime.now() - when).days
    return days >= 30 if confirmed else days >= 1


def _reset_used(host: str, confirmed: bool = True):
    """Recorded once a reset email arrived. `confirmed=True` only when the reset PAGE said the password was set; an unconfirmed
    attempt is retried on a later day but never starts the 30-day block."""
    import json
    log = _reset_log()
    log[host] = {"date": datetime.now().strftime("%Y-%m-%d"), "confirmed": bool(confirmed)}
    (ROOT_DATA / "reset_log.json").write_text(json.dumps(log), encoding="utf-8")


RESET_OK = re.compile(r"password (?:has been|was) (?:reset|changed|updated)|successfully (?:reset|changed|updated) your password"
                      r"|your password (?:has been|was) (?:reset|changed|updated)|password (?:reset|change) (?:was )?successful", re.I)
RESET_BAD = re.compile(r"(?:must (?:be|contain|have)|does not meet|not match|doesn't match|invalid|expired|error)[^.]{0,120}", re.I)


def _reset_confirmed(rp):
    """True ONLY when the reset page itself shows the password was set. An error, a closed page or a page we cannot read is UNKNOWN:
    an unknown must never store a password or start the 30-day block (a false 'confirmed' is the expensive mistake)."""
    try:
        body = re.sub(r"\s+", " ", rp.inner_text("body"))[:3000]
    except Exception:  # noqa: BLE001
        return False, "the reset page closed before it answered"
    m = RESET_OK.search(body)
    if m:
        return True, m.group(0)[:120]
    m = RESET_BAD.search(body)
    if m:
        return False, m.group(0).strip()[:140]
    try:
        left_form = rp.locator("input[type=password]:visible").count() == 0
        back_to_signin = rp.locator("[data-automation-id=signInContent], [data-automation-id=signInLink]").count() > 0
    except Exception:  # noqa: BLE001
        left_form, back_to_signin = False, False
    if left_form and back_to_signin:  # the reset page's own header says 'Sign In' too: only the sign-in ELEMENT counts
        return True, "the reset form was accepted and the page returned to Sign In"
    if not left_form:
        return False, "the reset form did not submit (the password boxes are still shown)"
    return False, "the reset form closed without a success message"


def _save_reset_evidence(rp, host: str, note: str):
    """Keep the reset page so a failed/unknown reset can be diagnosed offline instead of guessed from a blank banner."""
    try:
        d = ROOT_DATA / "failures"
        d.mkdir(parents=True, exist_ok=True)
        stem = f"{datetime.now().strftime('%m%d_%H%M%S')}_reset_{tenant_of(host)}"
        (d / f"{stem}.html").write_text(rp.content()[:600000], encoding="utf-8")
        (d / f"{stem}.txt").write_text(f"{note}\n{rp.url}\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def tenant_of(host: str) -> str:
    return host.split(".")[0].lower()


def reset_and_sign_in(pg, ctx, host: str):
    """Existing account with an unknown password: request a reset email, read the link from Gmail, set a NEW generated password,
    then sign in. The password is stored only when the RESET PAGE confirms it was set (and then in both ledgers); an unconfirmed
    attempt changes nothing and is retried on a later day (KB-33). Returns (ok, reason)."""
    from core import activity
    if not _reset_allowed(host):
        # KB-70: this used to say "vault bug", which blames our storage without evidence - the site may simply reject
        # the password for its own reasons (expiry, a reset made elsewhere). State what is true: it was rejected, and the one reset
        # this portal allows is already spent, so only a person can clear it. Logged with the host so it is checkable.
        activity.log("error", f"Workday: {host} rejected the saved password although a reset was used within 30 days: no second reset allowed")
        return False, ("saved password rejected again within 30 days of a reset: no second reset is allowed, so it needs a manual reset "
                       f"on {host}")
    activity.log("portal", f"Workday: the saved password was rejected on {host}; resetting it through Gmail", portal="Workday")
    try:
        pg.locator("[data-automation-id=forgotPasswordLink]").first.click(timeout=5000)
        pg.wait_for_timeout(2000)
        pg.locator("[data-automation-id=email]").first.fill(EMAIL)
        sub = pg.locator("[data-automation-id=forgotPasswordSubmitButton], [data-automation-id=resetPasswordSubmitButton]")
        if sub.count():
            sub.first.click(timeout=4000)
        else:
            pg.get_by_role("button", name=re.compile(r"reset|submit|send|continue", re.I)).first.click(timeout=4000)
        pg.wait_for_timeout(4000)
    except Exception as e:  # noqa: BLE001
        return False, f"could not request a password reset: {type(e).__name__}"
    # senders differ per company (wf -> wellsfargoworkday@wellsfargo.com): try the tenant, then any fresh reset email. The link must point
    # at THIS Workday host (allowed=), so a generic search can never pick another company's reset link.
    link = None
    for k, q in enumerate((f"from:{tenant_of(host)}", 'subject:"reset your password"', "subject:password workday")):
        link = mail.verification_link(ctx, q, 150 if k == 0 else 30, allowed=(host.lower(),))
        if link:
            break
    if not link:
        return False, "no password-reset email arrived (the account may not exist, or the email was delayed)"
    new_pw = accounts.generate_password()
    rp = ctx.new_page()
    confirmed, note = False, "the reset link was never opened"
    try:
        rp.goto(link, wait_until="domcontentloaded", timeout=45000)
        rp.wait_for_timeout(5000)
        pwf = rp.locator("input[type=password]:visible")
        if pwf.count() < 1:
            note = "the reset link did not show a new-password form"
        else:
            for i in range(pwf.count()):
                pwf.nth(i).fill(new_pw)
            sub = rp.get_by_role("button", name=re.compile(r"submit|reset|save|continue|update", re.I))
            (sub.first if sub.count() else rp.locator("[data-automation-id=click_filter]").first).click(timeout=5000)
            rp.wait_for_timeout(6000)
            confirmed, note = _reset_confirmed(rp)
    except Exception as e:  # noqa: BLE001
        note = f"reset form failed: {type(e).__name__}"
    finally:
        if not confirmed:
            _save_reset_evidence(rp, host, note)  # a blank banner is not a diagnosis: keep the page for offline triage
        rp.close()
    if not confirmed:
        # UNKNOWN is not a failure of the account: the site may still hold her old password, so the vault stays as it is and this is
        # retried later (never parked on a guess, never a 30-day block). Target/a company: the old code recorded the reset and
        # returned 'signed in failed after reset: ' on a page that never rendered.
        _reset_used(host, confirmed=False)
        activity.log("error", f"Workday: the reset page on {host} did not confirm a new password ({note[:120]}); vault untouched, retried later")
        return False, (f"sign-in did not complete (the reset page did not confirm a new password: {note[:100]}); "
                       f"will retry later with the same saved password")
    # The reset page confirmed the new password, so THAT is the site's password now: record it in BOTH ledgers and store it in the vault
    # even though an older entry exists (accounts.save only replaces a proven password; the reset is that proof).
    _reset_used(host, confirmed=True)
    accounts.record_reset(host)
    accounts.save(host, new_pw, proven=True)
    pg.reload(wait_until="domcontentloaded")
    pg.wait_for_timeout(4000)
    if pg.locator("[data-automation-id=signInContent]").count() and not pg.locator("[data-automation-id=signInLink]").count():
        pass
    else:
        try:
            pg.locator("[data-automation-id=signInLink]").first.click(timeout=3000)
            pg.wait_for_timeout(1200)
        except Exception:  # noqa: BLE001
            pass
    res = _sign_in(pg, new_pw)
    if res == "in":
        accounts.mark_verified(host)
        return True, "password reset and signed in"
    if res == "applied":
        return False, "ALREADY_APPLIED"
    if res == "badpw":  # the site explicitly rejects the password its own reset page just confirmed: a real question for her
        return False, f"signed in failed after reset: {_banner(pg) or 'the site rejected the password it had just accepted'}"
    # no answer / slow page: nothing is known, so this is transient - never "needs you" on an empty banner
    return False, (f"sign-in did not complete ({res}) right after a confirmed reset on {host}"
                   + (f": {_banner(pg)}" if _banner(pg) else "; the page never answered, retried later"))


def try_known_passwords(pg, host: str, tried: list, candidates: list | None = None):
    """Before ANY reset: one careful try with each other password we already hold for this company (the previous passwords of this host and the saved
    ones of its sibling hosts, accounts.candidate_passwords). "remember passwords, do not reset every time you apply to a company
    you applied to before" - a company was reset on wf.wd1... today although the same company's other portal host already had a working login.
    Returns (ok, reason) when a password worked or the site says we already applied, else None (so the caller may fall back to the one reset)."""
    from core import activity
    seen = list(tried)
    for cand in (candidates if candidates is not None else accounts.candidate_passwords(host)):
        if not cand or cand in seen:
            continue
        seen.append(cand)
        activity.log("portal", f"Workday: the saved password was rejected on {host}; trying another password already held for this company (no reset yet)", portal="Workday")
        try:
            res = _sign_in(pg, cand)
        except Exception:  # noqa: BLE001
            break
        if res == "in":
            accounts.save(host, cand, proven=True)
            accounts.mark_verified(host)
            return True, "signed in with a password already held for this company"
        if res == "applied":
            return False, "ALREADY_APPLIED"
        if res != "badpw":
            break      # a slow or unreadable page is not a rejection: stop guessing
    return None


def sign_in_or_create(pg, ctx, host: str, dry_run: bool):
    """Returns (ok, reason). Leaves the page on 'My Information' when ok."""
    from core import activity
    pw = accounts.get(host)
    activity.log("portal", f"Workday: {'signing in with the saved account' if pw else 'creating an account'} on {host}", portal="Workday")
    if pw:
        pg.locator("[data-automation-id=signInLink]").first.click(timeout=5000)
        pg.wait_for_selector("[data-automation-id=signInSubmitButton]", timeout=15000)  # wait for the sign-in form, not a fixed delay
        pg.wait_for_timeout(800)
        pg.locator("[data-automation-id=email]").first.fill(EMAIL)
        pg.locator("[data-automation-id=password]").first.fill(pw)
        _click_submit_like(pg, "[data-automation-id=signInSubmitButton]")
        res = wait_outcome(pg, 40)
        if res == "in":
            return True, "signed in"
        if res == "applied":
            return False, "ALREADY_APPLIED"
        if res == "badpw":  # only an explicit 'wrong password' justifies a reset (never a timeout or a slow page)
            known = try_known_passwords(pg, host, [pw])
            if known:
                return known
            return reset_and_sign_in(pg, ctx, host)
        return False, f"sign-in did not complete ({res}); will retry later without touching the password"
    # no account on this tenant yet: create one. Password goes to the keyring BEFORE the click so it can never be lost.
    new_pw = accounts.generate_password()
    accounts.save(host, new_pw)
    pg.locator("[data-automation-id=email]").first.fill(EMAIL)
    pg.locator("[data-automation-id=password]").first.fill(new_pw)
    pg.locator("[data-automation-id=verifyPassword]").first.fill(new_pw)
    cb = pg.locator("[data-automation-id=createAccountCheckbox]").first
    if not cb.is_checked():
        engine._tick(pg, cb)
    _click_submit_like(pg, "[data-automation-id=createAccountSubmitButton]")
    outcome = wait_outcome(pg, 45, expect_bounce=True)
    body = re.sub(r"\s+", " ", pg.inner_text("body")).lower()
    if outcome == "in":
        accounts.mark_verified(host)
        return True, "account created and signed in"
    if outcome in ("exists", "signin"):
        # Workday may have just created the account (and want verification) OR the account pre-existed. Try the password we just made first.
        try:
            if "signInLink" and pg.locator("[data-automation-id=signInLink]").count():
                pg.locator("[data-automation-id=signInLink]").first.click(timeout=3000)
                pg.wait_for_selector("[data-automation-id=signInSubmitButton]", timeout=10000)
            first = _sign_in(pg, new_pw)
        except Exception:  # noqa: BLE001
            first = "timeout"
        if first == "in":
            accounts.mark_verified(host)
            return True, "account created and signed in"
        if first == "applied":
            return False, "ALREADY_APPLIED"
        link = mail.verification_link(ctx, f"from:{tenant_of(host)}", 60, allowed=(host.lower(),))
        if link:  # a verification email exists: the account was just created
            v = ctx.new_page()
            try:
                v.goto(link, wait_until="domcontentloaded", timeout=45000)
                v.wait_for_timeout(4000)
            finally:
                v.close()
            pg.reload(wait_until="domcontentloaded")
            pg.wait_for_timeout(4000)
            try:
                pg.locator("[data-automation-id=signInLink]").first.click(timeout=3000)
                pg.wait_for_selector("[data-automation-id=signInSubmitButton]", timeout=10000)
            except Exception:  # noqa: BLE001
                pass
            if _sign_in(pg, new_pw) == "in":
                accounts.mark_verified(host)
                return True, "verified and signed in"
        held = accounts.candidate_passwords(host, limit=4)   # read BEFORE forget: it also drops the history
        accounts.forget(host)  # the generated password was rejected and there is no verification mail: an older account exists
        known = try_known_passwords(pg, host, [new_pw], held)
        if known:
            return known
        return reset_and_sign_in(pg, ctx, host)
    if outcome == "error":
        accounts.forget(host)
        return False, f"Workday rejected the new account: {_banner(pg)}"
    if outcome == "verify":
        link = mail.verification_link(ctx, f"from:{tenant_of(host)}", 120, allowed=(host.lower(),))
        if not link:
            return False, "account created but the verification email did not arrive in time"
        v = ctx.new_page()
        try:
            v.goto(link, wait_until="domcontentloaded", timeout=45000)
            v.wait_for_timeout(4000)
        finally:
            v.close()
        accounts.mark_verified(host)
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(4000)
        pg.locator("[data-automation-id=signInLink]").first.click(timeout=5000)
        pg.wait_for_timeout(1500)
        pg.locator("[data-automation-id=email]").first.fill(EMAIL)
        pg.locator("[data-automation-id=password]").first.fill(new_pw)
        _click_submit_like(pg, "[data-automation-id=signInSubmitButton]")
        ok = wait_outcome(pg, 40) == "in"
        return ok, "verified and signed in" if ok else f"sign-in failed after verify: {_banner(pg)}"
    return False, f"Workday did not finish creating the account (waited 45s): {_banner(pg) or body[:100]}"


def _choose_dropdown(pg, f, want) -> bool:
    """Workday single-select: button + listbox. Long lists (states) need type-ahead before the option is rendered."""
    box = pg.locator(f'[data-jh="{f["id"]}"]')
    try:
        box.locator("button[aria-haspopup=listbox], [role=combobox]").first.click(timeout=4000)
        pg.wait_for_selector("[role=option]", timeout=5000)
        for attempt in range(2):
            opts = [o.strip() for o in pg.locator("[role=option]:visible").all_inner_texts() if o.strip()]
            pick = engine._pick(opts, want)
            if pick:
                pg.locator("[role=option]:visible").filter(has_text=re.compile(rf"^\s*{re.escape(pick)}\s*$")).first.click(timeout=4000)
                pg.wait_for_timeout(700)
                return True
            if attempt == 0:  # type-ahead to bring the option into the rendered list
                pg.keyboard.type(str(want)[:6], delay=60)
                pg.wait_for_timeout(900)
        pg.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        try:
            pg.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
    return False


def _choose_prompt(pg, f, wants: list[str]) -> bool:
    """Workday searchable 'prompt' picker (How did you hear, Country phone code): type, pick from promptOption (may be nested)."""
    box = pg.locator(f'[data-jh="{f["id"]}"]')
    inp = box.locator("input").first
    for w in wants:
        try:
            pg.keyboard.press("Escape")  # a list left open by a previous picker would intercept the click
            pg.wait_for_timeout(400)
            inp.focus()
            inp.fill("")
            inp.type(w, delay=50)
            pg.keyboard.press("Enter")
            pg.wait_for_timeout(1800)
            for _ in range(3):  # categories can nest: pick the best matching option, drill down until something is selected
                opts = pg.locator("[data-automation-id=promptOption]:visible, [data-automation-id=menuItem]:visible, [role=option]:visible")
                texts = [t.strip() for t in opts.all_inner_texts() if t.strip()]
                if not texts:
                    break
                pick = engine._pick(texts, w) or (texts[0] if len(texts) == 1 else None)
                if not pick:
                    break
                opts.filter(has_text=pick).first.click(timeout=3000, force=True)
                pg.wait_for_timeout(1500)
                shown = engine._norm(box.inner_text())
                if re.search(r"citizen|national", f["label"], re.I) and "india" not in shown:
                    continue  # picked the wrong country: try the next candidate instead of accepting it
                if box.locator("[data-automation-id=selectedItem], [data-automation-id=selectedItemList] *").count() or \
                        engine._norm(pick.split("(")[0]) in shown.replace(engine._norm(f["label"]), ""):
                    pg.keyboard.press("Escape")
                    return True
        except Exception:  # noqa: BLE001
            continue
    from apply import widgets  # shared toolkit: several search terms + fuzzy pick + verification
    return bool(widgets.choose_searchable(pg, box, wants[0], multi=bool(re.search(r"skill", f["label"], re.I))))


def _hear_candidates(job_source: str) -> list[str]:
    if "linkedin" in (job_source or ""):
        return ["LinkedIn", "Social Media", "Job Board", "Company Website", "Careers"]
    if "naukri" in (job_source or ""):
        return ["Naukri", "Job Board", "Company Website", "Careers"]
    return ["Company Website", "Careers", "Job Board", "Internet", "LinkedIn"]


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
DEGREE_WANTS = {"mba": ["MBA", "Master of Business Administration", "Master's Degree", "Masters", "Post Graduate", "Postgraduate"],
                "be": ["Bachelor of Engineering", "B.E", "BE", "Bachelor of Technology", "Bachelor's Degree", "Bachelors", "Graduate"]}


def _read_date(field_box) -> tuple[str, str]:
    """The widget's month / year inputs as Workday itself stores them ('' when the segment is absent or empty)."""
    out = []
    for seg in ("Month", "Year"):
        loc = field_box.locator(f"[data-automation-id=dateSection{seg}-input]")
        try:
            out.append(loc.first.input_value().strip() if loc.count() else "")
        except Exception:  # noqa: BLE001
            out.append("")
    return out[0], out[1]


def _date_ok(field_box, month: int | None, year: str) -> bool:
    m, y = _read_date(field_box)
    if y.lstrip("0") != str(year).lstrip("0"):
        return False
    return not month or m.lstrip("0") == str(month).lstrip("0")


def _type_date(field_box, month: int | None, year: str):
    """Workday date widgets put a 'display' tile over the real input: click the tile, then type digits (month auto-advances to year).

    a company (KB-41): the old version accepted any substring - a row left at 06/2023 "passed" a 2023 check while the typed text had
    landed on top of it, and a wrong date is a FABRICATED FACT on a submitted application. It now compares the widget's own inputs, does
    NOTHING when they are already right, and only reports success after the fallback fill has been re-read.
    """
    pg = field_box.page
    try:
        if _date_ok(field_box, month, year):
            return True
        first = "Month" if month and field_box.locator("[data-automation-id=dateSectionMonth-display]").count() else "Year"
        field_box.locator(f"[data-automation-id=dateSection{first}-display]").first.click(force=True)
        pg.wait_for_timeout(250)
        pg.keyboard.type((f"{month:02d}" if first == "Month" else "") + str(year), delay=90)
        pg.wait_for_timeout(300)
        if _date_ok(field_box, month, year):
            return True
        # fallback: fill the real inputs directly (fill replaces, and every keystroke path above may have appended)
        if month and field_box.locator("[data-automation-id=dateSectionMonth-input]").count():
            field_box.locator("[data-automation-id=dateSectionMonth-input]").first.fill(f"{month:02d}")
        field_box.locator("[data-automation-id=dateSectionYear-input]").first.fill(str(year))
        field_box.locator("[data-automation-id=dateSectionYear-input]").first.dispatch_event("change")
        pg.wait_for_timeout(200)
        return _date_ok(field_box, month, year)
    except Exception:  # noqa: BLE001
        return False


def _type_full_date(box, dt) -> bool:
    """Workday date widget with month / day / year sections: click the first section's tile and type the digits in order."""
    pg = box.page
    try:
        has_m = box.locator("[data-automation-id=dateSectionMonth-display]").count() > 0
        has_d = box.locator("[data-automation-id=dateSectionDay-display]").count() > 0
        digits = (f"{dt.month:02d}" if has_m else "") + (f"{dt.day:02d}" if has_d else "") + str(dt.year)
        first = "Month" if has_m else ("Day" if has_d else "Year")
        box.locator(f"[data-automation-id=dateSection{first}-display]").first.click(force=True)
        pg.wait_for_timeout(250)
        pg.keyboard.type(digits, delay=90)
        pg.wait_for_timeout(300)
        vals = box.locator("input").evaluate_all("els => els.map(e => e.value).join('')")
        return str(dt.year) in vals
    except Exception:  # noqa: BLE001
        return False


def _dropdown_in(pg, box, wants: list[str]) -> bool:
    try:
        box.locator("button[aria-haspopup=listbox]").first.click(timeout=4000)
        pg.wait_for_selector("[role=option]", timeout=5000)
        opts = [o.strip() for o in pg.locator("[role=option]:visible").all_inner_texts() if o.strip()]
        for w in wants:
            pick = engine._pick(opts, w)
            if pick:
                pg.locator("[role=option]:visible").filter(has_text=re.compile(rf"^\s*{re.escape(pick)}\s*$")).first.click(timeout=4000)
                return True
        pg.keyboard.press("Escape")
    except Exception:  # noqa: BLE001
        pass
    return False


TRIM_EXPERIENCE_JS = """(keep) => {
  const rows = () => [...document.querySelectorAll('[data-automation-id="formField-jobTitle"]')];
  const all = rows();
  if (all.length <= keep) return 0;
  let p = all[all.length - 1];
  for (let k = 0; k < 8 && p.parentElement; k++) {   // nearest ancestor holding THIS row's Delete button and no other experience row
    p = p.parentElement;
    if (rows().filter(f => p.contains(f)).length > 1) return -1;
    const del = [...p.querySelectorAll('button')].find(b => /^\\s*delete\\s*$/i.test(b.innerText || ''));
    if (del) { del.click(); return 1; }
  }
  return -1; }"""


def _trim_experience_rows(pg, keep: int) -> int:
    """Earlier drafts / a CV parse leave rows Workday cannot accept (a company, KB-41: two identical Example Retail rows, an empty row and a
    fourth row beyond her history). Deleting the surplus is the only truthful repair: facts.yaml is the only source of dates, so a row we did
    not write must never be submitted. Never deletes below her real history."""
    deleted = 0
    for _ in range(12):
        try:
            r = pg.evaluate(TRIM_EXPERIENCE_JS, keep)
        except Exception:  # noqa: BLE001
            return deleted
        if r != 1:
            return deleted
        deleted += 1
        pg.wait_for_timeout(900)
    return deleted


def repair_experience(pg, filled: list | None = None) -> bool:
    """Navigator-path 'My Experience' repair (KB-41): trim surplus rows, then rewrite every row from facts.yaml. True when the page has rows."""
    filled = filled if filled is not None else []
    try:
        if not pg.locator('[data-automation-id="formField-jobTitle"]').count():
            return False
    except Exception:  # noqa: BLE001
        return False
    fill_experience(pg, filled)   # trims surplus rows first, then writes her real history + dates
    return True


def fill_experience(pg, filled: list):
    """'My Experience': work history + education straight from facts.yaml (truth only). Workday's CV parse is unreliable."""
    from core import profile
    f = profile.facts()
    q = lambda auto: pg.locator(f"[data-automation-id=formField-{auto}]")  # noqa: E731
    adds = lambda: pg.locator("[data-automation-id=add-button]")  # noqa: E731 [0]=work experience, [1]=education
    dropped = _trim_experience_rows(pg, len(f["experience"]))
    if dropped:
        filled.append(("experience rows beyond her history", f"deleted {dropped}"))
    for i, e in enumerate(f["experience"]):
        if q("jobTitle").count() <= i:
            adds().nth(0).click()
            pg.wait_for_timeout(1500)
        q("jobTitle").nth(i).locator("input").first.fill(e["title"])
        q("companyName").nth(i).locator("input").first.fill(e["company"])
        if q("location").count() > i:
            q("location").nth(i).locator("input").first.fill(e["location"])
        cw = q("currentlyWorkHere").nth(i).locator("input[type=checkbox]")
        if cw.count() and cw.first.is_checked():
            cw.first.uncheck(force=True)  # not currently employed
        sm, sy = e["start"].split()
        em, ey = e["end"].split()
        _type_date(q("startDate").nth(i), MONTHS[sm[:3].lower()], sy)
        _type_date(q("endDate").nth(i), MONTHS[em[:3].lower()], ey)
        if q("roleDescription").count() > i:
            q("roleDescription").nth(i).locator("textarea").first.fill("\n".join(f"- {b}" for b in e["bullets"]))
        filled.append((f"experience {i + 1}", f"{e['title']} @ {e['company']}"))
    for j, ed in enumerate(f["education"]):
        if q("schoolName").count() <= j:
            adds().nth(adds().count() - 1).click()  # the education section's Add Another (last add button)
            pg.wait_for_timeout(1500)
        school = q("schoolName").nth(j)
        if school.locator("input").count():
            school.locator("input").first.fill(ed["institution"])
        key = "mba" if "mba" in ed["degree"].lower() else "be"
        if not _dropdown_in(pg, q("degree").nth(j), DEGREE_WANTS[key]):
            filled.append((f"degree {j + 1}", "NOT SET"))
        if q("firstYearAttended").count() > j and ed.get("start_year"):
            _type_date(q("firstYearAttended").nth(j), None, ed["start_year"])
        if q("lastYearAttended").count() > j:
            _type_date(q("lastYearAttended").nth(j), None, ed["year"])
        filled.append((f"education {j + 1}", ed["degree"]))
    pg.wait_for_timeout(800)
    # read back every date widget so a silent failure is visible in the result
    for auto in ("startDate", "endDate", "firstYearAttended", "lastYearAttended"):
        for i in range(q(auto).count()):
            vals = q(auto).nth(i).locator("input").evaluate_all("els => els.map(e => e.value).join('/')")
            filled.append((f"{auto}[{i}]", vals or "EMPTY"))


def _dropdown_options(pg, f) -> list[str]:
    box = pg.locator(f'[data-jh="{f["id"]}"]')
    try:
        box.locator("button[aria-haspopup=listbox], [role=combobox]").first.click(timeout=4000)
        pg.wait_for_selector("[role=option]", timeout=4000)
        opts = [o.strip() for o in pg.locator("[role=option]:visible").all_inner_texts() if o.strip() and o.strip().lower() != "select one"]
        pg.keyboard.press("Escape")
        return opts[:60]
    except Exception:  # noqa: BLE001
        return []


def _settle(pg, max_ms: int = 9000):
    """Workday renders form sections progressively (esp. after a reload): wait until the field count stops changing."""
    last, stable, waited = -1, 0, 0
    while waited < max_ms and stable < 2:
        n = pg.locator("[data-automation-id^='formField-']").count()
        stable = stable + 1 if n == last and n > 0 else 0
        last = n
        pg.wait_for_timeout(700)
        waited += 700


def fill_step(pg, pdf, unknown: list, filled: list, job_id: str):
    """Rules first; anything left goes to one truth-bound LLM call; only then is the job parked."""
    _settle(pg)
    start = len(unknown)
    _fill_step_rules(pg, pdf, unknown, filled, job_id)
    todo = [u for u in dict.fromkeys(unknown[start:])]
    if not todo:
        return
    from apply import llm_answer
    fields = {f["label"]: f for f in pg.evaluate(FIELDS_JS)}
    qs = []
    for lab in todo:
        f = fields.get(lab.split(" [")[0])
        if f and f["kind"] == "checkbox":  # required non-consent checkbox: the LLM decides if it is truthfully tickable
            qs.append({"q": re.sub(r"\*", "", f["label"]).strip() + " (tick this box?)", "options": ["Yes", "No"], "_f": f})
            continue
        if not f or f["kind"] in ("file", "date"):
            continue
        opts = _dropdown_options(pg, f) if f["kind"] == "dropdown" else None
        if f["kind"] == "radio":
            box = pg.locator(f'[data-jh="{f["id"]}"]')
            opts = [t.strip() for t in box.locator("label").all_inner_texts() if t.strip()][1:] or None
        qs.append({"q": re.sub(r"\*", "", f["label"]).strip(), "options": opts, "_f": f})
    got = llm_answer.answer([{"q": x["q"], "options": x["options"]} for x in qs], engine.CURRENT_JOB)
    for x in qs:
        a, f = got.get(x["q"]), x["_f"]
        if not a:
            continue
        ok = False
        try:
            if f["kind"] == "checkbox":
                if a.strip().lower().startswith("y"):
                    ok = engine._tick(pg, pg.locator(f'[data-jh="{f["id"]}"]').locator("input[type=checkbox]").first)
            elif f["kind"] in ("text", "textarea"):
                pg.locator(f'[data-jh="{f["id"]}"]').locator("input:not([type=hidden]), textarea").first.fill(a)
                ok = True
            elif f["kind"] == "dropdown":
                ok = _choose_dropdown(pg, f, a)
            elif f["kind"] == "prompt":
                ok = _choose_prompt(pg, f, [a])
            elif f["kind"] == "radio":
                radios = pg.locator(f'[data-jh="{f["id"]}"]').locator("input[type=radio]")
                texts = [radios.nth(i).evaluate("e => ((e.id && document.querySelector('label[for=\"' + e.id + '\"]')) || e.parentElement).innerText.trim()")
                         for i in range(radios.count())]
                p = engine._pick(texts, a)
                if p is not None:
                    engine._tick(pg, radios.nth(texts.index(p)))
                    ok = True
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            filled.append((f["label"][:40] + " (AI)", a[:30]))
            for u in list(unknown):
                if u.split(" [")[0] == f["label"]:
                    unknown.remove(u)


INVALID_JS = """() => [...document.querySelectorAll('[data-jh]')].map(b => {
    const err = b.querySelector('[data-automation-id=errorMessage], [id*=error], [aria-invalid=true]');
    const msg = (b.querySelector('[data-automation-id=errorMessage]') || {}).innerText || '';
    return err ? {id: b.getAttribute('data-jh'), msg: msg.trim().slice(0, 160)} : null }).filter(Boolean)"""


def fix_invalid(pg, filled: list, job_id: str) -> int:
    """Workday refused to advance: read each field's own error text, ask the LLM for a corrected value, refill. Returns fields fixed."""
    from apply import llm_answer
    bad = {x["id"]: x["msg"] for x in pg.evaluate(INVALID_JS)}
    if not bad:
        return 0
    fields = {f["id"]: f for f in pg.evaluate(FIELDS_JS)}
    qs = []
    for fid, msg in bad.items():
        f = fields.get(fid)
        if not f or f["kind"] == "file":
            continue
        if re.fullmatch(r"\s*(language|overall|speaking|reading|writing)\s*\*?\s*", f["label"], re.I):
            continue  # handled by the language rule (distinct language per row); the LLM would just create duplicates
        opts = _dropdown_options(pg, f) if f["kind"] == "dropdown" else (["Yes", "No"] if f["kind"] == "checkbox" else None)
        qs.append({"q": f"{re.sub(r'[*]', '', f['label']).strip()} (current value '{f['value']}' was rejected: {msg or 'invalid'})",
                   "options": opts, "_f": f})
    got = llm_answer.answer([{"q": x["q"], "options": x["options"]} for x in qs], engine.CURRENT_JOB) if qs else {}
    n = 0
    for x in qs:
        a, f = got.get(x["q"]), x["_f"]
        if not a:
            continue
        try:
            box = pg.locator(f'[data-jh="{f["id"]}"]')
            if f["kind"] in ("text", "textarea"):
                inp = box.locator("input:not([type=hidden]), textarea").first
                inp.fill("")
                inp.fill(a)
            elif f["kind"] == "dropdown":
                _choose_dropdown(pg, f, a)
            elif f["kind"] == "prompt":
                _choose_prompt(pg, f, [a])
            elif f["kind"] == "date":
                d = None
                for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d", "%d %b %Y"):
                    try:
                        d = datetime.strptime(a, fmt)
                        break
                    except ValueError:
                        continue
                if d:
                    _type_full_date(box, d)
            elif f["kind"] == "checkbox" and a.lower().startswith("y"):
                engine._tick(pg, box.locator("input[type=checkbox]").first)
            filled.append((f["label"][:40] + " (AI fix)", a[:30]))
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def _languages() -> list:
    """The user's languages (answers.yaml `languages`, comma separated, or facts.yaml preferences.languages)."""
    try:
        from core import profile
        v = profile.answers().get("languages") or profile.preferences().get("languages") or []
    except Exception:  # noqa: BLE001
        v = []
    return [x.strip() for x in (v.split(",") if isinstance(v, str) else v) if str(x).strip()]


LANGUAGES = _languages()  # Each language row gets a DIFFERENT one (Workday rejects duplicates)


DELETE_EXTRA_LANG_JS = r"""(keep) => {
  const isLang = f => /^language/i.test((f.querySelector('label') || {}).innerText || '');
  const fields = [...document.querySelectorAll('[data-automation-id^="formField-"]')].filter(isLang);
  if (fields.length <= keep) return 0;
  const f = fields[fields.length - 1];
  let p = f;
  for (let k = 0; k < 6 && p.parentElement; k++) {   // nearest ancestor holding THIS row's Delete button and no other language row
    p = p.parentElement;
    const n = [...p.querySelectorAll('[data-automation-id^="formField-"]')].filter(isLang).length;
    const del = [...p.querySelectorAll('button')].find(b => /^\s*delete\s*$/i.test(b.innerText || ''));
    if (n > 1) return -1;
    if (del) { del.click(); return 1; }
  }
  return -1; }"""


def _trim_language_rows(pg, keep: int):
    """Earlier attempts leave extra (duplicate / empty) Language rows in the saved draft; Workday rejects duplicates, so delete the surplus."""
    for _ in range(8):
        try:
            r = pg.evaluate(DELETE_EXTRA_LANG_JS, keep)
        except Exception:  # noqa: BLE001
            return
        if r != 1:
            return
        pg.wait_for_timeout(900)


DELETE_EMPTY_LANG_JS = r"""() => {
  const isLang = f => /^language/i.test((f.querySelector('label') || {}).innerText || '');
  for (const f of [...document.querySelectorAll('[data-automation-id^="formField-"]')].filter(isLang)) {
    const shown = ((f.querySelector('button, input') || {}).innerText || (f.querySelector('input') || {}).value || '').trim();
    if (shown && !/^select one$/i.test(shown)) continue;           // this row has a language: keep it
    let p = f;
    for (let k = 0; k < 6 && p.parentElement; k++) {                 // nearest ancestor holding THIS row's Delete button and no other language row
      p = p.parentElement;
      const n = [...p.querySelectorAll('[data-automation-id^="formField-"]')].filter(isLang).length;
      const del = [...p.querySelectorAll('button')].find(b => /^\s*delete\s*$/i.test(b.innerText || ''));
      if (n > 1) break;
      if (del) { del.click(); return 1; }
    }
  }
  return 0; }"""


def drop_empty_language_rows(pg) -> int:
    """The Languages block is OPTIONAL, but a row that is added and left empty makes Workday demand Language, Able to Teach, Able to Translate, Reading,
    Speaking and Writing (a company's Return to Work Program,: the navigator added a row and then could not fill facts she never recorded).
    An empty row carries no information, so delete it instead of inventing answers. Returns how many rows were removed."""
    removed = 0
    for _ in range(6):
        try:
            r = pg.evaluate(DELETE_EMPTY_LANG_JS)
        except Exception:  # noqa: BLE001
            return removed
        if r != 1:
            return removed
        removed += 1
        pg.wait_for_timeout(900)
    return removed


DELETE_BAD_SITE_JS = r"""() => {
  const rows = [...document.querySelectorAll('[role=group]')].filter(g => [...g.children].some(c => /^\s*Websites?\s+\d+/i.test(c.innerText || '')) && g.querySelector('input[name=url]'));
  const seen = new Set();
  for (const g of rows) {
    const v = (g.querySelector('input[name=url]').value || '').trim().toLowerCase().replace(/\/+$/, '');
    if (!v || seen.has(v)) {                                         // empty or a repeat of an earlier row: carries no information
      const del = [...g.querySelectorAll('button')].find(b => /delete/i.test(b.innerText || ''));
      if (del) { del.click(); return 1; }
    }
    seen.add(v);
  }
  return 0; }"""


def drop_bad_website_rows(pg) -> int:
    """Earlier attempts leave extra Websites rows in the saved draft (the same LinkedIn address twice, or an empty row): Workday answers
    "You can't add duplicate website URLs" / "The field URL is required" and the page never advances (Caterpillar returnship).
    Delete the empty and the repeated rows; the first row holding each address stays. Returns how many rows were removed."""
    removed = 0
    for _ in range(6):
        try:
            r = pg.evaluate(DELETE_BAD_SITE_JS)
        except Exception:  # noqa: BLE001
            return removed
        if r != 1:
            return removed
        removed += 1
        pg.wait_for_timeout(900)
    return removed


def _fill_step_rules(pg, pdf, unknown: list, filled: list, job_id: str):
    _trim_language_rows(pg, len(LANGUAGES))
    lang_rows = 0
    for f in pg.evaluate(FIELDS_JS):
        k, label = f["kind"], f["label"]
        box = pg.locator(f'[data-jh="{f["id"]}"]')
        try:
            if k == "text" and re.search(r"\bgpa\b|overall result|grade point", label, re.I):
                if f["value"] and not re.fullmatch(r"[\d.]+\s*(/\s*[\d.]+)?", f["value"].strip()):  # junk left in a draft (e.g. a language level): clear it
                    box.locator("input:not([type=hidden])").first.fill("")
                    filled.append((label[:20], "cleared"))
                continue
            if k == "dropdown" and re.fullmatch(r"\s*(overall|speaking|reading|writing|comprehension|listening|proficiency)\s*\*?\s*", label, re.I):
                if not f["value"] or f["value"].lower().startswith("select"):  # language proficiency: answers.yaml language_proficiency, never invented
                    lvl = str(am.profile.answers().get("language_proficiency") or "").strip()
                    wants = [lvl] + (["Advanced - C1", "Advanced", "Fluent", "Proficient", "Professional"] if lvl.lower() in ("advanced", "fluent", "proficient") else [])
                    for want in (wants if lvl else []):
                        if _choose_dropdown(pg, f, want):
                            filled.append((label[:20], want))
                            break
                    else:
                        if f["required"]:
                            unknown.append(label)
                continue
            if k in ("dropdown", "prompt") and re.fullmatch(r"\s*language\s*\*?\s*", label, re.I):
                if not LANGUAGES:   # no languages recorded (answers.yaml languages): never guessed
                    if f["required"]:
                        unknown.append(label)
                    continue
                want = LANGUAGES[min(lang_rows, len(LANGUAGES) - 1)]
                lang_rows += 1
                if engine._norm(want) not in engine._norm(f["value"] or box.inner_text()):
                    ok = _choose_dropdown(pg, f, want) if k == "dropdown" else _choose_prompt(pg, f, [want])
                    if ok:
                        filled.append((f"language {lang_rows}", want))
                    elif f["required"]:
                        unknown.append(label)
                continue
            if k == "file":
                if re.search(r"resume|cv", label, re.I) or True:
                    box.locator("input[type=file]").first.set_input_files(str(pdf))
                    filled.append(("resume", pdf.name))
                    pg.wait_for_timeout(6000)  # Workday parses the CV and pre-fills experience
                continue
            if k == "checkbox":
                cb = box.locator("input[type=checkbox]").first
                if re.search(r"currently work|current(ly)? employed|present job|i work here", label, re.I):
                    if cb.is_checked() and str(am.profile.answers().get("currently_employed") or "").lower() == "no":  # not currently employed: never claim a current job
                        cb.uncheck(force=True)
                        filled.append(("currently works here", "unticked"))
                    continue
                if am.MARKETING.search(label):
                    continue
                if am.CONSENT.search(label) and not cb.is_checked():
                    engine._tick(pg, cb)
                    filled.append(("consent", label[:40]))
                elif f["required"] and not cb.is_checked():
                    unknown.append(label)
                continue
            if f["value"] and k in ("text", "textarea", "dropdown", "date") and f["value"].lower() not in ("select one", "select", ""):
                # Workday's CV auto-fill reads the ALL-CAPS name and '+91-...' phone off the CV and then rejects them itself:
                # identity fields are always overwritten with the correct values
                if not (k == "text" and re.search(r"given name|family name|first name|last name|address line 1|\bcity\b|postal|zip|phone number", label, re.I)):
                    continue
                want = am.answer_for(re.sub(r"\*", "", label).strip(), job_id)
                if not isinstance(want, str) or want == f["value"]:
                    continue
            if k == "prompt" and box.locator("[data-automation-id=selectedItem], [data-automation-id=selectedItemList] *").count():
                if re.search(r"citizen|national|country", label, re.I) and "india" not in engine._norm(box.inner_text()):
                    # a wrong earlier pick (e.g. the first alphabetical 'Citizen (Algeria)'): clear it and choose India
                    try:
                        box.locator("[data-automation-id=DELETE_charm], [data-automation-id=selectedItem] button").first.click(timeout=3000)
                        pg.wait_for_timeout(600)
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    continue
            clean = re.sub(r"\*", "", label).strip()
            if k == "prompt":  # searchable pickers
                if re.search(r"how did you hear|source", clean, re.I):
                    wants = _hear_candidates(JOB_SOURCE["src"])
                elif re.search(r"country phone code|phone code|country code", clean, re.I):
                    wants = ["India", "+91"]
                elif re.search(r"school|university|college|institution", clean, re.I):  # education picker: her real institutions only
                    from apply import widgets
                    from core import profile
                    edu = profile.facts()["education"]
                    idx = 1 if len(box.page.locator("[data-automation-id^='formField-school'], [data-automation-id*='school' i]").all()) > 1 and                         f["id"] != pg.evaluate(FIELDS_JS)[0]["id"] and False else 0
                    if widgets.choose_searchable(pg, box, edu[idx]["institution"], fallback="Other"):
                        filled.append((label[:40], edu[idx]["institution"][:30]))
                    elif f["required"]:
                        unknown.append(label)
                    continue
                elif re.search(r"skill", clean, re.I):  # skills picker: only skills on her CV
                    from apply import widgets
                    from core import profile
                    sk = profile.facts()["skills"]
                    want = ", ".join((sk.get("Tools", []) + sk.get("Domain", []))[:8])
                    got = widgets.choose_searchable(pg, box, want, multi=True)
                    if got:
                        filled.append((label[:40], ", ".join(got)[:40]))
                    elif f["required"]:
                        unknown.append(label)
                    continue
                elif re.search(r"citizen|nationality", clean, re.I):
                    from core import profile
                    wants = profile.citizenship_options(bool(re.search(r"nationality", clean, re.I)))  # never the first alphabetical 'Citizen (Algeria)'
                else:
                    a = am.answer_for(clean, job_id)
                    wants = [str(a)] if a not in (None, "SKIP", "DECLINE") and not hasattr(a, "strftime") else []
                if wants and _choose_prompt(pg, f, wants):
                    filled.append((label[:40], wants[0]))
                elif f["required"]:
                    unknown.append(label)
                continue
            ans = am.answer_for(clean, job_id)
            if ans == "SKIP":
                if f["required"]:
                    unknown.append(label)
                continue
            if ans == "ASAP":
                ans = datetime.now() if k == "date" else "Immediately"
            if hasattr(ans, "strftime") and k == "date":  # e.g. Date of Birth: Workday's three-part date widget
                if _type_full_date(box, ans):
                    filled.append((label[:40], ans.strftime("%d/%m/%Y")))
                elif f["required"]:
                    unknown.append(label)
                continue
            if ans is None or hasattr(ans, "strftime"):
                if f["required"]:
                    unknown.append(label)
                continue
            if ans == "DECLINE":
                if k == "dropdown" and _choose_dropdown(pg, f, "decline"):
                    filled.append((label[:40], "declined"))
                continue
            if k in ("text", "textarea"):
                box.locator("input:not([type=hidden]), textarea").first.fill(str(ans))
                filled.append((label[:40], str(ans)[:30]))
            elif k in ("dropdown", "prompt"):
                if _choose_dropdown(pg, f, ans):
                    filled.append((label[:40], str(ans)[:30]))
                elif f["required"]:
                    unknown.append(label)
            elif k == "radio":  # match the option's OWN label exactly (substring 'No' would hit 'a company')
                radios = box.locator("input[type=radio]")
                texts = [radios.nth(i).evaluate(
                    "e => { const l = e.id && document.querySelector('label[for=\"' + e.id + '\"]');"
                    " return ((l && l.innerText) || (e.closest('label') || e.parentElement).innerText || '').trim(); }")
                    for i in range(radios.count())]
                pick = engine._pick(texts, ans)
                if pick is not None:
                    engine._tick(pg, radios.nth(texts.index(pick)))
                    filled.append((label[:40], pick))
                elif f["required"]:
                    unknown.append(label)
        except Exception as e:  # noqa: BLE001
            if f["required"]:
                unknown.append(f"{label} [{type(e).__name__}: {str(e).splitlines()[0][:70]}]")



def _fix_state(pg) -> bool:
    """Workday's required State / Country Phone Code / Phone Device Type that the generic field scan does not list. Picks through the shared
    VERIFYING picker, treating 'Select' as a placeholder (a company: the old check read 'Select' as a real value and never fixed State)."""
    from apply import widgets
    try:
        return bool(widgets.fill_workday_required(pg, _banner(pg) or ""))
    except Exception:  # noqa: BLE001
        return False

def apply_workday(ctx, url: str, pdf, dry_run: bool, job_id: str, source: str = "") -> dict:
    res = {"status": "failed", "detail": "", "unknown": [], "filled": [], "screenshot": None}
    JOB_SOURCE["src"] = source
    shot = engine.SHOTS / datetime.now().strftime("%Y-%m-%d") / f"{job_id}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    engine.CURRENT_JOB["id"] = job_id
    url = engine.normalize_url(url)  # /ja-JP/ and /fr-CA/ portals break text-based detection
    host = urlparse(url).netloc
    t_start = datetime.now().timestamp()
    pg = ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            pg.wait_for_selector("[data-automation-id=adventureButton]", timeout=30000)
        except Exception:  # noqa: BLE001
            # a company and ICON were marked "closed" although Workday's own API said canApply=True (slow page / banner). Verify before giving up.
            from discover import ats_api
            open_ = ats_api.workday_open(url)
            if open_ is True:
                try:
                    pg.reload(wait_until="domcontentloaded", timeout=60000)
                    pg.wait_for_selector("[data-automation-id=adventureButton]", timeout=45000)
                except Exception:  # noqa: BLE001
                    res.update(status="failed", detail="TimeoutError: Workday Apply button did not appear although the posting is still open (retry later)")
                    return res
            elif open_ is False:
                res.update(status="closed", detail="Workday's own public API reports the posting cannot be applied to (canApply false); no Apply button on the page")
                return res
            else:
                # KB-58: None means the API said NOTHING (403 permission denied on globalhr.wd5, a tenant without a CXS route, a
                # timeout). a company was filed `closed` on exactly that silence - an API that could not answer is
                # not evidence that a posting is gone. Only the page's own confirmation may close it now; otherwise it is retried.
                try:
                    said = engine.page_state(pg, confirm=True) == "closed"
                except Exception:  # noqa: BLE001
                    said = False
                if said:
                    res.update(status="closed", detail="the page itself says this posting is closed ("
                               + (engine.LAST_CLOSED_HIT.get("text") or "")[:90] + ")")
                    return res
                res.update(status="failed", detail=("Workday's API gave no answer for this posting (could not confirm it is open) and the Apply "
                                                    "button did not appear; retried later, not a closure")[:220])
                return res
        try:
            pg.locator("[data-automation-id=legalNoticeDeclineButton]").click(timeout=1500)
        except Exception:  # noqa: BLE001
            pass
        pg.locator("[data-automation-id=adventureButton]").first.click()
        pg.wait_for_timeout(3000)
        # Prefer "Autofill with Resume": Workday parses the tailored CV and pre-fills experience + education (works across tenants).
        auto = pg.locator("[data-automation-id=autofillWithResume]")
        manual = pg.locator("[data-automation-id=applyManually]")
        if auto.count():
            auto.first.click()
        elif pg.get_by_text("Autofill with Resume").count():
            pg.get_by_text("Autofill with Resume").first.click()
        else:
            (manual.first if manual.count() else pg.get_by_text("Apply Manually").first).click()
        pg.wait_for_selector("[data-automation-id=applyFlowPage]", timeout=25000)
        pg.wait_for_timeout(4000)
        if pg.locator("[data-automation-id=signInContent]").count() or "create account" in _active_step(pg):
            ok, why = sign_in_or_create(pg, ctx, host, dry_run)
            if not ok and why == "ALREADY_APPLIED":
                res.update(status="already_applied", detail="Workday says you already applied to this job")
                return res
            if not ok and why.startswith("sign-in did not complete"):
                res.update(status="failed", detail=why)  # transient: retried later, never parked on the user
                return res
            if not ok:
                pg.screenshot(path=str(shot))
                res.update(status="needs_you", detail=why, screenshot=str(shot))
                return res
        glitches = 0
        for _ in range(12):  # (Autofill) My Information .. Review
            if datetime.now().timestamp() - t_start > 480:  # hard per-job budget (8 min): Workday is slow but must not run away
                res.update(status="failed", detail="time budget exceeded (8 min) on this Workday job")
                return res
            pg.wait_for_timeout(1500)
            if re.search(r"something went wrong|please refresh the page", pg.inner_text("body"), re.I):
                glitches += 1  # Workday's own transient error: progress is saved server-side, so refresh and carry on
                if glitches > 3:
                    res.update(status="needs_you", detail="Workday kept showing 'Something went wrong' after refreshing")
                    return res
                pg.reload(wait_until="domcontentloaded")
                pg.wait_for_timeout(7000)
                continue
            step = _active_step(pg)
            from core import activity
            activity.log("portal", "Workday: " + re.sub(r"current step \d+ of \d+\s*", "", step).strip(), portal="Workday")
            import os
            if os.environ.get("WD_DUMP") and os.environ["WD_DUMP"] in step:  # developer aid: dump the page layout and stop
                print("DUMP", step, pg.evaluate("""() => [...document.querySelectorAll('[data-automation-id]')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0})
                    .map(e=>e.getAttribute('data-automation-id')+'<'+e.tagName.toLowerCase()+(e.type?':'+e.type:'')+'>').filter(x=>!/^(muse|social|footer|header|logo|utility|navigation|accessib|legal|hammy|followUs|privacy|banner|progressBar)/.test(x))"""))
                print("BUTTONS", [t.strip() for t in pg.locator("[data-automation-id=applyFlowPage] button:visible").all_inner_texts() if t.strip()])
                pg.wait_for_timeout(6000)
                print("FIELDS", [(f["kind"], f["label"][:30], f["value"][:20]) for f in pg.evaluate(FIELDS_JS)])
                if os.environ.get("WD_FILL_TEST"):
                    unk, fil = [], []
                    if "experience" in step:
                        fill_experience(pg, fil)
                        print("EXP_FILLED", fil)
                    if os.environ.get("WD_LANG_DEBUG"):
                        print("LANGDBG", pg.evaluate("""() => [...document.querySelectorAll('[data-automation-id^="formField-"]')].filter(f => /^language/i.test((f.querySelector('label')||{}).innerText||'')).map(f => {
                            let p = f, out = []; for (let k = 0; k < 7 && p.parentElement; k++) { p = p.parentElement;
                              const b = [...p.querySelectorAll(':scope > div button, :scope button')].slice(0,3).map(x => (x.getAttribute('data-automation-id')||'')+'|'+(x.getAttribute('aria-label')||'')+'|'+(x.innerText||'').trim().slice(0,15));
                              out.push((p.getAttribute('data-automation-id')||p.tagName)+':'+b.join(',')); } return out.join(' >> '); })"""))
                    fill_step(pg, pdf, unk, fil, job_id)
                    print("FILLED", fil)
                    print("UNKNOWN", unk)
                    _next(pg)
                    pg.wait_for_timeout(5000)
                    print("STEP_AFTER", _active_step(pg))
                    print("INVALID", pg.evaluate(INVALID_JS), "BANNER", _banner(pg))
                    print("FIELDS2", [(f["kind"], f["label"][:30], f["value"][:20]) for f in pg.evaluate(FIELDS_JS)][-12:])
                if os.environ.get("WD_RADIO_TEST"):
                    fields = pg.evaluate(FIELDS_JS)
                    print("FIELDS", [(f["label"][:40], f["kind"], f["id"]) for f in fields])
                    rad = pg.locator("input[name=candidateIsPreviousWorker][value=false]")
                    for how in ("tick", "label", "span", "keyboard"):
                        try:
                            if how == "tick":
                                print("tick ->", engine._tick(pg, rad.first))
                            elif how == "label":
                                pg.locator(f"label[for='{rad.first.get_attribute('id')}']").click(timeout=3000)
                            elif how == "span":
                                rad.first.locator("xpath=following-sibling::span[1]").click(timeout=3000)
                            else:
                                rad.first.focus()
                                pg.keyboard.press("Space")
                        except Exception as e:  # noqa: BLE001
                            print(how, "ERR", str(e).splitlines()[0][:120])
                        print(how, "checked=", rad.first.is_checked(), "aria=", rad.first.get_attribute("aria-checked"))
                print("RADIOS", pg.evaluate("""() => [...document.querySelectorAll('input[type=radio],[role=radio],[role=radiogroup]')].slice(0, 4)
                    .map(e => { let p = e; for (let k = 0; k < 4 && p.parentElement; k++) p = p.parentElement;
                      return (e.getAttribute('data-automation-id') || '') + ' || ' + p.outerHTML.replace(/\\s+/g,' ').slice(0, 1800); })"""))
                res.update(status="needs_you", detail="debug dump")
                return res
            if "review" in step:
                pg.screenshot(path=str(shot), full_page=False)
                res["screenshot"] = str(shot)
                if dry_run:
                    res.update(status="dry_run_ok", detail="reached Review on Workday; not submitted")
                    return res
                if not _next(pg, final=True):  # on Review the button reads Submit
                    res.update(status="needs_you", detail="could not find the Submit button on Review")
                    return res
                pg.wait_for_timeout(6000)
                body = re.sub(r"\s+", " ", pg.inner_text("body")).lower()
                m = re.search(r"application submitted|thank you for applying|successfully submitted|we have received", body)
                if m:
                    pg.screenshot(path=str(shot), full_page=False)
                    res.update(status="applied", detail="Workday confirmed the submission", proof=f"PAGE: Workday '{m.group(0)}'",
                               proof_shot=engine.proof_shot(pg, job_id))
                else:
                    res.update(status="unconfirmed", detail=f"clicked Submit; no confirmation seen. {_banner(pg)}")
                return res
            if "experience" in step:
                fill_experience(pg, res["filled"])
            fill_step(pg, pdf, res["unknown"], res["filled"], job_id)
            if "experience" in step:  # truthfulness check on what ended up on the page
                txt = engine._norm(pg.locator("[data-automation-id=applyFlowPage]").evaluate(
                    "e => e.innerText + ' ' + [...e.querySelectorAll('input,textarea')].map(i => i.value).join(' ')"))
                first_employer = _first_employer_word()
                if first_employer and first_employer not in txt and not res["unknown"]:
                    res["unknown"].append(f"work history did not parse from the CV ({first_employer} missing)")
            if res["unknown"]:
                pg.screenshot(path=str(shot))
                res.update(status="needs_you", detail="unanswered required: " + "; ".join(dict.fromkeys(res["unknown"]))[:300], screenshot=str(shot))
                return res
            before = step
            if not _next(pg):
                pg.screenshot(path=str(shot))
                res.update(status="needs_you", detail=f"could not find the Next button on '{before[:40]}'", screenshot=str(shot))
                return res
            pg.wait_for_timeout(4500)
            if _active_step(pg) == before:  # rejected: re-scan once (a late-rendered field may have been missed), then try again
                fill_step(pg, pdf, res["unknown"], res["filled"], job_id)
                if not res["unknown"]:
                    _next(pg)
                    pg.wait_for_timeout(4500)
            if _active_step(pg) == before and re.search(r"\bState\b.{0,40}required|Country Phone Code|Phone Device Type", _banner(pg) or "", re.I) and _fix_state(pg):
                res["filled"].append(("Workday required fields", "state/phone filled"))  # never offered to the field scan
                _next(pg)
                pg.wait_for_timeout(4500)
            if _active_step(pg) == before and fix_invalid(pg, res["filled"], job_id):  # LLM repairs the rejected fields, then retry once
                _next(pg)
                pg.wait_for_timeout(4500)
            err = _banner(pg)
            if _active_step(pg) == before:
                pg.screenshot(path=str(shot))
                res.update(status="needs_you", detail=f"Workday would not continue past '{before[:40]}': {err or 'validation error'}", screenshot=str(shot))
                return res
        res.update(status="needs_you", detail="too many Workday steps")
    except Exception as e:  # noqa: BLE001
        res.update(status="failed", detail=f"{type(e).__name__}: {str(e)[:120]}")
        try:
            pg.screenshot(path=str(shot))
            res["screenshot"] = str(shot)
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            pg.close()
        except Exception:  # noqa: BLE001
            pass
    return res
