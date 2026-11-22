"""Data for the simple home page.
Four things only: today's numbers, what the bot is doing, results per portal, and what needs her. Read-only; every figure comes from the database."""
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from apply import learn
from core import db as dbm

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


class RO:
    """Read-only view with the same rows the applier's DB object has (apply/learn.py needs only that)."""

    def __init__(self):
        self.c = sqlite3.connect(f"file:{dbm.DB_PATH}?mode=ro", uri=True, timeout=5)
        self.c.row_factory = sqlite3.Row

    def rows(self, where: str = "1=1", params: tuple = ()):
        return [dict(r) for r in self.c.execute(f"SELECT * FROM jobs WHERE {where}", params)]

    def one(self, q: str, a: tuple = ()):
        return self.c.execute(q, a).fetchone()[0]


def kind_of(reason: str) -> str:
    """Which single action a parked job needs from her."""
    r = (reason or "").lower()
    if "captcha" in r or "email screen whose next" in r:
        return "captcha"
    if re.search(r"password|reset|sign-?in|locked", r):
        return "password"
    if "unanswered" in r or "?" in r:
        return "question"
    return "other"


ACTION = {"captcha": "Open the job and pass the CAPTCHA, then submit", "password": "Reset this site's password once, then tell the bot",
          "question": "Answer this question once (it is reused on later forms)", "other": "Open and check"}


def doing(d: RO) -> dict:
    """One sentence on what the applier is doing now, or why it is not applying."""
    try:
        rs = json.loads((DATA / "run_state.json").read_text(encoding="utf-8"))   # worker A (core/runstate.py)
    except Exception:  # noqa: BLE001
        rs = {}
    age = time.time() - float(rs.get("updated_at") or 0)
    now = rs.get("now") or {}
    if (DATA / "STOP").exists():
        return {"state": "stopped", "text": "Stopped (the stop switch is on)."}
    if rs and age < 600 and now.get("company") == "Preparing the queue":
        return {"state": "working", "text": "Preparing the next batch of jobs" + (f": {now.get('step')}" if now.get("step") else "") + "."}
    if rs and age < 600 and now.get("company"):
        step = f" - {now.get('step')}" if now.get("step") else ""
        title = f": {now.get('title')}" if now.get("title") else ""
        return {"state": "working", "text": f"Working on {now['company']}{title}{step}"}
    tail = ""
    try:
        lines = (DATA / "live_run_A.log").read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-40:]
        skips = [x for x in lines if x.startswith("not attempted:")]
        q = [x for x in lines if x.startswith("queue:")]
        if q and q[-1].startswith("queue: 0"):
            tail = "No job is ready to apply to right now; a search for new jobs starts by itself."
        elif skips:
            tail = "Last skip: " + skips[-1][len("not attempted:"):].strip()[:140]
    except Exception:  # noqa: BLE001
        pass
    if rs and age < 600:
        return {"state": "working", "text": "Preparing the next batch of jobs." + (" " + tail if tail else "")}
    return {"state": "idle", "text": tail or f"No activity for {int(age // 60) if rs else '?'} minutes; the guardian restarts the applier by itself."}


def simple_state() -> dict:
    d = RO()
    today = datetime.now().strftime("%Y-%m-%d")
    sent_rows = d.rows("status IN ('applied','unconfirmed') AND applied_at LIKE ?", (today + "%",))
    by_portal_today: dict = {}
    for r in sent_rows:
        by_portal_today[learn.portal(r)] = by_portal_today.get(learn.portal(r), 0) + 1
    week = []
    for i in range(6, -1, -1):
        day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        week.append({"day": day[5:], "sent": d.one("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ?", (day + "%",)),
                     "proven": d.one("SELECT COUNT(*) FROM jobs WHERE status='applied' AND applied_at LIKE ?", (day + "%",))})
    st = learn.stats(d, days=7)
    waiting: dict = {}
    for r in d.rows("status='candidate'"):
        waiting[learn.portal(r)] = waiting.get(learn.portal(r), 0) + 1
    names = set(st) | {p for p, n in waiting.items() if n >= 5}
    portals = sorted(({"portal": p, "sent": st.get(p, {}).get("win", 0), "failed": st.get(p, {}).get("loss", 0), "waiting": waiting.get(p, 0),
                       "today": by_portal_today.get(p, 0)} for p in names), key=lambda x: (-(x["sent"]), -x["waiting"]))
    portals = [p for p in portals if p["sent"] or p["waiting"] >= 5 or p["failed"] >= 3][:25]
    needs = []
    for r in d.rows("status IN ('needs_you','deferred_captcha')"):
        reason = r["needs_you_reason"] or r["status_detail"] or ""
        k = kind_of(reason)
        needs.append({"kind": k, "action": ACTION[k], "company": r["company"], "title": (r["title"] or "")[:70], "url": r["apply_url"] or r["url"],
                      "reason": re.sub(r"\s+", " ", reason)[:160], "score": r["score"] or 0})
    needs.sort(key=lambda x: ({"question": 0, "password": 1, "captcha": 2, "other": 3}[x["kind"]], -x["score"]))
    counts: dict = {}
    for n in needs:
        counts[n["kind"]] = counts.get(n["kind"], 0) + 1
    return {"now": time.time(),
            "today": {"sent": len(sent_rows), "proven": sum(1 for r in sent_rows if r["status"] == "applied"),
                      "waiting_receipt": sum(1 for r in sent_rows if r["status"] == "unconfirmed"),
                      "companies": len({(r["company"] or "").lower() for r in sent_rows}), "portals": len(by_portal_today),
                      "naukri": by_portal_today.get("naukri", 0)},
            "queue": d.one("SELECT COUNT(*) FROM jobs WHERE status='candidate' AND score>=35"),
            "doing": doing(d), "week": week, "portals": portals, "needs": needs[:150], "needs_counts": counts,
            "fix_next": learn.fix_list(d, top=6),
            "recent": [{"at": (r["applied_at"] or "")[11:16], "company": r["company"], "title": (r["title"] or "")[:60], "portal": learn.portal(r),
                        "proven": r["status"] == "applied"} for r in sorted(sent_rows, key=lambda r: r["applied_at"] or "", reverse=True)[:40]]}
