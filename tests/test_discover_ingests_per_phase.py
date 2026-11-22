"""Discovery ingests each source as soon as it is read (: 24,000 career-site/Naukri listings waited 35+ minutes behind a slow
aggregator; the worker, back after two days, had only stale jobs and submitted nothing)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import hunt  # noqa: E402


class _C:
    def commit(self): pass


class _D:
    c = _C()


def test_every_phase_is_ingested_when_it_finishes(monkeypatch):
    from core import backup, runstate
    from discover import aggregators, ats_api, boards, linkedin
    order = []
    monkeypatch.setattr(backup, "snapshot", lambda *a, **k: None)
    monkeypatch.setattr(runstate, "update", lambda **k: None)
    monkeypatch.setattr(hunt.dbm, "DB", lambda: type("X", (), {"expire_stale": lambda s, n: 0, "c": _C()})())
    monkeypatch.setattr(ats_api, "fetch_all", lambda workers=12: [({"name": "Co"}, [{"apply_url": "https://a/1", "title": "t"}], None)])
    monkeypatch.setattr(boards, "fetch_boards", lambda: ([{"title": "naukri job"}], []))
    monkeypatch.setattr(linkedin, "fetch_linkedin", lambda **k: [{"title": "li job"}])
    monkeypatch.setattr(aggregators, "fetch_aggregators", lambda log=None: ([{"title": "agg job"}], []))
    monkeypatch.setattr(hunt, "ingest", lambda d, raw, stats, rej, workers=12: order.append([j["title"] for j in raw]))
    monkeypatch.setattr(hunt, "prepare", lambda: None)
    hunt.discover()
    assert order == [["t"], ["naukri job"], ["li job"], ["agg job"]]
