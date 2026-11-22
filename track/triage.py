"""Failure triage without AI: every failure is sorted into a verdict so the limited Claude budget goes only where it pays off.
  genuine nothing to fix (listing closed, site lockout, not eligible)
  cheap_bug a missing answer rule / wording: a few lines + a test, worth fixing right away -> the monitor WAKES Claude for these
  hard_portal navigator stuck / time budget / too many steps on a difficult portal: real work -> logged to data/fix_backlog.md for next week
  infra LLM down, DeepSeek signed out, browser busy, Gmail: an alert, handled by the guardian
"""
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RULES = [
    ("infra", r"llm unavailable|signed out|browser busy|backup ai|usage limit|no verification email|password-reset email arrived|app password"),
    ("genuine", r"closed|expired|no longer|no apply button|listing closed|maximum attempts|too many attempts|locked|not eligible|already applied|blocked until"),
    ("cheap_bug", r"unanswered required|could not read the question"),
    ("hard_portal", r"stopped changing|did not advance|time budget|too many steps|no fillable form|no form found|no automated flow|sign-in/sign-up wall|login required|failed to|validation"),
]


def verdict(detail: str) -> str:
    d = (detail or "").lower()
    for v, rx in RULES:
        if re.search(rx, d):
            return v
    return "other"


def norm(detail: str) -> str:
    """Group key: the reason without numbers, urls, step counters, or company names."""
    s = (detail or "").lower()
    s = re.sub(r"^\s*retry at [\d:]+ \((transient|lockout)[^)]*\):\s*", "", s)   # scheduler prefix added by the retry logic
    s = re.sub(r"last: step.*$", "", s)                                          # which step it stopped on does not matter
    s = re.sub(r"https?://\S+|\d+|\(.*?\)", "", s)
    return re.sub(r"\s+", " ", s).strip(" :;|")[:70]


def clusters(hours: int = 6):
    from core import activity, db
    d = db.DB()
    since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    groups = defaultdict(lambda: {"jobs": set(), "portal": Counter(), "example": ""})
    for r in d.c.execute("SELECT j.id, j.company, j.apply_url, j.apply_route, e.detail FROM events e JOIN jobs j ON j.id=e.job_id "
                         "WHERE e.at>=? AND e.kind IN ('failed','needs_you','candidate') AND e.detail != ''", (since,)):
        det = r["detail"] or ""
        if not re.search(r"unanswered|stopped|budget|steps|form|closed|lock|apply button|signed out|unavailable|sign-in|validation|advance|read the question|verification", det, re.I):
            continue
        v = verdict(det)
        k = (v, norm(det))
        groups[k]["jobs"].add(r["id"])
        groups[k]["portal"][activity.portal_of(r["apply_url"] or "", r["apply_route"])] += 1
        groups[k]["example"] = groups[k]["example"] or f"{r['company'][:18]}: {det[:90]}"
    return groups


def write_backlog(groups, path: Path = ROOT / "data" / "fix_backlog.md"):
    lines = [f"# Fix backlog (auto-generated {datetime.now():%Y-%m-%d %H:%M})", "", "Cheap bugs are fixed immediately; everything below waits for a session with Claude budget.", ""]
    for verdict_name, title in (("hard_portal", "Hard portals (real work, next week)"), ("cheap_bug", "Cheap bugs (answer rules)"), ("infra", "Infrastructure")):
        items = sorted(((len(g["jobs"]), k, g) for k, g in groups.items() if k[0] == verdict_name), key=lambda x: -x[0])
        if items:
            lines.append(f"## {title}")
            for n, k, g in items[:12]:
                lines.append(f"- {n} jobs | {', '.join(p for p, _ in g['portal'].most_common(2))} | {k[1]} | e.g. {g['example']}")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    g = clusters(int(sys.argv[1]) if len(sys.argv) > 1 else 6)
    write_backlog(g)
    for (v, k), x in sorted(g.items(), key=lambda kv: -len(kv[1]["jobs"]))[:12]:
        print(f"{len(x['jobs']):3} {v:12} {k[:60]}")
