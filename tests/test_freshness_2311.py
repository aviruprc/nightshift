"""A queue that reads empty while rows exist: board rows pointing at a company site aged out at the board limit, Naukri rows aged out at 7 days although
a Naukri listing stays open for weeks, and the guardian counted every scored candidate as 'queued' while the worker's real queue was 0."""
import inspect
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import runner  # noqa: E402


def _row(source, url, days_old, family="generic"):
    seen = (datetime.now() - timedelta(days=days_old)).isoformat(timespec="seconds")
    return {"source": source, "apply_url": url, "url": url, "first_seen": seen, "posted_at": None, "family": family}


def test_a_board_row_with_a_company_site_apply_link_keeps_the_company_site_limit():
    old_company = _row("board:naukri", "https://careers.example-corp.com/in-en/jobdetails?id=1", 20)
    assert runner.fresh([old_company]) == [old_company]


def test_naukri_keeps_a_longer_limit_but_not_forever():
    n10 = _row("board:naukri", "https://www.naukri.com/job-listings-x", 10)
    n30 = _row("board:naukri", "https://www.naukri.com/job-listings-y", 30)
    assert runner.fresh([n10, n30]) == [n10]


def test_other_boards_keep_the_short_limit():
    shine = _row("board:shine", "https://www.shine.com/jobs/x", 10)
    li = _row("board:linkedin", "https://www.linkedin.com/jobs/view/1", 10)
    assert runner.fresh([shine, li]) == []


def test_runnable_rows_is_what_the_guardian_counts():
    from track import guardian
    assert "runner.runnable_rows(d)" in inspect.getsource(guardian.fit_queue)


def test_account_walled_aggregators_are_not_applied_to():
    assert runner.JUNK_HOSTS.search("https://bandana.com/jobs/955b5e86-179b-4157-8e3e-0197d72d5fde")


def test_the_guardian_watches_naukri_supply_on_its_own():
    from track import guardian
    assert guardian.NAUKRI_LOW == 10
    src = inspect.getsource(guardian)
    assert "naukri_supply() < NAUKRI_LOW" in src and "naukri_apply.paused()" in src
