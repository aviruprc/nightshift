"""Low-noise watcher for the Claude session: prints ONE line per new submission / guardian alert, and a digest every 15 min.
Run: python track/monitor.py (stdout lines are the events)"""
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import db  # noqa: E402

seen: set = set()
first = True
last = time.time()
last_triage = 0.0
while True:
    d = db.DB()
    today = datetime.now().strftime("%Y-%m-%d")
    for r in d.c.execute("SELECT company, status, substr(coalesce(email_proof,page_proof,status_detail,''),1,70), applied_at FROM jobs "
                         "WHERE applied_at>=? AND status IN ('applied','unconfirmed') ORDER BY applied_at", (today,)):
        k = (r[0], r[1])
        if k not in seen:
            seen.add(k)
            pass  # quiet mode: submissions are not announced one by one (they cost the user's limit); the digest summarises them
    first = False
    # failure triage (no AI): wake Claude only for a NEW cheap bug (>=4 jobs, same missing answer rule); log everything else to data/fix_backlog.md
    if time.time() - last_triage > 900:
        last_triage = time.time()
        try:
            from track import triage
            groups = triage.clusters(3)
            triage.write_backlog(triage.clusters(24))
            for (v, key), g in groups.items():
                if v == "cheap_bug" and len(g["jobs"]) >= 4 and ("triage", key) not in seen:
                    seen.add(("triage", key))
                    print(f"FIX CANDIDATE (cheap, fits the limit): {len(g['jobs'])} jobs failed on: {g['example']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"triage skipped: {type(e).__name__}", flush=True)
    # false-positive watch: a 'submitted' job with a confirmation page but NO employer email after 6 hours is suspect (Qualcomm-style false positives)
    cutoff = (datetime.now() - timedelta(hours=6)).isoformat(timespec="seconds")
    for r in d.c.execute("SELECT id, company, substr(title,1,40), applied_at, page_proof FROM jobs WHERE status='unconfirmed' AND applied_at<? AND page_proof IS NOT NULL "
                         "AND page_proof NOT LIKE 'PAGE: Naukri%'", (cutoff,)):
        key = ("suspect", r[0])
        if key not in seen:
            seen.add(key)
            print(f"SUSPECT (no receipt after 6h, check the screenshot): {r[1]} | {r[2]} | {r[4][:50]}", flush=True)
    # silent-fallback watch: DeepSeek is meant to answer first; if Claude answered most calls in the last 30 min, DeepSeek is down and the Claude limit is burning
    try:
        import json as _j
        st = _j.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
        if st.get("llm_primary") == "backup":
            cnt = Counter()
            for ln in (ROOT / "data" / "llm_usage.jsonl").read_text(encoding="utf-8").splitlines()[-400:]:
                u = _j.loads(ln)
                if u["t"] >= time.time() - 1800:
                    cnt["claude" if u["m"] in ("haiku", "sonnet") else "backup"] += 1
            key = ("fallback", int(time.time() // 3600))
            if cnt["claude"] >= 8 and cnt["claude"] > 2 * cnt["backup"] and key not in seen:
                seen.add(key)
                print(f"ALERT: Claude answered {cnt['claude']} calls in 30 min vs backup {cnt['backup']}: DeepSeek is probably signed out (dashboard: Backup AI > Sign in). Your Claude limit is being used.", flush=True)
    except Exception:  # noqa: BLE001
        pass
    try:
        for line in (ROOT / "data" / "guardian.log").read_text(encoding="utf-8").splitlines()[-6:]:
            if ("ALERT" in line or ("HEAL" in line and "receipts" not in line)) and line not in seen:
                seen.add(line)
                print(line[:200], flush=True)
    except Exception:  # noqa: BLE001
        pass
    if time.time() - last > 7200:
        last = time.time()
        since = (datetime.now() - timedelta(minutes=120)).isoformat(timespec="seconds")
        c, why = Counter(), Counter()
        for r in d.c.execute("SELECT kind, detail FROM events WHERE at>=? AND kind IN ('applied','unconfirmed','needs_you','failed','closed')", (since,)):
            c[r[0]] += 1
            if r[0] in ("failed", "needs_you"):
                why[(r[1] or "")[:70]] += 1
        try:
            h = (ROOT / "data" / "guardian.log").read_text(encoding="utf-8").splitlines()[-1][:170]
        except Exception:  # noqa: BLE001
            h = ""
        try:
            from track import triage
            vc = Counter()
            for (v, _k), g in triage.clusters(2).items():
                vc[v] += len(g["jobs"])
            tri = dict(vc)
        except Exception:  # noqa: BLE001
            tri = {}
        print("DIGEST 2h:", dict(c), "| failure verdicts:", tri, "(hard_portal = deferred to data/fix_backlog.md)", "|", h, flush=True)
    d.c.close()
    time.sleep(120)
