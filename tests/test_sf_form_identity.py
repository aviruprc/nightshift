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


def test_a_sponsorship_yes_on_a_successfactors_combobox_is_a_wrong_answer():
    """Three applications went out with 'Will you now or in the future require sponsorship for an employment visa? = Yes' for a citizen applying in her
    own country. SuccessFactors renders the question as a combobox INPUT (label = question), which only Workday buttons and checked radios were judged."""
    els = [{"tag": "input", "type": "text", "label": "* Will you now or in the future require sponsorship for an employment visa?", "value": "Yes"},
           {"tag": "input", "type": "text", "label": "* Please confirm your eligibility to work in India", "value": "Yes"}]
    bad = agent.question_mismatches(els, "j")
    assert len(bad) == 1 and bad[0][1] == "Yes" and bad[0][2] == "No" and "sponsorship" in bad[0][0]
    ok = [dict(els[0], value="No")]
    assert agent.question_mismatches(ok, "j") == []
    assert "SPONSORSHIP / VISA" in agent.PROMPT


def test_a_workday_dropdown_button_labelled_with_the_sponsorship_question_is_judged():
    """A Workday dropdown that is a BUTTON labelled with the question (no group) was submitted as 'Yes' to 'need sponsorship to work'."""
    els = [{"tag": "button", "type": "button", "label": "Error-Will you now or in the future need sponsorship to work in India?", "text": "Yes"}]
    bad = agent.question_mismatches(els, "j")
    assert len(bad) == 1 and bad[0][1] == "Yes" and bad[0][2] == "No"
    assert agent.question_mismatches([{"tag": "button", "label": "Open menu", "text": "Yes"}], "j") == []


def test_the_audit_line_names_the_question_of_a_select_one_dropdown():
    src = inspect.getsource(agent.navigate)
    assert "(el or {}).get(\"group\") and (not _lab or re.match(" in src and "[{str((el or {}).get('group'))[:60]}]" in src


def test_an_sms_opt_in_answered_yes_is_a_wrong_answer():
    """'I agree that <company> may reach out to me via SMS regarding my application' = Yes (the audit line that names the question exposed it)."""
    from apply import answers_map
    q = "I agree that Acme may reach out to me via SMS regarding my application"
    assert answers_map.answer_for(q, "j") == "No"
    els = [{"tag": "button", "type": "button", "label": "Select One Required", "group": q, "text": "Yes"}]
    assert agent.question_mismatches(els, "j")[0][2] == "No"
    assert answers_map.answer_for("Do you agree to the terms and conditions?", "j") is None   # ordinary consent is untouched
