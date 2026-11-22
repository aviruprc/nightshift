"""apply to LinkedIn jobs only when the page shows an EXACT applicant count under 10. Bounds and silence are not 'few'."""
import json

from apply import lowcomp, route


def test_exact_counts_are_read():
    assert route.read_applicants("Bengaluru · 2 days ago · 7 applicants")["applicants"] == 7
    r = route.read_applicants("Reposted 1 day ago · 3 people clicked apply")
    assert (r["kind"], r["applicants"]) == ("exact", 3)


def test_over_n_is_a_bound_not_a_count():
    r = route.read_applicants("Reposted 1 day ago · Over 100 people clicked apply")   # the real a company page,
    assert r["kind"] == "over" and r["applicants"] == 100
    assert not route.low_competition(r, 10)


def test_first_25_is_not_under_10():
    r = route.read_applicants("Be among the first 25 applicants")
    assert r["kind"] == "first" and not route.low_competition(r, 10)


def test_a_silent_page_is_unknown_not_few():
    r = route.read_applicants("Infosys PowerBI on Fabric Bengaluru Apply Saved")
    assert r["kind"] == "unknown" and r["applicants"] is None and not route.low_competition(r, 10)


def test_the_limit_is_strict():
    assert route.low_competition({"kind": "exact", "applicants": 9}, 10)
    assert not route.low_competition({"kind": "exact", "applicants": 10}, 10)


def test_limit_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(lowcomp, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    assert lowcomp.li_max_applicants() == 10                      # no settings file: her stated default
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"li_max_applicants": 0}), encoding="utf-8")
    assert lowcomp.li_max_applicants() is None                    # 0 switches the filter off
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"li_max_applicants": 5}), encoding="utf-8")
    assert lowcomp.li_max_applicants() == 5


def test_scan_log_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(lowcomp, "LOG", tmp_path / "li_scan.jsonl")
    lowcomp.record({"id": "a", "company": "X", "title": "T", "url": "u"}, {"kind": "exact", "applicants": 4, "text": "4 applicants"}, "qualified")
    lowcomp.record({"id": "b", "company": "Y", "title": "T", "url": "u"}, {"kind": "over", "applicants": 100, "text": "Over 100 applicants"}, "crowded")
    got = lowcomp.recent()
    assert [g["id"] for g in got] == ["b", "a"] and got[1]["applicants"] == 4


def test_plan_order_reads_linkedin_rows_first_but_only_li_pass_of_them(monkeypatch):
    """applicant counts must be read every run. The first LI_PASS unresolved LinkedIn rows go right behind returnships."""
    from apply import runner

    class D:
        def rows(self, *a, **k):
            return []
    monkeypatch.setattr(runner, "top_companies", lambda: set())
    monkeypatch.setattr(lowcomp, "li_max_applicants", lambda: 10)
    monkeypatch.setattr(runner, "LI_PASS", 2)
    mk = lambda i, url, fam="data_bi", route=None: {"id": str(i), "company": f"C{i}", "family": fam, "url": url, "apply_url": url, "apply_route": route}
    rows = [mk(1, "https://x.com/a"), mk(2, "https://www.linkedin.com/jobs/view/1"), mk(3, "https://www.linkedin.com/jobs/view/2"),
            mk(4, "https://www.linkedin.com/jobs/view/3"), mk(5, "https://www.linkedin.com/jobs/view/4", route="easy_apply"),
            mk(6, "https://www.linkedin.com/jobs/view/5", fam="returnship")]
    out = [r["id"] for r in runner.plan_order(rows, D())]
    assert out[0] == "6"                      # a returnship still comes first
    assert out[1:3] == ["2", "3"]             # then exactly LI_PASS LinkedIn rows
    assert out.index("4") > out.index("1")    # the third LinkedIn row and the Easy Apply one wait their turn
    monkeypatch.setattr(lowcomp, "li_max_applicants", lambda: None)
    assert [r["id"] for r in runner.plan_order(rows, D())][:2] != ["6", "2"]   # filter off: no special front bucket
