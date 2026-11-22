"""Durable, append-only history.

The DB is the live source of truth, and maintenance trims the activity log and deletes old failure snapshots, so a plain-text history is written
daily and kept forever:
  data/history/daily.jsonl one line per day: that day's applications (with their proof) and that day's LLM token totals per model
  data/history/YYYY-MM.jsonl every application ever sent in that month (deduped by job id), plus month token totals
It survives a lost or rebuilt database, an emptied activity log, and any later change to how the dashboard computes things.
"""
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SENT = ("applied", "unconfirmed")


def _history_dir(data: Path) -> Path:
    p = data / "history"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _usage_rows(data: Path) -> list:
    f = data / "llm_usage.jsonl"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001 — a half-written last line must not cost us the history
            continue
    return out


def _day_of(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def tokens_by_model(rows: list, day: str | None = None) -> dict:
    """{model: {tokens, calls}} for one day (YYYY-MM-DD) or for everything when day is None."""
    out = defaultdict(lambda: {"tokens": 0, "calls": 0})
    for r in rows:
        try:
            t = int(r.get("t") or 0)
        except Exception:  # noqa: BLE001
            continue
        if day and _day_of(t) != day:
            continue
        m = r.get("m") or "unknown"
        out[m]["tokens"] += int(r.get("i") or 0) + int(r.get("o") or 0)
        out[m]["calls"] += 1
    return dict(out)


def day_record(d, data: Path = DATA, day: str | None = None) -> dict:
    """One day's applications (status applied/unconfirmed, by applied_at) plus that day's token totals per model."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    rows = d.rows("date(applied_at) = ? AND status IN ('applied','unconfirmed')", (day,)) or []
    apps = [{"id": r.get("id"), "at": r.get("applied_at"), "company": r.get("company"), "title": r.get("title"),
             "status": r.get("status"), "proof": (r.get("email_proof") or "")[:180], "url": r.get("apply_url") or r.get("url")}
            for r in sorted(rows, key=lambda x: x.get("applied_at") or "")]
    return {"day": day, "recorded_at": datetime.now().isoformat(timespec="seconds"), "sends": len(apps),
            "proven": sum(1 for a in apps if a["status"] == "applied"), "tokens": tokens_by_model(_usage_rows(data), day),
            "applications": apps}


def write_daily(d, data: Path = DATA, day: str | None = None) -> Path:
    """Upsert today's line in data/history/daily.jsonl (re-running the same day replaces that day, never duplicates it)."""
    rec = day_record(d, data, day)
    f = _history_dir(data) / "daily.jsonl"
    keep = []
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                if json.loads(line).get("day") != rec["day"]:
                    keep.append(line)
            except Exception:  # noqa: BLE001
                keep.append(line)
    keep.append(json.dumps(rec, ensure_ascii=False))
    f.write_text("\n".join(keep) + "\n", encoding="utf-8")
    return f


def write_month(d, data: Path = DATA, month: str | None = None) -> Path:
    """Every application sent in the month, deduped by job id, with the month's token totals per model."""
    month = month or datetime.now().strftime("%Y-%m")
    rows = d.rows("status IN ('applied','unconfirmed') AND substr(applied_at,1,7) = ?", (month,)) or []
    apps = {r.get("id"): {"id": r.get("id"), "at": r.get("applied_at"), "company": r.get("company"), "title": r.get("title"),
                          "status": r.get("status"), "proof": (r.get("email_proof") or "")[:180], "url": r.get("apply_url") or r.get("url")}
            for r in rows}
    us = [u for u in _usage_rows(data) if _day_of(int(u.get("t") or 0)).startswith(month)]
    rec = {"month": month, "recorded_at": datetime.now().isoformat(timespec="seconds"), "sends": len(apps),
           "proven": sum(1 for a in apps.values() if a["status"] == "applied"), "tokens": tokens_by_model(us),
           "applications": [apps[k] for k in sorted(apps, key=lambda k: apps[k]["at"] or "")]}
    f = _history_dir(data) / f"{month}.jsonl"
    f.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    return f


def keep(d, data: Path = DATA) -> dict:
    """Called by the daily maintenance: refresh today's and yesterday's line and this month's file. Yesterday is re-read because a receipt that
    arrives after midnight turns an unproven send into a proven one, and the record must show the corrected status. Never deletes."""
    today = datetime.now()
    yday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    out = {"daily": [str(write_daily(d, data, today.strftime("%Y-%m-%d"))), str(write_daily(d, data, yday))]}
    out["month"] = str(write_month(d, data))
    out["at"] = time.time()
    return out
