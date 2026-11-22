"""Persistent LLM answer + navigator-recipe cache (SQLite, same DB file). Goal: never pay the LLM twice for the same question.

Question keys are NORMALISED: company names -> <co>, 'current value ... was rejected' hints, digits, punctuation and option order are stripped,
so "Were you ever employed by a company?" and "...by a company?" share one answer. Motivational / free-text answers are kept per company+job only.
"""
import hashlib
import json
import re
import sqlite3
import time

from core import db as dbm

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers_cache(
  qkey TEXT, opts TEXT, scope TEXT, answer TEXT, source TEXT, uses INTEGER DEFAULT 0, created TEXT, last_used TEXT,
  PRIMARY KEY(qkey, opts, scope));
CREATE TABLE IF NOT EXISTS portal_memory(
  host TEXT, outcome TEXT, reason TEXT, n INTEGER DEFAULT 1, last_at TEXT, PRIMARY KEY(host, outcome, reason));
CREATE TABLE IF NOT EXISTS portal_day(
  host TEXT, day TEXT, ok INTEGER DEFAULT 0, fail INTEGER DEFAULT 0, PRIMARY KEY(host, day));
CREATE TABLE IF NOT EXISTS claims(
  key TEXT PRIMARY KEY, worker TEXT, at REAL);
CREATE TABLE IF NOT EXISTS recipes(
  host TEXT, sig TEXT, actions TEXT, ok INTEGER DEFAULT 0, fail INTEGER DEFAULT 0, last_used TEXT,
  PRIMARY KEY(host, sig));
"""
MOTIV = re.compile(r"why|motivat|interest(ed)? in|tell us about|about yourself|cover letter|what (excites|attracts)|describe|explain|comments?", re.I)


def _db() -> sqlite3.Connection:
    c = sqlite3.connect(dbm.DB_PATH, timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def norm_q(q: str, company: str = "") -> str:
    s = (q or "").lower()
    s = re.sub(r"\((current value|was rejected|tick this box)[^)]*\)", " ", s)
    s = re.sub(r"current value '[^']*' was rejected:?[^)]*", " ", s)
    for w in re.findall(r"[a-z0-9&]{3,}", (company or "").lower()):
        if w not in ("the", "and", "india", "pvt", "ltd", "limited", "private", "group", "services"):
            s = re.sub(rf"\b{re.escape(w)}\b", "<co>", s)
    s = re.sub(r"(<co>\s*)+", "<co> ", s)  # multi-word company names collapse to ONE token
    s = re.sub(r"[\d]+", "#", s)
    s = re.sub(r"[^a-z<>#]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:200]


def _opts_key(options) -> str:
    if not options:
        return ""
    return hashlib.sha1("|".join(sorted(re.sub(r"\W+", "", str(o).lower()) for o in options)).encode()).hexdigest()[:12]


def _scope(q: str, company: str, job_id: str) -> str:
    return f"job:{job_id}" if MOTIV.search(q or "") and job_id else "global"


def _contextual(q: str) -> bool:
    """Retry corrections ('current value X was rejected') depend on the page state, not the question: never cached or reused."""
    return bool(re.search(r"was rejected|tick this box", q or "", re.I))


#  audit of what the cache had "learned":
#  - keys that are not questions at all: 'yes' => 'No', 'type your response' => her name, 'textbox', 'company' => 'Loop Methods',
#    'job title' => one posting's title, 'year' => 2001. Such a key matches unrelated fields on other sites.
#  - a model's GUESS at skill-years ('years of experience in surveillance' => 4, 'in bi tools' => 2, 'program coordinator' => 4): these are facts about
#    her that only data/answers.yaml may supply; a remembered guess is repeated on every later form.
# Neither is stored or served any more.
NOT_A_QUESTION = re.compile(r"^(yes|no|type your response|textbox|text|company|job title|role applied|month|day|year|must haves|fluent|foundational|"
                            r"other|select|select one|please select|answer|response|comments?|details|description|[#\s]+|qa #)$")
SKILL_YEARS = re.compile(r"(how many )?years (of )?(solid |relevant |total )?(work(ing)? )?experience .*\b(in|as|with|on)\b .+|"
                         r"experience (in|with) .+ \(?in years\)?$")


def cacheable(q: str, company: str = "") -> bool:
    k = norm_q(q, company)
    return bool(k) and not NOT_A_QUESTION.match(k) and not SKILL_YEARS.search(k)


def get(q: str, options, company: str = "", job_id: str = ""):
    """Cached answer or None. If options are given the cached answer must still be one of them (re-checked by the caller)."""
    if _contextual(q) or not cacheable(q, company):
        return None
    c = _db()
    try:
        row = c.execute("SELECT answer FROM answers_cache WHERE qkey=? AND opts=? AND scope=?",
                        (norm_q(q, company), _opts_key(options), _scope(q, company, job_id))).fetchone()
        if not row:
            return None
        c.execute("UPDATE answers_cache SET uses=uses+1, last_used=? WHERE qkey=? AND opts=? AND scope=?",
                  (dbm.now(), norm_q(q, company), _opts_key(options), _scope(q, company, job_id)))
        ans = row["answer"]
        if options and ans not in options:
            return None
        return ans
    finally:
        c.close()


def put(q: str, options, answer: str, company: str = "", job_id: str = "", source: str = "llm"):
    if not answer or _contextual(q) or not cacheable(q, company) or re.match(r"\s*(skip|n/?a|none|null|not applicable|prefer not|unknown)", str(answer), re.I):
        return  # a non-answer must never become a remembered answer
    c = _db()
    try:
        c.execute("INSERT OR REPLACE INTO answers_cache(qkey,opts,scope,answer,source,uses,created,last_used) VALUES(?,?,?,?,?,COALESCE((SELECT uses FROM answers_cache "
                  "WHERE qkey=? AND opts=? AND scope=?),0),?,?)",
                  (norm_q(q, company), _opts_key(options), _scope(q, company, job_id), str(answer), source,
                   norm_q(q, company), _opts_key(options), _scope(q, company, job_id), dbm.now(), dbm.now()))
    finally:
        c.close()


def stats() -> dict:
    c = _db()
    try:
        n, uses = c.execute("SELECT COUNT(*), COALESCE(SUM(uses),0) FROM answers_cache").fetchone()
        return {"answers": n, "reused": uses}
    finally:
        c.close()


# ---- navigator recipes: successful action plans per (host, page signature), replayed with NO LLM call ----
def page_sig(snap: dict) -> str:
    """Stable signature of a page: the sorted visible control labels (not values, not ids)."""
    labels = sorted({re.sub(r"[^a-z]+", " ", (e.get("label") or e.get("text") or "").lower()).strip()[:40] for e in snap.get("els", [])} - {""})
    return hashlib.sha1("|".join(labels[:60]).encode()).hexdigest()[:16]


def recipe_get(host: str, sig: str):
    c = _db()
    try:
        row = c.execute("SELECT actions, ok, fail FROM recipes WHERE host=? AND sig=?", (host, sig)).fetchone()
        if row and row["ok"] > row["fail"]:
            return json.loads(row["actions"])
        return None
    finally:
        c.close()


def recipe_forget(host: str, text: str) -> int:
    """Delete this host's remembered plans that click a control with this text (a route that led somewhere wrong)."""
    c = _db()
    try:
        n = c.execute("DELETE FROM recipes WHERE host=? AND actions LIKE ?", (host, f'%"text": "{text}"%')).rowcount
        return n
    finally:
        c.close()


def recipe_put(host: str, sig: str, actions: list, ok: bool):
    c = _db()
    try:
        row = c.execute("SELECT 1 FROM recipes WHERE host=? AND sig=?", (host, sig)).fetchone()
        if not row:
            c.execute("INSERT INTO recipes(host,sig,actions,ok,fail,last_used) VALUES(?,?,?,?,?,?)",
                      (host, sig, json.dumps(actions), 1 if ok else 0, 0 if ok else 1, dbm.now()))
        elif ok:
            c.execute("UPDATE recipes SET actions=?, ok=ok+1, last_used=? WHERE host=? AND sig=?", (json.dumps(actions), dbm.now(), host, sig))
        else:
            c.execute("UPDATE recipes SET fail=fail+1, last_used=? WHERE host=? AND sig=?", (dbm.now(), host, sig))
    finally:
        c.close()


# ---- portal memory: what has worked / failed on each host, fed back into the navigator prompt ----
def _reason_key(reason: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+|https?://\S+|'[^']{0,60}'", "#", (reason or "").lower()))[:110]


SHARED_ATS = re.compile(r"(^|\.)(lever\.co|greenhouse\.io|smartrecruiters\.com|ashbyhq\.com|workable\.com|darwinbox\.(in|com)|keka\.com|"
                        r"applytojob\.com|recruitee\.com|bamboohr\.com|zohorecruit\.(in|com))$")


# with no AI available for most of the day, every "navigator: LLM unavailable" was counted as a failure of the PORTAL: a company's Workday site
# reached 189 failures, a company and CareSource were switched off for the day with 0 real attempts. An AI outage says nothing about the portal.
AI_OUTAGE = re.compile(r"LLM unavailable|LLMUnavailable|no AI (?:could )?answer|AI providers unavailable|usage limit", re.I)


def ai_outage(reason: str) -> bool:
    return bool(AI_OUTAGE.search(reason or ""))


def breaker_key(url: str) -> str:
    """What the daily circuit breaker counts against. A company's own host, but on an ATS that hosts MANY companies (jobs.lever.co/<company>,
    job-boards.greenhouse.io/<company>) the company is part of the key.: three failures at three different companies switched off
    every company on Greenhouse, Lever and SmartRecruiters for the rest of the day (26 hosts were blocked that evening)."""
    from urllib.parse import urlparse
    u = urlparse(url if "://" in (url or "") else "https://" + (url or ""))
    host = u.netloc.lower()
    if SHARED_ATS.search(host):
        seg = [x for x in u.path.split("/") if x]
        if seg:
            return f"{host}/{seg[0].lower()}"
    return host


def remember(host: str, ok: bool, reason: str = "", day_key: str | None = None, count_day: bool = True):
    """Record one outcome for a host (success, or the reason it failed). `day_key` is what the circuit breaker counts (see breaker_key);
    `count_day=False` keeps an outcome that says nothing about the portal (a closed posting, a parked question) out of the breaker."""
    c = _db()
    try:
        if count_day:
            c.execute("INSERT INTO portal_day(host,day,ok,fail) VALUES(?,?,?,?) ON CONFLICT(host,day) DO UPDATE SET ok=ok+?, fail=fail+?",
                      (day_key or host, dbm.now()[:10], int(ok), int(not ok), int(ok), int(not ok)))
        outcome, rk = ("ok", "") if ok else ("fail", _reason_key(reason))
        c.execute("INSERT INTO portal_memory(host,outcome,reason,n,last_at) VALUES(?,?,?,1,?) ON CONFLICT(host,outcome,reason) DO UPDATE SET n=n+1, last_at=?",
                  (host, outcome, rk, dbm.now(), dbm.now()))
    finally:
        c.close()


def hints(host: str) -> str:
    """Short history for the navigator prompt: how often this portal worked, and what went wrong before (so it avoids repeating it)."""
    c = _db()
    try:
        ok = c.execute("SELECT COALESCE(SUM(n),0) FROM portal_memory WHERE host=? AND outcome='ok'", (host,)).fetchone()[0]
        fails = c.execute("SELECT reason, n FROM portal_memory WHERE host=? AND outcome='fail' ORDER BY n DESC, last_at DESC LIMIT 3", (host,)).fetchall()
        if not ok and not fails:
            return "no history on this portal yet"
        txt = f"{ok} earlier success(es) here."
        if fails:
            txt += " Earlier attempts failed because: " + "; ".join(f"{r['reason']} (x{r['n']})" for r in fails) + ". Avoid repeating that."
        return txt
    finally:
        c.close()


def host_blocked(host: str, limit: int = 3) -> bool:
    """Circuit breaker: a portal that failed `limit`+ times TODAY without a single success is skipped for the rest of the day (saves tokens and attempts)."""
    c = _db()
    try:
        r = c.execute("SELECT ok, fail FROM portal_day WHERE host=? AND day=?", (host, dbm.now()[:10])).fetchone()
        return bool(r and r["ok"] == 0 and r["fail"] >= limit)
    finally:
        c.close()


def claim(key: str, worker: str, ttl: int = 1200) -> bool:
    """Parallel workers: claim a company (or any key) before working on it. True if this worker owns it now (it already did, or the
    previous owner's claim expired). Atomic: the UPDATE only succeeds when the row is free, ours, or stale."""
    key = (key or "").strip().lower()
    now = time.time()
    c = _db()
    try:
        c.execute("INSERT INTO claims(key,worker,at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET worker=excluded.worker, at=excluded.at "
                  "WHERE claims.worker=excluded.worker OR claims.at < ?", (key, worker, now, now - ttl))
        return c.execute("SELECT worker FROM claims WHERE key=?", (key,)).fetchone()["worker"] == worker
    finally:
        c.close()
