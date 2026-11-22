"""A job deferred as "only Easy Apply; used last if needed" must still be reachable on a LATER run (KB-74).

The applier ran with an empty queue three times in a row while the database held 30 `deferred_easy` rows (a company 78,
a company 82, a company 74) and two 'candidate' rows whose only-available route is Easy Apply (a company 52 score / 4426 chars,
a company 60 / 2873). `prepare_queue` drops every `apply_route='easy_apply'` row (correct: a direct ATS send is
stronger), and the only path back to them was the in-run `deferred` list, which dies with the run. `deferred_easy`
is not a status `prepare_queue` reads at all, so those rows were never looked at again. Easy Apply itself works:
4 rows carry the route with status 'applied'.

The fix keeps the priority rule - Easy Apply is still only attempted after the main queue and only under EASY_CAP -
but seeds the last-resort list from the database so an earlier run's deferral is not a life sentence.
"""
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import runner  # noqa: E402
from core import db as dbm  # noqa: E402

DESC = ("Data analyst role: build SQL dashboards in Power BI, own reporting for the finance team, "
        "automate Excel MIS, work with stakeholders on data quality. " * 4)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never write test rows into the real applications ledger
    return dbm.DB(tmp_path / "t.db")


def _add(d, url, company="Printo", title="Commercial Analyst", location="Bengaluru, Karnataka, India",
         desc=DESC, score=60, status="deferred_easy", easy=True):
    jid, _ = d.upsert_job({"company": company, "title": title, "location": location, "url": url,
                           "source": "board:linkedin", "description": desc if easy else desc,
                           "easy_apply": easy})
    d.c.execute("UPDATE jobs SET score=?, status=? WHERE id=?", (score, status, jid))
    d.c.commit()
    return jid


def test_a_job_deferred_by_an_earlier_run_is_offered_again(db):
    jid = _add(db, "https://www.linkedin.com/jobs/view/4485452459")
    out = runner.prepare_easy_queue(db, runner.EASY_CAP, log=lambda *a: None)
    assert [r["id"] for r in out] == [jid]


def test_a_candidate_whose_easy_apply_failed_can_be_retried(db):
    # status was reset to 'candidate' by the requeue, but apply_route stayed 'easy_apply' and the main pool drops those
    jid = _add(db, "https://www.linkedin.com/jobs/view/4483458738", company="Alstom", score=60, status="candidate")   # 60: above any top-tier bar, so only the status rule is tested
    assert [r["id"] for r in runner.prepare_easy_queue(db, 5, log=lambda *a: None)] == [jid]


def test_the_quality_and_location_bars_still_apply(db):
    _add(db, "https://www.linkedin.com/jobs/view/1", title="Low fit", score=20)
    _add(db, "https://www.linkedin.com/jobs/view/2", title="Pune role", location="Pune, Maharashtra, India", score=80)
    _add(db, "https://www.linkedin.com/jobs/view/3", title="No real description", desc="short", score=80)
    out = runner.prepare_easy_queue(db, 10, log=lambda *a: None)
    assert out == []


def test_a_role_already_sent_through_another_listing_is_not_offered(db):
    sent = _add(db, "https://www.linkedin.com/jobs/view/4", title="Same Role", status="applied")
    _add(db, "https://www.linkedin.com/jobs/view/5", title="Same Role")
    out = runner.prepare_easy_queue(db, 10, log=lambda *a: None)
    assert [r["id"] for r in out] == [], sent


def test_the_cap_keeps_the_best_scored_jobs_first(db):
    _add(db, "https://www.linkedin.com/jobs/view/6", title="A", score=55)
    best = _add(db, "https://www.linkedin.com/jobs/view/7", title="B", score=82)
    _add(db, "https://www.linkedin.com/jobs/view/8", title="C", score=70)
    out = runner.prepare_easy_queue(db, 1, log=lambda *a: None)
    assert [r["id"] for r in out] == [best]


def test_run_seeds_the_last_resort_from_the_database_before_using_it():
    src = inspect.getsource(runner.run)
    assert "prepare_easy_queue(d, EASY_CAP, log)" in src
    seed = src.index("prepare_easy_queue(d, EASY_CAP, log)")
    use = src.index("if deferred and st[\"applied\"] < target")
    assert seed < use, "the seed must happen before the last-resort block reads `deferred`"
