"""The public release reads every personal value from the user's own files (cv/facts.yaml, data/answers.yaml, settings.json):
nothing about a real person is built in, and an unrecorded fact is parked, never filled from a default."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def person(tmp_path, monkeypatch):
    """A second invented person in temp files, swapped in for one test."""
    from core import profile
    facts = yaml.safe_load((ROOT / "tests" / "fixtures" / "profile" / "facts.yaml").read_text(encoding="utf-8"))
    answers = yaml.safe_load((ROOT / "tests" / "fixtures" / "profile" / "answers.yaml").read_text(encoding="utf-8"))
    f, a = tmp_path / "facts.yaml", tmp_path / "answers.yaml"

    def write(fx=None, ax=None):
        fd, ad = json.loads(json.dumps(facts)), json.loads(json.dumps(answers))
        (fx or (lambda d: None))(fd)
        (ax or (lambda d: None))(ad)
        f.write_text(yaml.safe_dump(fd), encoding="utf-8")
        a.write_text(yaml.safe_dump(ad), encoding="utf-8")
        from apply import answers_map
        monkeypatch.setattr(answers_map, "_RULES", None)
    monkeypatch.setattr(profile, "FACTS", f)
    monkeypatch.setattr(profile, "ANSWERS", a)
    write()
    return write


def test_names_and_phone_come_from_facts_yaml(person):
    from apply import answers_map as am
    person(fx=lambda d: d["personal"].update(name="Ravi Kumar Sample", first_name="Ravi", last_name="Sample", phone="+91 98765 43210"))
    assert am.answer_for("First name") == "Ravi" and am.answer_for("Last name") == "Sample"
    assert am.answer_for("Mobile number") == "9876543210" and am.answer_for("Full name") == "Ravi Kumar Sample"


def test_an_unrecorded_declaration_is_parked_not_answered_no(person):
    """'No criminal record' was a built-in answer for one person. For anyone else it is a fact they must confirm."""
    from apply import answers_map as am
    person(ax=lambda d: [d.pop(k, None) for k in ("no_compliance_flags", "knows_no_insider", "open_to_hybrid", "github")])
    assert am.answer_for("Do you have a criminal record?") is None
    assert am.answer_for("Do you know anyone who works at this company?") is None
    assert am.answer_for("Are you open to hybrid work?") is None
    assert am.answer_for("GitHub profile") is None
    person()
    assert am.answer_for("Do you have a criminal record?") == "No"


def test_city_and_state_follow_answers_yaml(person):
    from apply import answers_map as am
    person(ax=lambda d: d.update(current_city="Pune", current_state="Maharashtra", city_aliases=["Pune", "Poona"]))
    assert am.answer_for("Current city") == "Pune" and am.answer_for("State") == "Maharashtra"
    assert am.answer_for("Are you currently residing in Pune?") == "Yes"
    assert am.answer_for("Are you currently residing in Bengaluru?") == "No"


def test_the_scoring_prompt_is_built_from_the_profile(person):
    from rank import score
    person(fx=lambda d: d["preferences"].update(candidate_profile="Seven years of QA automation.", eligible_roles="QA, SDET", max_stated_min_years=0))
    p = score.render_prompt([])
    assert "Seven years of QA automation." in p and "QA, SDET" in p and "+ years" not in p


def test_search_queries_and_job_families_are_configurable(person):
    from core import profile
    person(fx=lambda d: d["preferences"].update(search_queries=["QA Engineer"], include_returnships=False))
    assert profile.search_queries(["Data Analyst"]) == ["QA Engineer"]
    person()
    assert profile.search_queries(["Data Analyst"]) == ["Data Analyst"]


def test_a_live_run_is_refused_on_the_example_profile():
    from core import setup_wizard as sw
    assert sw.example_markers_in(ROOT / "cv" / "facts.example.yaml"), "the example must be recognisable as the example"
    assert sw.example_markers_in(ROOT / "data" / "answers.example.yaml")
    import hunt
    assert "_refuse_live_on_example_profile()" in __import__("inspect").getsource(hunt)


def test_ai_keys_can_come_from_the_environment(monkeypatch):
    from core import llm
    monkeypatch.setenv("JOBHUNT_KEY_DEEPSEEK_API", "k-test")
    assert llm._has_key("deepseek-api") and llm.get_key("deepseek-api") == "k-test"
    monkeypatch.delenv("JOBHUNT_KEY_DEEPSEEK_API")
    assert llm._env_key("deepseek-api") == ""


def test_outreach_drafts_but_does_not_send_unless_switched_on(monkeypatch):
    from apply import outreach
    from core import profile
    sent = []
    monkeypatch.setattr(outreach, "scan", lambda **k: 0)
    monkeypatch.setattr(outreach, "draft_pending", lambda **k: 2)
    monkeypatch.setattr(outreach, "send_drafted", lambda **k: sent.append(1) or 2)
    monkeypatch.setattr(profile, "settings", lambda: {})
    assert outreach.daily(log=lambda *a: None)["sent"] == 0 and not sent
    monkeypatch.setattr(profile, "settings", lambda: {"outreach_send": True})
    assert outreach.daily(log=lambda *a: None)["sent"] == 2 and sent

