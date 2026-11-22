"""A job that failed 3+ times is tried AFTER the fresh ones (: six repeat failures led the first 15 minutes after the restart)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import runner  # noqa: E402


class _D:   # plan_order asks learn.* for stats; a stub keeps this test offline
    pass


def _row(i, company, attempts, family="data_bi", score=60):
    return {"id": i, "company": company, "title": "t", "url": f"https://{company}.example/{i}", "apply_url": "", "score": score,
            "attempts": attempts, "family": family, "apply_route": None, "posted_at": None, "first_seen": None, "fingerprint": i}


def test_three_time_failures_go_behind_fresh_jobs_and_returnships_stay_first(monkeypatch):
    from apply import learn
    monkeypatch.setattr(learn, "stats", lambda d: {})
    monkeypatch.setattr(learn, "applied_companies", lambda d: set())
    monkeypatch.setattr(learn, "job_value", lambda r, st, cov: 1.0)
    rows = [_row("a", "waphire", 3, score=90), _row("b", "fresh1", 0), _row("c", "fresh2", 1), _row("d", "ret", 3, family="returnship")]
    out = [r["id"] for r in runner.plan_order(rows, _D())]
    assert out[0] == "d" and out[-1] == "a" and set(out[1:3]) == {"b", "c"}
