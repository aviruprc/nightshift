"""Recruiter outreach DRAFTS: honest, from the recorded facts only, nothing sent. Plus a sanity check of every company-feed file."""
import glob
import re
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yaml  # noqa: E402
from apply import recruiter_drafts as rd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_a_draft_uses_only_the_recorded_facts():
    f = rd._facts()
    text = rd.intro(f, "Data Analyst", "Acme", "2026-11-27") + rd.generic_recruiter(f)
    for forbidden in f["not_claimable"]:
        assert not re.search(rf"\b{re.escape(str(forbidden))}\b", text, re.I), forbidden
    assert f["personal"]["name"] in text and "Example Retail Co" in text and "4+ years" in text
    assert "http" not in text.replace("linkedin.com", "")        # no invented links


def test_the_builder_reads_recent_applications_and_cleans_titles(tmp_path):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE jobs(company TEXT, title TEXT, status TEXT, applied_at TEXT)")
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    c.execute("INSERT INTO jobs VALUES('Acme Staffing','Data Analyst , Night Shift , Salary 5.6 + bonus','applied',?)", (now,))
    c.execute("INSERT INTO jobs VALUES('Old Co','Analyst','applied','2020-01-01T00:00:00')")
    c.commit()
    out = rd.build(db)
    assert "Acme Staffing - Data Analyst (applied" in out and "Old Co" not in out and "nothing was sent" in out


def test_the_module_never_sends_or_opens_anything():
    src = Path(rd.__file__).read_text(encoding="utf-8")
    assert not re.search(r"smtplib|send_message|requests\.|playwright|webbrowser|subprocess", src)


def test_every_company_feed_entry_names_a_company():
    files = glob.glob(str(ROOT / "discover" / "companies_*.yaml"))
    assert files
    for fn in files:
        data = yaml.safe_load(Path(fn).read_text(encoding="utf-8")) or {}
        for e in data.get("companies") or []:
            assert e.get("name"), (fn, e)
