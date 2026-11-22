"""SuccessFactors puts Create Account AND the application on one page (a company Technologies).
Evidence (embedded: data/failures is pruned): errs ['Password accepted','Password matches','Interest Level is required','Terms of Use (required)'],
'Technology/Skills' = 'Azure SQL Analytics' (the typeahead's pick for a typed 'SQL')."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402


def _i(label, value="", typ="text"):
    return {"tag": "input", "type": typ, "label": label, "value": value}


SNAP = {"els": [_i("Email Address: *", "x@y.z"), _i("Choose Password: *", "••••", "password"), _i("First Name: *", "Asha"),
                _i("City*", "Bengaluru"), _i("Technology/Skills", "Azure SQL Analytics"), _i("Total Work Experience (Years)", "4 Years"),
                _i("Notice Period", "Immediate"), _i("Current CTC*", "1,550,000.00"), _i("Interest Level")],
        "errs": ["Password accepted", "Interest Level is required"], "text": ""}


def test_a_create_account_plus_application_page_is_not_a_login_page():
    assert agent.application_fields_page(SNAP)
    login = {"els": [_i("Email"), _i("Password", typ="password"), _i("Country/Region Code")]}
    assert not agent.application_fields_page(login)
    assert "application_fields_page(snap)" in inspect.getsource(agent.navigate)


def test_a_skill_she_lacks_in_a_field_value_blocks_save_and_submit():
    hits = agent.lacking_skill_claims(SNAP["els"], ["Python", "Azure", "R", "AWS"])
    assert len(hits) == 1 and hits[0][0] == "Technology/Skills" and "Azure" in hits[0][2]
    assert agent.lacking_skill_claims([_i("Skills", "SQL, R2R, Power BI, Excel")], ["R", "Azure"]) == []
    assert "lacking_skill_claims(" in inspect.getsource(agent.navigate)


def test_a_model_answer_to_her_own_question_is_never_submitted(monkeypatch):
    from apply import answers_map
    els = [_i("Willingness to Travel within India or overseas for the projects?", "Yes"), _i("Notice Period", "Immediate"),
           _i("Interest Level")]
    assert agent.her_unrecorded_answers(els, "j") == ["Willingness to Travel within India or overseas for the projects?"]
    monkeypatch.setattr(answers_map, "answer_for", lambda lab, jid=None: "Yes")   # once she has recorded it, it passes
    assert agent.her_unrecorded_answers(els, "j") == []
    assert "her_unrecorded_answers(" in inspect.getsource(agent.navigate)


def test_only_skill_fields_are_judged_never_the_posting_or_a_letter():
    lacked = ["Python", "Azure", "AWS", "Snowflake", "Databricks"]
    job = {"title": "Senior Analyst - AWS Cost Reporting", "company": "Amazon Web Services (AWS)"}
    els = [_i("Job Title", "Senior Analyst - AWS Cost Reporting"), _i("Position applied for", "Data Analyst (SQL, Python)"),
           _i("Company", "Amazon Web Services (AWS)"), {"tag": "textarea", "type": "textarea", "label": "Cover letter",
           "value": "I have not used Python yet"}, _i("Notes", "Snowflake-free")]
    assert agent.lacking_skill_claims(els, lacked, job) == []
    assert agent.lacking_skill_claims([_i("Technology/Skills", "Azure SQL Analytics")], lacked, job)


def test_relevant_experience_in_data_analytics_is_her_recorded_figure():
    from apply import answers_map
    assert answers_map.answer_for("Relevant experience in data analytics") == "4"
    assert agent.her_unrecorded_answers([_i("Relevant experience in data analytics", "4")], "j") == []
