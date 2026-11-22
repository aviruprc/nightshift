"""Learn from mistakes: group every non-success outcome by (portal, reason class), with examples + screenshots, into data/lessons.md
and data/lessons.json. Written after each run; read at the start of each session (SKILL.md) so the biggest failure class is fixed first."""
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BAD = ("needs_you", "failed", "unconfirmed", "blocked_portal")


def _cls(detail: str) -> str:
    s = (detail or "").lower()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"'[^']{0,60}'", "'…'", s)
    s = re.sub(r"\d+", "#", s)
    for k, rx in [("captcha", r"captcha"), ("sign-in wall", r"sign-?in|sign-?up wall|login"), ("no form found", r"no fillable form|no application form"),
                  ("validation stall", r"would not continue|did not advance"), ("unanswered question", r"unanswered required"),
                  ("timeout", r"timeout|time budget"), ("no confirmation", r"no confirmation"), ("password reset cap", r"password already reset"),
                  ("tailored cv upload", r"upload the tailored cv"), ("browser error", r"error:|exception|target closed")]:
        if re.search(rx, s):
            return k
    return s[:50] or "unknown"


def build(days: int = 3) -> dict:
    from core import activity
    from core import db as dbm
    d = dbm.DB()
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M")
    rows = d.c.execute("SELECT e.at, e.kind, e.detail, j.company, j.title, j.apply_url, j.apply_route, j.screenshot_path, j.status "
                       "FROM events e JOIN jobs j ON j.id=e.job_id WHERE e.at>=? AND e.kind IN (%s) ORDER BY e.id DESC"
                       % ",".join("?" * len(BAD)), (since, *BAD)).fetchall()
    groups = defaultdict(lambda: {"n": 0, "examples": []})
    seen = set()
    for r in rows:
        key = (r["company"], r["title"])
        if key in seen:  # one count per job (its latest outcome)
            continue
        seen.add(key)
        g = groups[(activity.portal_of(r["apply_url"] or "", r["apply_route"]), _cls(r["detail"]))]
        g["n"] += 1
        if len(g["examples"]) < 3:
            g["examples"].append({"at": r["at"][5:16], "job": f"{r['company']} - {r['title']}"[:70], "detail": (r["detail"] or "")[:160],
                                  "url": r["apply_url"], "shot": r["screenshot_path"], "now": r["status"]})
    ok = d.c.execute("SELECT COUNT(*) FROM jobs WHERE status='applied' AND applied_at>=?", (since,)).fetchone()[0]
    out = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "days": days, "applied": ok,
           "classes": sorted([{"portal": p, "reason": c, **g} for (p, c), g in groups.items()], key=lambda x: -x["n"])}
    (ROOT / "data" / "lessons.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    lines = [f"# Lessons ({out['generated']}, last {days} days) — {ok} proven applications", "",
             "Fix the TOP rows first (biggest yield). After a fix: requeue the affected jobs and add a line to 'Fixed' below.", ""]
    for x in out["classes"][:25]:
        lines.append(f"## {x['n']} × {x['portal']} — {x['reason']}")
        for e in x["examples"]:
            lines.append(f"- {e['at']} {e['job']} → {e['detail']} (now {e['now']}){' · shot ' + e['shot'] if e['shot'] else ''}")
        lines.append("")
    fixed = ROOT / "data" / "lessons_fixed.md"
    if fixed.exists():
        lines += ["# Fixed (do not regress)", fixed.read_text(encoding="utf-8")]
    (ROOT / "data" / "lessons.md").write_text("\n".join(lines), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = build()
    for x in o["classes"][:15]:
        print(f"{x['n']:3}  {x['portal'][:22]:22} {x['reason']}")
