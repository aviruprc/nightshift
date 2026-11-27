"""Recruiter outreach DRAFTS ONLY: short, honest messages the user can paste into LinkedIn / email herself.

Nothing here sends anything, opens a browser or contacts anyone: it writes data/recruiter_drafts.md. Every sentence comes from cv/facts.yaml and
data/answers.yaml plus the application record; no skill outside the facts, no number that is not in them, no claim about the company's job beyond
its title. A recorded career break (answers.yaml career_gap_reason) is stated plainly. Addresses are never guessed: each draft is addressed to the
company's talent team and the file says where to look for a real recruiter (the careers page / the job post's contact line).

python -m apply.recruiter_drafts   ->  data/recruiter_drafts.md
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from core import profile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "recruiter_drafts.md"


def _facts() -> dict:
    return profile.facts()


def _background(f: dict) -> str:
    """One sentence about the candidate from the recorded facts only (years, latest role, highest education)."""
    a = profile.answers()
    years = profile.total_years_text()
    exp = (f.get("experience") or [{}])[0]
    edu = (f.get("education") or [{}])[0]
    bits = []
    if exp.get("title"):
        bits.append(f"I am a {exp['title']}" + (f" with {years}+ years of experience" if years else ""))
    elif years:
        bits.append(f"I have {years}+ years of experience")
    if exp.get("company"):
        bits.append(f"most recently at {exp['company']}")
    s = " ".join(bits)
    if edu.get("degree"):
        s += (". " if s else "") + f"I hold {edu['degree']}"
    gap = str(a.get("career_gap_reason") or "").strip()
    if gap:
        s += f". {gap.rstrip('.')}"
    where = str(a.get("current_city") or "").strip()
    if where:
        s += f". Based in {where}"
    return s.strip(" .") + "."


def _signoff(f: dict) -> str:
    p = f.get("personal") or {}
    contact = " | ".join(str(x) for x in (p.get("phone"), p.get("email"), p.get("linkedin")) if x)
    return f"Thank you,\n{p.get('name', '')}\n{contact}"


def intro(f: dict, role: str, company: str, applied_on: str = "") -> str:
    """One draft for a company the user applied to."""
    when = f" on {applied_on}" if applied_on else ""
    return (f"Hello {company} talent team,\n\n"
            f"I applied{when} for the {role} role. {_background(f)}\n\n"
            f"If it helps, I would welcome a short call to talk about the role. My CV is attached to the application.\n\n"
            f"{_signoff(f)}")


def generic_recruiter(f: dict) -> str:
    return (f"Hello,\n\n{_background(f)} I am looking for roles in my field and would be glad to share my CV and speak with you.\n\n"
            f"{_signoff(f)}")


def build(db_path: Path | None = None, days: int = 10, limit: int = 15) -> str:
    f = _facts()
    c = sqlite3.connect(str(db_path or ROOT / "data" / "jobhunt.db"))
    c.row_factory = sqlite3.Row
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    rows = c.execute("SELECT company, title, applied_at FROM jobs WHERE status IN ('applied','unconfirmed') AND applied_at >= ? ORDER BY applied_at DESC",
                     (since,)).fetchall()
    seen, picks = set(), []
    for r in rows:
        co = re.sub(r"\s+", " ", (r["company"] or "").strip())
        if not co or co.lower() in seen or co.lower().startswith(("careers at", "unknown")):
            continue
        seen.add(co.lower())
        title = re.split(r"\s*[|,(]\s*|\s+-\s+(?=[A-Z0-9])", r["title"].strip())[0].strip()[:70] or r["title"].strip()[:70]   # drop salary / shift noise
        picks.append((co, title, r["applied_at"][:10]))
        if len(picks) >= limit:
            break
    out = [f"# Recruiter message DRAFTS (built {datetime.now():%Y-%m-%d %H:%M}) - nothing was sent",
           "", "Paste these into LinkedIn / email yourself. Each uses only the facts in your CV record. Find a real recruiter on the company's careers page or the",
           "job post's contact line; the bot never guesses an address.", "", "## For companies you applied to in the last "
           f"{days} days", ""]
    for co, title, day in picks:
        out += [f"### {co} - {title} (applied {day})", "", "```", intro(f, title, co, day), "```", ""]
    out += ["## A general message for recruiters and staffing firms", "", "```", generic_recruiter(f), "```", ""]
    return "\n".join(out)


def main() -> None:
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
