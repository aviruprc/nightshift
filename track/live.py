"""Local live dashboard + action API: python hunt.py dashboard -> http://127.0.0.1:8799
GET /api/state (polled every 2s), GET /shot/<job id>, POST /api/action (localhost only, header-checked)."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import envfile as _envfile  # noqa: E402
_envfile.load()
import yaml  # noqa: E402

from apply import errors  # noqa: E402 (ONE failure taxonomy: canonical keys + labels)
from core import db as dbm  # noqa: E402
from core import runstate  # noqa: E402
from rank import ghost  # noqa: E402

PAGE = Path(__file__).resolve().parent / "dashboard.html"
OPS = Path(__file__).resolve().parent / "ops.html"
SIMPLE = Path(__file__).resolve().parent / "simple.html"
PORT = 8799  # 8765 is used by another local program (jobs_collect/srv.py)
DATA = ROOT / "data"
STOP = DATA / "STOP"
CAPS = {"naukri": 40, "indeed": 25, "linkedin_easy": 15}
OBSOLETE = ("CV build error%", "Easy Apply only%", "ghost/stale%", "low fit score%")  # noise or early-bug events: hidden, not deleted


def q(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args)]


def run_alive(run: dict) -> bool:
    return run.get("status") in ("running", "preparing", "discovering") and (time.time() - run.get("updated_at", 0)) < 240


# ---- honest, testable numbers -----------------------------------------------------------------
# Every metric the page shows is computed by one of these functions and carries its definition
# (numbers["defs"]) so the label and the query can never drift apart.
ATTEMPT_KINDS = ("applied", "unconfirmed", "needs_you", "failed", "dry_run_ok")

DEFS = {
    "proven_today": "jobs with status='applied' AND a proof recorded (email_proof set), applied_at today (local date)",
    "sent_today": "jobs with status 'applied' or 'unconfirmed' whose applied_at is today; includes the proven ones",
    "unproven_today": "sent_today minus proven_today: submitted today, no receipt/badge/page proof yet",
    "attempts_today": "distinct jobs with an applied/unconfirmed/needs_you/failed/dry_run_ok event today, plus every job sent today",
    "failed_today": "attempts_today that did not submit today and were not dry-run-only; a job later corrected from unconfirmed to failed counts here",
    "proven_total": "status='applied' AND a proof recorded, all time",
    "sent_total": "status 'applied', 'unconfirmed' or 'unverified', all time (unverified = older send whose proof never arrived)",
    "proven_week": "status='applied' AND a proof recorded, applied_at in the last 7 local days",
    "awaiting": "status='unconfirmed', or status='applied' without a proof recorded",
    "waiting": "status='needs_you' (parked for a human)",
    "queue": "status='candidate', fit score >= 45, posted or first seen within 7 days",
    "retrying": "status='candidate' with a next_attempt_at in the future",
    "success_rate_today": "sent_today / attempts_today, as a percentage: a submission, NOT a proof",
    "proven_rate_today": "proven_today / attempts_today, as a percentage",
    "tokens_today": "input+output tokens recorded in data/llm_usage.jsonl today (local date); recorded usage, not an estimate",
    "tokens_week": "input+output tokens recorded in data/llm_usage.jsonl over the last 7 local days; recorded usage, not an estimate",
    "tokens_day": "input+output tokens recorded in data/llm_usage.jsonl on that local day (calls in brackets); '–' means nothing recorded",
    "fail_classes": "jobs whose last run ended failed, needs_you or blocked_portal, grouped by the applier's own fail_class",
}
COUNT_KEYS = ("proven", "sent", "unproven", "attempts", "failed")


def proof_kind(email_proof) -> str:
    """page / account / email / none, from the proof text stored in jobs.email_proof."""
    p = str(email_proof or "")
    if p.startswith("PAGE"):
        return "page"
    if p.startswith(("ACCOUNT", "NAUKRI")):
        return "account"
    return "email" if p else "none"


def day_counts(c, day: str) -> dict:
    """Today's honest split. `day` is a local YYYY-MM-DD. Returns counts plus the id sets behind them.

    proven: status applied + proof + applied_at that day
    sent: status applied/unconfirmed + applied_at that day (proven is a subset)
    attempts: distinct jobs the applier worked that day (submit-ish events) + everything sent that day
    failed: attempts that did not submit that day (dry-run-only attempts are not failures)
    """
    like = day + "%"
    one = lambda sql, a=(): c.execute(sql, a).fetchone()[0]  # noqa: E731
    proven = one("SELECT COUNT(*) FROM jobs WHERE status='applied' AND email_proof IS NOT NULL AND applied_at LIKE ?", (like,))
    sent = one("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ?", (like,))
    sent_ids = {r[0] for r in c.execute("SELECT id FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ?", (like,))}
    marks = ",".join("?" for _ in ATTEMPT_KINDS)
    kinds: dict = {}
    for jid, kind in c.execute(f"SELECT DISTINCT job_id, kind FROM events WHERE at LIKE ? AND kind IN ({marks})", (like, *ATTEMPT_KINDS)):
        kinds.setdefault(jid, set()).add(kind)
    att_ids = set(kinds) | sent_ids
    dry_only = {jid for jid, ks in kinds.items() if ks <= {"dry_run_ok"} and jid not in sent_ids}
    failed_ids = att_ids - sent_ids - dry_only
    return {"proven": proven, "sent": sent, "unproven": max(0, sent - proven),
            "attempts": len(att_ids), "failed": len(failed_ids),
            "_sent_ids": sent_ids, "_att_ids": att_ids, "_failed_ids": failed_ids}


def usage_breakdown(path, today_ts: float, month_ts: float) -> tuple[dict, dict]:
    """(by_model, by_purpose) token/call totals from data/llm_usage.jsonl, for today and this calendar month.

    One row per LLM call: {"t": unix, "m": provider, "i": input, "o": output, "p": purpose}. Input+output.
    Missing/garbled rows are skipped; no day or model is invented.
    """
    by_model: dict = {}
    by_purpose: dict = {}

    def bump(bucket: dict, key, tok: int, t: float):
        b = bucket.setdefault(str(key or "unknown"), {"today": {"tokens": 0, "calls": 0},
                                                      "month": {"tokens": 0, "calls": 0}})
        if t >= month_ts:
            b["month"]["tokens"] += tok
            b["month"]["calls"] += 1
        if t >= today_ts:
            b["today"]["tokens"] += tok
            b["today"]["calls"] += 1

    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()[-20000:]
    except OSError:
        return by_model, by_purpose
    for ln in lines:
        if not ln.startswith("{"):
            continue
        try:
            u = json.loads(ln)
        except ValueError:
            continue
        t = u.get("t")
        if not isinstance(t, (int, float)):
            continue
        tok = (u.get("i") or 0) + (u.get("o") or 0)
        bump(by_model, u.get("m"), tok, t)
        bump(by_purpose, u.get("p"), tok, t)
    return by_model, by_purpose


def ai_chip(ai: dict) -> dict:
    """Compact LIVE AI status for the main dashboard, computed from core.llm.status only.

    Returns the ready provider names and every provider that cannot answer right now, with its retry
    minutes parsed from status's own reason (e.g. "quota reached ... (retry in 16 min)").
    """
    if not isinstance(ai, dict) or not ai:
        return {"any": False, "ready": [], "down": [], "text": "AI status unavailable", "cls": "bad"}
    ready = [str(x) for x in (ai.get("ready") or [])]
    down = []
    for p in ai.get("providers") or []:
        st = p.get("state")
        if st == "ready":
            continue
        why = str(p.get("why") or "")
        mm = re.search(r"retry in (\d+)\s*min", why)
        down.append({"name": p.get("name") or "?",
                     "state": "resting" if st == "resting" else ("no key" if st == "nokey" else "off"),
                     "why": why, "minutes": int(mm.group(1)) if mm else None})
    any_ai = bool(ai.get("any"))
    if ready:
        text = "AI: " + " · ".join(ready) + " ready"
    elif ai.get("claude_ok"):
        text = "AI: Claude only"
    else:
        text = "AI: none available"
    return {"any": any_ai, "ready": ready, "down": down, "text": text, "cls": "ok" if any_ai else "bad"}


def week_rows(c, days) -> list[dict]:
    """Per-day honest metrics for the dashboard's week view, each from the same day_counts as Today."""
    out = []
    for d in days:
        day = day_counts(c, d)
        out.append({"day": d, "n": day["proven"], "sent": day["sent"], "unproven": day["unproven"],
                    "attempts": day["attempts"], "failed": day["failed"]})
    return out


JOB_COLS = ("id,company,title,status,applied_at,email_proof,proof_shot,screenshot_path,page_proof,apply_url,url,"
            "apply_route,source,status_detail,needs_you_reason")


def day_jobs(c, day: str, limit: int = 200) -> list[dict]:
    """That day's sends (status applied or unconfirmed), newest first, with the same proof_kind policy.

    applied_at is the SEND date: a send whose only evidence is a page/account badge but no receipt keeps
    proof_kind 'none' (the UI labels it UNPROVEN). A day with nothing sent returns [] — no inferred row.
    """
    rows = q(c, f"SELECT {JOB_COLS} FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ? "
                "ORDER BY applied_at DESC, rowid DESC LIMIT ?", (day + "%", limit))
    for r in rows:
        r["proof_kind"] = proof_kind(r.get("email_proof"))
    return rows


def recent_submissions(c, day: str, limit: int = 50) -> list[dict]:
    """Submissions (proven and unproven) dated `day`, newest first. Undated rows are NOT invented into today."""
    cols = ("id,company,title,status,applied_at,email_proof,proof_shot,screenshot_path,page_proof,apply_url,url,"
            "apply_route,source,status_detail,needs_you_reason")
    rows = q(c, f"SELECT {cols} FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ? "
                "ORDER BY applied_at DESC, rowid DESC LIMIT ?", (day + "%", limit))
    for r in rows:
        r["proof_kind"] = proof_kind(r.get("email_proof"))
    return rows


def undated_submissions(c) -> int:
    """Submitted rows with no applied_at (older navigator bug): real sends, but not attributable to any day."""
    return c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at IS NULL").fetchone()[0]


def failure_classes(c, limit: int = 10) -> list[dict]:
    """Failures by the applier's own class (fail_class), for the ops page. No inferred buckets.

    The class vocabulary has ONE definition (apply/errors.py). Legacy strings already stored in the
    DB (e.g. runner's `llm_down`) are merged into their canonical key, so one failure never shows as
    two buckets; unknown values are shown exactly as stored, never re-bucketed."""
    merged: dict[str, int] = {}
    for raw, n in c.execute(
            "SELECT fail_class, COUNT(*) n FROM jobs WHERE status IN ('failed','needs_you','blocked_portal') "
            "AND fail_class IS NOT NULL AND TRIM(fail_class)<>'' GROUP BY fail_class"):
        key = errors.canonical(raw)
        merged[key] = merged.get(key, 0) + n
    out = []
    for key, n in sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
        entry = errors.fail_class(key)
        out.append({"cls": key, "label": entry.label if entry else key, "n": n})
    return out


def fail_reasons(c, limit: int = 8) -> list[dict]:
    """Top concrete failure / park reasons (needs_you_reason, else status_detail) for the ops page."""
    return [{"reason": r[0], "n": r[1]} for r in c.execute(
        "SELECT COALESCE(NULLIF(TRIM(needs_you_reason),''), NULLIF(TRIM(status_detail),''), 'no reason recorded') reason, "
        "COUNT(*) n FROM jobs WHERE status IN ('failed','needs_you','blocked_portal') GROUP BY reason "
        "ORDER BY n DESC, reason LIMIT ?",
        (limit,))]


def needs_reasons(c, limit: int = 3) -> list[dict]:
    """The most common needs_you reasons, counted from the DB (no inferred buckets)."""
    rows = c.execute(
        "SELECT COALESCE(NULLIF(TRIM(needs_you_reason),''), NULLIF(TRIM(status_detail),''), 'no reason recorded') reason, "
        "COUNT(*) n FROM jobs WHERE status='needs_you' GROUP BY reason ORDER BY n DESC, reason LIMIT ?", (limit,))
    return [{"reason": r[0], "n": r[1]} for r in rows]


_SHOT_CACHE: dict = {}


def safe_shot(path, root) -> Path | None:
    """Resolve a stored screenshot path and accept it only inside `root` (defeats ../ and symlinks)."""
    if not path:
        return None
    try:
        p = Path(str(path)).resolve()
        r = Path(root).resolve()
    except (OSError, RuntimeError):
        return None
    if p == r or r not in p.parents:
        return None
    key = str(p)
    hit = _SHOT_CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < 20:  # short negative cache: a just-written proof screenshot appears within seconds
        return p if hit[1] else None
    ok = p.is_file()
    _SHOT_CACHE[key] = (now, ok)
    return p if ok else None


def annotate_shots(rows: list[dict], root) -> None:
    """Add proof_url/form_url only when the file really exists under the screenshots folder."""
    for r in rows:
        jid = str(r.get("id") or "")
        r["proof_url"] = f"/proofshot/{jid}" if jid and safe_shot(r.get("proof_shot"), root) else None
        r["form_url"] = f"/shot/{jid}" if jid and safe_shot(r.get("screenshot_path"), root) else None


def tail_line(path, nbytes: int = 16384) -> str:
    """Last non-empty line of a text log, read from the end (never loads a big file whole)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - nbytes))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    for ln in reversed(text.splitlines()):
        if ln.strip():
            return ln.strip()[:240]
    return ""


def newest_log(folder: Path) -> Path | None:
    try:
        files = [p for p in folder.glob("live_run*.log") if p.is_file()]
        return max(files, key=lambda p: p.stat().st_mtime) if files else None
    except OSError:
        return None


_PROC_CACHE: dict = {"at": 0.0, "v": {"worker": 0, "guardian": 0, "ok": False}}


def live_procs() -> dict:
    """Count the real `hunt.py run` and `track/guardian.py` processes (5s cache).

    ok=False means the scan itself failed (e.g. psutil missing): the UI then falls back to the run-state
    heartbeat and the guardian snapshot instead of showing a false red.
    """
    now = time.time()
    if now - _PROC_CACHE["at"] < 5:
        return _PROC_CACHE["v"]
    out = {"worker": 0, "guardian": 0, "ok": False}
    try:
        import psutil
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if p.pid == os.getpid():
                    continue
                cl = " ".join(p.info["cmdline"] or []).replace("\\", "/").lower()
            except Exception:  # noqa: BLE001
                continue
            if "hunt.py run" in cl:
                out["worker"] += 1
            elif "guardian.py" in cl and "track/" in cl:
                out["guardian"] += 1
        out["ok"] = True
    except Exception:  # noqa: BLE001
        pass
    _PROC_CACHE.update(at=now, v=out)
    return out


def iso_age(iso: str | None) -> float | None:
    """Seconds since an ISO timestamp written with datetime.isoformat (local, naive)."""
    if not iso:
        return None
    try:
        return max(0.0, time.time() - datetime.fromisoformat(str(iso)).timestamp())
    except (TypeError, ValueError):
        return None


def usage_totals(path, days, today_ts: float, month_ts: float) -> tuple[dict, dict]:
    """Per-day {tokens, calls} for `days`, plus today/month token totals, from data/llm_usage.jsonl.

    Never raises on a missing or garbled log: an unknown day stays 0 rather than being invented.
    """
    per_day = {d: {"tokens": 0, "calls": 0} for d in days}
    totals = {"today": 0, "month": 0, "calls_today": 0}
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()[-20000:]
    except OSError:
        return per_day, totals
    for ln in lines:
        if not ln.startswith("{"):
            continue
        try:
            u = json.loads(ln)
        except ValueError:
            continue
        t = u.get("t")
        if not isinstance(t, (int, float)):
            continue
        tok = (u.get("i") or 0) + (u.get("o") or 0)
        if t >= month_ts:
            totals["month"] += tok
        if t >= today_ts:
            totals["today"] += tok
            totals["calls_today"] += 1
        bucket = per_day.get(datetime.fromtimestamp(t).strftime("%Y-%m-%d"))
        if bucket is not None:
            bucket["tokens"] += tok
            bucket["calls"] += 1
    return per_day, totals


RESPONSE_KINDS = ("offer", "interview", "assessment", "rejected", "closed")


def outcomes_map(c) -> dict:
    """job id -> {kind, at} for every job an employer has answered (apply/replies.py writes jobs.outcome). Empty before the first scan."""
    try:
        return {r["id"]: {"kind": r["outcome"], "at": r["outcome_at"]} for r in c.execute("SELECT id, outcome, outcome_at FROM jobs WHERE outcome IS NOT NULL")}
    except sqlite3.OperationalError:
        return {}


def outreach_block() -> dict:
    """LinkedIn low-competition scan + startup outreach drafts, for the dashboard. Read-only; never raises (a panel must not break the page)."""
    out = {"li_limit": None, "li": [], "li_counts": {}, "startups": [], "startup_counts": {}, "mode": "drafts"}
    try:
        from apply import lowcomp
        out["li_limit"] = lowcomp.li_max_applicants()
        rows = lowcomp.recent(80)
        out["li"] = rows
        for r in lowcomp.recent(2000):
            out["li_counts"][r["decision"]] = out["li_counts"].get(r["decision"], 0) + 1
    except Exception:  # noqa: BLE001
        pass
    try:
        if (DATA / "outreach.db").exists():
            from apply import outreach
            rows = outreach.rows(300)
            out["startups"] = rows
            for r in rows:
                out["startup_counts"][r["status"]] = out["startup_counts"].get(r["status"], 0) + 1
    except Exception:  # noqa: BLE001
        pass
    return out


def responses_block(c, limit: int = 80) -> dict:
    """Employer replies for the dashboard: counts by outcome, how many sends are still unanswered, and the newest replies (against a job when one is
    singled out, else against the company)."""
    kinds = {k: 0 for k in RESPONSE_KINDS}
    try:
        for r in c.execute("SELECT outcome, COUNT(*) n FROM jobs WHERE outcome IS NOT NULL GROUP BY outcome"):
            if r["outcome"] in kinds:
                kinds[r["outcome"]] = r["n"]
        sent = c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at IS NOT NULL AND applied_at NOT LIKE '2026-01-01%'").fetchone()[0]
        answered = c.execute("SELECT COUNT(*) FROM jobs WHERE outcome IS NOT NULL AND status IN ('applied','unconfirmed')").fetchone()[0]
        company_only = c.execute("SELECT COUNT(*) FROM replies WHERE job_id IS NULL").fetchone()[0]
        last = c.execute("SELECT MAX(created_at) FROM replies").fetchone()[0]
        rows = q(c, "SELECT r.kind, r.subject, r.sender, r.received_at, r.company, r.job_id, j.title, j.status, j.apply_url, j.url "
                    "FROM replies r LEFT JOIN jobs j ON j.id = r.job_id ORDER BY r.ts DESC LIMIT ?", (600,))
    except sqlite3.OperationalError:
        return {"ready": False}
    _all_rows = rows
    grouped: dict = {}
    for r in rows:   # newest first: one row per (job or company, kind) with how many mails say so (a company sent ~20 interview reminders)
        key = (r.get("job_id") or (r.get("company") or "").lower(), r.get("kind"))
        if key in grouped:
            grouped[key]["count"] += 1
        else:
            grouped[key] = {**r, "count": 1}
    rows = list(grouped.values())
    # the headline counts every DISTINCT reply (same job-or-company, kind and subject counted once), whether or not it could be tied to a job
    seen_replies = {(r.get("job_id") or (r.get("company") or "").lower(), r.get("kind"), (r.get("subject") or "").lower()[:60]) for r in _all_rows}
    kinds = {k: sum(1 for x in seen_replies if x[1] == k) for k in RESPONSE_KINDS}
    rows = rows[:limit]
    return {"ready": True, "by_outcome": kinds, "sent": sent, "answered": answered, "waiting": max(0, sent - answered), "company_only": company_only,
            "response_rate": round(100 * answered / sent) if sent else 0, "last_scan": last, "recent": rows}


def tracker_block(c) -> dict:
    """The application tracker sheet: one row per application the employer has answered (latest reply per job, per company when no job is singled out)
    plus every sent application still waiting. Each row says what happens next, by when and where; nothing is guessed - the text comes from the mail."""
    try:
        try:
            reps = q(c, "SELECT r.msgkey, r.kind, r.subject, r.received_at, r.ts, r.company, r.job_id, r.next_step, r.deadline, r.link, j.title, j.applied_at, j.apply_url, j.url "
                        "FROM replies r LEFT JOIN jobs j ON j.id = r.job_id ORDER BY r.ts DESC LIMIT 3000")
        except sqlite3.OperationalError:   # columns not migrated yet (first run after the update)
            reps = q(c, "SELECT r.msgkey, r.kind, r.subject, r.received_at, r.ts, r.company, r.job_id, '' next_step, '' deadline, '' link, j.title, j.applied_at, j.apply_url, j.url "
                        "FROM replies r LEFT JOIN jobs j ON j.id = r.job_id ORDER BY r.ts DESC LIMIT 3000")
        waiting = q(c, "SELECT id, company, title, applied_at, apply_url, url FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at IS NOT NULL "
                       "AND applied_at NOT LIKE '2026-01-01%' AND outcome IS NULL ORDER BY applied_at DESC LIMIT 800")
    except sqlite3.OperationalError:
        return {"ready": False}
    best: dict = {}
    for r in reps:   # newest first: the latest mail of a job / company decides its status, but an offer is never overwritten by a later note
        key = r.get("job_id") or ("co:" + (r.get("company") or "").lower())
        cur = best.get(key)
        if cur is None:
            best[key] = {**r, "mails": 1}
        else:
            cur["mails"] += 1
            if r["kind"] == "offer" and cur["kind"] != "offer":
                best[key] = {**r, "mails": cur["mails"]}
    rows = []
    for r in best.values():
        rows.append({"key": r.get("job_id") or ("co:" + (r.get("company") or "").lower()), "ts": r.get("ts") or 0, "job_id": r.get("job_id"), "company": r.get("company"), "title": r.get("title") or "", "status": r["kind"], "step": r.get("next_step") or "",
                     "deadline": r.get("deadline") or "", "link": r.get("link") or "", "mail": re.sub(r"\s+", " ", r.get("subject") or ""), "at": r.get("received_at") or "",
                     "applied_at": r.get("applied_at") or "", "job_url": r.get("apply_url") or r.get("url") or "", "mails": r["mails"]})
    try:
        done = {x["key"]: x["done_ts"] for x in q(c, "SELECT key, done_ts FROM tracker_done")}
    except sqlite3.OperationalError:
        done = {}
    for r in rows:   # marked done by her: stays done until the employer writes again
        if r["key"] in done and (r["ts"] or 0) <= done[r["key"]] and r["status"] in ("offer", "assessment", "interview"):
            r["status"], r["step"] = "done", "Done - marked by you"
    today = datetime.now().strftime("%Y-%m-%d")
    stale_before = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    for r in rows:   # an interview or test whose date has gone, or whose mail is old and names no date, is history - not something waiting for her
        if r["status"] in ("interview", "assessment") and ((r["deadline"] and r["deadline"] < today) or (not r["deadline"] and (r["at"] or "")[:10] < stale_before)):
            r["was"], r["status"] = r["status"], "past"
            r["step"] = f"Earlier {r['was']} - date has passed, nothing to do unless the employer writes again"
    answered_jobs = {r["job_id"] for r in rows if r["job_id"]}
    for w in waiting:
        if w["id"] in answered_jobs:
            continue
        rows.append({"job_id": w["id"], "company": w["company"], "title": w["title"], "status": "waiting", "step": "Wait for the employer - no reply yet", "deadline": "",
                     "link": "", "mail": "", "at": "", "applied_at": w["applied_at"] or "", "job_url": w["apply_url"] or w["url"] or "", "mails": 0})
    order = {"offer": 0, "assessment": 1, "interview": 2, "rejected": 3, "closed": 4, "past": 5, "done": 5, "waiting": 6}
    todo = [r for r in rows if r["status"] in ("offer", "assessment", "interview")]
    rest = [r for r in rows if r["status"] not in ("offer", "assessment", "interview")]
    todo.sort(key=lambda r: (r["deadline"] or "9999-99-99", order[r["status"]]))
    rest.sort(key=lambda r: (r["at"] or r["applied_at"]), reverse=True)
    rest.sort(key=lambda r: order[r["status"]])
    out = todo + rest
    counts = {k: sum(1 for r in out if r["status"] == k) for k in order}
    return {"ready": True, "rows": out, "counts": counts, "today": datetime.now().strftime("%Y-%m-%d")}


def tracker_csv(c) -> str:
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Company", "Role", "Status", "Next step", "Deadline / date", "Link", "Employer mail", "Reply received", "Applied", "Job link"])
    for r in tracker_block(c).get("rows", []):
        w.writerow([r["company"], r["title"], r["status"], r["step"], r["deadline"], r["link"], r["mail"], r["at"], r["applied_at"], r["job_url"]])
    return buf.getvalue()


def state() -> dict:
    c = sqlite3.connect(f"file:{dbm.DB_PATH}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    today = datetime.now().strftime("%Y-%m-%d")
    counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    cols = ("id,company,title,location,source,family,score,apply_url,url,applied_at,status,status_detail,needs_you_reason,"
            "ats_coverage,screenshot_path,posted_at,first_seen,apply_route,email_proof,proof_shot,page_proof")
    # no silent truncation: the list length must equal the count shown in its header (Show all N)
    applied = q(c, f"SELECT {cols} FROM jobs WHERE status='applied' AND email_proof IS NOT NULL ORDER BY applied_at DESC LIMIT 500")
    needs = q(c, f"SELECT {cols} FROM jobs WHERE status='needs_you' ORDER BY rowid DESC LIMIT 500")
    awaiting = q(c, f"SELECT {cols} FROM jobs WHERE status='unconfirmed' OR (status='applied' AND email_proof IS NULL) ORDER BY applied_at DESC LIMIT 500")
    for r in applied + awaiting:
        r["proof_kind"] = proof_kind(r.get("email_proof"))
    plan = q(c, f"SELECT {cols} FROM jobs WHERE status='candidate' AND (score>=45 OR score IS NULL) ORDER BY score IS NULL, score DESC, first_seen DESC LIMIT 200")
    plan = [r for r in plan if (ghost.age_or(r["posted_at"], r["first_seen"])) <= 7][:25]
    ph = " AND ".join("COALESCE(e.detail,'') NOT LIKE ?" for _ in OBSOLETE)
    events = q(c, "SELECT e.at, e.kind, e.detail, j.company, j.title, j.id FROM events e LEFT JOIN jobs j ON j.id=e.job_id "
                  f"WHERE e.kind IN ('applied','needs_you','closed','skipped','failed','unconfirmed','dry_run_ok') AND {ph} "
                  "ORDER BY e.id DESC LIMIT 40", OBSOLETE)
    nxt = q(c, f"SELECT {cols} FROM jobs WHERE status='candidate' AND score>=45 ORDER BY score DESC LIMIT 60")
    nxt = [r for r in nxt if (ghost.age_or(r["posted_at"], r["first_seen"])) <= 7][:10]
    fam = {r["family"] or "other": r["n"] for r in c.execute("SELECT family, COUNT(*) n FROM jobs WHERE status='applied' GROUP BY family")}
    src = {r["source"] or "?": r["n"] for r in c.execute("SELECT source, COUNT(*) n FROM jobs WHERE status='applied' GROUP BY source")}
    one = lambda sql, a=(): c.execute(sql, a).fetchone()[0]  # noqa: E731
    caps_used = {
        "naukri": one("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ? AND (source='board:naukri' OR apply_url LIKE '%naukri.com%')", (today + "%",)),
        "indeed": one("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ? AND apply_url LIKE '%indeed.%'", (today + "%",)),
        "linkedin_easy": one("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ? AND apply_route='easy_apply'", (today + "%",)),
    }
    email_confirmed = one("SELECT COUNT(*) FROM jobs WHERE status='applied' AND email_proof IS NOT NULL")
    total = one("SELECT COUNT(*) FROM jobs")
    scored = one("SELECT COUNT(*) FROM jobs WHERE score IS NOT NULL")
    day = day_counts(c, today)
    proven_today = day["proven"]
    today_applied = one("SELECT COUNT(*) FROM jobs WHERE status='applied' AND applied_at LIKE ?", (today + "%",))
    awaiting_undated = undated_submissions(c)
    # where we are applying: outcome counts per portal (Workday, Naukri, LinkedIn, Oracle, ...)
    from core import activity
    portals: dict = {}
    blocked: dict = {}
    for r in c.execute("SELECT status, apply_url, url, apply_route FROM jobs WHERE status IN "
                       "('applied','needs_you','unconfirmed','failed','closed','dry_run_ok','deferred_easy','deferred_board','blocked_portal')"):
        p = activity.portal_of(r["apply_url"] or r["url"], r["apply_route"])
        b = portals.setdefault(p, {"applied": 0, "waiting": 0, "failed": 0, "closed": 0, "dry": 0, "deferred": 0})
        b[{"applied": "applied", "needs_you": "waiting", "unconfirmed": "waiting", "failed": "failed", "closed": "closed",
           "dry_run_ok": "dry", "deferred_easy": "deferred", "deferred_board": "deferred", "blocked_portal": "deferred"}[r["status"]]] += 1
        if r["status"] == "blocked_portal":
            blocked[p] = blocked.get(p, 0) + 1
    cand_portals: dict = {}
    for r in c.execute("SELECT apply_url, url, apply_route FROM jobs WHERE status='candidate'"):
        p = activity.portal_of(r["apply_url"] or r["url"], r["apply_route"])
        cand_portals[p] = cand_portals.get(p, 0) + 1
    for lst in (applied, needs, nxt):
        for j in lst:
            j["portal"] = activity.portal_of(j.get("apply_url") or j.get("url"), j.get("apply_route"))
    # ---- mission-control numbers (all computed here, before the connection closes) ----
    from datetime import timedelta
    week0 = (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d")
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    week = week_rows(c, days)   # proven/sent/unproven/attempts/failed per day, same definitions as Today
    sent_today = day["sent"]
    sent_today_ids = day["_sent_ids"]
    att_rows = q(c, "SELECT DISTINCT e.job_id, e.kind, j.apply_url, j.url, j.apply_route FROM events e JOIN jobs j ON j.id=e.job_id "
                    "WHERE e.at LIKE ? AND e.kind IN ('applied','unconfirmed','needs_you','failed','dry_run_ok')", (today + "%",))
    failed_today_ids = day["_failed_ids"]
    attempts_today = day["attempts"]
    recent = recent_submissions(c, today)
    for r in recent:
        r["portal"] = activity.portal_of(r.get("apply_url") or r.get("url"), r.get("apply_route"))
    today_jobs = day_jobs(c, today)   # today's sends for the compact list under the Today hero
    for r in today_jobs:
        r["portal"] = activity.portal_of(r.get("apply_url") or r.get("url"), r.get("apply_route"))
    top_needs = needs_reasons(c)
    top_fail_classes = failure_classes(c)
    top_fail_reasons = fail_reasons(c)
    annotate_shots(recent, DATA / "screenshots")  # links only for files that really exist (and inside the folder)
    annotate_shots(today_jobs, DATA / "screenshots")
    annotate_shots(applied, DATA / "screenshots")
    annotate_shots(awaiting, DATA / "screenshots")
    outcome_map = outcomes_map(c)   # what the employer answered, per job (rejected / closed / interview / assessment / offer)
    for lst in (recent, today_jobs, applied, awaiting):
        for r in lst:
            o = outcome_map.get(r.get("id"))
            if o:
                r["outcome"], r["outcome_at"] = o["kind"], o["at"]
    responses = responses_block(c)
    tracker = tracker_block(c)   # must be read before the connection is closed
    plat: dict = {}
    pl = lambda name: plat.setdefault(name, {"proven": 0, "awaiting": 0, "waiting": 0, "failed": 0, "blocked": 0,  # noqa: E731
                                              "today_sent": 0, "today_failed": 0, "today_attempts": 0})
    for r in c.execute("SELECT id, status, email_proof, apply_url, url, apply_route FROM jobs WHERE status IN "
                       "('applied','unconfirmed','needs_you','failed','blocked_portal')"):
        b = pl(activity.portal_of(r["apply_url"] or r["url"], r["apply_route"]))
        st = r["status"]
        if st == "applied":
            b["proven" if r["email_proof"] else "awaiting"] += 1
        elif st == "unconfirmed":
            b["awaiting"] += 1
        elif st == "needs_you":
            b["waiting"] += 1
        elif st == "failed":
            b["failed"] += 1
        else:
            b["blocked"] += 1
        if r["id"] in sent_today_ids:
            b["today_sent"] += 1
    seen_att = set()
    for r in att_rows:
        if r["job_id"] in seen_att:
            continue
        seen_att.add(r["job_id"])
        b = pl(activity.portal_of(r["apply_url"] or r["url"], r["apply_route"]))
        b["today_attempts"] += 1
        if r["job_id"] in failed_today_ids:
            b["today_failed"] += 1
    n_fresh = 0
    for r in c.execute("SELECT posted_at, first_seen FROM jobs WHERE status='candidate' AND score>=45"):
        if (ghost.age_or(r["posted_at"], r["first_seen"])) <= 7:
            n_fresh += 1
    numbers = {
        "proven_total": email_confirmed,
        "sent_total": one("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed','unverified')"),  # every submission, with or without proof yet
        "proven_today": proven_today,
        "sent_today": sent_today,
        "unproven_today": day["unproven"],
        "proven_week": one("SELECT COUNT(*) FROM jobs WHERE status='applied' AND email_proof IS NOT NULL AND applied_at >= ?", (week0,)),
        "per_day": week,
        "awaiting": one("SELECT COUNT(*) FROM jobs WHERE status='unconfirmed' OR (status='applied' AND email_proof IS NULL)"),
        "awaiting_undated": awaiting_undated,
        "waiting": counts.get("needs_you", 0),
        "waiting_captcha": one("SELECT COUNT(*) FROM jobs WHERE status='needs_you' AND needs_you_reason LIKE '%CAPTCHA%'"),
        "needs_reasons": top_needs,
        "fail_classes": top_fail_classes,
        "fail_reasons": top_fail_reasons,
        "queue": n_fresh,
        "retrying": one("SELECT COUNT(*) FROM jobs WHERE status='candidate' AND next_attempt_at IS NOT NULL AND next_attempt_at > ?", (datetime.now().isoformat(timespec="seconds"),)),
        "attempts_today": attempts_today,
        "failed_today": day["failed"],
        "success_rate_today": (round(100 * sent_today / attempts_today) if attempts_today else None),
        "proven_rate_today": (round(100 * proven_today / attempts_today) if attempts_today else None),
        "platforms": plat,
        "defs": DEFS,
    }
    c.close()
    # every applier's live state (A = main, B = parallel on the profile copy)
    runs = []
    for f in sorted(DATA.glob("run_state*.json")):
        try:
            rs = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        rs.setdefault("label", "A" if f.name == "run_state.json" else f.stem.split("_")[-1])
        rs["alive"] = run_alive(rs)
        if rs.get("status") in ("running", "preparing") and not rs["alive"]:
            rs["status"] = "stalled"
        runs.append(rs)
    acts = activity.tail(140)
    today0 = datetime.now().replace(hour=0, minute=0, second=0).timestamp()
    llm_today = [a for a in acts if a.get("kind") == "llm" and a.get("state") == "done" and a.get("t", 0) >= today0]
    llm_stats = {"calls": len(llm_today), "by_model": {}, "by_purpose": {}, "secs": round(sum(a.get("secs", 0) for a in llm_today), 1),
                 "last": next((a for a in reversed(acts) if a.get("kind") in ("llm", "ai")), None),
                 "active": any(a.get("kind") == "llm" and a.get("state") == "start" and time.time() - a.get("t", 0) < 90 and
                               not any(b.get("kind") == "llm" and b.get("t", 0) > a["t"] for b in acts) for a in acts[-3:])}
    for a in llm_today:
        llm_stats["by_model"][a.get("model", "?")] = llm_stats["by_model"].get(a.get("model", "?"), 0) + 1
        purpose = re.sub(r"\s*\d+\s*", " ", a.get("msg", "").split(":")[0]).strip()
        llm_stats["by_purpose"][purpose] = llm_stats["by_purpose"].get(purpose, 0) + 1
    usage = {"llm_calls": 0, "ai_answers": 0, "ai_asked": 0, "ai_rounds": 0, "by_model": {}, "llm_secs": 0.0, "llm_failed": 0}
    try:
        with open(activity.PATH, encoding="utf-8", errors="ignore") as fh:
            for ln in fh:
                if not ln.startswith("{"):
                    continue
                try:
                    a = json.loads(ln)
                except ValueError:
                    continue
                if a.get("t", 0) < today0:
                    continue
                if a.get("kind") == "llm" and a.get("state") == "done":
                    usage["llm_calls"] += 1
                    usage["llm_secs"] += a.get("secs", 0) or 0
                    mdl = a.get("model", "?")
                    usage["by_model"][mdl] = usage["by_model"].get(mdl, 0) + 1
                elif a.get("kind") == "error" and a.get("state") == "fail":
                    usage["llm_failed"] += 1
                elif a.get("kind") == "ai" and "asked" in a:
                    usage["ai_rounds"] += 1
                    usage["ai_answers"] += a.get("answered", 0) or 0
                    usage["ai_asked"] += a.get("asked", 0) or 0
        usage["llm_secs"] = round(usage["llm_secs"], 1)
    except OSError:
        pass
    ai_answers = sum(a.get("answered", 0) for a in acts if a.get("kind") == "ai")
    run = runstate.read()
    run["alive"] = run_alive(run)
    if run.get("status") in ("running", "preparing") and not run["alive"]:
        run["status"] = "stalled"
    sess = {}
    try:
        sess = json.loads((DATA / "session_status.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    refresh = ""
    try:
        refresh = (DATA / "refresh.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    except Exception:  # noqa: BLE001
        pass
    try:
        goal = int(json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("daily_goal", 100))
    except Exception:  # noqa: BLE001
        goal = 100
    try:
        lessons = json.loads((DATA / "lessons.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        lessons = {}
    for r in plan:
        r["portal"] = activity.portal_of(r["apply_url"] or "", r["apply_route"])
    for r in applied + awaiting + needs:
        r["portal"] = activity.portal_of(r["apply_url"] or "", r["apply_route"])
    try:
        t_day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        t_mon = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        tok_day, usage_tokens = usage_totals(DATA / "llm_usage.jsonl", days, t_day, t_mon)
        usage["by_model_tokens"], usage["by_purpose_tokens"] = usage_breakdown(DATA / "llm_usage.jsonl", t_day, t_mon)
    except Exception:  # noqa: BLE001
        tok_day = {d: {"tokens": 0, "calls": 0} for d in days}
        usage_tokens = {"today": 0, "month": 0, "calls_today": 0}
        usage["by_model_tokens"], usage["by_purpose_tokens"] = {}, {}
    # each per-day row also carries the tokens burnt that day (tooltip on the strip + the Usage row in history)
    for item in numbers["per_day"]:
        b = tok_day.get(item["day"], {"tokens": 0, "calls": 0})
        item["tokens"], item["calls"] = b["tokens"], b["calls"]
    systems = {}
    for fn, key in (("integrations.json", "integrations"), ("health.json", "health")):
        try:
            systems[key] = json.loads((DATA / fn).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            systems[key] = {}
    systems["llm_login_needed"] = (DATA / "llm_login_needed.json").exists()
    # LIVE truth for the Backup AI light (not the guardian's 15-minute-old snapshot): the flag, and who really answered the last 30 minutes of calls
    bk = {"claude_30m": 0, "backup_30m": 0, "claude_10m": 0, "backup_10m": 0, "last_backup_min": None, "primary": "claude"}
    try:
        bk["primary"] = json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("llm_primary", "claude")
        last_b = 0
        for ln in (DATA / "llm_usage.jsonl").read_text(encoding="utf-8").splitlines()[-600:]:
            u = json.loads(ln)
            if u["m"] in ("haiku", "sonnet"):
                bk["claude_30m"] += u["t"] >= time.time() - 1800
                bk["claude_10m"] += u["t"] >= time.time() - 600
            else:
                bk["backup_30m"] += u["t"] >= time.time() - 1800
                bk["backup_10m"] += u["t"] >= time.time() - 600
                last_b = max(last_b, u["t"])
        bk["last_backup_min"] = round((time.time() - last_b) / 60) if last_b else None
    except Exception:  # noqa: BLE001
        pass
    systems["backup"] = bk
    try:
        from core import llm as _llm
        systems["ai"] = _llm.status()
    except Exception:  # noqa: BLE001
        systems["ai"] = {"providers": [], "ready": [], "any": False, "claude_ok": False}
    systems["ai_chip"] = ai_chip(systems.get("ai"))   # one compact, LIVE line for the main page
    # health strip: live process scan + the guardian's own snapshot + the newest worker log file, never one signal alone
    procs = live_procs()
    glog, wlog = DATA / "guardian.log", newest_log(DATA)
    guardian_age = iso_age((systems.get("health") or {}).get("at"))
    health = {
        "proc_scan_ok": procs["ok"],
        "worker_procs": procs["worker"] if procs["ok"] else None,
        "guardian_procs": procs["guardian"] if procs["ok"] else None,
        "guardian_last_check_age_s": round(guardian_age) if guardian_age is not None else None,
        "guardian_log_age_s": round(time.time() - glog.stat().st_mtime) if glog.exists() else None,
        "guardian_last": tail_line(glog),
        "log_file": wlog.name if wlog else None,
        "log_age_s": round(time.time() - wlog.stat().st_mtime) if wlog else None,
        "log_last": tail_line(wlog) if wlog else "",
        "queue_worker": run.get("queue_size"),
    }
    ui_version = max(PAGE.stat().st_mtime, OPS.stat().st_mtime if OPS.exists() else 0)
    return {"now": time.time(), "tokens": usage_tokens, "systems": systems, "ui_version": ui_version, "awaiting": awaiting, "plan": plan, "lessons": lessons,
            "proven_today": proven_today, "goal": goal, "email_confirmed": email_confirmed, "run": run, "runs": runs, "activity": acts[-90:], "llm": llm_stats, "ai_answers": ai_answers,
            "portals": portals, "cand_portals": cand_portals, "blocked": blocked, "health": health, "recent": recent,
            "today_jobs": today_jobs, "responses": responses, "tracker": tracker, "outreach": outreach_block(),
            "numbers": numbers, "usage": usage, "counts": counts, "today_applied": today_applied, "applied": applied,
            "needs": needs, "events": events, "next": nxt, "families": fam, "sources": src,
            "caps": {k: {"used": caps_used[k], "limit": v} for k, v in CAPS.items()},
            "sessions": sess, "refresh_last": refresh, "refresh_schedule": "Daily 09:30",
            "funnel": {"seen": total, "candidates": counts.get("candidate", 0), "scored": scored,
                       "applied": counts.get("applied", 0), "needs_you": counts.get("needs_you", 0)}}


def spawn(args: list[str], log: str):
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    f = open(DATA / "logs" / f"{log}-{datetime.now():%Y%m%d-%H%M%S}.log", "ab")
    subprocess.Popen([sys.executable, *args], cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


def action(body: dict) -> dict:
    t = body.get("type")
    run = runstate.read()
    busy = run_alive(run)
    d = dbm.DB()
    if t == "start_run":
        if busy:
            return {"ok": False, "message": "A run is already in progress."}
        STOP.unlink(missing_ok=True)
        try:  # the slider sends an int; a stray value must not become a 500 or a nonsense run
            n = max(1, min(500, int(float(body.get("target", 10)))))
        except (TypeError, ValueError):
            n = 10
        tgt = str(n)
        if body.get("live"):
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "supervise.ps1"), "-Target", tgt],
                             cwd=str(ROOT), creationflags=0x08000000)
        else:
            spawn([str(ROOT / "hunt.py"), "run", "--target", tgt], "run")
        runstate.update(status="preparing", target=n, applied=0, attempts=0, dry_run=not body.get("live"),
                        started_at=time.time(), now=None, finished=False)
        return {"ok": True, "message": f"Started {'live' if body.get('live') else 'dry'} run for {n}."}
    if t == "captcha_tab":  # open (or re-open) the pre-filled CAPTCHA tab in the visible helper window
        from apply import assist
        ids = [body["id"]] if body.get("id") else assist.pending_captchas(d)
        for jid in ids:
            assist.enqueue(jid)
        return {"ok": True, "message": f"Opening {len(ids)} pre-filled CAPTCHA tab(s) in a visible Chrome window."}
    if t == "stop_run":
        STOP.write_text("stop", encoding="utf-8")
        return {"ok": True, "message": "Stopping after the current job finishes."}
    if t == "discover":
        if busy:
            return {"ok": False, "message": "Wait for the current run to finish first."}
        spawn([str(ROOT / "hunt.py"), "discover"], "discover")
        return {"ok": True, "message": "Looking for new jobs (about 25 minutes, no tokens)."}
    if t == "job_action":
        jid, act = body["id"], body["action"]
        if act == "done":
            d.c.execute("UPDATE jobs SET email_proof=COALESCE(email_proof, 'ACCOUNT: you confirmed it in your account/email') WHERE id=?", (jid,))
            d.set_status(jid, "applied", "marked applied by you", applied_at=dbm.now())
        elif act == "skip":
            d.set_status(jid, "skipped", "skipped by you")
        elif act == "retry":
            d.set_status(jid, "candidate", "", needs_you_reason=None)
        return {"ok": True, "message": {"done": "Marked as applied.", "skip": "Skipped.", "retry": "Back in the queue."}.get(act, "Done.")}
    if t == "answer":
        qn, ans = (body.get("question") or "").strip(), (body.get("answer") or "").strip()
        if not qn or not ans:
            return {"ok": False, "message": "Type an answer first."}
        p = DATA / "answers.yaml"
        a = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        key = slug(qn)
        if body.get("per_job") and body.get("id"):
            key = f"{key}@{body['id']}"  # motivational answers belong to ONE job and are never reused for another company
        a[key] = ans
        p.write_text(yaml.safe_dump(a, allow_unicode=True, sort_keys=False), encoding="utf-8")
        if body.get("id"):
            d.set_status(body["id"], "candidate", "", needs_you_reason=None)
        return {"ok": True, "message": "Saved. I'll use this answer from now on and retry the job."}
    if t == "requeue_all":  # put every parked job (except CAPTCHA) back in the queue after a fix
        n = 0
        for r in d.rows("status IN ('needs_you','failed','blocked_portal') AND COALESCE(needs_you_reason,'') NOT LIKE '%CAPTCHA%'"):
            d.set_status(r["id"], "candidate", "requeued after fixes", needs_you_reason=None)
            n += 1
        return {"ok": True, "message": f"{n} parked jobs are back in the queue."}
    if t == "check_email_proof":
        spawn([str(ROOT / "hunt.py"), "proof"], "proof")
        return {"ok": True, "message": "Checking your Gmail for application receipts (about 30 seconds)."}
    if t in ("llm_toggle", "llm_up", "llm_down", "llm_wake", "llm_test", "llm_addkey"):
        from core import llm as _llm
        name = str(body.get("name") or "")
        if name not in [p["name"] for p in _llm.all_providers()]:
            return {"ok": False, "message": "Unknown provider."}
        if t == "llm_toggle":
            on = not next(p for p in _llm.all_providers() if p["name"] == name).get("enabled", True)
            _llm.set_enabled(name, on)
            return {"ok": True, "message": f"{name} switched {'ON' if on else 'OFF'}."}
        if t in ("llm_up", "llm_down"):
            _llm.move(name, -1 if t == "llm_up" else 1)
            return {"ok": True, "message": f"{name} moved {'earlier' if t == 'llm_up' else 'later'} in the order."}
        if t == "llm_wake":
            _llm.clear_cooldown(name)
            return {"ok": True, "message": f"{name} will be tried again now."}
        if t == "llm_test":
            spawn([str(ROOT / "hunt.py"), "llm-test", "--only", name], "llm_test")
            return {"ok": True, "message": f"Testing {name} (a few seconds): the chip updates by itself."}
        # llm_addkey: the key is typed into a normal PowerShell window (hidden input), never through the web page or the chat
        import subprocess
        p = next(p for p in _llm.all_providers() if p["name"] == name)
        cmd = f"cd '{ROOT}'; python hunt.py llm-key {_llm.key_name(p)} --provider {name}; Read-Host 'Press Enter to close'"
        subprocess.Popen(["powershell", "-NoProfile", "-Command", cmd], creationflags=0x00000010)  # CREATE_NEW_CONSOLE
        return {"ok": True, "message": f"A PowerShell window opened: paste the {name} key there (hidden), press Enter."}
    if t == "llm_login":
        from core import llm_browser
        llm_browser.open_login_window("deepseek-web")
        return {"ok": True, "message": "Edge is opening on DeepSeek. Sign in (solve the CAPTCHA), then close the window: the bot picks it up by itself."}
    if t in ("check_sessions", "login_window", "refresh_naukri"):
        if busy:
            return {"ok": False, "message": "A run is using the browser. Try again when it finishes."}
        script = {"check_sessions": ["apply/session_check.py"], "login_window": ["hunt.py", "login"], "refresh_naukri": ["apply/refresh.py"]}[t]
        spawn([str(ROOT / script[0]), *script[1:]], t)
        return {"ok": True, "message": {"check_sessions": "Checking your sign-ins (about 30 seconds).",
                                         "login_window": "A Chrome window is opening. Sign in, then close it.",
                                         "refresh_naukri": "Refreshing your Naukri resume."}[t]}
    if t == "tracker_done":
        key = str(body.get("key") or "")[:200]
        if not key:
            return {"ok": False, "message": "Nothing to mark."}
        d.c.execute("INSERT OR REPLACE INTO tracker_done(key, done_ts, note) VALUES(?,?,?)", (key, time.time(), "marked done from the dashboard"))
        return {"ok": True, "message": "Marked done. It comes back only if the employer writes again."}
    if t == "open_path":
        target = {"tracker": DATA / "tracker.xlsx", "resumes": DATA / "resumes", "proofs": DATA / "screenshots", "logs": DATA / "logs"}.get(body.get("what"))
        if target and target.exists():
            os.startfile(str(target))  # noqa: S606
            return {"ok": True, "message": "Opened."}
        return {"ok": False, "message": "Nothing there yet."}
    return {"ok": False, "message": "Unknown action."}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        try:
            if self.path.startswith("/api/simple"):   # the simple home page
                from track import simple
                self._send(json.dumps(simple.simple_state(), default=str).encode(), "application/json")
            elif self.path.startswith("/api/state"):
                self._send(json.dumps(state(), default=str).encode(), "application/json")
            elif self.path.startswith("/api/tracker.csv"):
                c = sqlite3.connect(f"file:{dbm.DB_PATH}?mode=ro", uri=True, timeout=5)
                c.row_factory = sqlite3.Row
                try:
                    data = tracker_csv(c)
                finally:
                    c.close()
                self._send(("﻿" + data).encode("utf-8"), "text/csv; charset=utf-8")
            elif self.path.startswith("/api/day/"):
                day = self.path[len("/api/day/"):].split("?")[0]
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    self._send(b'{"day":"","jobs":[],"error":"bad date"}', "application/json", 400)
                    return
                c = sqlite3.connect(f"file:{dbm.DB_PATH}?mode=ro", uri=True, timeout=5)
                c.row_factory = sqlite3.Row
                try:
                    jobs = day_jobs(c, day)
                finally:
                    c.close()
                annotate_shots(jobs, DATA / "screenshots")  # only files that really exist
                self._send(json.dumps({"day": day, "jobs": jobs}, default=str).encode(), "application/json")
            elif self.path.startswith(("/shot/", "/proofshot/")):
                col = "proof_shot" if self.path.startswith("/proofshot/") else "screenshot_path"  # proof = taken only AFTER the final Submit
                jid = self.path[len("/proofshot/"):] if self.path.startswith("/proofshot/") else self.path[len("/shot/"):]
                jid = jid.split("?")[0]
                c = sqlite3.connect(f"file:{dbm.DB_PATH}?mode=ro", uri=True)
                r = c.execute(f"SELECT {col} FROM jobs WHERE id=?", (jid,)).fetchone()
                c.close()
                p = safe_shot(r[0], DATA / "screenshots") if r and r[0] else None  # resolved + contained, never ../ or a symlink out
                if p:
                    self._send(p.read_bytes(), "image/png")
                else:
                    self._send(b"", "image/png", 404)
            else:
                _p = self.path.split("?")[0].rstrip("/")
                # she tried the simple page and prefers the full dashboard (dark, live status, daily updates). "/" is the full one again;
                # the short page stays at /simple.
                page = OPS if _p == "/ops" else SIMPLE if _p == "/simple" else PAGE
                self._send(page.read_bytes(), "text/html; charset=utf-8")
        except Exception as e:  # noqa: BLE001
            self._send(str(e).encode(), "text/plain", 500)

    def do_POST(self):  # noqa: N802
        try:
            origin = self.headers.get("Origin")
            if self.path != "/api/action" or self.headers.get("X-JH") != "1" or (origin and origin != f"http://127.0.0.1:{PORT}"):
                return self._send(b'{"ok":false,"message":"Forbidden"}', "application/json", 403)
            n = int(self.headers.get("Content-Length", 0))
            self._send(json.dumps(action(json.loads(self.rfile.read(n) or b"{}"))).encode(), "application/json")
        except Exception as e:  # noqa: BLE001
            self._send(json.dumps({"ok": False, "message": f"{type(e).__name__}: {e}"[:200]}).encode(), "application/json", 500)


def ensure_running(open_browser: bool = True):
    """Start the dashboard server if it is not up (detached), then open it in the browser. Called at the start of every run."""
    import socket
    import webbrowser
    s = socket.socket()
    s.settimeout(0.6)
    up = s.connect_ex(("127.0.0.1", PORT)) == 0
    s.close()
    if not up:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve())], cwd=str(ROOT), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=0x00000008 | 0x00000200)
        time.sleep(1.5)
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{PORT}")


def serve():
    ThreadingHTTPServer.allow_reuse_address = False  # on Windows reuse lets two servers share a port silently
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"Dashboard: http://127.0.0.1:{PORT}  (Ctrl+C to stop)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    serve()
