"""Dashboard metrics: the numbers the user sees must match the SQL that defines them.

These tests build their own temporary sqlite DB (they never open data/jobhunt.db) and only call the small
computation helpers in track/live.py. No server, no browser.
"""
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from track import live

ROOT = Path(__file__).resolve().parents[1]

TODAY = "2026-11-05"
YESTERDAY = "2026-11-04"

SCHEMA = """
CREATE TABLE jobs(
  id TEXT PRIMARY KEY, company TEXT, title TEXT, status TEXT, applied_at TEXT, email_proof TEXT,
  proof_shot TEXT, screenshot_path TEXT, page_proof TEXT, apply_url TEXT, url TEXT, apply_route TEXT,
  source TEXT, status_detail TEXT, needs_you_reason TEXT, score INTEGER, posted_at TEXT,
  first_seen TEXT, next_attempt_at TEXT, fail_class TEXT);
CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, job_id TEXT, kind TEXT, detail TEXT);
"""


def make_db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row  # same as track/live.state
    c.executescript(SCHEMA)
    return c


def add_job(c, jid, status, applied_at=None, proof=None, company="Acme", title="Data Analyst", **extra):
    cols = {"id": jid, "company": company, "title": title, "status": status, "applied_at": applied_at,
            "email_proof": proof, **extra}
    keys = ",".join(cols)
    marks = ",".join("?" for _ in cols)
    c.execute(f"INSERT INTO jobs({keys}) VALUES({marks})", tuple(cols.values()))


def add_event(c, jid, kind, at=f"{TODAY}T00:30:00"):
    c.execute("INSERT INTO events(at,job_id,kind,detail) VALUES(?,?,?,?)", (at, jid, kind, ""))


# ---------------------------------------------------------------- proof kinds

def test_proof_kind_matches_the_stored_prefixes():
    assert live.proof_kind(None) == "none"
    assert live.proof_kind("") == "none"
    assert live.proof_kind("Workday: your application") == "email"
    assert live.proof_kind("PAGE: confirmation page") == "page"
    assert live.proof_kind("ACCOUNT: shows in account") == "account"
    assert live.proof_kind("NAUKRI: Applied badge") == "account"


# ---------------------------------------------------------------- today's split

def test_today_separates_proven_sent_attempts_and_failed():
    c = make_db()
    add_job(c, "proven", "applied", f"{TODAY}T00:10:00", proof="PAGE: submitted")          # proven + sent
    add_job(c, "unproven", "unconfirmed", f"{TODAY}T00:20:00")                              # sent, no proof
    add_job(c, "failed", "failed")                                                          # attempted, failed
    # the 108bff41 case: an 'unconfirmed' event that was later corrected to failed
    add_job(c, "corrected", "failed")
    add_job(c, "dry", "dry_run_ok")                                                         # a dry run is not a failure
    add_job(c, "old", "applied", f"{YESTERDAY}T23:00:00", proof="Workday receipt")          # not today
    add_event(c, "failed", "failed")
    add_event(c, "corrected", "unconfirmed")
    add_event(c, "dry", "dry_run_ok")

    d = live.day_counts(c, TODAY)
    assert d["proven"] == 1
    assert d["sent"] == 2
    assert d["unproven"] == 1
    assert d["attempts"] == 5            # proven, unproven, failed, corrected, dry
    assert d["failed"] == 2              # failed + corrected; dry is excluded, sends are excluded
    assert d["_sent_ids"] == {"proven", "unproven"}
    assert d["_failed_ids"] == {"failed", "corrected"}
    # every attempt is accounted for: sent + failed + dry
    assert d["attempts"] == d["sent"] + d["failed"] + 1
    # the dashboard's total-first ordering stays honest: proven is a subset of sent, sent of attempts
    assert d["proven"] <= d["sent"] <= d["attempts"]


def test_today_attempts_include_a_send_that_has_no_event():
    c = make_db()
    add_job(c, "quiet", "applied", f"{TODAY}T01:00:00", proof="ACCOUNT: badge")
    d = live.day_counts(c, TODAY)
    assert d["attempts"] == 1 and d["sent"] == 1 and d["proven"] == 1
    assert d["failed"] == 0


def test_undated_submission_is_never_counted_into_today():
    c = make_db()
    add_job(c, "undated", "unconfirmed", None)      # older navigator bug: sent, no applied_at
    add_job(c, "today", "unconfirmed", f"{TODAY}T00:05:00")
    d = live.day_counts(c, TODAY)
    assert d["sent"] == 1 and d["unproven"] == 1
    assert live.undated_submissions(c) == 1


def test_week_rows_use_the_same_definitions_as_today():
    c = make_db()
    add_job(c, "t_proven", "applied", f"{TODAY}T00:10:00", proof="PAGE: submitted")
    add_job(c, "t_unproven", "unconfirmed", f"{TODAY}T00:20:00")
    add_job(c, "t_failed", "failed")
    add_job(c, "y_proven", "applied", f"{YESTERDAY}T10:00:00", proof="ACCOUNT: badge")
    add_event(c, "t_failed", "failed", at=f"{TODAY}T00:30:00")
    rows = live.week_rows(c, [YESTERDAY, TODAY])
    assert [r["day"] for r in rows] == [YESTERDAY, TODAY]
    assert rows[0] == {"day": YESTERDAY, "n": 1, "sent": 1, "unproven": 0, "attempts": 1, "failed": 0}
    assert rows[1] == {"day": TODAY, "n": 1, "sent": 2, "unproven": 1, "attempts": 3, "failed": 1}


def test_day_jobs_returns_that_days_sends_newest_first_with_proof_kinds():
    c = make_db()
    add_job(c, "page", "applied", f"{TODAY}T00:10:00", proof="PAGE: confirmation page", company="A")
    add_job(c, "receipt", "applied", f"{TODAY}T00:20:00", proof="Workday receipt", company="B")
    # a page was seen but no receipt/badge was recorded: still an unproven send on this day
    add_job(c, "unproven", "unconfirmed", f"{TODAY}T00:40:00", company="C", page_proof="PAGE: seen")
    add_job(c, "old", "applied", f"{YESTERDAY}T10:00:00", proof="Workday receipt", company="D")
    add_job(c, "candidate", "candidate", f"{TODAY}T00:00:00", company="E")
    rows = live.day_jobs(c, TODAY)
    assert [r["id"] for r in rows] == ["unproven", "receipt", "page"]      # newest first, sends only
    by = {r["id"]: r for r in rows}
    assert by["page"]["proof_kind"] == "page"
    assert by["receipt"]["proof_kind"] == "email"
    assert by["unproven"]["proof_kind"] == "none"                          # page_proof alone never proves a send
    assert live.day_jobs(c, "2026-01-01") == []                            # empty day: no fake row


# ---------------------------------------------------------------- recent submissions

def test_recent_submissions_are_today_only_newest_first_with_working_shots(tmp_path):
    shots = tmp_path / "screenshots"
    shots.mkdir()
    (shots / "a_confirmed.png").write_bytes(b"png")
    (shots / "b_agent.png").write_bytes(b"png")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"png")

    c = make_db()
    add_job(c, "a", "applied", f"{TODAY}T00:10:00", proof="PAGE: submitted", proof_shot=str(shots / "a_confirmed.png"))
    add_job(c, "b", "unconfirmed", f"{TODAY}T00:40:00", screenshot_path=str(shots / "b_agent.png"))
    add_job(c, "evil", "unconfirmed", f"{TODAY}T00:50:00", proof_shot=str(outside))  # outside the folder
    add_job(c, "old", "applied", f"{YESTERDAY}T10:00:00", proof="Workday receipt")

    rows = live.recent_submissions(c, TODAY)
    assert [r["id"] for r in rows] == ["evil", "b", "a"]      # newest first, today only
    live.annotate_shots(rows, shots)
    by = {r["id"]: r for r in rows}
    assert by["a"]["proof_url"] == "/proofshot/a" and by["a"]["form_url"] is None
    assert by["b"]["proof_url"] is None and by["b"]["form_url"] == "/shot/b"
    assert by["evil"]["proof_url"] is None                    # ../ escape is rejected even though the file exists
    assert by["a"]["proof_kind"] == "page" and by["b"]["proof_kind"] == "none"


def test_safe_shot_rejects_paths_outside_the_screenshot_folder(tmp_path):
    root = tmp_path / "screenshots"
    root.mkdir()
    inside = root / "ok.png"
    inside.write_bytes(b"png")
    outside = tmp_path / "nope.png"
    outside.write_bytes(b"png")
    assert live.safe_shot(str(inside), root) == inside.resolve()
    assert live.safe_shot(str(outside), root) is None
    assert live.safe_shot(str(root / ".." / "nope.png"), root) is None
    assert live.safe_shot(str(root), root) is None
    assert live.safe_shot(None, root) is None
    assert live.safe_shot(str(root / "missing.png"), root) is None


# ---------------------------------------------------------------- needs you reasons

# ---------------------------------------------------------------- ops page metrics

def test_failure_classes_and_reasons_are_counted_from_the_db():
    c = make_db()
    add_job(c, "f1", "failed", status_detail="page stopped changing", needs_you_reason=None)
    c.execute("UPDATE jobs SET fail_class='logic' WHERE id='f1'")
    add_job(c, "f2", "failed", status_detail="page stopped changing")
    c.execute("UPDATE jobs SET fail_class='logic' WHERE id='f2'")
    add_job(c, "n1", "needs_you", needs_you_reason="CAPTCHA: solve it")
    c.execute("UPDATE jobs SET fail_class='captcha' WHERE id='n1'")
    add_job(c, "b1", "blocked_portal", status_detail="portal blocked")
    c.execute("UPDATE jobs SET fail_class='transient' WHERE id='b1'")
    add_job(c, "x1", "candidate")
    c.execute("UPDATE jobs SET fail_class='logic' WHERE id='x1'")          # not a failure status: excluded
    classes = {r["cls"]: r["n"] for r in live.failure_classes(c)}
    assert classes == {"logic": 2, "captcha": 1, "transient": 1}
    reasons = {r["reason"]: r["n"] for r in live.fail_reasons(c)}
    assert reasons["page stopped changing"] == 2
    assert reasons["CAPTCHA: solve it"] == 1
    assert reasons["portal blocked"] == 1


def test_main_page_does_not_show_attempts_or_failed_metrics():
    html = (ROOT / "track" / "dashboard.html").read_text(encoding="utf-8")
    # the internal metrics are relocated to Ops, not deleted from the payload
    assert "attempts_today" not in html
    assert "failed_today" not in html
    assert ">Attempts" not in html and ">Failed" not in html
    assert "Attempts today" not in html and "Failed today" not in html
    # one obvious way to the ops page
    assert 'href="/ops"' in html
    # the compact live AI chip and the per-provider token lines live on the main page
    assert 'id="aichip"' in html
    assert "by_model_tokens" in html
    assert "ai_chip" in html


def test_main_page_shows_where_each_application_went():
    """."""
    html = (ROOT / "track" / "dashboard.html").read_text(encoding="utf-8")
    # each row in the "Sent today" list carries the platform (server `portal`, or derived from the apply URL)
    assert '<span class="src"' in html and "platformOf" in html and "sourceMix" in html
    # sources sit at the same level as the token count; both show the headline and hide the detail behind See more
    assert 'class="statpair"' in html and "tokenLines(s) + sourceLines(s)" in html
    assert 'class="toklines statbox"' in html and "srclines" in html
    #
    assert 'moreBtn("tokens"' in html and 'moreBtn("sources"' in html
    assert "data-sec=\"' + sec + '\"" in html and 'open[sec]' in html and "jh.open" in html   # choice persisted, not page state
    assert '<details class="toklines' not in html, "the stat boxes must not expand on their own: the See more button owns that state"
    # the same portal vocabulary the server uses (core.activity.portal_of); a raw hostname stays a usable label
    for word in ("Workday", "Oracle HCM", "LinkedIn", "Naukri"):
        assert word in html, word
    # the fallback reads the discovery board only when the apply URL names no known platform
    assert "j.source" in html and 'class="send"' in html


def test_run_target_is_a_slider_on_both_pages_never_two_fixed_buttons():
    """."""
    for name in ("dashboard.html", "ops.html"):
        html = (ROOT / "track" / name).read_text(encoding="utf-8")
        assert 'id="target"' in html and 'type="range"' in html, name
        assert 'min="1" max="100"' in html, name
        assert 'data-n="10"' not in html and 'data-n="100"' not in html, name
        assert 'class="slider"' in html, name
        # the chosen number is remembered and survives a re-render of the actions bar
        assert 'store.set("jh.target"' in html and "jh.target" in html, name


def test_start_run_clamps_a_bad_target_instead_of_crashing(tmp_path, monkeypatch):
    from track import live
    seen = {}
    monkeypatch.setattr(live, "STOP", tmp_path / "STOP")  # start_run clears a stale stop: never the LIVE data/STOP (KB-37)
    monkeypatch.setattr(live, "run_alive", lambda r: False)
    monkeypatch.setattr(live, "runstate", type("R", (), {"read": staticmethod(lambda: {}),
                                                          "update": staticmethod(lambda **k: seen.update(k))}))
    monkeypatch.setattr(live.dbm, "DB", lambda *a, **k: type("D", (), {})())
    monkeypatch.setattr(live, "spawn", lambda *a, **k: None)
    for bad, want in (("abc", 10), (None, 10), (-5, 1), (5000, 500), (37, 37)):
        seen.clear()
        res = live.action({"type": "start_run", "target": bad})
        assert res["ok"] and seen["target"] == want, (bad, seen)


def test_ops_page_has_the_internal_metrics_and_a_way_back():
    html = (ROOT / "track" / "ops.html").read_text(encoding="utf-8")
    assert "attempts_today" in html
    assert "failed_today" in html
    assert "fail_classes" in html and "fail_reasons" in html
    assert "guardian" in html.lower() and "llm_usage.jsonl" in html
    assert 'href="/"' in html


# ---------------------------------------------------------------- needs you reasons

def test_needs_reasons_are_counted_from_the_db_top_first():
    c = make_db()
    for i, reason in enumerate(["CAPTCHA: solve it", "CAPTCHA: solve it", "CAPTCHA: solve it", "Apply did not open", None]):
        add_job(c, f"n{i}", "needs_you", needs_you_reason=reason,
                status_detail="fallback detail" if reason is None else "")
    add_job(c, "x1", "failed", needs_you_reason="CAPTCHA: solve it")   # not needs_you: must not be counted
    rows = live.needs_reasons(c)
    assert rows[0] == {"reason": "CAPTCHA: solve it", "n": 3}
    assert rows[1] == {"reason": "Apply did not open", "n": 1}
    assert rows[2] == {"reason": "fallback detail", "n": 1}
    assert len(rows) == 3


# ---------------------------------------------------------------- health helpers

def test_tail_line_returns_last_non_empty_line(tmp_path):
    log = tmp_path / "live_run_A.log"
    log.write_text("first\n\nsecond line\n\n", encoding="utf-8")
    assert live.tail_line(log) == "second line"
    assert live.tail_line(tmp_path / "missing.log") == ""
    assert live.newest_log(tmp_path) == log


def test_usage_totals_counts_tokens_per_day_and_skips_garbage(tmp_path):
    log = tmp_path / "llm_usage.jsonl"
    ts = lambda day: datetime.fromisoformat(day + "T12:00:00").timestamp()  # noqa: E731
    log.write_text("\n".join([
        json.dumps({"t": ts(TODAY), "i": 100, "o": 50, "m": "haiku"}),
        json.dumps({"t": ts(TODAY), "i": 10, "o": 5, "m": "sonnet"}),
        json.dumps({"t": ts(YESTERDAY), "i": 7, "o": 3, "m": "haiku"}),
        "not json at all",
        json.dumps({"t": 1, "i": 999, "o": 999}),          # outside the window: not attributed to a day
    ]), encoding="utf-8")
    days = [YESTERDAY, TODAY]
    per_day, totals = live.usage_totals(log, days, ts(TODAY), ts(YESTERDAY))
    assert per_day[TODAY] == {"tokens": 165, "calls": 2}
    assert per_day[YESTERDAY] == {"tokens": 10, "calls": 1}
    assert totals == {"today": 165, "month": 175, "calls_today": 2}
    # a missing log is unknown, not invented
    empty, zero = live.usage_totals(tmp_path / "missing.jsonl", days, ts(TODAY), ts(YESTERDAY))
    assert empty == {YESTERDAY: {"tokens": 0, "calls": 0}, TODAY: {"tokens": 0, "calls": 0}}
    assert zero == {"today": 0, "month": 0, "calls_today": 0}


def test_usage_breakdown_sums_tokens_by_model_and_purpose(tmp_path):
    log = tmp_path / "llm_usage.jsonl"
    ts = lambda day: datetime.fromisoformat(day + "T12:00:00").timestamp()  # noqa: E731
    rows = [
        {"t": ts(TODAY), "m": "deepseek-api", "p": "score", "i": 100, "o": 50},
        {"t": ts(TODAY), "m": "deepseek-api", "p": "tailor", "i": 10, "o": 5},
        {"t": ts(TODAY), "m": "gemini", "p": "score", "i": 7, "o": 3},
        {"t": ts(YESTERDAY), "m": "deepseek-api", "p": "score", "i": 200, "o": 100},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json", encoding="utf-8")
    by_model, by_purpose = live.usage_breakdown(log, ts(TODAY), ts(YESTERDAY))
    assert by_model["deepseek-api"]["today"] == {"tokens": 165, "calls": 2}   # input+output
    assert by_model["deepseek-api"]["month"] == {"tokens": 465, "calls": 3}
    assert by_model["gemini"]["today"] == {"tokens": 10, "calls": 1}
    assert by_purpose["score"]["today"] == {"tokens": 160, "calls": 2}
    assert by_purpose["tailor"]["today"] == {"tokens": 15, "calls": 1}
    empty_model, empty_purpose = live.usage_breakdown(tmp_path / "missing.jsonl", ts(TODAY), ts(YESTERDAY))
    assert empty_model == {} and empty_purpose == {}


def test_ai_chip_is_built_from_live_provider_states_with_retry_minutes():
    ai = {"ready": ["deepseek-api"], "any": True, "claude_ok": True, "claude_reserved": False,
          "providers": [
              {"name": "deepseek-api", "model": "deepseek-chat", "type": "openai", "state": "ready", "why": ""},
              {"name": "gemini", "model": "gemini-flash", "type": "gemini", "state": "resting",
               "why": "daily free quota used up (resets once a day) (retry in 75 min)"}]}
    chip = live.ai_chip(ai)
    assert chip["any"] is True and chip["cls"] == "ok"
    assert chip["text"] == "AI: deepseek-api ready"
    assert chip["down"] == [{"name": "gemini", "state": "resting",
                             "why": ai["providers"][1]["why"], "minutes": 75}]
    dead = {"ready": [], "any": False, "claude_ok": False, "providers": [
        {"name": "groq", "state": "nokey", "why": "no API key yet", "model": "llama", "type": "openai"}]}
    c2 = live.ai_chip(dead)
    assert c2["cls"] == "bad" and c2["text"] == "AI: none available"
    assert c2["down"][0]["state"] == "no key" and c2["down"][0]["minutes"] is None
    c3 = live.ai_chip({"ready": [], "any": True, "claude_ok": True, "providers": []})
    assert c3["cls"] == "ok" and c3["text"] == "AI: Claude only"


def test_iso_age_and_definitions():
    assert live.iso_age(None) is None and live.iso_age("not a date") is None
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    assert live.iso_age(now) is not None and live.iso_age(now) < 120
    # every number the dashboard shows has a definition it renders as a tooltip
    for key in ("proven_today", "sent_today", "unproven_today", "attempts_today", "failed_today",
                "proven_total", "sent_total", "proven_week", "awaiting", "waiting", "queue",
                "retrying", "success_rate_today", "proven_rate_today",
                "tokens_today", "tokens_week", "tokens_day", "fail_classes"):
        assert live.DEFS[key].strip(), key


def test_state_includes_the_tracker_and_does_not_read_a_closed_connection(tmp_path, monkeypatch):
    from core import db as dbm
    from track import live
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)
    dbm.DB(tmp_path / "t.db").c.commit()                  # an empty schema: the test never reads the real database
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "t.db")
    assert "tracker" in live.state()


def test_a_marked_done_assessment_leaves_the_to_do_list_until_the_employer_writes_again(tmp_path, monkeypatch):
    import sqlite3, time
    from track import live
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE jobs(id TEXT, company TEXT, title TEXT, status TEXT, applied_at TEXT, apply_url TEXT, url TEXT, outcome TEXT)")
    c.execute("CREATE TABLE replies(msgkey TEXT, job_id TEXT, company TEXT, kind TEXT, subject TEXT, ts REAL, received_at TEXT, next_step TEXT, deadline TEXT, link TEXT)")
    c.execute("CREATE TABLE tracker_done(key TEXT PRIMARY KEY, done_ts REAL, note TEXT)")
    now = time.time()
    c.execute("INSERT INTO replies VALUES('k',NULL,'Synchrony','assessment','Do Select test',?,'2026-11-09T10:00:00','Complete the assessment','2099-01-01','')", (now - 1000,))
    assert live.tracker_block(c)["counts"]["assessment"] == 1
    c.execute("INSERT INTO tracker_done VALUES('co:synchrony',?,'')", (now,))
    assert live.tracker_block(c)["counts"]["assessment"] == 0
    c.execute("INSERT INTO replies VALUES('k2',NULL,'Synchrony','assessment','Reminder',?,'2026-11-11T10:00:00','Complete the assessment','2099-01-01','')", (now + 500,))
    assert live.tracker_block(c)["counts"]["assessment"] == 1
