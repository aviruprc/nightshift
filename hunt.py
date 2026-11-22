"""Nightshift CLI. python hunt.py setup | doctor | discover | run [--live] | dashboard | ... (python hunt.py -h lists every command)"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import envfile  # noqa: E402

envfile.load()   # .env (optional, see .env.example) before anything reads the environment
from core import db as dbm  # noqa: E402
from core import llm, profile  # noqa: E402

_P_ROOT = Path(__file__).resolve().parent
OLD_TRACKER = Path(__import__("os").environ.get("JOBHUNT_OLD_TRACKER") or _P_ROOT / "data" / "tracker.csv")  # import-old: CSV of jobs you applied to elsewhere


def doctor():
    """First-run and health check. [ OK ] fine, [WARN] optional or degraded, [FAIL] must be fixed (exit code 1)."""
    import shutil as _sh

    from core import setup_wizard as sw
    worst = {"v": 0}

    def check(name, fn, level_on_error="FAIL"):
        try:
            res = fn()
            lvl, msg = res if isinstance(res, tuple) else ("OK", res)
        except Exception as e:  # noqa: BLE001
            lvl, msg = level_on_error, f"{type(e).__name__}: {str(e)[:160]}"
        worst["v"] = max(worst["v"], {"OK": 0, "WARN": 1, "FAIL": 2}[lvl])
        print(f"[{lvl:^4}] {name}: {msg}")

    def facts_check():
        f = profile.facts()
        hits = sw.example_markers_in(profile.FACTS)
        if hits:
            return "FAIL", f"still contains example values ({', '.join(hits[:4])}...): replace them with your own (docs/CONFIGURING_FACTS.md)"
        return f"{f['personal']['name']} | {len(f.get('experience') or [])} jobs, {len(f.get('education') or [])} degrees, {len(f.get('not_claimable') or [])} not-claimable skills"

    def answers_check():
        a = profile.answers()
        hits = sw.example_markers_in(profile.ANSWERS)
        if hits:
            return "FAIL", f"still contains example values ({', '.join(hits[:4])}): replace them"
        missing = [k for k in ("total_experience_years", "notice_period", "current_city", "current_country") if not a.get(k)]
        return ("WARN", f"{len(a)} answers; not recorded (forms asking for them will be parked): {', '.join(missing)}") if missing else f"{len(a)} answers"

    def prefs_check():
        pr = profile.preferences()
        if not pr.get("home_location_regex") and not profile.city():
            return "WARN", "no home location (facts.yaml preferences.home_location_regex or personal.city): only remote jobs will pass"
        return f"locations /{pr.get('home_location_regex') or profile.city()}/, " + (f"{len(pr['search_queries'])} search titles" if pr.get("search_queries") else "built-in search titles")

    def settings_check():
        if not profile.SETTINGS.exists():
            return "WARN", "data/settings.json missing: defaults are used (copy data/settings.example.json)"
        s = profile.settings()
        return f"min_score {s.get('min_score', 50)}, company cap {s.get('company_daily_cap', 3)}/day, outreach sending {'ON' if s.get('outreach_send') else 'off'}"

    def cv_check():
        p = profile.base_cv_pdf()
        return ("OK", str(p)) if p.exists() else ("WARN", f"{p} not found (needed for job-board profile uploads and outreach; settings.json base_cv_pdf)")

    check("facts.yaml", facts_check)
    check("answers.yaml", answers_check)
    check("preferences", prefs_check, "WARN")
    check("settings.json", settings_check, "WARN")
    check("your CV PDF", cv_check, "WARN")

    def database():
        from core import backup
        n = len(dbm.DB().rows())
        return f"{n} jobs | integrity {backup.check()} | {len(backup.list_backups())} backups"
    check("database", database)

    def kr():
        import keyring
        keyring.set_password("nightshift-doctor", "t", "x")
        v = keyring.get_password("nightshift-doctor", "t")
        keyring.delete_password("nightshift-doctor", "t")
        assert v == "x", "the credential store did not return what was saved"
        return f"credential store works ({type(keyring.get_keyring()).__name__})"
    check("keyring", kr)

    def mailbox():
        from core import imap
        if not imap.ADDR:
            return "FAIL", "no email in facts.yaml personal.email"
        return ("OK", f"app password stored for {imap.ADDR} on {imap.HOST} (test: python hunt.py gmail-test)") if imap.password() else \
            ("WARN", "no mailbox app password: run python hunt.py gmail-setup (codes, receipts and replies need it)")
    check("mailbox", mailbox, "WARN")

    def ai():
        ready = [p["name"] for p in llm.providers()]
        if ready:
            return f"ready: {', '.join(ready)} (python hunt.py llm-test)"
        return "WARN", "no AI provider ready: scoring and the form navigator will not run (python hunt.py llm-key --provider gemini)"
    check("AI providers", ai, "WARN")

    def claude():
        if not _sh.which("claude"):
            return "WARN", "Claude Code CLI not installed (optional)"
        return llm.ask("Reply with the single word: pong", "score", retries=0)[:20]
    if profile.settings().get("claude_for_bot", True):
        check("claude CLI (optional)", claude, "WARN")

    def chrome():
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(channel="chrome", headless=True)
            v = b.version
            b.close()
            return f"Chrome {v}"
    check("playwright + Chrome", chrome)

    def platform():
        if sys.platform != "win32":
            return "WARN", f"{sys.platform}: single commands may work; the unattended autopilot (guardian/supervisor/workers) is Windows-only"
        return "Windows: full autopilot supported"
    check("platform", platform, "WARN")
    sys.exit(1 if worst["v"] >= 2 else 0)


def _refuse_live_on_example_profile():
    """A live run with the shipped example person would submit invented facts. Refuse it outright."""
    from core import setup_wizard as sw
    hits = sw.example_markers_in(profile.FACTS, profile.ANSWERS)
    if hits:
        print("REFUSED: your profile still contains example values (" + ", ".join(hits[:5]) + ").\n"
              "Replace them with your own facts (cv/facts.yaml, data/answers.yaml), then run `python hunt.py doctor`.")
        sys.exit(2)


def import_old():
    """Mark jobs already applied in the old bot so we never re-apply."""
    d = dbm.DB()
    n = 0
    if not OLD_TRACKER.exists():
        print(f"no tracker CSV at {OLD_TRACKER} (set JOBHUNT_OLD_TRACKER). Columns: status, company, title, location, apply_url, ats, posted_at, applied_at")
        return
    with open(OLD_TRACKER, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["status"] != "applied":
                continue
            jid, _ = d.upsert_job({"source": "old-jobbot", "company": r["company"], "title": r["title"],
                                   "location": r["location"], "url": r["apply_url"], "ats": r["ats"],
                                   "posted_at": r["posted_at"]})
            d.set_status(jid, "applied", "imported from old jobbot", applied_at=r["applied_at"])
            n += 1
    print(f"imported {n} previously applied jobs")


def discover(use_boards=True, workers=12, li_seconds=480):
    from collections import Counter

    from discover import ats_api
    from rank import filter as flt
    from rank import ghost

    from core import backup, runstate
    try:
        backup.snapshot("pre-discover")
    except Exception as e:  # noqa: BLE001
        print("backup warning:", e)
    runstate.update(status="discovering", finished=False, started_at=__import__("time").time(), now=None)
    d = dbm.DB()
    stats, rej = Counter(), Counter()
    # was 3 days. Feeds are paged and search-based, and whole feeds go down (51 Workday tenants answered "Workday is currently unavailable"
    # on a Saturday): not being re-seen for 3 days retired 1,343 candidates. The apply step checks the posting itself.
    stats["expired_stale"] = d.expire_stale(10)
    raw = []
    results = ats_api.fetch_all(workers=workers)
    ok_companies = 0
    for c, jobs, err in results:
        if err:
            stats["company_errors"] += 1
            print(f"  ! {c['name']}: {err}")
            continue
        ok_companies += 1
        raw += jobs
        # close ATS jobs that vanished from a feed we fetched successfully
        live = {dbm.job_id(j["apply_url"]) for j in jobs}
        # NOTE: absence from a feed is NOT proof a job closed (feeds are paged, recency-limited or search-based, e.g. a company/Amazon/Oracle/Workday).
        # Closed jobs are detected on the job's own page when we try to apply, and old candidates expire via expire_stale.
        continue
    print(f"career sites: {ok_companies}/{len(results)} companies ok, {len(raw)} listings")

    def flush(label):
        """: ingest ran ONCE, after the slowest source. Machine back after two days: the career-site and Naukri listings (24,000) sat unread
        for 35+ minutes behind a slow aggregator, the worker had only stale jobs and submitted nothing. Each phase is ingested as soon as it is read."""
        nonlocal raw
        if raw:
            stats["listings"] += len(raw)
            ingest(d, raw, stats, rej, workers)
            print(f"{label}: ingested, {stats['new_candidates']} new candidates so far")
            d.c.commit()
        raw = []

    flush("career sites")
    if use_boards:
        from discover import aggregators, boards, linkedin
        bj, errs = boards.fetch_boards()
        print(f"indeed+naukri: {len(bj)} listings ({len(errs)} query errors)")
        raw += bj
        flush("indeed+naukri")
        try:
            lj = linkedin.fetch_linkedin(hours=72, max_seconds=li_seconds, log=lambda *a: None)
            print(f"linkedin: {len(lj)} listings ({sum(1 for j in lj if j.get('easy_apply'))} easy apply)")
            raw += lj
        except Exception as e:  # noqa: BLE001
            print(f"  ! linkedin failed: {e}")
        flush("linkedin")
        try:
            aj, aerrs = aggregators.fetch_aggregators(log=lambda *a: None)
            print(f"aggregators: {len(aj)} listings ({len(aerrs)} errors)")
            raw += aj
        except Exception as e:  # noqa: BLE001
            print(f"  ! aggregators failed: {e}")
    flush("aggregators")
    print("rejected:", dict(rej))
    print("stats:", dict(stats))
    try:
        prepare()
    except Exception as e:  # noqa: BLE001
        print("prepare warning:", type(e).__name__, str(e)[:120])
    runstate.update(status="finished", finished=True, now=None, last={"title": "Job discovery", "status": "finished",
                                                                      "detail": f"{stats['new_candidates']} new candidates found"})


def _note_rejected(j, why, cap=20000):
    import json
    p = Path(__file__).resolve().parent / "data" / "rejected_sample.jsonl"
    try:
        if p.exists() and p.stat().st_size > cap * 200:
            return
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"why": why, "company": j.get("company"), "title": j.get("title"), "location": j.get("location"), "source": j.get("source")}, ensure_ascii=False) + chr(10))
    except Exception:  # noqa: BLE001
        pass


def prepare(limit=400):
    """Score new candidates right after discovery so the applier only applies.
    Nothing is skipped here: the fit bar is applied at queue time, so changing the bar later needs no rescoring."""
    from rank import score as sc
    d = dbm.DB()
    rows = [r for r in d.rows("status='candidate' AND score IS NULL") if len((r["description"] or "").strip()) >= 400]
    rows.sort(key=lambda r: r["posted_at"] or r["first_seen"] or "", reverse=True)
    n = sc.score_all(d, rows[:limit], log=lambda *a: None) if rows else 0
    print(f"prepared: scored {n} of {len(rows)} new candidates with a description")
    return n


def ingest(d, raw, stats, rej, workers=12):
    """Filter -> fetch missing JDs -> full filter -> dedupe -> upsert -> ghost check. Shared by every discovery source."""
    from rank import filter as flt
    from rank import ghost

    from discover import ats_api
    # pass 1: cheap title/location filter (no description yet)
    keep = []
    for j in raw:
        fam, why = flt.check({**j, "description": j.get("description", "") if j.get("description") else ""})
        if why in ("title_excluded", "off_target", "location"):
            rej[why] += 1
            # 13,585 listings were rejected on the title alone and nobody could say how many were Bengaluru roles she could do.
            # A title/off-target rejection whose LOCATION is fine is written down, so the filter can be audited with data (data/rejected_sample.jsonl).
            if why != "location" and flt.location_ok(j.get("location", ""), j.get("_remote", False)):
                rej[why + "_in_her_location"] += 1
                _note_rejected(j, why)
            continue
        keep.append((j, fam))
    # pass 2: fetch missing descriptions only for survivors, then full check
    from concurrent.futures import ThreadPoolExecutor
    need = [j for j, _ in keep if not j.get("description")]
    li = [j for j in need if j.get("source") == "board:linkedin"]
    need = [j for j in need if j.get("source") not in ("board:linkedin", "board:naukri")] + li[:40]  # LinkedIn rate-limited; Naukri JDs are read in the browser at apply time
    with ThreadPoolExecutor(workers) as ex:
        for j, desc in zip(need, ex.map(ats_api.lazy_description, need)):
            j["description"] = desc
    for j, _ in keep:
        fam, why = flt.check(j)
        if why:
            rej[why.split("_")[0] if why.startswith("experience") else why] += 1
            continue
        if d.already_applied(j["company"], j["title"], j.get("location", "")):
            rej["already_applied"] += 1
            continue
        # the user's rule: avoid Easy Apply whenever possible. Prefer a direct (company/ATS/external) route to the same job.
        fp = dbm.fingerprint(j["company"], j["title"], j.get("location", ""))
        if j.get("easy_apply"):
            if d.c.execute("SELECT 1 FROM jobs WHERE fingerprint=? AND apply_route IS NULL "
                           "AND status IN ('candidate','planned','needs_you','applied')", (fp,)).fetchone():
                rej["easy_apply_has_direct"] += 1
                continue
        else:
            d.c.execute("UPDATE jobs SET status='skipped', status_detail='direct route exists' "
                        "WHERE fingerprint=? AND apply_route='easy_apply' AND status='candidate'", (fp,))
        jid, new = d.upsert_job(j)
        if not new:
            stats["seen_before"] += 1
            continue
        row = d.rows("id=?", (jid,))[0]
        fl = ghost.flags(row, d.repost_count(row["company"], row["title"], row["location"] or ""))
        d.c.execute("UPDATE jobs SET family=?, ghost_flags=? WHERE id=?", (fam, ",".join(fl), jid))
        if ghost.is_ghost(fl) and fam != "returnship":   # returnship / career-break programmes are exactly what she wants and stay open for months
            d.set_status(jid, "skipped", "ghost/stale: " + ",".join(fl))
            stats["ghost_skipped"] += 1
        else:
            d.set_status(jid, "candidate", "")
            stats["new_candidates"] += 1


def discover_naukri(pages=2, job_age=3):
    """Fresh Naukri jobs from the signed-in browser: the route with the best proof (Naukri's 'Applied' badge) and throughput."""
    from collections import Counter

    from playwright.sync_api import sync_playwright

    from apply import browser
    from discover import naukri_search
    d = dbm.DB()
    stats, rej = Counter(), Counter()
    with sync_playwright() as pw:
        ctx = browser.launch(pw, headless=True)
        try:
            raw = naukri_search.fetch_naukri(ctx, pages=pages, job_age=int(__import__("os").environ.get("JOBHUNT_NAUKRI_AGE") or job_age))
        finally:
            ctx.close()
    stats["listings"] = len(raw)
    ingest(d, raw, stats, rej)
    print("naukri rejected:", dict(rej))
    print("naukri stats:", dict(stats))


def score(limit=300):
    from rank import score as sc
    d = dbm.DB()
    rows = d.rows("status='candidate' AND score IS NULL")
    rows.sort(key=lambda r: r["posted_at"] or r["first_seen"], reverse=True)
    rows = rows[:limit]
    print(f"scoring {len(rows)} candidates with Haiku...")
    sc.score_all(d, rows)
    # this used to retire every candidate under 60 as 'low fit score' (271 jobs) while the real bar was 45. The bar is applied at queue time
    # (runner.min_score); a scored job stays a candidate.


def report():
    import yaml

    from track import export
    ret = []
    p = Path(__file__).resolve().parent / "discover" / "returnships.yaml"
    if p.exists():
        y = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        ret = y.get("returnships") or y.get("programmes") or (y if isinstance(y, list) else [])
    x, h = export.export(dbm.DB(), ret)
    print(f"tracker: {x}\ndashboard: {h}")


def pool():
    d = dbm.DB()
    q = lambda s: [tuple(r) for r in d.c.execute(s)]  # noqa: E731
    print("candidates by family:", q("SELECT family, COUNT(*) FROM jobs WHERE status='candidate' GROUP BY family"))
    print("candidates by source:", q("SELECT source, COUNT(*) FROM jobs WHERE status='candidate' GROUP BY source"))
    print("score>=75:", q("SELECT COUNT(*) FROM jobs WHERE status='candidate' AND score>=75")[0][0],
          "| 60-74:", q("SELECT COUNT(*) FROM jobs WHERE status='candidate' AND score BETWEEN 60 AND 74")[0][0])
    print("posted within 7d:", sum(1 for r in d.rows("status='candidate'")
                                   if (ghost_age(r) or 99) <= 7))
    for r in q("SELECT score, company, title, location FROM jobs WHERE status='candidate' ORDER BY score DESC LIMIT 15"):
        print(r)


def ghost_age(r):
    from rank import ghost
    return ghost.age_days(r["posted_at"], r["first_seen"])


def status():
    d = dbm.DB()
    for r in d.c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status"):
        print(f"{r['status']:12} {r['n']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "doctor", "import-old", "status", "discover", "score", "report", "pool", "login", "run", "dashboard",
                                    "backup", "restore", "history", "assist", "proof", "naukri", "gmail-setup", "gmail-test", "llm-key", "llm-test", "set-password", "accounts-todo", "replies", "learn", "prepare"])
    ap.add_argument("--provider", default="gemini", help="llm-key: gemini or groq")
    ap.add_argument("--days", type=int, default=60, help="replies: how many days of mail to read")
    ap.add_argument("--file", default=None, help="backup file for restore (default: newest)")
    ap.add_argument("--target", type=int, default=10)
    ap.add_argument("--live", action="store_true", help="really submit (default is dry run)")
    ap.add_argument("--show", action="store_true", help="show the browser window")
    ap.add_argument("--max-attempts", type=int, default=40)
    ap.add_argument("--only", default=None, help="only jobs whose apply URL contains this text, e.g. naukri.com")
    ap.add_argument("--no-boards", action="store_true")
    ap.add_argument("--limit", type=int, default=300)
    a = ap.parse_args()
    if a.cmd == "setup":
        from core import setup_wizard
        raise SystemExit(setup_wizard.run())
    if a.cmd == "discover":
        discover(not a.no_boards)
    elif a.cmd == "naukri":
        discover_naukri()
    elif a.cmd == "score":
        score(a.limit)
    elif a.cmd == "prepare":
        prepare(a.limit)
    elif a.cmd == "learn":   # what the outcomes say: portal success, what goes first, what to fix next
        from apply import learn
        learn.print_report(dbm.DB())
    elif a.cmd == "gmail-setup":
        import getpass

        from core import imap
        print("Create an app password at https://myaccount.google.com/apppasswords (name it 'nightshift'), then paste the 16 letters here.")
        imap.save_password(getpass.getpass("Gmail app password (hidden): "))
        print("login test:", *imap.check())
    elif a.cmd == "llm-key":
        import getpass

        import keyring

        from core import llm
        urls = {"gemini": "https://aistudio.google.com/apikey", "groq": "https://console.groq.com/keys", "deepseek-api": "https://platform.deepseek.com/api_keys", "grok": "https://console.x.ai"}
        print(f"Create a free API key at {urls.get(a.provider, '')} and paste it here (input is hidden).")
        prov = next(p for p in __import__("json").loads((llm.DATA / "llm_providers.json").read_text(encoding="utf-8")) if p["name"] == a.provider)
        keyring.set_password("nightshift-llm", llm.key_name(prov), getpass.getpass(f"{a.provider} API key: ").strip())
        try:
            print("test reply:", llm._ask_provider("Reply with the single word: ok", prov, "score", 60)[:40], "-> provider is live")
            llm.clear_cooldown(a.provider)
        except Exception as e:  # noqa: BLE001
            print("key stored, but the test call failed:", type(e).__name__, str(e)[:140])
    elif a.cmd == "accounts-todo":  # one list of every portal whose login needs ONE manual reset by the user, with the jobs waiting behind it
        import re as _re
        from collections import defaultdict
        from urllib.parse import urlparse as _up

        from apply import accounts
        from core import db as _dbm
        _d = _dbm.DB()
        todo = defaultdict(lambda: {"jobs": 0, "best": 0, "url": ""})
        for r in _d.rows("status IN ('needs_you','failed','candidate') AND (coalesce(needs_you_reason,'') || ' ' || coalesce(status_detail,'')) LIKE '%password%'"):
            txt = f"{r['needs_you_reason'] or ''} {r['status_detail'] or ''}".lower()
            if not _re.search(r"saved password|sign-in|password rejected|manual reset|one reset", txt):
                continue
            h = accounts.host_key(_up(r["apply_url"] or r["url"] or "").netloc)
            x = todo[h]
            x["jobs"] += 1
            x["best"] = max(x["best"], r["score"] or 0)
            x["url"] = x["url"] or (r["apply_url"] or r["url"] or "")
        print("Portals waiting for ONE manual password reset by you (most jobs first):")
        for h, x in sorted(todo.items(), key=lambda kv: -kv[1]["jobs"]):
            print(f"  {h:38} {x['jobs']:3} jobs (best fit {x['best']})  saved pw: {'yes' if accounts.get(h) else 'no'}  verified: {accounts.is_verified(h)}")
            print(f"      1) open {x['url'][:100]}   2) Sign In -> Forgot password (ONCE)   3) python hunt.py set-password {h}")
        if not todo:
            print("  none")
    elif a.cmd == "set-password":  # python hunt.py set-password --provider <site host>: store a portal password you typed yourself (hidden), mark the account working
        import getpass

        from apply import accounts
        host = a.provider if "." in a.provider else input("site host (e.g. wf.wd1.myworkdayjobs.com): ").strip()
        accounts.save(host, getpass.getpass(f"password for {accounts.host_key(host)} (hidden): ").strip())
        accounts.mark_verified(host)
        print("saved for", accounts.host_key(host), "and marked as a working account: the bot signs in with it and never resets it")
    elif a.cmd == "llm-test":
        import json as _j

        from core import llm
        print("provider chain (priority = order in data/llm_providers.json):")
        for p in _j.loads((llm.DATA / "llm_providers.json").read_text(encoding="utf-8")):
            cd = llm.cooldown_reason(p["name"])
            state = ("OFF" if not p.get("enabled", True) else "resting: " + cd[0] if cd else "ready" if (p.get("type") not in ("openai", "gemini") or llm._has_key(llm.key_name(p))) else "no key yet")
            print(f"  {p['name']:14} {p['type']:8} {p['model']:28} {state}")
        if a.only:  # one named provider: success wakes it up, failure rests it with the real reason (the dashboard shows it)
            prov = next((p for p in llm.all_providers() if p["name"] == a.only), None)
            try:
                out = llm._ask_provider('Reply with exactly this JSON and nothing else: {"ok": true}', prov, "score", 60)
                llm.clear_cooldown(a.only)
                print(a.only, "OK ->", out[:60])
            except Exception as e:  # noqa: BLE001
                llm.set_cooldown(a.only, e)
                print(a.only, "FAILED:", type(e).__name__, str(e)[:120])
            raise SystemExit
        import os as _os
        _os.environ["JOBHUNT_FORCE_BACKUP"] = "1"
        out, who = llm._ask_with_fallback('Reply with exactly this JSON and nothing else: {"ok": true}', "score", "haiku", 60, 0)
        print("answered by:", who, "->", out[:80])
    elif a.cmd == "gmail-test":
        from core import imap
        ok, why = imap.check()
        print("Gmail IMAP:", "OK" if ok else "FAILED", why)
        if ok:
            for m in imap.search("newer_than:2d", limit=5):
                print("-", m["from"][:24], "|", m["subject"][:60])
    elif a.cmd == "proof":
        from apply import proof
        proof.reconcile()
    elif a.cmd == "replies":   # employer replies (rejections, closed positions, interviews, assessments, offers) -> replies table + jobs.outcome
        from apply import replies
        print(replies.scan(days=a.days))
    elif a.cmd == "assist":
        from apply import assist
        assist.serve()
    elif a.cmd == "backup":
        from core import backup
        print("saved", backup.snapshot("manual"), "| integrity:", backup.check())
    elif a.cmd == "restore":
        from pathlib import Path as _P

        from core import backup
        src = _P(a.file) if a.file else (backup.list_backups() or [None])[0]
        if not src:
            print("no backups found")
        else:
            print("restored from", src, "| safety copy of the previous database:", backup.restore(src))
    elif a.cmd == "history":
        p = _P_ROOT / "data" / "applications.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        print(f"{len(lines)} recorded status changes in {p}")
        for ln in lines[-15:]:
            import json as _j
            x = _j.loads(ln)
            print(f"{x['at'][:16]} {x['status']:13} {x['company'][:22]:22} {x['title'][:40]}")
    elif a.cmd == "run":
        if a.live:
            _refuse_live_on_example_profile()
        from apply import runner
        runner.run(a.target, dry_run=not a.live, max_attempts=a.max_attempts, show=a.show, only=a.only)
    elif a.cmd == "dashboard":
        from track import live
        live.serve()
    elif a.cmd == "login":
        from apply import browser
        browser.login_session()
    else:
        {"doctor": doctor, "import-old": import_old, "status": status, "report": report, "pool": pool}[a.cmd]()
