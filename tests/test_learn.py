""", "only the easiest paths", "use reinforcement learning", "auto load when the queue is over".
The queue order is learned from outcomes (apply/learn.py, a UCB bandit over portals); nothing here loosens proof or truthfulness rules."""
import inspect

from apply import learn, runner


class D:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def rows(self, where="", params=()):
        if "status='candidate'" in where:
            return [r for r in self._rows if r["status"] == "candidate"]
        if "'failed'" in where and "'applied'" not in where:
            return [r for r in self._rows if r["status"] in learn.LOSS]
        if "'failed'" in where:
            return [r for r in self._rows if r["status"] in learn.WIN + learn.LOSS]
        return [r for r in self._rows if r["status"] in learn.WIN]


def J(status, url, company="C"):
    return {"status": status, "apply_url": url, "url": url, "company": company, "status_detail": "x", "family": "data_bi", "source": "ats:x"}


def test_portal_names_the_arm():
    assert learn.portal({"apply_url": "https://www.naukri.com/job-1"}) == "naukri"
    assert learn.portal({"apply_url": "https://wf.wd1.myworkdayjobs.com/x"}) == "workday"
    assert learn.portal({"apply_url": "https://career5.successfactors.eu/x"}) == "successfactors"
    assert learn.portal({"apply_url": "https://www.acme.com/careers/1"}) == "acme.com"


def test_an_untried_portal_is_explored_before_a_proven_one_and_a_failing_one_sinks():
    hist = [J("applied", "https://www.naukri.com/1")] * 50 + [J("failed", "https://boards.greenhouse.io/x")] * 6
    st = learn.stats(D(hist))
    untried, proven, failing = learn.value("lever", st), learn.value("naukri", st), learn.value("greenhouse", st)
    assert untried > proven > failing


def test_a_portal_that_starts_working_climbs_back():
    bad = learn.stats(D([J("failed", "https://jobs.lever.co/x")] * 6 + [J("applied", "https://www.naukri.com/1")] * 30))
    good = learn.stats(D([J("failed", "https://jobs.lever.co/x")] * 6 + [J("applied", "https://jobs.lever.co/x")] * 12 + [J("applied", "https://www.naukri.com/1")] * 30))
    assert learn.value("lever", good) > learn.value("lever", bad)


def test_plan_order_puts_a_new_company_ahead_of_one_already_covered(monkeypatch):
    from apply import lowcomp
    monkeypatch.setattr(lowcomp, "li_max_applicants", lambda: None)
    d = D([J("applied", "https://careers.ey.com/1", "EY")])
    rows = [{"id": "1", "company": "EY", "apply_url": "https://careers.ey.com/2", "url": "", "family": "data_bi"},
            {"id": "2", "company": "Newco", "apply_url": "https://careers.ey.com/3", "url": "", "family": "data_bi"}]
    assert [r["id"] for r in runner.plan_order(rows, d)] == ["2", "1"]


def test_company_daily_cap_keeps_one_company_from_filling_the_queue(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "ROOT", tmp_path)   # no settings file: default cap 3
    ranked = [{"id": str(i), "company": "EY", "family": "data_bi"} for i in range(6)] + [{"id": "r", "company": "EY", "family": "returnship"},
                                                                                           {"id": "n", "company": "Newco", "family": "data_bi"}]

    class Empty:
        def rows(self, *a, **k):
            return []
    assert [r["id"] for r in runner.company_cap(Empty(), ranked)] == ["0", "1", "2", "r", "n"]


def test_career_site_jobs_stay_fresh_longer_than_board_jobs():
    from datetime import datetime, timedelta
    old = (datetime.now() - timedelta(days=12)).isoformat(timespec="seconds")
    ats = {"family": "data_bi", "posted_at": old, "first_seen": old, "source": "ats:workday"}
    board = {"family": "data_bi", "posted_at": old, "first_seen": old, "source": "board:shine"}
    assert runner.fresh([ats, board]) == [ats]


def test_fix_list_ranks_the_portal_with_most_blocked_jobs_first():
    rows = [J("candidate", "https://www.shine.com/j/%d" % i) for i in range(40)] + [J("candidate", "https://www.naukri.com/%d" % i) for i in range(40)] \
        + [J("applied", "https://www.naukri.com/a%d" % i) for i in range(40)]
    assert learn.fix_list(D(rows))[0]["portal"] == "shine"


def test_a_low_queue_starts_the_full_search_and_scoring_runs_at_discovery():
    from track import guardian
    import hunt
    src = inspect.getsource(guardian.main)
    assert "last_refill" in src and "full discovery on every source" in src
    assert "prepare()" in inspect.getsource(hunt.discover)
    assert "low fit score" not in inspect.getsource(hunt.score).split("#")[0]   # scoring never retires a job: the bar is applied at queue time


def test_the_scorer_judges_eligibility_and_still_names_the_skills_she_lacks():
    from rank import score
    rendered = score.render_prompt([])   # the eligible-role list now comes from facts.yaml preferences.eligible_roles
    assert "ELIGIBLE" in rendered and "KYC" in rendered and "{lacks}" in score.PROMPT and "Be strict" not in rendered


def test_an_old_career_site_posting_is_not_a_ghost_but_an_old_board_card_is():
    from datetime import datetime, timedelta
    from rank import ghost
    old = (datetime.now() - timedelta(days=35)).isoformat(timespec="seconds")
    assert not ghost.is_ghost(ghost.flags({"title": "x", "posted_at": old, "source": "ats:workday"}))
    assert ghost.is_ghost(ghost.flags({"title": "x", "posted_at": old, "source": "board:shine"}))


def test_a_company_career_site_goes_ahead_of_a_job_board_all_else_equal():
    st = {}
    direct = {"company": "A", "apply_url": "https://careers.acme.com/1"}
    board = {"company": "B", "apply_url": "https://www.shine.com/job/1"}
    assert learn.job_value(direct, st, set()) > learn.job_value(board, st, set())


def test_exploring_new_portals_never_stops_the_sends(monkeypatch):
    """-23:00: 25 attempts, 2 sends, because every untried company site went ahead of the portals that work."""
    hist = [J("applied", "https://www.naukri.com/%d" % i) for i in range(20)]
    d = D(hist)
    q = [{"id": "x%d" % i, "company": "X%d" % i, "apply_url": "https://careers.x%d.com/1" % i, "url": "", "family": "data_bi"} for i in range(4)] \
        + [{"id": "n%d" % i, "company": "N%d" % i, "apply_url": "https://www.naukri.com/j%d" % i, "url": "", "family": "data_bi"} for i in range(4)] \
        + [{"id": "r", "company": "R", "apply_url": "https://careers.r.com/1", "url": "", "family": "returnship"}]
    out = [r["id"] for r in runner.keep_sends_flowing(q, d)]
    assert out[0] == "r"                                   # returnships first
    assert out[1:4] == ["n0", "n1", "x0"]                  # two on a portal that works, then one being explored
    assert sorted(out) == sorted(r["id"] for r in q)       # nobody is dropped
