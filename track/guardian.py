"""Guardian: runs forever, every 3 minutes checks the whole machine and heals it. Writes data/guardian.log (STATUS/ALERT/HEAL lines) and data/health.json.
  - supervisor + workers alive (restarts the supervisor; honours data/STOP)
  - each worker's heartbeat fresh (kills a worker stuck > 12 min; the supervisor relaunches it)
  - dashboard server up (restarts it)
  - receipts: runs `hunt.py proof` every 15 min while jobs await proof
  - queue: when fewer than 15 fresh candidates are left, discovers more on a COPY of the browser profile (never fights the workers)
  - Naukri / LinkedIn session still signed in (alerts if not)
Usage: python track/guardian.py (start detached; stop by creating data/GUARDIAN_STOP)"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import envfile as _envfile  # noqa: E402
_envfile.load()
DATA = ROOT / "data"
LOG = DATA / "guardian.log"
PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
NOWIN = 0x08000000  # CREATE_NO_WINDOW
TARGET, WORKERS = 50, 1  # one applier


def log(kind: str, msg: str):
    line = f"{datetime.now().strftime('%m-%d %H:%M:%S')} {kind}|{msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def procs(match: str, name: str | None = None):
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
            if match in cl and (not name or (p.info["name"] or "").lower() == name) and p.pid != os.getpid():
                out.append(p)
        except Exception:  # noqa: BLE001
            continue
    return out


def start_supervisor():
    (DATA / "supervisor.lock").unlink(missing_ok=True)
    subprocess.Popen(PS + [str(ROOT / "supervise.ps1"), "-Target", str(TARGET), "-Workers", str(WORKERS)], cwd=str(ROOT), creationflags=NOWIN)
    log("HEAL", f"supervisor started (target {TARGET}, {WORKERS} workers)")


def dashboard_up() -> bool:
    import urllib.request
    try:
        return urllib.request.urlopen("http://127.0.0.1:8799/api/state", timeout=8).status == 200
    except Exception:  # noqa: BLE001
        return False


def heartbeat_ages(since: float | None = None) -> dict:
    """Age of each running worker's heartbeat. `since`: the start time of the OLDEST live worker process. A state file last
    touched before that belongs to a worker that no longer exists (it was killed and never wrote `finished`), so it is not a
    stalled worker -: a dead worker B's file read 'silent 580 min' and the guardian killed the HEALTHY worker A every
    15 minutes."""
    ages = {}
    for f in DATA.glob("run_state*.json"):
        try:
            s = json.loads(f.read_text(encoding="utf-8-sig"))
            if s.get("status") in ("running", "preparing") and not s.get("finished"):
                if since is not None and s.get("updated_at", 0) < since:
                    continue
                ages[s.get("label", f.stem)] = time.time() - s.get("updated_at", 0)
        except Exception:  # noqa: BLE001
            continue
    return ages


QUEUE_LOW = 40


def fit_queue(d) -> int:
    """Candidates that clear the worker's REAL bar (runner.min_score), not the loose 45/50 the dashboard used: 185 'queued' jobs were really 10, so the
    'queue low -> discover' rule never fired and the worker idled ."""
    try:
        from apply import runner
        now = datetime.now().isoformat(timespec="seconds")
        rows = d.rows("status='candidate' AND (next_attempt_at IS NULL OR next_attempt_at<=?)", (now,))
        return sum(1 for r in rows if (r["score"] or 0) >= runner.min_score(r))
    except Exception:  # noqa: BLE001
        return 0


def counts() -> dict:
    from core import db
    d = db.DB()
    today = datetime.now().strftime("%Y-%m-%d")
    one = lambda q, a=(): d.c.execute(q, a).fetchone()[0]  # noqa: E731
    return {"proven_today": one("SELECT COUNT(*) FROM jobs WHERE status='applied' AND email_proof IS NOT NULL AND applied_at LIKE ?", (today + "%",)),
            "awaiting": one("SELECT COUNT(*) FROM jobs WHERE status='unconfirmed'"),
            "queue": fit_queue(d),
            "needs": one("SELECT COUNT(*) FROM jobs WHERE status='needs_you'")}


def discover():
    prof = DATA / "browser_profile_disc"
    subprocess.run(["robocopy", str(DATA / "browser_profile"), str(prof), "/MIR", "/XD", "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
                    "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:0", "/W:0"], capture_output=True)
    env = {**os.environ, "JOBHUNT_PROFILE": str(prof), "JOBHUNT_LABEL": "D", "JOBHUNT_NO_OPEN": "1"}
    subprocess.Popen([sys.executable, "-u", "hunt.py", "naukri"], cwd=str(ROOT), env=env, creationflags=NOWIN,
                     stdout=open(DATA / "guardian_discover.log", "a"), stderr=subprocess.STDOUT)
    log("HEAL", "queue low: discovering fresh Naukri jobs on a profile copy")


def integrations() -> dict:
    """Health of every outside dependency. A failure here is an ALERT: these used to fail silently (Gmail session expiry stopped all receipts and OTPs)."""
    out = {}
    try:
        from core import imap
        if imap.available():
            ok, why = imap.check()
            out["gmail"] = {"ok": ok, "detail": why}
        else:
            out["gmail"] = {"ok": False, "detail": "no app password stored: run python hunt.py gmail-setup"}
    except Exception as e:  # noqa: BLE001
        out["gmail"] = {"ok": False, "detail": str(e)[:80]}
    try:
        from core import llm
        out["claude"] = {"ok": not llm.claude_is_down(), "detail": "usage-limit cool-down" if llm.claude_is_down() else "ok"}
        need = (DATA / "llm_login_needed.json").exists()
        out["backup_ai"] = {"ok": bool(llm.providers()) and not need, "detail": "SIGNED OUT: press Sign in on the dashboard" if need else (", ".join(p["name"] for p in llm.providers()) or "NO backup AI: run  python hunt.py llm-key gemini  (free key, 1 minute)")}
    except Exception as e:  # noqa: BLE001
        out["claude"] = {"ok": False, "detail": str(e)[:80]}
    try:
        s = json.loads((DATA / "session_status.json").read_text(encoding="utf-8")).get("sites", {})
        for k in ("naukri", "linkedin"):
            if k in s:
                out[k] = {"ok": bool(s[k].get("ok")), "detail": s[k].get("detail", "")[:60]}
    except Exception:  # noqa: BLE001
        pass
    return out


def _daily_state() -> dict:
    """When the once-a-day tasks last ran, kept on disk.: they were only in memory, so every guardian restart re-ran the daily startup
    emails and the full search (two outreach runs on one day)."""
    try:
        return json.loads((DATA / "guardian_state.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_daily(**kw) -> None:
    try:
        (DATA / "guardian_state.json").write_text(json.dumps({**_daily_state(), **kw}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def main():
    last_proof = last_disc = last_int = last_sess = last_feeds = last_hist = last_replies = last_bounce = last_refill = 0.0
    last_outreach, last_full = float(_daily_state().get("last_outreach", 0)), float(_daily_state().get("last_full", 0))
    prev_bad: set = set()
    log("INFO", "guardian started")
    while not (DATA / "GUARDIAN_STOP").exists():
        try:
            stop = (DATA / "STOP").exists()
            sup = procs("supervise.ps1", "powershell.exe")
            wk = procs("hunt.py run", "python.exe")
            c = counts()
            ages = heartbeat_ages(since=min((p.info.get("create_time") or 0) for p in wk) - 5 if wk else float("inf"))
            # 1. supervisor
            if not stop and not sup:
                log("ALERT", "supervisor not running")
                start_supervisor()
            # 2. stalled workers
            for p in wk:
                age = min(ages.values()) if ages else 0
            for label, age in ages.items():
                if age > 720:
                    log("ALERT", f"worker {label} silent for {int(age / 60)} min: killing it (supervisor relaunches)")
                    for p in wk:
                        try:
                            p.kill()
                        except Exception:  # noqa: BLE001
                            pass
                    break
            # 3. dashboard
            if not dashboard_up():
                log("ALERT", "dashboard down")
                subprocess.Popen([sys.executable, str(ROOT / "track" / "live.py")], cwd=str(ROOT), creationflags=NOWIN)
                log("HEAL", "dashboard restarted")
            # 4. receipts
            if c["awaiting"] and time.time() - last_proof > 900:
                subprocess.Popen([sys.executable, "hunt.py", "proof"], cwd=str(ROOT), creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                last_proof = time.time()
                log("HEAL", f"checking Gmail receipts ({c['awaiting']} awaiting)")
            if (DATA / "outreach.db").exists() and time.time() - last_bounce > 1800:   # startup emails: a Gmail bounce demotes 'sent' to 'bounced'
                subprocess.Popen([sys.executable, "-c", "from apply import outreach; outreach.check_bounces()"], cwd=str(ROOT), creationflags=NOWIN,
                                 env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                last_bounce = time.time()
                log("HEAL", "checking Gmail for bounces of the startup emails")
            if time.time() - last_replies > 1800:   # employer replies (rejections / interviews / assessments): the last 4 days only, the first full read is a manual `hunt.py replies`
                subprocess.Popen([sys.executable, "hunt.py", "replies", "--days", "4"], cwd=str(ROOT), creationflags=NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                last_replies = time.time()
                log("HEAL", "reading employer replies (rejections / interviews / assessments)")
            # daily startup outreach: scan the next startups, draft + send honest emails with her CV (cap 15/day), once every 24 h
            if (DATA / "outreach.db").exists() and time.time() - last_outreach > 24 * 3600:
                subprocess.Popen([sys.executable, "-c", "from apply import outreach; outreach.daily()"], cwd=str(ROOT), creationflags=NOWIN,
                                 env={**os.environ, "PYTHONPATH": str(ROOT)}, stdout=open(DATA / "outreach_daily.log", "a"), stderr=subprocess.STDOUT)
                last_outreach = time.time()
                _save_daily(last_outreach=last_outreach)
                log("HEAL", "daily startup outreach: scanning, drafting and sending (cap 15)")
            # daily full discovery incl. LinkedIn (guest pages) and the job boards, token-free: feeds the under-10-applicants LinkedIn pass
            if time.time() - last_full > 24 * 3600 and not procs("hunt.py discover", "python.exe"):
                subprocess.Popen([sys.executable, "-u", "hunt.py", "discover"], cwd=str(ROOT), env={**os.environ, "JOBHUNT_NO_OPEN": "1", "JOBHUNT_LABEL": "D"}, creationflags=NOWIN,
                                 stdout=open(DATA / "guardian_discover.log", "a"), stderr=subprocess.STDOUT)
                last_full = time.time()
                _save_daily(last_full=last_full)
                log("HEAL", "daily full discovery (LinkedIn + boards)")
            # 5. queue
            if c["queue"] < QUEUE_LOW and time.time() - last_disc > 3600 and not procs("hunt.py naukri", "python.exe"):
                discover()
                last_disc = time.time()
            # a low queue used to refill from Naukri only, while the full search (career sites,
            # LinkedIn, every board) ran on a 24 h clock - the worker sat idle ~70% of the time. A low queue now starts the FULL search, at most hourly.
            if c["queue"] < QUEUE_LOW and time.time() - last_refill > 3600 and not procs("hunt.py discover", "python.exe"):
                subprocess.Popen([sys.executable, "-u", "hunt.py", "discover"], cwd=str(ROOT), env={**os.environ, "JOBHUNT_NO_OPEN": "1", "JOBHUNT_LABEL": "D"}, creationflags=NOWIN,
                                 stdout=open(DATA / "guardian_discover.log", "a"), stderr=subprocess.STDOUT)
                last_refill = last_full = last_feeds = time.time()
                log("HEAL", f"queue low ({c['queue']}): full discovery on every source")
            # company career feeds (Workday, Greenhouse, Lever, Oracle...: no browser, no LLM) every 4 hours whatever the queue says: fresh listings keep the bot from ever running dry
            if time.time() - last_feeds > 4 * 3600 and not procs("hunt.py discover", "python.exe"):
                subprocess.Popen([sys.executable, "-u", "hunt.py", "discover", "--no-boards"], cwd=str(ROOT), env={**os.environ, "JOBHUNT_NO_OPEN": "1"}, creationflags=NOWIN,
                                 stdout=open(DATA / "guardian_discover.log", "a"), stderr=subprocess.STDOUT)
                log("HEAL", "refreshing company career feeds (scheduled, every 4h)")
                last_feeds = time.time()
            # signed out and the user's sign-in window is closed: re-check every cycle so recovery happens within ~3 min of closing the window
            need_login = (DATA / "llm_login_needed.json").exists()
            edge_open = any("browser_profile_llm_edge" in " ".join(p.info["cmdline"] or []) and "--type=" not in " ".join(p.info["cmdline"] or [])
                            for p in psutil.process_iter(["cmdline"]) if p.info["cmdline"])
            if need_login and not edge_open and time.time() - last_sess > 150:
                last_sess = 0
            if time.time() - last_sess > (1800 if not need_login else 150):  # is the backup AI still signed in? flag it before Claude is ever needed
                last_sess = time.time()
                try:
                    from core import llm, llm_browser
                    for prov in llm.providers():
                        if prov.get("type") == "browser" and not llm_browser.check_session(prov):
                            log("ALERT", f"{prov['name']} is signed out: press Sign in on the dashboard")
                except Exception as e:  # noqa: BLE001
                    log("INFO", f"session check skipped: {type(e).__name__}")
            try:  # daily self-maintenance (no AI): expire unproven submissions, retry failures once, trim logs/snapshots
                from core import db as _dbm
                from core import maintenance
                res = maintenance.run_daily(_dbm.DB())
                if res:
                    log("HEAL", f"daily maintenance: {res}")
            except Exception as e:  # noqa: BLE001
                log("INFO", f"maintenance skipped: {type(e).__name__}")
            if time.time() - last_hist > 3600:  # persistent history, hourly: applications + token calls are never lost even if the DB is
                last_hist = time.time()
                try:
                    from core import db as _dbh
                    from core import history
                    history.keep(_dbh.DB())
                except Exception as e:  # noqa: BLE001
                    log("INFO", f"history not written: {type(e).__name__}")
            if time.time() - last_int > 900:
                last_int = time.time()
                integ = integrations()
                (DATA / "integrations.json").write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **integ}), encoding="utf-8")
                bad = {k for k, v in integ.items() if not v["ok"] and k not in ("backup_ai", "claude")}
                for k in bad - prev_bad:  # alert once per new failure
                    log("ALERT", f"integration down: {k}: {integ[k]['detail']}")
                prev_bad = bad
            health = {"at": datetime.now().isoformat(timespec="seconds"), "supervisor": bool(sup), "workers": len(wk), "heartbeat_age_s": {k: int(v) for k, v in ages.items()},
                      "dashboard": True, **c}
            (DATA / "health.json").write_text(json.dumps(health), encoding="utf-8")
            log("STATUS", f"workers={len(wk)} supervisor={'up' if sup else 'DOWN'} proven_today={c['proven_today']} awaiting={c['awaiting']} queue={c['queue']} needs_you={c['needs']} "
                          f"heartbeat={ {k: int(v) for k, v in ages.items()} }")
        except Exception as e:  # noqa: BLE001
            log("ALERT", f"guardian error {type(e).__name__}: {str(e)[:120]}")
        time.sleep(180)
    log("INFO", "guardian stopped")


if __name__ == "__main__":
    main()
