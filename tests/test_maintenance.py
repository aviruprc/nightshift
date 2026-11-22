"""Daily self-maintenance: nothing loops, nothing is deleted that matters, nothing unproven is ever counted."""
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as dbm  # noqa: E402
from core import maintenance as mt  # noqa: E402


@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)
    db = dbm.DB(tmp_path / "m.db")
    ids = {}
    for name in ("old_unconf", "new_unconf", "failed_old", "failed_recent", "failed_maxed"):
        ids[name], _ = db.upsert_job({"source": "t", "company": name, "title": "Analyst", "location": "Bengaluru", "url": f"https://x.test/{name}", "apply_url": f"https://x.test/{name}"})
    return db, ids


def age(db, jid, hours):
    db.c.execute("UPDATE jobs SET applied_at=? WHERE id=?", ((datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds"), jid))


def test_unproven_submissions_expire_after_48h_and_are_never_counted(d):
    db, ids = d
    for k, hrs in (("old_unconf", 60), ("new_unconf", 5)):
        db.set_status(ids[k], "unconfirmed", "submitted")
        age(db, ids[k], hrs)
    assert mt.expire_unverified(db) == 1
    assert db.rows("id=?", (ids["old_unconf"],))[0]["status"] == "unverified"
    assert db.rows("id=?", (ids["new_unconf"],))[0]["status"] == "unconfirmed"
    assert not db.rows("status='applied'")  # expiring never promotes anything


def test_failed_jobs_get_one_bounded_retry(d):
    db, ids = d
    db.set_status(ids["failed_old"], "failed", "x")
    db.set_status(ids["failed_recent"], "failed", "x")
    db.set_status(ids["failed_maxed"], "failed", "x")
    db.c.execute("UPDATE jobs SET attempts=1 WHERE id IN (?,?)", (ids["failed_old"], ids["failed_recent"]))
    db.c.execute("UPDATE jobs SET attempts=5 WHERE id=?", (ids["failed_maxed"],))
    old = (datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds")
    db.c.execute("UPDATE events SET at=? WHERE job_id IN (?,?)", (old, ids["failed_old"], ids["failed_maxed"]))
    assert mt.requeue_failed(db) == 1
    assert db.rows("id=?", (ids["failed_old"],))[0]["status"] == "candidate"
    assert db.rows("id=?", (ids["failed_recent"],))[0]["status"] == "failed"   # failed an hour ago: rests
    assert db.rows("id=?", (ids["failed_maxed"],))[0]["status"] == "failed"    # attempts exhausted: never loops


def test_rotation_trims_old_snapshots_but_keeps_post_submit_proof(tmp_path):
    (tmp_path / "failures").mkdir()
    (tmp_path / "screenshots" / "2026-10-01").mkdir(parents=True)
    old = time.time() - 40 * 86400
    files = [tmp_path / "failures" / "a.html", tmp_path / "screenshots" / "2026-10-01" / "j.png", tmp_path / "screenshots" / "2026-10-01" / "j_confirmed.png"]
    for f in files:
        f.write_text("x")
        os.utime(f, (old, old))
    (tmp_path / "activity.jsonl").write_text("\n".join(str(i) for i in range(100)) + "\n")
    out = mt.rotate(tmp_path, max_log_mb=0)
    assert out["failures_deleted"] == 1 and out["shots_deleted"] == 1 and out["log_trimmed"]
    assert files[2].exists()  # the post-submit proof screenshot is never deleted


def test_daily_run_happens_once_per_day(d, tmp_path):
    db, _ = d
    first = mt.run_daily(db, tmp_path)
    assert set(first) >= {"unverified", "requeued", "log_trimmed"}  # the first call really ran every task
    assert mt.run_daily(db, tmp_path) == {}  # the second call within 24h does nothing
