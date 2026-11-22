import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as dbm  # noqa: E402
from core import llm, profile  # noqa: E402


def test_dedup_and_fingerprint(tmp_path):
    d = dbm.DB(tmp_path / "t.db")
    j = {"source": "x", "company": "Acme", "title": "Data Analyst", "location": "Bangalore", "url": "https://a.com/1?utm=1"}
    jid, new = d.upsert_job(j)
    assert new
    _, new2 = d.upsert_job({**j, "url": "https://a.com/1"})
    assert not new2
    d.set_status(jid, "applied")
    assert d.already_applied("ACME", "Data  Analyst!", "Bengaluru")


def test_repost_count_uses_location(tmp_path):
    d = dbm.DB(tmp_path / "r.db")
    for i in range(3):
        d.upsert_job({"source": "x", "company": "Acme", "title": "Data Analyst", "location": "Bengaluru",
                      "url": f"https://a.com/{i}"})
    assert d.repost_count("Acme", "Data Analyst", "Bangalore") == 3  # three postings, same company+title+city
    assert d.repost_count("Acme", "Data Analyst", "Pune") == 0


def test_expire_stale(tmp_path):
    d = dbm.DB(tmp_path / "s.db")
    jid, _ = d.upsert_job({"source": "x", "company": "A", "title": "Data Analyst", "location": "Bengaluru", "url": "https://a.com/1"})
    d.set_status(jid, "candidate")
    assert d.expire_stale(3) == 0
    d.c.execute("UPDATE jobs SET last_seen='2020-01-01T00:00:00' WHERE id=?", (jid,))
    assert d.expire_stale(3) == 1
    assert d.rows("id=?", (jid,))[0]["status"] == "stale"


def test_facts_and_answers():
    f = profile.facts()
    assert f["personal"]["name"] == "Asha Example"
    assert "Python" in f["not_claimable"]
    assert profile.lookup("What is your notice period?")[1] == "Immediate"
    assert "break" in profile.lookup("Reason for career gap")[1].lower()


def test_extract_json():
    assert llm.extract_json('text ```json\n{"a": 1}\n``` tail') == {"a": 1}
    assert llm.extract_json('[1,2]') == [1, 2]
