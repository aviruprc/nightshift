"""Daily self-maintenance (run by the guardian, no AI calls): keeps a week of unattended running tidy and self-healing.
  expire_unverified submitted jobs with no proof after 48h stop waiting: status 'unverified' (never counted as applied, kept for a manual look)
  requeue_failed failed jobs get ONE more automatic try per day while their attempts are low (many failures were bugs fixed later)
  rotate trims the activity log and deletes old failure snapshots / screenshots so the disk never fills
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def expire_unverified(d, hours: int = 48) -> int:
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    n = 0
    for r in d.rows("status='unconfirmed' AND email_proof IS NULL AND applied_at IS NOT NULL AND applied_at < ?", (cutoff,)):
        d.set_status(r["id"], "unverified", f"no receipt email or account proof after {hours}h: NOT counted as applied (check the screenshot / your account if it matters)")
        n += 1
    return n


def requeue_failed(d, max_attempts: int = 3, min_age_hours: int = 12, limit: int = 40) -> int:
    """One more automatic try for failed jobs: bounded by attempts (<max_attempts), age and a per-day limit, so nothing loops."""
    n = 0
    cutoff = (datetime.now() - timedelta(hours=min_age_hours)).isoformat(timespec="seconds")
    for r in d.rows("status='failed' AND COALESCE(attempts,0) < ? ORDER BY rowid DESC LIMIT ?", (max_attempts, limit)):
        last = d.c.execute("SELECT at FROM events WHERE job_id=? ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()
        if last and last[0] > cutoff:
            continue  # failed recently: let it rest
        d.c.execute("UPDATE jobs SET next_attempt_at=NULL, fail_class=NULL WHERE id=?", (r["id"],))
        d.set_status(r["id"], "candidate", "automatic daily retry (the code may have been fixed since it failed)", needs_you_reason=None)
        n += 1
    return n


def rotate(data: Path = DATA, keep_failures_days: int = 4, keep_shots_days: int = 21, max_log_mb: int = 15) -> dict:
    out = {"log_trimmed": False, "failures_deleted": 0, "shots_deleted": 0}
    log = data / "activity.jsonl"
    if log.exists() and log.stat().st_size > max_log_mb * 1e6:
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()[-30000:]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out["log_trimmed"] = True
    for sub, days, key in (("failures", keep_failures_days, "failures_deleted"), ("screenshots", keep_shots_days, "shots_deleted")):
        base = data / sub
        if not base.exists():
            continue
        cutoff = time.time() - days * 86400
        for f in base.rglob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff and not f.name.endswith("_confirmed.png"):  # post-submit proof shots are kept
                    f.unlink()
                    out[key] += 1
            except OSError:
                pass
    return out


def run_daily(d, data: Path = DATA) -> dict:
    """Once per 24h (state in data/maintenance.json)."""
    stamp = data / "maintenance.json"
    try:
        if time.time() - json.loads(stamp.read_text(encoding="utf-8")).get("at", 0) < 86400:
            return {}
    except Exception:  # noqa: BLE001
        pass
    res = {"unverified": expire_unverified(d), "requeued": requeue_failed(d), **rotate(data)}
    try:  # persistent history first: rotate trims logs, so the record is written before anything is deleted
        from core import history
        res["history"] = history.keep(d, data)
    except Exception as e:  # noqa: BLE001 — a history failure must never stop maintenance
        res["history_error"] = f"{type(e).__name__}: {e}"[:120]
    stamp.write_text(json.dumps({"at": time.time(), "result": res}), encoding="utf-8")
    return res
