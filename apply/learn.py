"""Learning from outcomes.

A multi-armed bandit over PORTALS (Naukri, Workday, Greenhouse, each company's own site...). Every attempt is a pull:
reward 1 when the application was sent (applied / unconfirmed), 0 when it failed, was parked or hit a CAPTCHA.
The value of a portal is UCB1: its observed success rate plus a bonus that is large while the portal has been tried
little. So an untried portal is explored first, a portal that keeps failing sinks to the back of the queue by itself,
and it is still re-tried now and then (after a fix it climbs back without anyone editing a list).

Deterministic (no random numbers): the same database gives the same order, which keeps the tests stable.
The second output is a FIX LIST: portals ranked by how many jobs a fix would release. Any model can read
`python hunt.py learn` and know what to repair next.
"""
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "learn.json"
WINDOW_DAYS = 14
WIN = ("applied", "unconfirmed")
LOSS = ("failed", "needs_you", "deferred_captcha")
EXPLORE = 0.35         # weight of the exploration bonus
DIRECT_BONUS = 0.2     # than LinkedIn / Naukri / other boards
BOARDS = {"naukri", "linkedin", "shine", "foundit", "apna", "instahyre", "hirist", "indeed"}
NEW_COMPANY_BONUS = 0.25   # a company never applied to goes ahead of one already covered (her complaint: "the same ones are repeated")

_HOSTS = (("naukri.com", "naukri"), ("linkedin.com", "linkedin"), ("myworkdayjobs.com", "workday"), ("myworkdaysite.com", "workday"),
          ("oraclecloud.com", "oracle_hcm"), ("successfactors", "successfactors"), ("sapsf.", "successfactors"), ("greenhouse.io", "greenhouse"),
          ("lever.co", "lever"), ("ashbyhq.com", "ashby"), ("smartrecruiters.com", "smartrecruiters"), ("eightfold.ai", "eightfold"),
          ("icims.com", "icims"), ("taleo.net", "taleo"), ("shine.com", "shine"), ("foundit.in", "foundit"), ("apna.co", "apna"),
          ("instahyre.com", "instahyre"), ("hirist.", "hirist"), ("indeed.com", "indeed"))


def _g(r, k):
    try:
        return r[k]
    except (KeyError, IndexError, TypeError):
        return None


def portal(r) -> str:
    """The arm a job belongs to: a known ATS/board family, else the host of its apply page."""
    u = (_g(r, "apply_url") or _g(r, "url") or "").lower()
    for needle, name in _HOSTS:
        if needle in u:
            return name
    host = urlparse(u).netloc
    return re.sub(r"^www\.", "", host) or (_g(r, "source") or "unknown")


def stats(d, days: int = WINDOW_DAYS) -> dict:
    """{portal: {"win": n, "loss": n}} from the last `days` days of outcomes in the jobs table."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    out: dict = defaultdict(lambda: {"win": 0, "loss": 0})
    try:
        rows = d.rows("status IN ('applied','unconfirmed','failed','needs_you','deferred_captcha') AND COALESCE(applied_at, last_seen, first_seen) >= ?", (since,))
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        st = _g(r, "status")
        if st in WIN:
            out[portal(r)]["win"] += 1
        elif st in LOSS:
            out[portal(r)]["loss"] += 1
    return dict(out)


def value(p: str, st: dict) -> float:
    """UCB1 value of a portal: success rate (with a neutral prior) + exploration bonus."""
    total = sum(v["win"] + v["loss"] for v in st.values())
    s = st.get(p, {"win": 0, "loss": 0})
    n = s["win"] + s["loss"]
    mean = (s["win"] + 1) / (n + 2)
    return mean + EXPLORE * math.sqrt(math.log(total + 2) / (n + 1))


def reliable(p: str, st: dict, min_n: int = 5, min_rate: float = 0.6) -> bool:
    """A portal that has been tried enough and mostly works. Used to keep sends flowing while the others are explored."""
    s = st.get(p, {"win": 0, "loss": 0})
    n = s["win"] + s["loss"]
    return n >= min_n and s["win"] / n >= min_rate


def applied_companies(d) -> set:
    try:
        return {(r["company"] or "").strip().lower() for r in d.rows("status IN ('applied','unconfirmed')") if _g(r, "company")}
    except Exception:  # noqa: BLE001
        return set()


def job_value(r, st: dict, covered: set) -> float:
    p = portal(r)
    return value(p, st) + (0 if p in BOARDS else DIRECT_BONUS) + (0 if (_g(r, "company") or "").strip().lower() in covered else NEW_COMPANY_BONUS)


def fix_list(d, top: int = 12) -> list:
    """Portals ranked by the number of jobs a repair would release: waiting candidates + parked failures, weighted by how badly the portal fails."""
    st = stats(d)
    waiting, why = Counter(), defaultdict(Counter)
    try:
        for r in d.rows("status='candidate'"):
            waiting[portal(r)] += 1
        for r in d.rows("status IN ('failed','needs_you','deferred_captcha')"):
            why[portal(r)][re.sub(r"\d+", "#", (_g(r, "status_detail") or "")[:70])] += 1
    except Exception:  # noqa: BLE001
        pass
    out = []
    for p in set(waiting) | set(why) | set(st):
        s = st.get(p, {"win": 0, "loss": 0})
        n = s["win"] + s["loss"]
        fail_rate = s["loss"] / n if n else 1.0   # never tried = fully unproven
        blocked = sum(why[p].values())
        out.append({"portal": p, "waiting": waiting[p], "sent_14d": s["win"], "failed_14d": s["loss"], "fail_rate": round(fail_rate, 2),
                    "gain": round(fail_rate * (waiting[p] + blocked), 1), "top_reason": (why[p].most_common(1) or [("", 0)])[0][0]})
    out.sort(key=lambda x: -x["gain"])
    return out[:top]


def report(d) -> dict:
    st = stats(d)
    rep = {"at": datetime.now().isoformat(timespec="seconds"), "window_days": WINDOW_DAYS,
           "portals": sorted(({"portal": p, **v, "value": round(value(p, st), 2)} for p, v in st.items()), key=lambda x: -x["value"]),
           "fix_next": fix_list(d)}
    try:
        OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return rep


def print_report(d) -> None:
    rep = report(d)
    print(f"Portal results, last {WINDOW_DAYS} days (value = success rate + bonus for little-tried portals; higher goes first):")
    for p in [x for x in rep["portals"] if x["win"] + x["loss"] >= 3]:   # single-attempt sites are in data/learn.json
        print(f"  {p['portal'][:30]:30} sent {p['win']:4}  failed {p['loss']:4}  value {p['value']}")
    print("\nFix next (jobs a repair would release):")
    for f in rep["fix_next"]:
        print(f"  {f['portal'][:30]:30} waiting {f['waiting']:4}  sent {f['sent_14d']:3}  failed {f['failed_14d']:3}  gain {f['gain']:6}  {f['top_reason']}")
