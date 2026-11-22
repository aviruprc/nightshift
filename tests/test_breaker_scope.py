""": the daily circuit breaker switched off 26 portals in one evening. It counted sent-but-unconfirmed applications, closed postings
and parked questions as failures, and on a shared ATS three failures at three companies blocked every company there."""
import inspect

from apply import runner
from core import cache
from core import db as dbm


def test_a_shared_ats_is_counted_per_company():
    assert cache.breaker_key("https://jobs.lever.co/acme/123/apply") == "jobs.lever.co/acme"
    assert cache.breaker_key("https://job-boards.greenhouse.io/rubrik/jobs/1") == "job-boards.greenhouse.io/rubrik"
    assert cache.breaker_key("https://jobs.smartrecruiters.com/WNS/744000") == "jobs.smartrecruiters.com/wns"
    assert cache.breaker_key("https://careers.wipro.com/job/x/1") == "careers.wipro.com"
    assert cache.breaker_key("https://wf.wd1.myworkdayjobs.com/WellsFargoJobs/job/1") == "wf.wd1.myworkdayjobs.com"


def test_three_failures_at_one_company_do_not_block_another_on_the_same_ats(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "b.db")
    for _ in range(3):
        cache.remember("jobs.lever.co", False, "page stopped changing", day_key="jobs.lever.co/acme")
    assert cache.host_blocked("jobs.lever.co/acme") and not cache.host_blocked("jobs.lever.co/zed")


def test_outcomes_that_say_nothing_about_the_portal_are_not_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "b2.db")
    for _ in range(4):
        cache.remember("x.example.com", False, "posting closed", count_day=False)
    assert not cache.host_blocked("x.example.com")


def test_the_runner_counts_a_sent_application_as_a_success():
    src = inspect.getsource(runner)
    assert '"already_applied", "unconfirmed")' in src and "count_day=_sent or s == \"failed\"" in src


def test_an_ai_outage_is_not_a_portal_failure():
    """: no AI for most of the day; a company's Workday site was charged 189 failures and two portals were switched off with no real attempt."""
    import inspect
    from apply import runner
    assert cache.ai_outage("navigator: LLM unavailable (LLMUnavailable: claude: resting after a failure (usage limit: ...))")
    assert cache.ai_outage("answering 3 form question(s): no AI answered")
    assert not cache.ai_outage("navigator: page stopped changing () last: step 9: click #20 Next")
    assert "not _cache.ai_outage(res.get(\"detail\", \"\"))" in inspect.getsource(runner)
