"""One model call per PAGE: fields her recorded answers decide are filled by the tool BEFORE the model is asked ."""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent, answers_map, batch  # noqa: E402


def _el(i, label, **kw):
    d = {"i": i, "tag": "input", "type": "text", "label": label, "text": "", "value": "", "req": True}
    d.update(kw)
    return d


def _fake(monkeypatch, table):
    monkeypatch.setattr(answers_map, "answer_for", lambda lab, jid=None: table.get(lab))


def test_a_field_her_record_answers_is_filled_without_the_model(monkeypatch):
    _fake(monkeypatch, {"Willing to relocate": "Yes"})
    acts, idx = batch.prefill({"els": [_el(3, "Willing to relocate*")]}, "j1")
    assert acts == [{"do": "fill", "i": 3, "value": "Yes", "_rule": True}] and idx == {3}


def test_an_unknown_or_doubtful_answer_is_left_to_the_model_and_the_park_rules(monkeypatch):
    _fake(monkeypatch, {})   # answer_for -> None: never invent
    acts, idx = batch.prefill({"els": [_el(3, "How many years of Palantir experience")]}, "j1")
    assert acts == [] and idx == set()


def test_choice_controls_identity_fields_and_filled_values_are_never_prefilled(monkeypatch):
    _fake(monkeypatch, {"Relocate": "Yes", "Email": "x@y.z", "First Name": "M", "Notice period": "Immediate"})
    els = [_el(1, "Relocate", type="radio"), _el(2, "Email"), _el(3, "First Name"), _el(4, "Notice period", value="30 days"),
           _el(5, "Notice period", tag="button"), _el(6, "Notice period", disabled=True)]
    assert batch.prefill({"els": els}, "j")[0] == []


def test_a_select_is_answered_only_by_an_option_that_says_exactly_that(monkeypatch):
    _fake(monkeypatch, {"Notice period": "Immediate"})
    ok = _el(1, "Notice period", tag="select", type="select-one", options=["Select", "Immediate", "30 days"])
    bad = _el(2, "Notice period", tag="select", type="select-one", options=["Select", "Immediately available", "30 days"])
    assert batch.prefill({"els": [ok]}, "j")[0][0]["value"] == "Immediate"
    assert batch.prefill({"els": [bad]}, "j")[0] == []   # no fuzzy pick (KB-57)


def test_the_model_sees_the_field_marked_and_its_own_action_on_it_is_dropped():
    snap = {"els": [_el(1, "Q"), {"i": 2, "tag": "button", "text": "Next"}]}
    shown = batch.annotate(snap, {1})
    assert "rule_answer" in shown["els"][0]
    pre = [{"do": "fill", "i": 1, "value": "Yes", "_rule": True}]
    merged = batch.merge(pre, [{"do": "fill", "i": 1, "value": "No"}, {"do": "click", "i": 2}], {1})
    assert merged == pre + [{"do": "click", "i": 2}]


def test_a_reask_never_hides_a_value_the_model_may_need():
    els = [_el(1, "A", value="x"), _el(2, "B", value="wrong", invalid=True)]
    assert [e["i"] for e in batch.annotate({"els": els}, set(), trim=True)["els"]] == [1, 2]


# --- the REAL rules (review): answer_for is unanchored; these labels once produced wrong answers ---------------------------
BAD = ["CGPA*", "Percentage in 12th", "Extension", "Please state why you want to join us", "Which data streaming tools have you used?",
       "Describe a major project you led", "What are you currently working on?", "Tell us about any gap in your skills",
       "Variable pay expectations comments",
       "Current CTC (INR)", "Total experience (months)", "Expected Salary", "Expected CTC", "Current CTC", "Current salary", "Notice period (days)"]


def test_real_rules_never_prefill_a_long_or_unlisted_question():
    for lab in BAD:
        for tag, typ in (("input", "text"), ("textarea", "textarea")):
            acts, idx = batch.prefill({"els": [_el(1, lab, tag=tag, type=typ)]}, "j")
            assert acts == [] and idx == set(), (lab, tag, acts)


def test_sentinels_are_never_typed(monkeypatch):
    for sentinel in ("SKIP", "DECLINE", "ASAP", "Opt Out", "None"):
        _fake(monkeypatch, {"Notice period": sentinel})
        assert batch.prefill({"els": [_el(1, "Notice period")]}, "j")[0] == []


def test_a_real_notice_period_field_is_still_prefilled_from_her_record():
    acts, _ = batch.prefill({"els": [_el(1, "Notice period*")]}, "j")
    assert acts and acts[0]["_rule"] and acts[0]["value"].strip()


def test_a_shown_none_is_a_real_value_not_a_placeholder(monkeypatch):
    _fake(monkeypatch, {"Notice period": "Immediate"})
    assert batch.prefill({"els": [_el(1, "Notice period", value="None")]}, "j")[0] == []


def test_a_rule_answer_is_never_stored_in_a_recipe():
    snap = {"els": [_el(1, "Relocate"), {"i": 2, "tag": "button", "type": "button", "label": "", "text": "Next", "value": ""}]}
    stored = agent._storable([{"do": "fill", "i": 1, "value": "Yes", "_rule": True}, {"do": "click", "i": 2}], snap, "")
    assert [a["do"] for a in stored] == ["click"]   # KB-83: a route, never an answer


def test_the_navigator_uses_the_batch_path_and_falls_back_when_it_faults():
    src = inspect.getsource(agent.navigate)
    assert "_batch.prefill(" in src and "_batch.merge(" in src
    assert "pre_acts, pre_idx, shown_snap = [], set(), snap" in src   # any fault -> the old per-step path


def test_an_lpa_labelled_ctc_is_prefilled_in_lpa_only_for_an_indian_posting(monkeypatch):
    _fake(monkeypatch, {"Current CTC (LPA)": "11.5"})
    monkeypatch.setattr(answers_map, "_job_location", lambda jid: "Bengaluru, India")
    assert batch.prefill({"els": [_el(1, "Current CTC (LPA)")]}, "j")[0][0]["value"] == "11.5"
    monkeypatch.setattr(answers_map, "_job_location", lambda jid: "London, UK")
    assert batch.prefill({"els": [_el(1, "Current CTC (LPA)")]}, "j")[0] == []


def test_city_inside_repeating_blocks_is_left_to_the_model(monkeypatch):
    _fake(monkeypatch, {"City": "Bengaluru"})
    assert batch.prefill({"els": [_el(1, "City"), _el(2, "City")]}, "j")[0] == []          # two City fields: employer + school
    assert batch.prefill({"els": [_el(1, "City", row="1/2")]}, "j")[0] == []
    assert batch.prefill({"els": [_el(1, "City", rowctx="Education School")]}, "j")[0] == []
    assert batch.prefill({"els": [_el(1, "City")]}, "j")[0][0]["value"] == "Bengaluru"       # a lone home-city box is still filled
