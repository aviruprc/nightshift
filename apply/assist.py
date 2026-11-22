"""CAPTCHA hand-off: a VISIBLE Chrome window where each CAPTCHA job is opened with the tailored CV uploaded and every field filled.
the user solves the CAPTCHA and presses Submit; the helper sees the confirmation and marks the job applied.
Jobs are queued in data/assist_queue.txt (one job id per line); one helper process serves all of them."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "data" / "assist_queue.txt"
PIDF = ROOT / "data" / "assist.pid"
PROFILE = ROOT / "data" / "browser_profile_assist"
MAIN = ROOT / "data" / "browser_profile"


def record_captcha_submit(d, jid: str, body: str, shot: str) -> str:
    """The human solved the CAPTCHA and pressed Submit: that is a SEND, not proof. Proof standard v2 applies to a
    human-submitted form exactly as it does to the bot's: unconfirmed with the page proof, promoted only by a real employer receipt.
    (The a company row was marked 'applied' here with no proof, and a later receipt pass attached a company-only AmbitionBox notice.)"""
    proof = f"PAGE: submitted by you after the CAPTCHA (form filled by the bot): {body[:80]}"
    d.c.execute("UPDATE jobs SET page_proof=?, proof_shot=COALESCE(proof_shot, ?) WHERE id=?", (proof, shot, jid))
    from core import db as dbm
    d.set_status(jid, "unconfirmed", "submitted by you after the CAPTCHA (form filled by the bot); waiting for the receipt email",
                 applied_at=dbm.now(), screenshot_path=str(shot), needs_you_reason=None)
    return proof


def park_open_captcha_tabs(d, tabs: dict) -> int:
    """A CAPTCHA tab dies with this helper process. Before it closes, replace "the tab is open, press Submit" with a DURABLE instruction
    that names the job's URL, so the dashboard never again tells her to press Submit on a tab that no longer exists
    (: 17 parked rows all said "CAPTCHA tab is open and pre-filled")."""
    n = 0
    for jid in list(tabs):
        try:
            rows = d.rows("id=?", (jid,))
            if not rows:
                continue
            url = rows[0]["apply_url"] or rows[0]["url"] or ""
            msg = "CAPTCHA: open the job and submit it yourself" + (f" ({url})" if url else "")
            cur = d.c.execute("UPDATE jobs SET status_detail=?, needs_you_reason=? WHERE id=? AND status='needs_you'",
                              (msg[:300], msg[:300], jid))
            n += cur.rowcount or 0
        except Exception:  # noqa: BLE001
            continue
    return n


MAX_TABS = int(os.environ.get("JOBHUNT_ASSIST_TABS", "3"))


def pending_slots(ids: list, done_ids: set, open_ids: set, cap: int = MAX_TABS) -> list:
    """The queue ids that may be opened RIGHT NOW: never more than `cap` tabs at once.

    the helper had 20 ids queued and opened a visible tab for every one of them within a minute (8 rows parked
    "CAPTCHA tab is open" in 47 s). A hand-off only works at a pace a human can actually solve, and every idle tab is memory and a
    page that expires before she reaches it. The rest stay in data/assist_queue.txt and open as tabs close."""
    room = max(0, int(cap) - len(open_ids))
    return [j for j in ids if j not in done_ids and j not in open_ids][:room]


def enqueue(job_id: str):
    """Called by runs/dashboard: add a job and make sure the helper window is running."""
    QUEUE.parent.mkdir(exist_ok=True)
    with open(QUEUE, "a", encoding="utf-8") as f:
        f.write(job_id + "\n")
    if not _alive():
        subprocess.Popen([sys.executable, str(ROOT / "hunt.py"), "assist"], cwd=str(ROOT), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=0x00000008 | 0x00000200)


def _alive() -> bool:
    try:
        pid = int(PIDF.read_text())
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    except Exception:  # noqa: BLE001
        return False


def _refresh_profile():
    """Copy the signed-in main profile for the helper (skips files Chrome has locked)."""
    subprocess.run(["robocopy", str(MAIN), str(PROFILE), "/MIR", "/XD", "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
                    "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:0", "/W:0"], capture_output=True)


def _toast(title, body):
    try:
        from win11toast import notify
        notify(title, body)
    except Exception:  # noqa: BLE001
        pass


def not_hers(r) -> str:
    """Reason a parked CAPTCHA job must not be opened for her (location she did not ask for), else ''."""
    from rank import filter as flt
    loc = str((r["location"] if "location" in r.keys() else "") or "")
    if flt.location_conflict(loc):
        return f"location: {loc[:60]} - outside your locations (facts.yaml preferences.home_location_regex); not opened for a CAPTCHA hand-off"
    return ""


def serve(max_minutes: int = 60):
    sys.path.insert(0, str(ROOT))
    os.environ["JOBHUNT_PROFILE"] = str(PROFILE)
    from playwright.sync_api import sync_playwright

    from apply import browser, engine
    from core import activity
    from core import db as dbm
    PIDF.write_text(str(os.getpid()))
    _refresh_profile()
    d = dbm.DB()
    done_ids: set = set()
    tabs: dict = {}  # job id -> page
    deadline = time.time() + max_minutes * 60
    with sync_playwright() as pw:
        ctx = browser.launch(pw, headless=False)
        try:
            while time.time() < deadline:
                ids = [x.strip() for x in QUEUE.read_text(encoding="utf-8").splitlines() if x.strip()] if QUEUE.exists() else []
                for jid in pending_slots(ids, done_ids, set(tabs)):
                    if jid in done_ids or jid in tabs:
                        continue
                    rows = d.rows("id=?", (jid,))
                    if not rows:
                        done_ids.add(jid)
                        continue
                    r = rows[0]
                    why_not = not_hers(r)
                    if why_not:   # a company (Pune) was opened for her on although she asked for Bengaluru only (KB-63)
                        done_ids.add(jid)
                        d.set_status(jid, "skipped", why_not, needs_you_reason=None)
                        continue
                    pdf = Path(r["resume_path"]) if r["resume_path"] and Path(r["resume_path"]).exists() else None
                    if pdf is None:
                        from cv import tailor
                        fam = r["family"] if r["family"] in ("data_bi", "risk_reg", "finance", "etl_sas") else "data_bi"
                        pdf, _ = tailor.build(r["id"], r["description"] or "", fam, r["title"])
                    pg = ctx.new_page()
                    activity.set_job(r["company"], r["title"], activity.portal_of(r["apply_url"] or r["url"]))
                    activity.log("portal", "CAPTCHA helper: opening the form in a visible window and filling it for you")
                    try:
                        pg.goto(r["apply_url"] or r["url"], wait_until="domcontentloaded", timeout=60000)
                        pg.wait_for_timeout(3000)
                        pg = engine.follow_embedded_form(engine.click_apply_if_needed(pg))
                        engine.CURRENT_JOB.update(id=r["id"], company=r["company"], title=r["title"], jd=(r["description"] or "")[:3000])
                        unknown, filled = [], []
                        engine.fill_page(pg, pdf, unknown, filled)
                        pg.bring_to_front()
                    except Exception as e:  # noqa: BLE001
                        activity.log("error", f"CAPTCHA helper could not prepare the form: {type(e).__name__}")
                    tabs[jid] = pg
                    d.set_status(jid, "needs_you", "CAPTCHA tab is open and pre-filled: solve it and press Submit",
                                 needs_you_reason="CAPTCHA tab is open and pre-filled: solve it, then press Submit")
                    _toast("Job Hunt: CAPTCHA needs you", f"{r['company']}: form is filled, solve the CAPTCHA and press Submit")
                # watch every open tab for a confirmation or a close
                for jid, pg in list(tabs.items()):
                    if pg.is_closed():
                        tabs.pop(jid)
                        done_ids.add(jid)
                        continue
                    try:
                        body = pg.inner_text("body")[:6000]
                    except Exception:  # noqa: BLE001
                        continue
                    if engine.SUCCESS.search(body):
                        shot = engine.SHOTS / time.strftime("%Y-%m-%d") / f"{jid}.png"
                        shot.parent.mkdir(parents=True, exist_ok=True)
                        pg.screenshot(path=str(shot))
                        record_captcha_submit(d, jid, body, str(shot))
                        activity.log("result", "sent: you solved the CAPTCHA and submitted (awaiting the receipt)")
                        _toast("Job Hunt: sent", "Application submitted. You can close that tab.")
                        tabs.pop(jid)
                        done_ids.add(jid)
                if not tabs and all(x in done_ids for x in ids) and ids:
                    QUEUE.write_text("", encoding="utf-8")  # everything handled: keep the window idle for new arrivals
                time.sleep(3)
        finally:
            try:
                park_open_captcha_tabs(d, tabs)
            except Exception:  # noqa: BLE001
                pass
            PIDF.unlink(missing_ok=True)
            ctx.close()


def pending_captchas(d) -> list[str]:
    return [r["id"] for r in d.rows("status='needs_you'") if re.search(r"captcha", (r["needs_you_reason"] or "") + (r["status_detail"] or ""), re.I)]
