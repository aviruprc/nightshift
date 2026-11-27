"""`hunt.py run`: pick fresh jobs -> real JD -> re-filter -> score (front of queue only) -> resolve route -> tailor + ATS gate -> apply -> record.
Rules baked in: always tailored+ATS-checked CV; Easy Apply only as a last resort; never click one-click boards from the generic engine."""
import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from core import db as dbm
from core import llm, runstate
from cv import ats_check, tailor
from discover import ats_api
from rank import filter as flt
from rank import ghost
from rank import score as scorer

from apply import browser, easy, engine, learn, lowcomp, naukri_apply, route, workday

STOP = Path(__file__).resolve().parent.parent / "data" / "STOP"
EASY_CAP = 15
LI_PASS = 25   # LinkedIn rows read for applicant counts at the front of every run (settings li_max_applicants switches the pass off)
LINKEDIN_RESOLVE_CAP = 60 // max(1, int((__import__("os").environ.get("JOBHUNT_SHARD") or "0/1").split("/")[1]))  # per run, split across workers: be gentle with LinkedIn
# KB-7: the Naukri 'Applied' badge is only visible in the LIVE signed-in context (a profile copy meets a login wall, 0/13 confirmed).
NAUKRI_LIVE = {"until": 0.0, "logged_signout": False}
NAUKRI_CHECK_EVERY = 3      # Naukri/proof sends between badge passes
NAUKRI_CHECK_GAP = 480      # ...or 8 minutes, whichever comes first
NAUKRI_CHECK_LIMIT = 8      # job pages per pass (each ~3 s: keep the applier moving)
NAUKRI_WALL_BACKOFF = 3600  # signed out: stop asking for an hour; never demote or block anything


# TalentBrew career sites whose "Apply now" runs through Google's INVISIBLE reCAPTCHA callback: clicked by the bot, the button silently does nothing
# (no navigation, no new window - probed headless on jobs.cargill.com). A person's click works. Parked for her, never worked around.
INVISIBLE_RECAPTCHA_HOSTS = re.compile(r"(?:jobs|careers)\.cargill\.com|careers\.moodys\.com", re.I)
KNOWN_BAD_HOSTS = re.compile(r"portal\.careers\.hsbc|^HSBC", re.I)  # CAPTCHA hand-off leaves the form incomplete: parked until fixed


def _fam(r) -> str:
    """Job family of a row (sqlite Row or plain dict; some callers and tests have no family)."""
    try:
        return r["family"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def min_score(r=None) -> int:
    """Fit-score bar to be worth applying (settings.json: min_score / naukri_min_score). The scorer rarely gives 60+ (6 of 985 jobs), so the bar is 50,
    and 45 on Naukri (one click, strongest proof): 'SQL Data Analyst', 'Sr. Finance Analyst', 'Credit Analyst' all score 50-56."""
    import json
    try:
        st = json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        st = {}
    base, nk = int(st.get("min_score", 50)), int(st.get("naukri_min_score", 45))
    bar = nk if (r is not None and is_naukri(r)) else base
    try:
        if r is not None and _fam(r) == "returnship":   # returnships are made for her situation: only clearly unrelated roles are dropped
            return min(bar, int(st.get("returnship_min_score", 30)))
    except Exception:  # noqa: BLE001
        pass
    # a poor-fit application at a TOP firm costs more than it gains (a company took four in an hour, scoring 48-55 with 'weak analytics'):
    # top-tier companies need a fit of at least 55 (settings.json top_min_score), whatever the route. No per-company cap (her choice).
    try:
        if r is not None and (r["company"] or "").strip().lower() in top_companies():
            bar = max(bar, int(st.get("top_min_score", 55)))
    except Exception:  # noqa: BLE001
        pass
    return bar


NAUKRI_BOOST = 0       # Naukri raises the count but the COMPANY career sites give the better results, so they are favoured now
COMPANY_BOOST = 15


def is_naukri(r) -> bool:
    return "naukri.com" in (r["apply_url"] or r["url"] or "")


def interleave(fast: list, slow: list, ratio: int = 2) -> list:
    """`ratio` items from `fast` for every 1 from `slow`, then whatever is left."""
    out, i, j = [], 0, 0
    while i < len(fast) or j < len(slow):
        for _ in range(ratio):
            if i < len(fast):
                out.append(fast[i])
                i += 1
        if j < len(slow):
            out.append(slow[j])
            j += 1
    return out


def _naukri_cap() -> int:
    """Naukri applications per day (settings.json naukri_daily_cap, default 12): a filler behind the company sites, no longer the main route."""
    import json
    try:
        return int(json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8")).get("naukri_daily_cap", 12))
    except Exception:  # noqa: BLE001
        return 12


def queue_priority(r, naukri_left: int) -> float:
    """Fit score, plus a boost for Naukri jobs while today's Naukri cap has room: Naukri is the fastest route (~2 min) with the strongest proof."""
    naukri = "naukri.com" in (r["apply_url"] or r["url"] or "")
    return (r["score"] or 0) + (NAUKRI_BOOST if naukri and naukri_left > 0 else 0) + (0 if naukri else COMPANY_BOOST) + (100 if _fam(r) == "returnship" else 0)


def shard_of(r, n: int) -> int:
    """Which parallel worker owns this job. Keyed by COMPANY so one company's jobs always go to the same worker: no two workers ever race to
    create the same account, share a portal's rate limit, or apply to the same company twice at once."""
    import zlib
    return zlib.crc32((r["company"] or "").strip().lower().encode()) % n


def skipped_company(r) -> bool:
    """True when the company is on her do-not-apply list (settings.json skip_companies: lower-case words matched inside the company name)."""
    import json
    try:
        words = [str(w).lower() for w in json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8")).get("skip_companies", []) if str(w).strip()]
    except Exception:  # noqa: BLE001
        return False
    name = (r["company"] or "").lower()
    return any(w in name for w in words)


ATS_FRESH_DAYS = 45
NAUKRI_FRESH_DAYS = 14
QUARANTINE_LOSSES = 8   # a portal with this many losses and no win in the learning window runs last


def fresh(rows, days=7):
    # Returnship / career-restart programmes run for weeks and are the user's best route after her career break ("prioritise that"): they are never aged out
    # 473 waiting jobs were dropped only for being older than 7 days. Company career sites keep a posting open for weeks and the
    # apply step checks the page is still open, so their limit is ATS_FRESH_DAYS; job boards (reposts, expired cards) keep the short limit.
    def limit(r):
        try:
            src = r["source"] or ""
            if src.startswith("ats:"):
                return max(days, ATS_FRESH_DAYS)
            # A row FOUND on a board whose apply link is the company's own career site / ATS (a Naukri "apply on company site", a LinkedIn external link)
            # is a company-site posting: it stays live for weeks like any ATS feed row, and the apply step checks the page itself. Aging it out at the
            # board limit emptied the queue while the company sites still listed the jobs.
            target = r["apply_url"] or r["url"] or ""
            if target and not engine.ONE_CLICK.search(urlparse(target).netloc):
                return max(days, ATS_FRESH_DAYS)
            # A Naukri listing stays open ~30 days and the apply step reads "listing closed" from the page, so it keeps a longer limit than the reposting boards.
            if src == "board:naukri":
                return max(days, NAUKRI_FRESH_DAYS)
            return days
        except (KeyError, IndexError, TypeError):
            return days
    return [r for r in rows if _fam(r) == "returnship" or (ghost.age_or(r["posted_at"], r["first_seen"])) <= limit(r)]


JUNK_HOSTS = re.compile(r"mycareernet\.co|iitjobs\.com|bandana\.com|flexiple\.com|cutshort.io|getmereferred\.com|naukrimitra\.in|docthub\.com|jobaaj\.com|jobsora|jooble\.org|whatjobs|jobrapido|talent\.com|careerjet|himalayas\.app/companies", re.I)  # aggregators with no real form


def one_click_host(url: str) -> bool:
    h = urlparse(url or "").netloc
    return bool(engine.ONE_CLICK.search(h)) and "linkedin.com" not in h and "naukri.com" not in h  # naukri has its own flow


def beat(step: str, **kw):
    runstate.update(status="preparing", now={"company": "Preparing the queue", "title": "", "step": step}, **kw)


def browser_description(ctx, url: str) -> str:
    """Boards that block plain HTTP (Naukri): read the JD from the page in the signed-in browser."""
    pg = ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(3500)
        text = pg.inner_text("body")
        if naukri_apply.human_check(text):
            naukri_apply.note_human_check()   # Naukri's own reCAPTCHA wall: never bypassed, Naukri pauses for a while
            return ""
        i = text.lower().find("job description")
        return re.sub(r"\n{2,}", "\n", text[i:i + 7000] if i >= 0 else text[:7000]).strip()
    except Exception:  # noqa: BLE001
        return ""
    finally:
        pg.close()


def plan_order(rows: list, d) -> list:
    """work the companies/portals where the path is KNOWN to work first, and finish all jobs of one company together (the account, the
    password and the page flow are warm). Proven = we already have an applied/unconfirmed application at that company or on that non-Naukri portal host.
    Stable: inside a group the fit-score order is kept; companies are ordered by their best job's position."""
    # the user ("the same ones are repeated again and again", "only the easiest paths are being taken", "use reinforcement learning"):
    # the old rule put companies/portals that had already worked FIRST, which is exactly what kept the bot on the same companies and on Naukri.
    # The order is now learned from outcomes (apply/learn.py): a portal's value is its recent success rate plus a bonus while it is little tried,
    # and a company never applied to goes ahead of one already covered. Returnships stay first.
    st, covered = learn.stats(d), learn.applied_companies(d)
    val = {id(r): learn.job_value(r, st, covered) for r in rows}

    def bucket(r) -> float:  # -1 = returnship / career-restart programme; everything else is ordered by learned value
        if _fam(r) == "returnship":
            return -1
        # daily maintenance requeues failed rows after fixes, and a job already tried 3+ times (WapHire, Skan.ai, emagine, AMETEK) came
        # back at the FRONT every run: 15 minutes and six attempts after the restart, nothing had been submitted. A job that has failed three times goes
        # BEHIND every fresh one - it still gets its retry, just not ahead of the jobs that can succeed.
        try:
            if int(r["attempts"] or 0) >= 3:
                return 1
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        # A portal with QUARANTINE_LOSSES+ losses and no win in the learning window goes behind every other portal (it still runs when nothing else is
        # left): the front of the queue was portals with no success in 14 days while the sources that send waited behind them on an exploration bonus.
        try:
            sp = st.get(learn.portal(r)) or {}
            if sp.get("win", 0) == 0 and sp.get("loss", 0) >= QUARANTINE_LOSSES:
                return 1
        except Exception:  # noqa: BLE001
            pass
        return 0

    # look at LinkedIn jobs for LOW COMPETITION first. The first LI_PASS not-yet-resolved LinkedIn rows (best fit first) go right
    # behind the returnships, so the applicant counts are read and shown on the dashboard in every run instead of only when a LinkedIn row happens to
    # reach the front. The applier then applies on the company's own site only for the ones under the limit (route.resolve_linkedin / lowcomp).
    bk = {id(r): bucket(r) for r in rows}
    if lowcomp.li_max_applicants() is not None:
        n_li = 0
        for r in rows:
            if n_li >= LI_PASS:
                break
            def _g(k):
                try:
                    return r[k]
                except (KeyError, IndexError):
                    return None
            if "linkedin.com" in (_g("apply_url") or _g("url") or "") and _g("apply_route") != "easy_apply" and bk[id(r)] >= 0:
                bk[id(r)] = -0.5
                n_li += 1

    first: dict = {}
    best: dict = {}
    for i, r in enumerate(rows):
        k = ((r["company"] or "").strip().lower(), bk[id(r)])
        first.setdefault(k, i)
        best[k] = max(best.get(k, -1.0), val[id(r)])
    # one company's jobs stay together (warm account); companies are ordered by their best learned value, ties by the incoming (fit-score) order
    return sorted(rows, key=lambda r: (bk[id(r)], -round(best[((r["company"] or "").strip().lower(), bk[id(r)])], 3) if bk[id(r)] >= 0 else 0,
                                       first[((r["company"] or "").strip().lower(), bk[id(r)])]))


_TOP_TIERS = {"bank", "mnc", "bigtech", "payments", "consulting", "insurance", "bureau", "fintech", "it-services"}
_TOP_CACHE: dict = {}


def top_companies() -> set:
    """Lower-case names of top-tier employers (tier in the discovery registry discover/companies*.yaml). Cached for the process."""
    if "s" not in _TOP_CACHE:
        try:
            _TOP_CACHE["s"] = {(c.get("name") or "").strip().lower() for c in ats_api.load_companies() if (c.get("tier") or "") in _TOP_TIERS}
        except Exception:  # noqa: BLE001
            _TOP_CACHE["s"] = set()
    return _TOP_CACHE["s"]


def keep_sends_flowing(ordered: list, d) -> list:
    """Two jobs on portals that are known to work for every one on a portal still being explored.

    -23:00: the learned order put untried company sites first (their exploration bonus is the largest) and the applier spent 50 minutes
    on 25 attempts with 2 sends - Workday was in maintenance, three SuccessFactors sign-ins failed. Exploring is how new portals get proven, but it
    must not stop the applications. Returnships stay at the very front; the relative order inside each group is kept."""
    st = learn.stats(d)
    from discover import returnships
    front = [r for r in ordered if _fam(r) == "returnship"]
    rest = [r for r in ordered if _fam(r) != "returnship"]
    # contract work goes right behind returnships (a quicker way back in after the break)

    def _contract(r):
        try:
            return returnships.is_contract(r["title"], r["description"])
        except (KeyError, IndexError, TypeError):
            return False
    front += [r for r in rest if _contract(r)]
    rest = [r for r in rest if not _contract(r)]
    proven = [r for r in rest if learn.reliable(learn.portal(r), st)]
    explore = [r for r in rest if not learn.reliable(learn.portal(r), st)]
    return front + interleave(proven, explore, ratio=2)


def company_cap(d, ranked: list) -> list:
    """At most `company_daily_cap` (settings.json, default 3) applications per company per day, counting today's sends. The rest stay candidates
    for tomorrow. a company had 18 applications and a company 15 while 200 other companies waited. Returnships are exempt."""
    import json
    try:
        cap = int(json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8")).get("company_daily_cap", 3))
    except Exception:  # noqa: BLE001
        cap = 3
    if cap <= 0:
        return ranked
    used: dict = {}
    try:
        for r in d.rows("status IN ('applied','unconfirmed') AND applied_at LIKE ?", (dbm.now()[:10] + "%",)):
            k = (r["company"] or "").strip().lower()
            used[k] = used.get(k, 0) + 1
    except Exception:  # noqa: BLE001
        pass
    out = []
    for r in ranked:
        k = (r["company"] or "").strip().lower()
        if _fam(r) != "returnship" and used.get(k, 0) >= cap:
            continue
        used[k] = used.get(k, 0) + 1
        out.append(r)
    return out


def defer_no_description(d, r, hours: float = 6) -> None:
    """A job whose real description could not be fetched is set aside for a few hours instead of staying at the front of the line.

    prepare_queue looks at only the first ~25 candidates per round and recorded nothing when a fetch failed, so the same
    undescribable jobs (custom career sites, Goldman-style pages) were re-checked every round and ("0 usable of 25 checked", four rounds in
    a row) crowded out the 195 unscored company-site jobs behind them. The row stays a candidate and comes back after `hours`."""
    from datetime import datetime, timedelta
    when = (datetime.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
    d.c.execute("UPDATE jobs SET next_attempt_at=? WHERE id=?", (when, r["id"]))
    d.c.commit()


def runnable_rows(d) -> list:
    """Rows the worker could actually run NOW, by the same read-only tests prepare_queue applies (freshness, route, hosts, score bar) - without
    touching any row. A queue counter that treats every scored candidate as runnable (stale rows, aggregator rows nobody applies to, hand-only
    portals) reads "full" while the worker's real queue is 0, so 'queue low -> discover' never fires and the dashboard sits idle."""
    pool = fresh(d.rows("status IN ('candidate','dry_run_ok') AND (next_attempt_at IS NULL OR next_attempt_at <= ?)", (dbm.now(),)))
    done_fp = {r["fingerprint"] for r in d.rows("status IN ('applied','unconfirmed') OR email_proof IS NOT NULL")}
    out = []
    for r in pool:
        u = r["apply_url"] or r["url"] or ""
        if (flt.location_conflict(r["location"]) or skipped_company(r) or r["fingerprint"] in done_fp or JUNK_HOSTS.search(u)
                or KNOWN_BAD_HOSTS.search(u) or KNOWN_BAD_HOSTS.search(r["company"] or "") or INVISIBLE_RECAPTCHA_HOSTS.search(u)
                or r["apply_route"] == "easy_apply" or one_click_host(u) or "jpmc.fa.oraclecloud.com" in u):
            continue
        sc = r["score"]
        if sc is None or sc >= min_score(r) or (_fam(r) == "returnship"):
            out.append(r)
    return out


def prepare_queue(d, want: int, log, only: str | None = None, ctx=None):
    """Only the front of the queue is processed. Every job used gets a REAL description (>=400 chars), the experience/Python
    filters re-run on it, and it is (re)scored on it."""
    if only and only.startswith("id:"):  # one specific job (testing a platform one job at a time): no freshness/score gate
        ids = tuple(only[3:].split(","))
        for r in d.rows("id IN (%s)" % ",".join("?" * len(ids)), ids):
            if len((r["description"] or "").strip()) < 400 and ctx is not None and "naukri.com" in (r["apply_url"] or ""):
                d.c.execute("UPDATE jobs SET description=? WHERE id=?", (browser_description(ctx, r["apply_url"]), r["id"]))
        return d.rows("id IN (%s)" % ",".join("?" * len(ids)), ids)
    pool = fresh(d.rows("status IN ('candidate','dry_run_ok') AND (next_attempt_at IS NULL OR next_attempt_at <= ?)", (dbm.now(),)))
    # KB-63: the stored row has lost the discovery-time remote flag, and `flt.check(..., skip_location=True)` below was
    # letting Pune/Mumbai rows back in. The stored TEXT is still there: retire anything that names another Indian city.
    for r in [r for r in pool if flt.location_conflict(r["location"])]:
        d.set_status(r["id"], "skipped", f"location: {(r['location'] or '')[:60]} - outside your locations (facts.yaml preferences.home_location_regex) or remote elsewhere")
    pool = [r for r in pool if not flt.location_conflict(r["location"])]
    # companies she told us to leave alone (settings.json skip_companies, matched on the company name)
    for r in [r for r in pool if skipped_company(r)]:
        d.set_status(r["id"], "skipped", "on your do-not-apply list (settings skip_companies)")
    pool = [r for r in pool if not skipped_company(r)]
    naukri_left = _naukri_cap() - d.c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at LIKE ? AND apply_url LIKE '%naukri.com%'",
                                   (dbm.now()[:10] + "%",)).fetchone()[0]
    # never apply twice: the same job often appears under several listings (LinkedIn + the company's own page)
    done_fp = {r["fingerprint"] for r in d.rows("status IN ('applied','unconfirmed') OR email_proof IS NOT NULL")}
    for r in [r for r in pool if r["fingerprint"] in done_fp]:
        d.set_status(r["id"], "skipped", "already applied to this job through another listing")
    pool = [r for r in pool if r["fingerprint"] not in done_fp]
    for r in [r for r in pool if JUNK_HOSTS.search(r["apply_url"] or r["url"] or "")]:
        d.set_status(r["id"], "skipped", "aggregator listing without a real application form")
    for r in [r for r in pool if KNOWN_BAD_HOSTS.search(r["apply_url"] or r["url"] or "") or KNOWN_BAD_HOSTS.search(r["company"] or "")]:
        d.set_status(r["id"], "deferred_captcha", "parked: known CAPTCHA hand-off bug on this portal (data/known_bugs.md)")
    for r in [r for r in pool if INVISIBLE_RECAPTCHA_HOSTS.search(r["apply_url"] or r["url"] or "")]:
        d.set_status(r["id"], "deferred_captcha", "this career site's Apply button runs through Google's invisible reCAPTCHA and does nothing for an automated "
                     "browser; open the job and click Apply now yourself (the form is not pre-filled)")
    pool = [r for r in pool if not INVISIBLE_RECAPTCHA_HOSTS.search(r["apply_url"] or r["url"] or "")]
    pool = [r for r in pool if not (KNOWN_BAD_HOSTS.search(r["apply_url"] or r["url"] or "") or KNOWN_BAD_HOSTS.search(r["company"] or ""))]
    pool = [r for r in pool if r["apply_route"] != "easy_apply" and not one_click_host(r["apply_url"] or r["url"])
            and not JUNK_HOSTS.search(r["apply_url"] or r["url"] or "")]
    if not __import__("os").environ.get("JOBHUNT_OFFSCREEN"):
        #  "why is the dashboard idle": all 16 queue slots were a company rows (high scores, hCaptcha first screen) that this worker skips one by one,
        # so it attempted nothing while 100+ other jobs waited. A job this worker will never attempt must not hold a slot.
        pool = [r for r in pool if "jpmc.fa.oraclecloud.com" not in (r["apply_url"] or r["url"] or "")]
    if only:  # e.g. --only naukri.com: restrict this run to one kind of site
        pool = [r for r in pool if only in (r["apply_url"] or r["url"] or "")]
    shard = __import__("os").environ.get("JOBHUNT_SHARD")  # "0/2", "1/2": parallel appliers split the queue, never the same job
    if shard:
        k, n = (int(x) for x in shard.split("/"))
        pool.sort(key=lambda r: shard_of(r, n) != k)  # my own slice first; the rest only if I run out (company claims prevent collisions)
    tier1 = sorted([r for r in pool if (r["score"] or 0) >= min_score(r)], key=lambda r: -queue_priority(r, naukri_left))
    tier2 = sorted([r for r in pool if r["score"] is None],
                   key=lambda r: (len((r["description"] or "").strip()) < 400,  # jobs that already have a real description first (the others are mostly skipped)
                                  {"risk_reg": 0, "data_bi": 0, "finance": 1, "etl_sas": 1}.get(r["family"], 2), ghost.age_or(r["posted_at"], r["first_seen"])))
    ordered = plan_order(tier1 + tier2, d)  # keep proven companies inside the first 120 candidates that get prepared
    company = [r for r in ordered if not is_naukri(r)]
    nk_jobs = [r for r in ordered if is_naukri(r)]
    # company sites first, 3 for every 1 Naukri job, and Naukri only while today's cap (naukri_daily_cap) has room
    ordered = interleave(company, nk_jobs[:max(0, naukri_left)], ratio=3) if naukri_left > 0 else company
    cands = ordered[: min(want * 8, 120)]  # cap preparation so applying starts within minutes; later runs take the next slice
    keep, rescore = [], set()
    for i, r in enumerate(cands):
        beat(f"checking job descriptions ({i + 1}/{len(cands)})")
        desc = (r["description"] or "").strip()
        if len(desc) < 400:
            u = r["apply_url"] or r["url"] or ""
            if ctx is not None and "naukri.com" in u:
                desc = browser_description(ctx, u).strip()
            else:
                desc = (ats_api.lazy_description({**r, "_wd_api": None}) or "").strip()
            if len(desc) >= 400:
                d.c.execute("UPDATE jobs SET description=?, score=NULL WHERE id=?", (desc, r["id"]))
                d.c.commit()
                r["description"], r["score"] = desc, None
                rescore.add(r["id"])
        if len(desc) < 400:
            if not ("naukri.com" in (r["apply_url"] or r["url"] or "") and ctx is None):   # a Naukri row with no browser was never really tried
                defer_no_description(d, r)
            continue  # cannot tailor honestly without a real JD: leave it for later
        fam, why = flt.check({**r, "description": desc}, skip_location=True)
        if why:
            d.set_status(r["id"], "skipped", f"after reading the full description: {why}")
            continue
        keep.append(r)
    from core import activity
    activity.set_job("Preparing the queue", "", "")
    activity.log("info", f"read real descriptions: {len(keep)} usable of {len(cands)} checked")
    todo = [r for r in keep if r["score"] is None]
    if todo:
        log(f"scoring {len(todo)} jobs with Haiku")
        beat(f"scoring {len(todo)} jobs for fit")
        scorer.score_all(d, todo, log=lambda *a: None)
    ids = [r["id"] for r in keep]
    ranked = [r for r in d.rows("status IN ('candidate','dry_run_ok') AND (score>=? OR (family='returnship' AND score>=?))", (min(min_score(), min_score({"apply_url": "naukri.com", "url": ""})), min_score({"family": "returnship", "apply_url": "", "url": "", "company": ""}))) if r["id"] in ids and (r["score"] or 0) >= min_score(r)]
    ranked.sort(key=lambda r: (-(r["score"] or 0), ghost.age_or(r["posted_at"], r["first_seen"])))
    ranked = company_cap(d, ranked)
    return keep_sends_flowing(plan_order(ranked, d), d)  # proven companies/portals first, one company's jobs together


def prepare_easy_queue(d, want: int, log=print) -> list:
    """Easy Apply-only jobs as a run's LAST RESORT, including the ones earlier runs deferred (KB-74).

    They stay out of the main queue on purpose - a direct ATS send is stronger and a company career account can prove it -
    but the in-run `deferred` list used to be the ONLY way one could be reached: it died with the run, `prepare_queue`
    drops every `apply_route='easy_apply'` row, and 'deferred_easy' is not a status it reads. So a job parked as
    "only Easy Apply; used last if needed" was never looked at again: 30 rows stuck (a company 78, a company 82, a company 74), and
    the two whose Easy Apply failed and were reset to 'candidate' (a company 52, a company 60) could never be retried. The
    applier then ran runs with nothing to do while these sat in the database.
    Same gates as the main pool; the caller still enforces EASY_CAP and duplicate_role.
    """
    rows = d.rows("status IN ('candidate','dry_run_ok','deferred_easy') AND apply_route='easy_apply' "
                  "AND (next_attempt_at IS NULL OR next_attempt_at <= ?)", (dbm.now(),))
    rows = fresh(rows)
    rows = [r for r in rows if not flt.location_conflict(r["location"])]
    done_fp = {r["fingerprint"] for r in d.rows("status IN ('applied','unconfirmed') OR email_proof IS NOT NULL")}
    rows = [r for r in rows if r["fingerprint"] not in done_fp]
    rows = [r for r in rows if not JUNK_HOSTS.search(r["apply_url"] or r["url"] or "")
            and not KNOWN_BAD_HOSTS.search(r["apply_url"] or r["url"] or "")
            and not KNOWN_BAD_HOSTS.search(r["company"] or "")]
    rows = [r for r in rows if len((r["description"] or "").strip()) >= 400 and (r["score"] or 0) >= min_score(r)]
    rows.sort(key=lambda r: (-(r["score"] or 0), ghost.age_or(r["posted_at"], r["first_seen"])))
    out = rows[:max(0, int(want))]
    if out:
        log(f"easy-apply last resort: {len(out)} job(s) waiting (score >= {min_score()}, daily cap {EASY_CAP})")
    return out


ROOT = Path(__file__).resolve().parent.parent


LLM_DOWN = {"until": 0.0}
WD_MAINT = {"until": 0.0}   # Workday's own maintenance window (it takes whole tenants offline at once, e.g. Saturday/Sunday)
_WD_MAINT_RX = re.compile(r"workday is currently unavailable|maintenance page|service interruption|under maintenance", re.I)


def note_workday_maintenance(detail: str, url: str, minutes: int = 45) -> bool:
    """: 77 Workday tenants answered 'maintenance page' in one discovery pass, and the applier kept spending attempts (and the three-try
    budget) on Workday jobs that could not possibly load. One tenant reporting maintenance pauses every Workday job for `minutes`."""
    if "myworkdayjobs.com" in (url or "") and _WD_MAINT_RX.search(detail or ""):
        WD_MAINT["until"] = time.time() + minutes * 60
        return True
    return False


def workday_paused(r) -> str:
    """Reason to leave a Workday job for later (no attempt is spent), else ''."""
    if time.time() < WD_MAINT["until"] and "myworkdayjobs.com" in ((r["apply_url"] or r["url"] or "") if r else ""):
        return f"Workday is in maintenance (seen {int((WD_MAINT['until'] - time.time()) / 60)} min before it is retried); not attempted"
    return ""


def protect_claude_limit() -> bool:
    """The backup AI (DeepSeek) is the primary and it is signed out: every AI call would silently burn the user's Claude limit. While that lasts, only routes
    that need almost no AI (Naukri, Workday) run; navigator portals wait untouched and resume the moment she signs in."""
    from core import llm, llm_browser
    if llm.backup_first() and llm_browser.LOGIN_FLAG.exists():
        return True
    return not llm.providers() and not llm.claude_for_bot()  # no other AI is available and Claude is reserved: only LLM-free routes run


WORKDAY_RE = re.compile(r"myworkday(jobs|site)\.com", re.I)  # a company apply pages live on wd1.myworkdaysite.com: same Workday UI, previously unrouted -> navigator -> State field never filled


def llm_free_route(url: str) -> bool:
    """Routes that work without the LLM (rules + saved answers): Naukri and Workday. Everything else needs the navigator."""
    return "naukri.com" in (url or "") or bool(WORKDAY_RE.search(url or ""))
EARLIER = "2026-01-01T00:00:00"  # 'applied some time before this system existed': keeps already-applied jobs out of today's counts


def wait_if_llm_down(log=print):
    """While the LLM is down, wait (heartbeating) instead of failing job after job."""
    while time.time() < LLM_DOWN["until"] and not STOP.exists():
        from core import activity
        activity.log("info", f"waiting for the LLM to come back ({int(LLM_DOWN['until'] - time.time())}s)")
        time.sleep(30)
    if LLM_DOWN["until"]:
        try:  # probe once before resuming
            llm.ask("reply ok", "score", timeout=60, retries=0, purpose="LLM health probe")
            LLM_DOWN["until"] = 0.0
        except Exception:  # noqa: BLE001
            LLM_DOWN["until"] = time.time() + 600


def _why_skip(why: str, r) -> None:
    """a run that attempts nothing must say WHY for every queued job, not exit silently after 15 s."""
    try:
        from core import activity
        activity.log("info", f"not attempted: {why} - {(r['company'] or '')[:30]} | {(r['title'] or '')[:40]}")
    except Exception:  # noqa: BLE001
        pass


def still_queued(d, r) -> bool:
    """False when the row is no longer waiting to be applied: another worker submitted it, a person or a repair parked/closed it, or its
    retry time moved into the future since THIS worker built its queue. A queue is built once, so without this check a worker keeps
    working rows that changed under it (a company: worker B reopened an Easy Apply job worker A had just submitted; 29 parked
    a company rows were still attempted). Unknown row -> False (never attempt what the database no longer holds)."""
    cur = d.rows("id=?", (r["id"],))
    if not cur:
        return False
    c = cur[0]
    if c["status"] not in ("candidate", "dry_run_ok"):
        return False
    nxt = c.get("next_attempt_at")
    return not (nxt and nxt > dbm.now())


def score_now_below_bar(d, r) -> str:
    """Reason when the row's CURRENT score (read now, not the queue snapshot) is below its bar, else ''.
    score caps (missing mandatory skill, stated minimum years) were written to waiting rows while a worker held a queue built
    earlier; without this re-read a capped job could still be applied to. A row with no score yet is left to the queue's own rules."""
    try:
        now = d.c.execute("SELECT score FROM jobs WHERE id=?", (r["id"],)).fetchone()
    except Exception:  # noqa: BLE001
        return ""
    sc = now[0] if now else None
    if sc is None:
        return ""
    bar = min_score(r)
    return f"score is now {sc}, below the bar {bar} (capped after the queue was built)" if sc < bar else ""


def duplicate_role(d, r, handled: set) -> str:
    """Reason when this row's role was already sent — in the database, or earlier in THIS run — else ''.

    The queue is prepared once, so two rows of the same company+title+city (a company R800105 / R800100,, applied two
    minutes apart) both reached the browser: `already_applied` was evaluated before the first one became 'unconfirmed'. A second
    application for the same role cannot be undone, so the per-row check happens here."""
    fp = str(r.get("fingerprint") or "")
    if fp and fp in handled:
        return "same role handled earlier in this run (not applied twice)"
    other = d.same_role_sent(r["company"], r["title"], r.get("location") or "", exclude_id=r["id"])
    if other:
        return f"same role already sent as {other[0]} ({other[1]}); not applying twice"
    return ""


def route_board_direct(d, r, res: dict) -> str:
    """A board sent the application to the company's own site (KB-23).

    No target known -> `deferred_board`, exactly as before. A known ATS target captured from the hand-off CONTROL itself -> store it in
    `apply_url` and put the row back in the queue, so the next pass runs the Workday / Oracle / SuccessFactors adapter instead of stopping
    at a note. Only a URL the control owned is ever stored; an unrelated external link is never an apply target."""
    if "Workday skipped" in (res.get("detail") or ""):
        return "skipped"  # leave it as a candidate for the Workday flow
    target = str(res.get("apply_url") or "")
    if re.match(r"^https?://", target, re.I):
        d.c.execute("UPDATE jobs SET apply_url=? WHERE id=?", (target, r["id"]))
        d.c.commit()
        d.set_status(r["id"], "candidate", f"following the company-site hand-off: {target[:150]}")
        return "routed"
    d.set_status(r["id"], "deferred_board", res.get("detail") or "")
    return "deferred"


def handle_failure(d, r, s: str, res: dict, dry_run: bool, extra: dict) -> str:
    """Classify (apply/errors.py) and act: retry transient/lockout later, hand CAPTCHA to the user, ask personal facts once, log our bugs."""
    from apply import errors
    from core import activity
    if errors.is_llm_down(res.get("detail", "")):
        # the LLM (usage limit / outage) is not the job's fault: do NOT burn an attempt, pause the worker, retry the job afterwards
        LLM_DOWN["until"] = time.time() + 600
        when = (datetime.now() + timedelta(minutes=10)).isoformat(timespec="seconds")
        d.c.execute("UPDATE jobs SET next_attempt_at=?, fail_class='llm_down' WHERE id=?", (when, r["id"]))
        d.set_status(r["id"], "candidate", "LLM unavailable: not the job's fault; retrying later", needs_you_reason=None)
        activity.log("alert", "LLM unavailable: pausing this worker for 10 minutes (jobs are NOT being failed)")
        return "llm_down"
    note_workday_maintenance(res.get("detail", ""), (r["apply_url"] or r["url"] or ""))
    cls = errors.classify(s, res.get("detail", ""))
    attempts = (r["attempts"] or 0) + 1
    d.c.execute("UPDATE jobs SET attempts=?, fail_class=? WHERE id=?", (attempts, cls, r["id"]))
    detail = res.get("detail", "")
    if cls in ("transient", "lockout"):
        when = errors.next_attempt(cls, attempts - 1)
        if when:
            d.c.execute("UPDATE jobs SET next_attempt_at=? WHERE id=?", (when, r["id"]))
            d.set_status(r["id"], "candidate", f"retry at {when[11:16]} ({cls}): {detail[:120]}", needs_you_reason=None, **extra)
            activity.log("info", f"{cls} problem, retrying at {when[11:16]}: {detail[:100]}")
            return cls
        cls = "logic"  # transient that kept recurring: treat as our bug
    if cls == "captcha":
        d.set_status(r["id"], "needs_you", detail, needs_you_reason="CAPTCHA: the form is filled; solve the CAPTCHA, then submit", **extra)
        if not dry_run:
            from apply import assist
            assist.enqueue(r["id"])
    elif cls == "personal":
        d.set_status(r["id"], "needs_you", detail, needs_you_reason=detail, **extra)
    elif cls == "permanent":
        d.set_status(r["id"], "closed", detail, **extra)
    else:  # logic: a bug in OUR toolchain, never the user's job. Shows in 'What is failing'; 'Retry parked jobs' after a fix.
        d.set_status(r["id"], "failed", detail, needs_you_reason=None, **extra)
    return cls


def record_submission(d, jid: str, res: dict, dry_run: bool, extra: dict):
    """Proof standard v2: 'applied' ONLY with conclusive proof = the employer's receipt email (apply/proof.py) or the career account
    showing it (Naukri 'Applied' badge, Workday 'already applied'). A confirmation page seen after OUR final click = 'unconfirmed' (shown as
    'Submitted') with the post-submit screenshot, upgraded when the email arrives."""
    if dry_run:
        d.set_status(jid, "applied", res["detail"], applied_at=dbm.now(), **extra)
        return
    proof = res.get("proof") or ""
    account_proof = proof.startswith("ACCOUNT") or "Naukri shows 'Applied'" in proof
    d.c.execute("UPDATE jobs SET page_proof=?, proof_shot=COALESCE(?, proof_shot) WHERE id=?", (proof[:200] or None, res.get("proof_shot"), jid))
    if account_proof:
        d.c.execute("UPDATE jobs SET email_proof=?, email_proof_at=? WHERE id=?", (proof[:200], dbm.now(), jid))
        d.set_status(jid, "applied", res["detail"], applied_at=dbm.now(), **extra)
    else:
        d.set_status(jid, "unconfirmed", ("submitted: confirmation page seen, waiting for the receipt email" if proof else
                                          "submitted, waiting for the receipt email") + f" ({res['detail'][:90]})", applied_at=dbm.now(), **extra)


def _maybe_verify(st):
    """Every 4 sends or 8 minutes, check Gmail receipts in a background process (own profile copy, never blocks the applier) so the
    dashboard's Verified column keeps pace with what was actually sent. The Naukri badge is read in the LIVE context by
    _maybe_verify_naukri (KB-7); the copy-based Naukri check in that subprocess now stops at Naukri's login wall."""
    import subprocess
    import sys
    now = time.time()
    st["since_verify"] = st.get("since_verify", 0) + 1
    if st["since_verify"] < 4 and now - st.get("last_verify", 0) < 480:
        return
    lock = ROOT / "data" / "proof.lock"
    if lock.exists() and now - lock.stat().st_mtime < 600:
        return
    lock.write_text(str(now))
    st["since_verify"], st["last_verify"] = 0, now
    subprocess.Popen([sys.executable, str(ROOT / "hunt.py"), "proof"], cwd=str(ROOT), creationflags=0x08000000,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _maybe_verify_naukri(ctx, d, st, log=print) -> int:
    """KB-7: read the Naukri 'Applied' badge INSIDE the worker's live, signed-in browser context, between jobs.

    The old check copied the profile, where Naukri shows a login wall (session cookies are not on disk) and confirmed 0/13. Here the
    already-signed-in context is reused: proof.check_naukri_badges opens at most ONE tab and closes it. ONLY a real badge on that
    job's page promotes an 'unconfirmed' Naukri row to 'applied' (keeping its real applied_at). A timeout, a missing badge, a closed
    listing or a sign-in wall changes NOTHING; one sign-in wall is logged once and starts an hour-long back-off."""
    from apply import proof
    from core import activity
    now = time.time()
    if now < NAUKRI_LIVE["until"]:
        return 0
    st["since_nk"] = st.get("since_nk", 0) + 1
    if st["since_nk"] < NAUKRI_CHECK_EVERY and now - st.get("last_nk", 0) < NAUKRI_CHECK_GAP:
        return 0
    st["since_nk"], st["last_nk"] = 0, now
    rows = d.rows("status='unconfirmed' AND email_proof IS NULL AND (source='board:naukri' OR apply_url LIKE '%naukri.com%') "
                  "ORDER BY applied_at DESC LIMIT ?", (NAUKRI_CHECK_LIMIT,))
    if not rows:
        return 0
    try:
        res = proof.check_naukri_badges(ctx, rows, log=log)
    except Exception as e:  # noqa: BLE001 (a proof check must never hurt the applier)
        activity.log("info", f"Naukri badge check skipped ({type(e).__name__}: {str(e)[:80]}); jobs stay unconfirmed")
        return 0
    if res.get("signed_out"):
        NAUKRI_LIVE["until"] = time.time() + NAUKRI_WALL_BACKOFF
        if not NAUKRI_LIVE["logged_signout"]:
            NAUKRI_LIVE["logged_signout"] = True
            activity.log("info", "Naukri badge check: the live browser is signed out (login wall). Nothing was demoted or blocked; "
                                 "backing off for 1h. Sign in to Naukri to restore account-badge proof.")
        return 0
    NAUKRI_LIVE["logged_signout"] = False
    by_id = {j["id"]: j for j in rows}
    promoted = 0
    for jid, v in (res.get("jobs") or {}).items():
        if (v or {}).get("verdict") != "applied" or jid not in by_id:
            continue
        j = by_id[jid]
        evidence = str(v.get("evidence") or "")[:150]
        cur = d.c.execute("UPDATE jobs SET email_proof=?, email_proof_at=? "
                          "WHERE id=? AND email_proof IS NULL AND status='unconfirmed'",
                          (f"ACCOUNT: Naukri Applied badge {evidence}"[:200], dbm.now(), jid))
        if not cur.rowcount:
            continue  # another process already proved/changed it: never overwrite someone else's verdict
        d.set_status(jid, "applied", f"confirmed: Naukri Applied badge for this job ({evidence[:100]})",
                     applied_at=j["applied_at"] or dbm.now())  # keep the real submission time, never reset it
        promoted += 1
        activity.log("result", f"Naukri Applied badge confirms {j['company']} — {j['title']}", status="email_proof")
    if promoted:
        log(f"naukri badge: {promoted}/{len(rows)} unconfirmed Naukri jobs now proven by the Applied badge")
    return promoted


def _record_wedge(s: dict, limit: int) -> str:
    """KB-80: a hard exit skips every record, so the row stayed `candidate` and was FIRST in line again on the
    relaunch - the same job could wedge the next worker too, forever. Record the attempt before exiting: `transient` (retried
    with backoff), and after MAX_TRANSIENT the normal policy parks it as our bug. Returns the job id or ""."""
    now = s.get("now") or {}
    jid = now.get("id")
    if not jid:
        return ""
    detail = (f"the applier froze inside a browser call (no progress for {limit}s) on '{now.get('step') or 'a job'}'; "
              f"the worker was restarted")
    try:
        d = dbm.DB()
        row = d.c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            return ""
        handle_failure(d, dict(row), "failed", {"detail": detail}, False, {})
        d.c.commit()
        from core import activity
        activity.log("error", f"watchdog: {detail}", status="failed")
        return jid
    except Exception:  # noqa: BLE001 (recording must never stop the restart)
        return ""


def _watchdog(limit=600):
    """If the run state has not moved for `limit` seconds the browser is wedged: exit hard so the supervisor restarts the applier."""
    import os
    import threading

    def loop():
        while True:
            time.sleep(30)
            try:
                s = runstate.read()
                if s.get("finished"):
                    return
                if time.time() - s.get("updated_at", time.time()) > limit:
                    _record_wedge(s, limit)
                    from core import activity
                    activity.log("info", f"watchdog: no progress for {limit}s, restarting the applier")
                    os._exit(3)
            except Exception:  # noqa: BLE001
                pass
    threading.Thread(target=loop, daemon=True).start()


def run(target=10, dry_run=True, max_attempts=40, show=False, log=print, only=None):
    from playwright.sync_api import sync_playwright

    from track import live
    # KB-42: the runner used to DELETE data/STOP here and at the end of a run. The operator's stop marker is a command,
    # not a stale flag: deleting it let worker.ps1's `while (-not (Test-Path data\STOP))` restart a whole new applier right after a stop
    # (seconds after). Only the dashboard's explicit start action may clear it (track/live.py:687).
    live.ensure_running(open_browser=False)  # whenever we start applying, the dashboard is up and open
    from core import backup
    try:
        backup.snapshot("pre-run")
    except Exception as e:  # noqa: BLE001
        log(f"backup warning: {e}")
    d = dbm.DB()
    t0 = time.time()
    if not dry_run:
        _watchdog()
    st = dict(attempts=0, applied=0, li_resolved=0)
    runstate.update(status="preparing", target=target, applied=0, attempts=0, max_attempts=max_attempts, dry_run=dry_run,
                    started_at=t0, now=None, finished=False, last=None)
    deferred: list = []

    def set_now(r, step):
        from core import activity
        portal = activity.portal_of(r["apply_url"] or r["url"], r["apply_route"])
        runstate.update(now={"id": r["id"], "company": r["company"], "title": r["title"], "step": step, "score": r["score"],
                             "source": r["source"], "portal": portal, "family": r["family"]})
        activity.set_job(r["company"], r["title"], portal)
        activity.log("step", step)

    def attempt(r, url, ctx, pg, easy_route=False):
        """Tailor + ATS gate + apply. Returns True when it counts toward the target."""
        url = engine.normalize_url(url)
        st["attempts"] += 1
        runstate.update(attempts=st["attempts"])
        title = f"{r['company']} — {r['title']}"
        set_now(r, "tailoring CV + ATS check")
        fam = r["family"] if r["family"] in ("data_bi", "risk_reg", "finance", "etl_sas") else "data_bi"
        try:
            pdf, _ = tailor.build(r["id"], r["description"], fam, r["title"])
            chk = ats_check.check(pdf, r["description"], r["title"])
        except Exception as e:  # noqa: BLE001
            d.c.execute("UPDATE jobs SET attempts=attempts+1 WHERE id=?", (r["id"],))
            d.set_status(r["id"], "failed", f"CV build error: {str(e)[:100]}")
            return False
        if not chk["ok"]:
            d.set_status(r["id"], "needs_you", "ATS check failed: " + "; ".join(chk["problems"])[:200], needs_you_reason="ats")
            return False
        d.c.execute("UPDATE jobs SET resume_path=?, ats_coverage=? WHERE id=?", (str(pdf), chk["coverage"], r["id"]))
        set_now(r, "filling application form" + (" (dry run)" if dry_run else ""))
        engine.CURRENT_JOB.update(id=r["id"], company=r["company"], title=r["title"], jd=(r["description"] or "")[:3000])  # context for the LLM fallback
        if easy_route:
            res = easy.apply_easy(ctx, r["url"], pdf, dry_run, r["id"])
        elif WORKDAY_RE.search(url) and __import__("os").environ.get("JOBHUNT_SKIP_WORKDAY"):
            res = {"status": "board_direct", "detail": "Workday skipped in this run (flow still being tested)", "screenshot": None}
        elif WORKDAY_RE.search(url):
            engine.pace(url)
            res = workday.apply_workday(ctx, url, pdf, dry_run, r["id"], r["source"] or "")
        elif "naukri.com" in url:
            engine.pace(url)
            res = naukri_apply.apply_naukri(ctx, url, pdf, dry_run, r["id"])
        elif engine.BLOCKED_PORTALS.search(url):  # no rule-based flow for these: straight to the LLM navigator
            res = {"status": "no_form", "detail": "portal without a rule-based flow"}
        else:
            engine.pace(url)
            res = engine.apply_to_url(pg, url, pdf, dry_run=dry_run, job_id=r["id"])
            if res["status"] == "redirect" and WORKDAY_RE.search(res["detail"]) and not __import__("os").environ.get("JOBHUNT_SKIP_WORKDAY"):
                d.c.execute("UPDATE jobs SET apply_url=? WHERE id=?", (res["detail"], r["id"]))
                d.c.commit()
                set_now(r, "Workday: following the careers-site hand-off")
                res = workday.apply_workday(ctx, res["detail"], pdf, dry_run, r["id"], r["source"] or "")
            elif res["status"] == "redirect":
                res = {"status": "board_direct", "detail": "Workday skipped in this run (flow still being tested)", "screenshot": None}
        # LLM navigator: every non-terminal outcome of the rule-based flows gets one LLM-driven attempt before anything is parked
        from apply import agent as _ag
        if res["status"] in (("login_required", "no_form", "failed", "needs_you", "unconfirmed") + (("captcha",) if _ag.handoff_seconds() else ())) and not easy_route and "naukri.com" not in url                 and not protect_claude_limit()                 and not re.search(r"password already reset", res.get("detail", "")):
            from apply import agent
            from core import activity
            first = res
            activity.log("ai", f"rule-based flow stopped ({res['status']}: {res['detail'][:80]}); LLM navigator takes over")
            set_now(r, "LLM navigator: driving the portal")
            apg = ctx.new_page()
            try:
                res = agent.navigate(apg, ctx, url, pdf, {"id": r["id"], "company": r["company"], "title": r["title"]}, dry_run)
            finally:
                for p_ in list(ctx.pages)[1:]:
                    if p_ is not pg and "mail.google" not in p_.url:
                        try:
                            p_.close()
                        except Exception:  # noqa: BLE001
                            pass
            if res["status"] in ("failed", "no_form") and first["status"] == "needs_you":
                res = {**first, "detail": first["detail"] + " | navigator: " + res["detail"][:80]}
        s = res["status"]
        extra = {"screenshot_path": res.get("screenshot")}
        ok = False
        if s == "applied":
            record_submission(d, r["id"], res, dry_run, extra)
            ok = True
        elif s == "dry_run_ok":
            d.set_status(r["id"], "dry_run_ok", res["detail"], **extra)
            ok = True
        elif s == "already_applied":  # true state is applied (found when revisiting); does not count toward this run's target
            d.c.execute("UPDATE jobs SET email_proof=COALESCE(email_proof, ?), email_proof_at=COALESCE(email_proof_at, ?) WHERE id=?",
                        (f"ACCOUNT: {res['detail'][:150]}", dbm.now(), r["id"]))  # the career account itself shows the application
            # found already applied (earlier, by her or the old bot): true state is applied, but it is NOT an application made today
            # `r` was read when THIS worker built its queue: another worker may have submitted the job since (a company: B sent it at
            # , A's stale copy had no applied_at and wrote EARLIER over the real time). Re-read the stored time before choosing.
            live_at = (d.c.execute("SELECT applied_at FROM jobs WHERE id=?", (r["id"],)).fetchone() or [None])[0]
            d.set_status(r["id"], "applied", res["detail"], applied_at=(live_at or r["applied_at"] or EARLIER), **extra)
        elif s == "closed":
            d.set_status(r["id"], "closed", res["detail"], **extra)
        elif s == "unconfirmed":
            # A submission awaiting its receipt is still a SEND: stamp applied_at and keep the page proof, or the job never shows in
            # 'sent today', never enters the receipt matcher's awaiting list, and the dashboard undercounts.
            record_submission(d, r["id"], {**res, "status": "applied"}, dry_run, extra)
        elif s == "board_direct":
            route_board_direct(d, r, res)
        else:  # every other outcome is CLASSIFIED; only CAPTCHA and un-guessable personal facts ever reach the user
            handle_failure(d, r, s, res, dry_run, extra)
        try:  # remember what works / fails per portal: fed back into the navigator prompt next time
            from core import cache as _cache
            # a SENT application awaiting its receipt ("unconfirmed") was counted as a portal FAILURE, and so were closed postings and
            # parked questions; three of those switched the whole portal off for the day. Only a real failure counts against the portal.
            _sent = s in ("applied", "dry_run_ok", "already_applied", "unconfirmed")
            _cache.remember(urlparse(url).netloc.lower(), _sent, res.get("detail", ""), day_key=_cache.breaker_key(url),
                            count_day=_sent or s == "failed" and not _cache.ai_outage(res.get("detail", "")))
        except Exception:  # noqa: BLE001
            pass
        if ok:
            st["applied"] += 1
            if not dry_run:
                _maybe_verify(st)
        # KB-7: after a Naukri send (counted 'applied' OR left 'unconfirmed'), read the Applied badge in this live signed-in context.
        # Only a real badge promotes; a timeout / missing badge / sign-in wall changes nothing.
        if not dry_run and (ok or s == "unconfirmed") and "naukri.com" in url:
            _maybe_verify_naukri(ctx, d, st, log)
        runstate.update(applied=st["applied"], now=None, last={"title": title, "status": s, "detail": res["detail"][:140]})
        # say 'submitted' for a page-proved send that has no receipt yet: the DB stores it as 'unconfirmed', so an '[N/50] applied' line
        # would overstate what is proven
        label = "submitted" if s == "applied" and not dry_run and not (res.get("proof") or "").startswith("ACCOUNT") else s
        log(f"[{st['applied']}/{target}] {label:12} {title[:70]} | {res['detail'][:70]}")
        from core import activity
        activity.log({"applied": "result", "dry_run_ok": "result", "already_applied": "result"}.get(s, "park" if s in ("needs_you", "captcha", "login_required", "no_form", "unconfirmed") else "info"),
                     f"{('submitted' if (s == 'applied' and not dry_run and not (res.get('proof') or '').startswith('ACCOUNT')) else s.replace('_', ' '))}: {res['detail'][:200]}", status=s)
        return ok

    with sync_playwright() as pw:
        ctx = browser.launch(pw, headless=not show)
        ctx.set_default_timeout(15000)  # short default: a missing element must not stall a job for 30s per click
        pg = ctx.new_page()
        try:
            queue = prepare_queue(d, target, log, only, ctx)
        except llm.UsageLimit:
            runstate.update(status="paused: usage limit reached", finished=True)
            ctx.close()
            return
        log(f"queue: {len(queue)} ranked jobs with full descriptions")
        runstate.update(status="running", queue_size=len(queue), now=None)
        handled_fps: set = set()
        for r in queue:
            if st["applied"] >= target or st["attempts"] >= max_attempts or STOP.exists():
                break
            if not still_queued(d, r):   # the row changed under this worker (submitted/parked/closed/rescheduled): leave it alone
                _why_skip("row changed under this worker", r)
                continue
            dup = duplicate_role(d, r, handled_fps)  # the queue is built once: re-check the role NOW, not only then
            if dup:
                d.set_status(r["id"], "skipped", dup)
                continue
            low = score_now_below_bar(d, r)   # the queue is built once: a cap written since (score caps) must still stop it
            if low:
                _why_skip(low, r)
                continue
            paused = workday_paused(r)
            if paused:
                _why_skip(paused, r)
                continue
            if is_naukri(r) and naukri_apply.paused():
                _why_skip("Naukri is showing its human check (reCAPTCHA); Naukri paused for an hour, not attempted", r)
                continue
            # Rows THIS worker will never attempt are skipped BEFORE the company is claimed. Worker B (company sites only) used to claim every Naukri
            # company first and then skip it; it re-claimed them every round, so worker A found all 24 companies "owned" and attempted nothing.
            if is_naukri(r) and __import__("os").environ.get("JOBHUNT_SKIP_NAUKRI"):
                continue  # a second worker runs on a COPY of the profile, which is signed OUT of Naukri (session cookies are not on disk): company sites only
            if "jpmc.fa.oraclecloud.com" in (r["apply_url"] or r["url"] or "") and not (__import__("os").environ.get("JOBHUNT_OFFSCREEN")
                                                                                         or __import__("os").environ.get("JOBHUNT_HANDOFF")):
                _why_skip("JPMorgan: off-screen run only (CAPTCHA page)", r)
                continue  # KB-9: a company serves an EMPTY page to headless Chrome; its jobs are worked by the off-screen headed run (JOBHUNT_OFFSCREEN=1), not here
            try:  # claim the company: two parallel workers never work on the same company at once
                from core import cache as _cc
                if not _cc.claim(r["company"], runstate._LABEL):
                    _why_skip("company claimed by another worker", r)
                    continue
            except Exception:  # noqa: BLE001
                pass
            if protect_claude_limit() and not llm_free_route(r["apply_url"] or r["url"] or ""):
                _why_skip("limit-protect mode (AI usage limit)", r)
                continue  # limit-protect mode (see protect_claude_limit)
            if time.time() < LLM_DOWN["until"] and not llm_free_route(r["apply_url"] or r["url"] or ""):
                _why_skip("AI providers unavailable right now (only no-AI routes run)", r)
                continue  # LLM outage: only routes that need no LLM (Naukri, Workday with saved answers) keep going; the rest wait untouched
            try:
                from core import cache as _c
                _h = urlparse(engine.normalize_url(r["apply_url"] or r["url"] or "")).netloc.lower()
                if "linkedin.com" not in _h and not llm_free_route(_h) and _c.host_blocked(_c.breaker_key(engine.normalize_url(r["apply_url"] or r["url"] or ""))):
                    d.c.execute("UPDATE jobs SET next_attempt_at=? WHERE id=?", ((datetime.now() + timedelta(hours=14)).isoformat(timespec="seconds"), r["id"]))
                    from core import activity as _a
                    _a.log("info", f"circuit breaker: {_c.breaker_key(engine.normalize_url(r['apply_url'] or r['url'] or ''))} failed 3+ times today "
                                   f"with no success; skipping its jobs until tomorrow")
                    continue
            except Exception:  # noqa: BLE001
                pass
            url = r["apply_url"] or r["url"]
            url = engine.normalize_url(url)
            if "linkedin.com" in url:
                if st["li_resolved"] >= LINKEDIN_RESOLVE_CAP:
                    continue
                set_now(r, "finding the real apply page")
                engine.pace("https://www.linkedin.com", 12)  # signed-in page loads at a human pace (was 25s: too slow for 100+/day)
                time.sleep(random.uniform(0, 5))
                st["li_resolved"] += 1
                kind, val = route.resolve_linkedin(ctx, r["url"], max_applicants=lowcomp.li_max_applicants())
                _rd = dict(route.LAST)
                if _rd.get("applicants") is not None:
                    d.c.execute("UPDATE jobs SET applicants=? WHERE id=?", (_rd["applicants"], r["id"]))
                lowcomp.record(r, _rd, {"external": "qualified"}.get(kind, kind), val if kind == "error" else "")
                if kind == "crowded":
                    # LinkedIn jobs only when the page shows an EXACT count under the limit. A bound ('Over 100',
                    # 'first 25') or a silent page is not "few". Skipped with the page's own words; the limit is settings.json li_max_applicants.
                    d.set_status(r["id"], "skipped", f"LinkedIn competition: {_rd.get('text') or 'no applicant count shown'} (needs an exact count under {lowcomp.li_max_applicants()})")
                    continue
                if kind == "closed":
                    d.set_status(r["id"], "closed", "listing closed on LinkedIn")
                    continue
                if kind == "easy_apply":  # kept as a LAST RESORT, handled after direct routes if the target is not met
                    d.c.execute("UPDATE jobs SET apply_route='easy_apply' WHERE id=?", (r["id"],))
                    d.set_status(r["id"], "deferred_easy", "only Easy Apply; used last if needed")
                    deferred.append(r)
                    continue
                if kind == "error":
                    d.set_status(r["id"], "needs_you", val, needs_you_reason=val)
                    continue
                url = val
                if one_click_host(url):
                    d.set_status(r["id"], "deferred_board", "points to a one-click job board")
                    continue
                d.c.execute("UPDATE jobs SET apply_url=? WHERE id=?", (url, r["id"]))
                d.c.commit()
            attempt(r, url, ctx, pg)
            handled_fps.add(str(r.get("fingerprint") or ""))
            time.sleep(random.uniform(6, 14))
        # last resort: Easy Apply, only if the target is still not met, under the daily cap
        if st["applied"] < target and not STOP.exists() and not protect_claude_limit():
            # KB-74: jobs deferred by an EARLIER run are only in the database; the in-run `deferred` list never had them
            _seen = {r["id"] for r in deferred}
            deferred.extend([r for r in prepare_easy_queue(d, EASY_CAP, log) if r["id"] not in _seen])
        if __import__("os").environ.get("JOBHUNT_SKIP_NAUKRI"):
            # The company-sites-only worker never runs Easy Apply: worker A owns the LinkedIn session, and two workers on the same Easy Apply row
            # re-opened an application A had just submitted (a company: B's "no apply button" overwrote A's unconfirmed row with candidate).
            deferred = [r for r in deferred if r["apply_route"] != "easy_apply"]
        if deferred and st["applied"] < target and not STOP.exists():
            used = d.c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('applied','unconfirmed') AND apply_route='easy_apply' AND applied_at LIKE ?",
                               (time.strftime("%Y-%m-%d") + "%",)).fetchone()[0]
            for r in deferred:
                if st["applied"] >= target or used >= EASY_CAP or STOP.exists() or st["attempts"] >= max_attempts:
                    break
                _now = d.rows("id=?", (r["id"],))   # Easy Apply rows wait as `deferred_easy`: skip only the ones another worker/repair already settled
                if not _now or _now[0]["status"] in ("applied", "unconfirmed", "needs_you", "closed", "skipped", "already_applied", "unverified"):
                    continue
                dup = duplicate_role(d, r, handled_fps)
                if dup:
                    d.set_status(r["id"], "skipped", dup)
                    continue
                if score_now_below_bar(d, r):
                    continue
                engine.pace("https://www.linkedin.com", 25)
                if attempt(r, r["url"], ctx, pg, easy_route=True) and not dry_run:
                    used += 1
                handled_fps.add(str(r.get("fingerprint") or ""))
                time.sleep(random.uniform(8, 16))
        ctx.close()
    stopped = STOP.exists()  # KB-42: the marker STAYS; the wrapper and the guardian must keep seeing it (see the top of run)
    try:
        backup.snapshot("post-run")
        from track import export  # refresh data/tracker.xlsx so the spreadsheet is always current
        export.export(d)
    except Exception as e:  # noqa: BLE001
        log(f"post-run housekeeping warning: {e}")
    runstate.update(status="stopped" if stopped else "finished", finished=True, now=None, ended_at=time.time(), applied=st["applied"])
    try:  # learn from this run: failure classes -> data/lessons.md (read at the start of every session)
        from track import lessons
        lessons.build()
    except Exception as e:  # noqa: BLE001
        log(f"lessons warning: {e}")
    log(f"done: {st['applied']}/{target} ({st['attempts']} attempted) in {int(time.time() - t0)}s")
