"""SQLite store. Statuses: new, skipped, planned, applied, needs_you, unconfirmed, failed, closed."""
import hashlib
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobhunt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, fingerprint TEXT, source TEXT, ats TEXT, company TEXT, title TEXT, location TEXT,
  url TEXT, apply_url TEXT, description TEXT, posted_at TEXT, family TEXT,
  score INTEGER, score_reason TEXT, ghost_flags TEXT,
  status TEXT DEFAULT 'new', status_detail TEXT, needs_you_reason TEXT,
  resume_path TEXT, screenshot_path TEXT, ats_coverage REAL,
  first_seen TEXT, last_seen TEXT, applied_at TEXT, attempts INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_fp ON jobs(fingerprint);
CREATE INDEX IF NOT EXISTS ix_status ON jobs(status);
CREATE TABLE IF NOT EXISTS sightings(job_id TEXT, seen_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS accounts(site TEXT PRIMARY KEY, username TEXT, created_at TEXT, verified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS questions(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, text TEXT, answer TEXT, asked_at TEXT, answered_at TEXT);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, job_id TEXT, kind TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS replies(msgkey TEXT PRIMARY KEY, job_id TEXT, company TEXT, kind TEXT, subject TEXT, sender TEXT, ts REAL, received_at TEXT, snippet TEXT, score INTEGER, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_replies_job ON replies(job_id);
CREATE TABLE IF NOT EXISTS tracker_done(key TEXT PRIMARY KEY, done_ts REAL, note TEXT);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", "", (s or "").lower())).strip()


def job_id(url: str) -> str:
    u = re.sub(r"[?#].*$", "", (url or "").lower().rstrip("/"))
    return hashlib.sha1(u.encode()).hexdigest()[:16]


def fingerprint(company: str, title: str, location: str) -> str:
    city = "bengaluru" if re.search(r"bengaluru|bangalore|bangaluru", (location or "").lower()) else norm(location)[:20]
    return hashlib.sha1(f"{norm(company)}|{norm(title)}|{city}".encode()).hexdigest()[:16]


class DB:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        # autocommit: every statement commits immediately, so a slow web page never holds a write lock against the other appliers
        self.c = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        for pragma in ("PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL", "PRAGMA busy_timeout=15000"):  # crash-safe, multi-process friendly
            try:
                self.c.execute(pragma)
            except sqlite3.OperationalError:
                pass
        self.c.executescript(SCHEMA)
        for col in ("apply_route TEXT", "applicants INTEGER", "email_proof TEXT", "email_proof_at TEXT", "proof_shot TEXT",
                    "page_proof TEXT", "next_attempt_at TEXT", "fail_class TEXT", "outcome TEXT", "outcome_at TEXT"):  # lightweight migration for existing DBs
            try:
                self.c.execute(f"ALTER TABLE jobs ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        for col in ("next_step TEXT", "deadline TEXT", "link TEXT"):   # employer replies: what to do next, by when, and where
            try:
                self.c.execute(f"ALTER TABLE replies ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass

    def event(self, jid: str, kind: str, detail: str = ""):
        self.c.execute("INSERT INTO events(at,job_id,kind,detail) VALUES(?,?,?,?)", (now(), jid, kind, detail))
        self.c.commit()

    def upsert_job(self, j: dict) -> tuple[str, bool]:
        """Insert or refresh a sighting. Returns (id, is_new)."""
        jid = job_id(j.get("apply_url") or j["url"])
        fp = fingerprint(j["company"], j["title"], j.get("location", ""))
        t = now()
        row = self.c.execute("SELECT id FROM jobs WHERE id=?", (jid,)).fetchone()
        self.c.execute("INSERT INTO sightings VALUES(?,?,?)", (jid, t, j.get("source", "")))
        if row:
            self.c.execute("UPDATE jobs SET last_seen=? WHERE id=?", (t, jid))
            self.c.commit()
            return jid, False
        self.c.execute(
            "INSERT INTO jobs(id,fingerprint,source,ats,company,title,location,url,apply_url,description,posted_at,"
            "first_seen,last_seen,apply_route,applicants) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, fp, j.get("source"), j.get("ats"), j["company"], j["title"], j.get("location"), j["url"],
             j.get("apply_url") or j["url"], (j.get("description") or "")[:20000], j.get("posted_at"), t, t,
             "easy_apply" if j.get("easy_apply") else None, j.get("applicants")))
        self.c.commit()
        return jid, True

    def same_role_sent(self, company: str, title: str, location: str = "", exclude_id: str = ""):
        """(id, status) of ANOTHER row with the same fingerprint that is already applied/unconfirmed, else None.

        a company: the queue is built once, so `already_applied` at build time could not see R800105 becoming
        'unconfirmed' at — R800100 (same company, title and city) was applied 2 minutes later. Checked again per row."""
        r = self.c.execute("SELECT id, status FROM jobs WHERE fingerprint=? AND id<>? AND status IN ('applied','unconfirmed') "
                           "ORDER BY applied_at LIMIT 1", (fingerprint(company, title, location), exclude_id)).fetchone()
        return (r["id"], r["status"]) if r else None

    def already_applied(self, company: str, title: str, location: str = "") -> bool:
        fp = fingerprint(company, title, location)
        r = self.c.execute("SELECT 1 FROM jobs WHERE fingerprint=? AND status IN ('applied','unconfirmed')", (fp,)).fetchone()
        return r is not None

    def repost_count(self, company: str, title: str, location: str = "", days: int = 60) -> int:
        since = (datetime.now() - timedelta(days=days)).isoformat()
        r = self.c.execute("SELECT COUNT(DISTINCT id) n FROM jobs WHERE fingerprint=? AND first_seen>=?",
                           (fingerprint(company, title, location), since)).fetchone()
        return r["n"]

    def expire_stale(self, days: int = 3) -> int:
        """Candidates not re-seen by a fresh discovery within `days` are stale: re-verify, never apply blindly."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        n = self.c.execute("UPDATE jobs SET status='stale', status_detail='not re-seen in discovery'"
                           " WHERE status='candidate' AND last_seen<?", (cutoff,)).rowcount
        self.c.commit()
        return n

    def set_status(self, jid: str, status: str, detail: str = "", **extra):
        cols = ["status=?", "status_detail=?"]
        vals = [status, detail]
        for k, v in extra.items():
            cols.append(f"{k}=?")
            vals.append(v)
        self.c.execute(f"UPDATE jobs SET {','.join(cols)} WHERE id=?", (*vals, jid))
        self.c.commit()
        self.event(jid, status, detail)
        self._ledger(jid, status, detail)

    def _ledger(self, jid: str, status: str, detail: str):
        """Append-only plain-text history (data/applications.jsonl): a second source of truth that survives a corrupted database."""
        if status not in ("applied", "needs_you", "unconfirmed", "failed", "closed", "skipped", "deferred_easy", "deferred_board"):
            return
        r = self.c.execute("SELECT company,title,location,source,apply_url,url FROM jobs WHERE id=?", (jid,)).fetchone()
        if not r:
            return
        import json
        line = {"at": now(), "id": jid, "status": status, "detail": detail, **{k: r[k] for k in r.keys()}}
        with open(DB_PATH.parent / "applications.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def rows(self, where: str = "1=1", params: tuple = ()):
        return [dict(r) for r in self.c.execute(f"SELECT * FROM jobs WHERE {where}", params)]
