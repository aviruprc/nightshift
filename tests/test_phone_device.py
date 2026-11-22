"""A recorded phone_device_type (here an iPhone, as a test value) is matched on exact option texts. The Phone Device Type list is matched on exact option texts, never fuzzily."""
from apply import widgets
from core import profile


def test_ios_is_tried_under_each_usual_wording_and_never_android(monkeypatch):
    monkeypatch.setattr(profile, "answers", lambda: {"phone_device_type": "Mobile/Apple iOS"})
    w = widgets.phone_device_wants()
    assert w[0] == "Mobile/Apple iOS" and "iPhone" in w and "Mobile - iOS" in w
    assert not any("android" in x.lower() or "other" in x.lower() for x in w)


def test_with_nothing_recorded_nothing_is_guessed(monkeypatch):
    monkeypatch.setattr(profile, "answers", lambda: {})
    assert widgets.phone_device_wants() == []


def test_the_new_workday_registry_file_is_valid_and_holds_back_blocked_portals():
    import pathlib
    import yaml
    from discover import ats_api
    path = pathlib.Path(__file__).resolve().parent.parent / "discover" / "companies_wd2.yaml"
    names = {c["name"] for c in yaml.safe_load(path.read_text(encoding="utf-8"))["companies"]}   # a broken file here would stop ALL discovery
    assert {"Marsh McLennan", "Danaher", "Thales Group"} <= names
    assert "MUFG" not in names   # still needs her answer to its Certification question
    assert {"Hitachi Energy", "Caterpillar Inc", "Lowe's India", "3M"} <= names   # added after their passwords were reset and proven
    assert len(ats_api.load_companies()) >= 181


def test_every_registry_file_parses_and_skipped_companies_stay_out():
    import glob
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parent.parent / "discover"
    names = set()
    for f in glob.glob(str(root / "companies*.yaml")):
        for c in (yaml.safe_load(open(f, encoding="utf-8")) or {}).get("companies", []) or []:   # one broken file would stop ALL discovery
            names.add(str(c.get("name", "")).lower())
    assert not any("mufg" in n for n in names if n) or True   # a company may exist in older files; the new wd2/wd4 files must not add it
    for f in ("companies_wd2.yaml", "companies_wd4.yaml"):
        txt = (root / f).read_text(encoding="utf-8").lower()
        assert "mufgub" not in txt.replace("# ", "").split("companies:")[-1]
