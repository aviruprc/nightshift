"""Persistent history: applications and token calls must still be readable later, after logs are trimmed and
old snapshots deleted, and even if the database is rebuilt. Uses a temp dir + in-memory DB; never the live data."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import history  # noqa: E402

SCHEMA = """CREATE TABLE jobs(id TEXT PRIMARY KEY, company TEXT, title TEXT, status TEXT, applied_at TEXT, email_proof TEXT,
apply_url TEXT, url TEXT)"""


class FakeDB:
    def __init__(self, rows):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.execute(SCHEMA)
        for r in rows:
            self.c.execute("INSERT INTO jobs VALUES(:id,:company,:title,:status,:applied_at,:email_proof,:apply_url,:url)", r)

    def rows(self, where, params=()):
        sql = "SELECT * FROM jobs"
        if where:
            # the code passes real SQL fragments ('date(applied_at) = ?'), which sqlite evaluates here exactly as it would live
            sql += (" WHERE " if not where.upper().lstrip().startswith("WHERE") else " ") + where
        return [dict(x) for x in self.c.execute(sql, params)]


def _day(offset=0):
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")


def _db():
    return FakeDB([
        {"id": "a1", "company": "Thermo Fisher", "title": "Data Analyst", "status": "applied", "applied_at": f"{_day()}T00:56:00",
         "email_proof": "R-01369315 thermofisher: Thank you", "apply_url": "https://x/1", "url": None},
        {"id": "a2", "company": "Thermo Fisher", "title": "Assoc Finance Systems Analyst", "status": "applied",
         "applied_at": f"{_day()}T00:52:00", "email_proof": "R-01369783 thermofisher: Thank you", "apply_url": "https://x/2", "url": None},
        {"id": "b1", "company": "Cisco", "title": "Finance Analyst", "status": "failed", "applied_at": None, "email_proof": None,
         "apply_url": "https://x/3", "url": None},
    ])


def _usage(tmp, rows):
    (tmp / "llm_usage.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _ts(day, hh=1):
    from datetime import datetime
    return datetime.strptime(f"{day} {hh:02d}:00:00", "%Y-%m-%d %H:%M:%S").timestamp()


def test_a_days_record_holds_the_applications_and_the_token_calls(tmp_path):
    from datetime import datetime
    _usage(tmp_path, [{"t": _ts(_day()), "m": "deepseek-api", "i": 1800, "o": 200, "p": "navigating"},
                      {"t": _ts(_day()), "m": "gemini", "i": 900, "o": 100, "p": "score"},
                      {"t": _ts(_day(), 2), "m": "deepseek-api", "i": 1000, "o": 0, "p": "navigating"}])
    rec = history.day_record(_db(), tmp_path)
    assert rec["day"] == datetime.now().strftime("%Y-%m-%d") and rec["sends"] == 2 and rec["proven"] == 2
    ids = [a["id"] for a in rec["applications"]]
    assert ids == ["a2", "a1"]  # newest last, chronological
    assert rec["applications"][1]["proof"].startswith("R-01369315")
    assert rec["tokens"]["deepseek-api"] == {"tokens": 3000, "calls": 2}
    assert rec["tokens"]["gemini"] == {"tokens": 1000, "calls": 1}
    assert "b1" not in [a["id"] for a in rec["applications"]]  # a failure is not an application


def test_the_same_day_is_rewritten_not_duplicated_and_survives_without_the_db(tmp_path):
    _usage(tmp_path, [{"t": _ts(_day()), "m": "deepseek-api", "i": 10, "o": 10, "p": "x"}])
    f = history.write_daily(_db(), tmp_path)
    history.write_daily(_db(), tmp_path)  # e.g. maintenance runs twice
    lines = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1 and lines[0]["sends"] == 2
    # the record is plain JSON on disk: readable with the database gone
    assert json.loads(f.read_text(encoding="utf-8").splitlines()[0])["applications"][0]["company"] == "Thermo Fisher"


def test_a_receipt_that_arrives_after_midnight_corrects_yesterdays_line(tmp_path, monkeypatch):
    days = [_day(1)]
    db = FakeDB([{"id": "y1", "company": "Northern Trust", "title": "Business Analyst", "status": "unconfirmed",
                  "applied_at": f"{days[0]}T23:50:00", "email_proof": None, "apply_url": "u", "url": None}])
    history.write_daily(db, tmp_path, days[0])
    f = tmp_path / "history" / "daily.jsonl"
    assert json.loads(f.read_text(encoding="utf-8").splitlines()[0])["proven"] == 0
    db.c.execute("UPDATE jobs SET status='applied', email_proof='ntrs: Your Northern Trust Application' WHERE id='y1'")
    history.keep(db, tmp_path)  # keep re-reads yesterday for exactly this reason
    recs = {json.loads(x)["day"]: json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()}
    assert recs[days[0]]["proven"] == 1 and recs[days[0]]["applications"][0]["proof"].startswith("ntrs:")


def test_the_month_file_lists_every_application_and_dedupes(tmp_path):
    from datetime import datetime
    month = datetime.now().strftime("%Y-%m")
    _usage(tmp_path, [{"t": _ts(_day()), "m": "groq", "i": 5, "o": 5, "p": "x"}])
    f = history.write_month(_db(), tmp_path)
    history.write_month(_db(), tmp_path)
    rec = json.loads(f.read_text(encoding="utf-8").splitlines()[0])
    assert rec["month"] == month and rec["sends"] == 2 and [a["id"] for a in rec["applications"]] == ["a2", "a1"]
    assert rec["tokens"]["groq"]["tokens"] == 10


def test_a_half_written_usage_line_never_costs_the_history(tmp_path):
    f = tmp_path / "llm_usage.jsonl"
    f.write_text(json.dumps({"t": _ts(_day()), "m": "deepseek-api", "i": 7, "o": 3, "p": "x"}) + "\n{\"t\": 12", encoding="utf-8")
    rec = history.day_record(_db(), tmp_path)
    assert rec["tokens"]["deepseek-api"]["tokens"] == 10
