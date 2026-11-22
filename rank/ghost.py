"""Ghost / stale posting detection. Returns list of flag strings; any 'hard' flag means skip."""
import re
from datetime import datetime, timezone

EVERGREEN = re.compile(r"talent (pool|community|network)|future (opportunit|openings)|evergreen|always hiring|"
                       r"general application|expression of interest|pipeline (of|for) (future|upcoming)|"
                       r"we are not currently hiring|register your interest", re.I)
MULTI_CLIENT = re.compile(r"our (esteemed )?clients?|for (one of )?(our|a) (leading|reputed|top)|confidential (client|client)|"
                          r"client name (will|is) (be )?(disclosed|confidential)|multiple (openings|positions) (with|for) (our )?clients?", re.I)
# "repost" is NOT hard any more. It fired when one company+title+city had 3 listings in
# 60 days - but the same real job arrives from several SOURCES (LinkedIn + the company site + Naukri), so 457 live jobs were retired as ghosts
# (a company "Data Analyst", posted the day before). Duplicates are already handled at apply time by the fingerprint check.
HARD = {"stale", "evergreen", "multi_client"}
BOARD_MAX_AGE, ATS_MAX_AGE = 30, 45   # a company career site lists a posting only while it is open ("Posted 30+ Days Ago" is normal on Workday)


def parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00")) if fmt is None else datetime.strptime(s, fmt)
            # A timestamp with no offset is the machine's LOCAL clock: dbm.now writes datetime.now. Treating it as UTC
            # made every row discovered in the last 5.5 h (IST +) look FUTURE-dated (KB-75).
            return d if d.tzinfo else d.astimezone()
        except ValueError:
            continue
    return None


def age_days(posted_at, first_seen=None):
    d = parse_dt(posted_at) or parse_dt(first_seen)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400)


def age_or(posted_at, first_seen=None, default=99.0):
    """Age in days, or `default` ONLY when unknown. Never use `age_days(...) or default`: 0.0 is falsy, so a job found
    today was read as 99 days old and dropped by fresh - the queue then ran empty while fresh jobs sat in the DB (KB-74)."""
    a = age_days(posted_at, first_seen)
    return default if a is None else a


def flags(job: dict, repost_count: int = 1, max_age: int | None = None):
    f = []
    a = age_days(job.get("posted_at"), job.get("first_seen"))
    if max_age is None:
        max_age = ATS_MAX_AGE if str(job.get("source") or "").startswith("ats:") else BOARD_MAX_AGE
    if a is not None and a > max_age:
        f.append("stale")
    if repost_count >= 3:
        f.append("repost")
    text = f"{job['title']} {(job.get('description') or '')[:2500]}"
    if EVERGREEN.search(text):
        f.append("evergreen")
    if job.get("ats") == "board" and MULTI_CLIENT.search(text):
        f.append("multi_client")
    if a is None:
        f.append("undated")  # soft flag
    return f


def is_ghost(fl: list[str]) -> bool:
    return any(x in HARD for x in fl)
