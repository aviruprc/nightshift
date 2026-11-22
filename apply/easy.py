"""LinkedIn Easy Apply: LAST RESORT only. Dry run opens the form but never submits."""
import re
from datetime import datetime

from apply import engine

SUBMIT = re.compile(r"^\s*submit application\s*$", re.I)
NEXT = re.compile(r"^\s*(next|review|continue)\s*$", re.I)
SENT = re.compile(r"your application was sent|application (was )?sent to", re.I)


def page_text(pg) -> str:
    """Everything the page says, dialog first. LinkedIn renders the confirmation in a modal at the END of the DOM, after a
    job description that is routinely longer than any small slice we might read (KB-76)."""
    try:
        dlg = pg.locator("div[role=dialog], div[role=alertdialog]")
        return (dlg.first.inner_text() + "\n" if dlg.count() else "") + pg.inner_text("body")
    except Exception:  # noqa: BLE001
        return pg.inner_text("body")


def confirmation(pg):
    """"Your application was sent to <company>" on THIS page, or None. Never a success toast alone, never a guess."""
    return SENT.search(page_text(pg)[:40000])


def scan(pg):
    """(the visible 'Easy Apply' control or None, whether the page shows an 'Applied' label).

    KB-77: five jobs known to have Easy Apply (a company 78, HRM Counsel x2, iConsultera, a company) came back
    "Easy Apply button not found" / "could not finish the Easy Apply steps" within two minutes of two successful sends,
    with nothing saved to check. One look at a page is not a fact - a slow page, a challenge page and an already-applied
    job all look the same from here - so the caller reloads once and saves what the page actually shows.
    """
    applied, btn = False, None
    try:
        els = pg.locator("button:visible, a:visible").all()[:150]
    except Exception:  # noqa: BLE001
        return None, False
    for el in els:
        try:
            t = (el.inner_text() or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if btn is None and re.match(r"^easy apply$", t, re.I):
            btn = el
        elif re.match(r"^applied\b", t, re.I) and len(t) <= 30:
            applied = True
    return btn, applied


def evidence(pg, job_id: str, tag: str) -> tuple[str, str]:
    """Screenshot + a one-line 'what the page said' for a failure path (data/failures-style proof, KB-54/KB-77)."""
    shot = engine.SHOTS / datetime.now().strftime("%Y-%m-%d") / f"{job_id}_{tag}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    try:
        pg.screenshot(path=str(shot), full_page=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        txt = re.sub(r"\s+", " ", pg.inner_text("body")).strip()[:220]
    except Exception:  # noqa: BLE001
        txt = ""
    return str(shot), txt


def _discard(pg):
    try:
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(800)
        b = pg.get_by_role("button", name=re.compile(r"^discard$", re.I))
        if b.count():
            b.first.click()
    except Exception:  # noqa: BLE001
        pass


def apply_easy(ctx, job_url: str, resume, dry_run: bool, job_id: str) -> dict:
    res = {"status": "failed", "detail": "", "unknown": [], "filled": [], "screenshot": None}
    shot = engine.SHOTS / datetime.now().strftime("%Y-%m-%d") / f"{job_id}.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    engine.CURRENT_JOB["id"] = job_id
    pg = ctx.new_page()
    try:
        pg.goto(job_url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3500)
        btn, applied = scan(pg)
        if btn is None:  # a slow page is the likeliest cause: reload ONCE before believing the control is gone (KB-77)
            pg.reload(wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(4000)
            btn, applied = scan(pg)
        if btn is None:
            shot_p, txt = evidence(pg, job_id, "easy_missing")
            res["screenshot"] = shot_p
            if applied:
                res.update(status="already_applied", detail="LinkedIn shows this job as 'Applied' (no Easy Apply button)")
                return res
            # KB-58: a missing Apply control is a page state, never a closure; the wording keeps it `transient` in errors.py
            res.update(status="failed",
                       detail=("no apply button on the LinkedIn job page after a reload" + (f"; page says: '{txt}'" if txt else ""))[:300])
            return res
        btn.click()
        pg.wait_for_timeout(2500)
        for step in range(10):
            unknown: list = []
            engine.fill_page(pg, resume, unknown, res["filled"])
            if unknown:
                shot_p, _ = evidence(pg, job_id, "easy_unanswered")
                res["screenshot"] = shot_p
                _discard(pg)
                res.update(status="needs_you", detail="unanswered required: " + "; ".join(dict.fromkeys(unknown))[:300])
                return res
            for cb in pg.locator("input[type=checkbox]:checked").all():  # never follow companies / opt into updates
                try:
                    lab = cb.evaluate("e => (e.closest('label') || e.parentElement).innerText")
                    if re.search(r"follow", lab or "", re.I):
                        cb.uncheck(force=True)
                except Exception:  # noqa: BLE001
                    pass
            sub = engine.find_button(pg, SUBMIT)
            if sub:
                if dry_run:
                    pg.screenshot(path=str(shot), full_page=True)
                    _discard(pg)
                    res.update(status="dry_run_ok", detail="Easy Apply form filled; not submitted", screenshot=str(shot))
                    return res
                sub.click()
                pg.wait_for_timeout(4500)
                pg.screenshot(path=str(shot), full_page=True)
                res["screenshot"] = str(shot)
                # KB-76: the confirmation sits in a modal at the END of the DOM; reading only the first 4000 characters of the
                # body reported "no confirmation seen" for applications LinkedIn had confirmed on screen.
                ok = confirmation(pg)
                res.update(status="applied" if ok else "unconfirmed", detail="Easy Apply submitted" if ok else "submitted; no confirmation seen")
                if ok:
                    res["proof"] = f"PAGE: LinkedIn '{ok.group(0)}'"
                    res["proof_shot"] = engine.proof_shot(pg, job_id)
                return res
            nxt = engine.find_button(pg, NEXT)
            if not nxt:
                break
            nxt.click()
            pg.wait_for_timeout(1800)
        shot_p, txt = evidence(pg, job_id, "easy_steps")
        res["screenshot"] = shot_p
        _discard(pg)
        res.update(status="failed",
                   detail=("did not show the next step in the Easy Apply form" + (f"; page says: '{txt}'" if txt else ""))[:300])
    except Exception as e:  # noqa: BLE001
        res.update(status="failed", detail=f"{type(e).__name__}: {str(e)[:100]}")
    finally:
        try:
            pg.close()
        except Exception:  # noqa: BLE001
            pass
    return res
