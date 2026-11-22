"""P0.4 answer + recipe cache: the same question is never paid for twice, across attempts AND across companies."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import llm_answer  # noqa: E402
from core import cache  # noqa: E402
from core import db as dbm  # noqa: E402


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "c.db")
    monkeypatch.delenv("JOBHUNT_NO_LLM", raising=False)


def test_question_key_ignores_company_and_retry_hints():  # bug: a company re-asked the LLM on every attempt
    a = cache.norm_q("Were you ever employed by Genpact?*", "Genpact")
    b = cache.norm_q("Were you ever employed by Wells Fargo?*", "Wells Fargo")
    assert a == b
    assert cache.norm_q("Language (current value 'Tamil' was rejected: Duplicate language entries)") == cache.norm_q("Language")


def test_llm_answer_is_cached_and_reused_across_companies(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_answer.llm, "ask_json", lambda *a, **k: calls.append(1) or {"answers": [{"i": 0, "a": "No"}]})
    q = [{"q": "Were you ever employed by Genpact?*", "options": ["Yes", "No"]}]
    assert llm_answer.answer(q, {"company": "Genpact", "id": "j1"}) == {q[0]["q"]: "No"}
    q2 = [{"q": "Were you ever employed by Wells Fargo?*", "options": ["Yes", "No"]}]
    assert llm_answer.answer(q2, {"company": "Wells Fargo", "id": "j2"}) == {q2[0]["q"]: "No"}
    assert llm_answer.answer(q, {"company": "Genpact", "id": "j1"}) == {q[0]["q"]: "No"}
    assert len(calls) == 1  # one LLM call total for three asks


def test_motivational_answers_stay_with_their_job(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_answer.llm, "ask_json", lambda *a, **k: calls.append(1) or {"answers": [{"i": 0, "a": "Because X."}]})
    q = [{"q": "Why do you want to work here?", "options": None}]
    llm_answer.answer(q, {"company": "A", "id": "j1"})
    llm_answer.answer(q, {"company": "B", "id": "j2"})  # another job: must NOT reuse A's pitch
    llm_answer.answer(q, {"company": "A", "id": "j1"})  # same job: reused
    assert len(calls) == 2


def test_cached_answer_must_still_be_a_valid_option(monkeypatch):
    cache.put("Notice period", ["Immediate", "30 days"], "Immediate")
    assert cache.get("Notice period", ["Immediate", "30 days"]) == "Immediate"
    cache.put("Work mode", ["Hybrid"], "Remote")  # stale answer that is no longer among the options
    assert cache.get("Work mode", ["Hybrid"]) is None


def test_recipe_replay_only_after_success():
    snap = {"els": [{"label": "Email"}, {"text": "Next"}]}
    sig = cache.page_sig(snap)
    assert cache.recipe_get("h.example", sig) is None
    cache.recipe_put("h.example", sig, [{"do": "fill", "label": "Email", "value": "x"}], ok=True)
    assert cache.recipe_get("h.example", sig)[0]["do"] == "fill"
    cache.recipe_put("h.example", sig, [], ok=False)
    cache.recipe_put("h.example", sig, [], ok=False)
    assert cache.recipe_get("h.example", sig) is None  # failed more than it worked: back to the LLM


def test_gpa_is_never_guessed():
    from apply import answers_map as am
    assert am.answer_for("Overall Result (GPA)") == "SKIP"


def test_college_question_is_answered_without_the_user():  # "unanswered required: name of your college/university"
    from apply import answers_map as am
    assert "Larkmoor" in am.answer_for("What is the name of your college/university?")
    assert "Larkmoor" in am.answer_for("University")


def test_work_authorisation_is_yes_and_sponsorship_is_no():  # backlog: RippleHire/UST got "India" for a yes/no question
    from apply import answers_map as am
    assert am.answer_for("Are you legally authorized to work in the country?") == "Yes"
    assert am.answer_for("Will you require sponsorship to work here?") == "No"


def test_navigator_recipe_roundtrip_by_label_not_index():
    from apply import agent
    snap1 = {"els": [{"i": 0, "tag": "input", "type": "email", "label": "Email", "text": ""},
                     {"i": 1, "tag": "input", "type": "password", "label": "Password", "text": ""},
                     {"i": 2, "tag": "button", "type": "submit", "label": "", "text": "Next"}]}
    stored = agent._storable([{"do": "fill", "i": 0, "value": "me@x.com"}, {"do": "fill", "i": 1, "value": "S3cret!"},
                              {"do": "click", "i": 2}], snap1, "S3cret!")
    assert stored[1]["value"] == "{{PASSWORD}}"  # the password itself is never stored
    snap2 = {"els": [{"i": 7, "tag": "button", "type": "submit", "label": "", "text": "Next"},
                     {"i": 8, "tag": "input", "type": "password", "label": "Password", "text": ""},
                     {"i": 9, "tag": "input", "type": "email", "label": "Email", "text": ""}]}
    acts = agent._resolve(stored, snap2, "S3cret!")
    assert [(a["do"], a["i"]) for a in acts] == [("fill", 9), ("fill", 8), ("click", 7)]


def test_submit_steps_are_never_remembered():
    from apply import agent
    snap = {"els": [{"i": 0, "tag": "button", "type": "submit", "label": "", "text": "Submit application"}]}
    assert agent._storable([{"do": "click", "i": 0}], snap, "x") is None


def test_retry_corrections_are_never_cached():  # bug: 'Language (Tamil was rejected)' cached as 'English' -> duplicate language rows forever
    cache.put("Language (current value 'Tamil' was rejected: Duplicate language entries)", None, "English")
    assert cache.get("Language (current value 'Tamil' was rejected: Duplicate language entries)", None) is None
    assert cache.get("Language", None) is None


def test_navigator_prompt_template_formats():  # bug: literal braces added to PROMPT broke every navigator call (KeyError '"do"')
    from apply import agent
    out = agent.PROMPT.format(history_hint="none", acct="NO account exists yet", facts="f", email="e@x.com", address="a", company="C", title="T", history="[]", snap="{}")
    assert "STEPS SO FAR" in out and "{{PASSWORD}}" not in out.replace("{PASSWORD}", "")


def test_llm_answer_prompt_template_formats():
    from apply import llm_answer
    assert "QUESTIONS" in llm_answer.PROMPT.format(facts="f", rules="", company="C", title="T", jd="j", questions="[]")


def test_apply_now_is_not_the_final_submit():  # bug: 'Apply now' on a job page was treated as final submit (a company), stopping dry runs and mislabelling steps
    from apply import agent
    for txt in ("Apply now", "Apply", "Apply for this job", "Next", "Continue"):
        assert not agent.SUBMIT_BTN.search(txt), txt
    for txt in ("Submit", "Submit application", "Send application", "Finish"):
        assert agent.SUBMIT_BTN.search(txt), txt


def test_navigator_stuck_guard_handles_first_sight_after_a_wait():  # bug: KeyError on a page first seen right after a 'wait' action
    import inspect

    from apply import agent
    src = inspect.getsource(agent.navigate)
    assert "seen_sigs[sig] >=" not in src  # direct indexing would KeyError for an unseen signature


def test_portal_memory_feeds_back_into_hints():
    assert "no history" in cache.hints("ex.com")
    cache.remember("ex.com", True)
    cache.remember("ex.com", False, "page stopped changing (Password accepted) last: step 8")
    cache.remember("ex.com", False, "page stopped changing (Password accepted) last: step 9")
    h = cache.hints("ex.com")
    assert "1 earlier success" in h and "page stopped changing" in h and "x2" in h  # same failure at different step numbers is ONE recurring cause


def test_required_optional_fields_get_a_true_filler():  # bug: a company marked 'Address Line 2' required; rules skipped it and the job was parked for the user
    from apply import answers_map as am
    assert am.optional_filler("Address Line 2*")  # her area from answers.yaml
    assert am.optional_filler("Phone extension") == "0"
    assert am.optional_filler("Favourite colour") is None  # anything else is NOT invented


def test_residence_questions():  # bug: Naukri 'Are you currently residing in Bengaluru?' parked the job
    from apply import answers_map as am
    assert am.answer_for("Are you currently residing in Bengaluru?") == "Yes"
    assert am.answer_for("Are you currently residing in Bangalore, Karnataka?") == "Yes"
    assert am.answer_for("Are you currently residing in Pune?") == "No"
    assert am.answer_for("Are you willing to relocate to Pune?") != "No"  # relocation is a separate rule


def test_experience_years_for_specific_skills_are_never_guessed():  # superseded (accuracy first): a skill-specific figure is HER fact
    # The old rule answered every owned/domain skill with the total "4" (Regulatory Reports, dashboards...), which overclaims
    # the years she actually spent on each. Only career-level questions (total/relevant) use the recorded 4 now.
    from apply import answers_map as am
    # (: the user approved CV-date years for the skills in answers_map.DEFAULT_SKILL_YEARS, so regulatory/credit-risk now have a figure;
    #  generic MIS reporting / dashboards are NOT in her approved list and stay unanswered)
    for q in ("How many years of experience do you have in MIS reporting?", "Experience in dashboards (years)"):
        assert am.answer_for(q) is None, q
        assert am.needs_her(q), q  # -> needs_you with this exact question
    for q in ("How many years of experience do you have in Python?", "Years of experience in Quick commerce", "Experience in Snowflake (years)",
              "How many years of experience do you have in Salesforce?"):
        assert am.answer_for(q) is None, q  # never claim what she cannot defend


def test_non_answers_are_never_remembered():  # 'Skip this question' was cached as the answer to a payroll question
    for bad in ("Skip this question", "N/A", "none", "null", "Not applicable"):
        cache.put("Are you ok with third party payroll?", None, bad)
    assert cache.get("Are you ok with third party payroll?", None) is None
    cache.put("Are you ok with third party payroll?", ["Yes", "No"], "Yes")
    assert cache.get("Are you ok with third party payroll?", ["Yes", "No"]) == "Yes"


def test_tests_do_not_write_into_the_real_logs():
    from core import activity, llm
    assert "pytest-of" in str(activity.PATH)
    assert "pytest-of" in str(llm.USAGE_FILE)


def test_contract_and_third_party_payroll_are_her_decision_not_a_guess():  # the "Yes" was never stated by her
    from apply import answers_map as am
    for q in ("Are you ok with Third party Payroll?", "Are you open to a contract-to-hire role?", "Are you comfortable with contract basis?"):
        assert am.answer_for(q) is None, q
        assert am.needs_her(q), q  # -> needs_you, answer once on the dashboard


def test_workday_description_endpoint_is_derived_from_the_job_url(monkeypatch):  # 140 Workday jobs sat unscored because no JD was ever fetched
    from discover import ats_api
    seen = []
    monkeypatch.setattr(ats_api, "workday_description", lambda job: seen.append(job["_wd_api"]) or "x" * 500)
    ats_api.lazy_description({"apply_url": "https://statestreet.wd1.myworkdayjobs.com/en-US/Global/job/Bangalore-India/AQE-Analyst_R-1/apply"})
    assert seen == ["https://statestreet.wd1.myworkdayjobs.com/wday/cxs/statestreet/Global/job/Bangalore-India/AQE-Analyst_R-1"]


def test_workday_open_check_distinguishes_open_closed_unknown(monkeypatch):  # a company/ICON were marked closed while the API said canApply=True
    from discover import ats_api
    url = "https://fnz.wd3.myworkdayjobs.com/en-US/FNZ/job/Bangalore/Analyst_R-1/apply"

    class R:
        def __init__(self, code, js=None):
            self.status_code, self._js = code, js or {}

        def json(self):
            return self._js
    monkeypatch.setattr(ats_api.requests, "get", lambda *a, **k: R(200, {"jobPostingInfo": {"canApply": True}}))
    assert ats_api.workday_open(url) is True
    monkeypatch.setattr(ats_api.requests, "get", lambda *a, **k: R(404))
    assert ats_api.workday_open(url) is False
    monkeypatch.setattr(ats_api.requests, "get", lambda *a, **k: R(500))
    assert ats_api.workday_open(url) is None                     # unknown never reopens or blocks anything
    assert ats_api.workday_open("https://example.com/job/1") is None


def test_data_research_experience_is_her_fact_and_unowned_skills_stay_parked(monkeypatch):  # Ness: the "4" was a total-career figure, not years of Data Research
    from apply import answers_map as am
    from core import profile
    real = profile.answers()   # her live answers.yaml may now HOLD an answer she saved from the dashboard; this test checks the RULE, so run it without those
    monkeypatch.setattr(profile, "answers", lambda: {k: v for k, v in real.items() if not k.startswith("how_many_years_of_experience_do_you_have_in_")})
    assert am.answer_for("How many years of experience do you have in Data Research?") is None
    assert am.needs_her("How many years of experience do you have in Data Research?")
    assert am.answer_for("How many years of experience do you have in Python?") is None
    assert am.answer_for("How many years of experience do you have in Salesforce?") is None


def test_job_description_from_json_ld_next_data_and_plain_page():  # ~60 top-company candidates (a company, JPMC, companies) never had a JD, so were never scored
    from discover import ats_api
    jd = "Responsibilities: build dashboards and reports. Qualifications: SQL, Power BI and 3 years of experience. " * 6
    ld = f'<html><script type="application/ld+json">{{"@type":"JobPosting","description":"<p>{jd}</p>"}}</script></html>'
    assert len(ats_api.page_description(ld)) >= 400
    nd = '<html><script id="__NEXT_DATA__" type="application/json">{"props":{"a":"short","role":{"desc":"' + jd + '"}}}</script></html>'
    assert "Responsibilities" in ats_api.page_description(nd)
    plain = "<html><body><nav>Home Careers</nav><main><h1>Business Analyst</h1><p>" + jd + "</p></main></body></html>"
    assert "dashboards" in ats_api.page_description(plain)
    assert ats_api.page_description("<html><body>Cookie banner. Sign in. Search jobs.</body></html>") == ""   # a nav bar / banner is never a JD


def test_oracle_hcm_description_is_rebuilt_from_the_stored_url(monkeypatch):  # stored rows lose the discovery-time _ora dict
    from discover import ats_api, ats_oracle
    seen = []
    monkeypatch.setattr(ats_oracle, "fetch_description", lambda job: seen.append(job["_ora"]) or "x" * 500)
    ats_api.lazy_description({"ats": "oracle_hcm", "apply_url": "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/210805043"})
    assert seen == [{"host": "jpmc.fa.oraclecloud.com", "site": "CX_1001", "id": "210805043"}]


def test_skill_years_come_from_cv_dates_for_owned_skills_only(monkeypatch):  # the user; 24 jobs were parked on these
    from apply import answers_map as am
    from core import profile
    real = profile.answers
    monkeypatch.setattr(profile, "answers", lambda: {**real(), "skill_years_confirmed": True})
    q = lambda s: am.answer_for(f"How many years of experience do you have in {s}?")  # noqa: E731
    assert q("Power Bi") == "2" and q("SQL Server") == "3" and q("Tableau dashboards") == "2" and q("Google Sheets and/or Excel") == "4"
    assert q("Marketing Analytics") == "2" and q("ETL developer") is None
    assert q("Python") is None and q("SAP production planning") is None and q("Salesforce") is None
    assert q("Python and SQL") is None                      # one lacked skill in the question: never answered
    assert q("total experience") == "4"                    # career-level phrases keep the recorded total


def test_skill_years_stay_off_until_the_user_confirms(monkeypatch):  # accuracy gate: no new figure is ever submitted before she confirms it
    from apply import answers_map as am
    from core import profile
    monkeypatch.setattr(profile, "answers", lambda: {"total_experience_years": "4"})
    assert am.answer_for("How many years of experience do you have in Power Bi?") is None


def test_skill_years_do_not_cover_other_roles_on_the_same_tool():  # a company "Informatica Administration": she developed with it, never administered it
    from apply import answers_map as am
    q = lambda s: am.answer_for(f"How many years of experience do you have in {s}?")  # noqa: E731
    assert q("Power BI") == "2" and q("Informatica Administration") is None and q("SQL Server DBA") is None and q("Power BI architecture") is None


def test_whole_year_answer_maps_onto_range_chips_lower_range_on_edges():  # a company: answer 2 was right but chips were ranges, reported as "needs the user"
    from apply import naukri_apply as n
    q = "How many years of experience do you have in Power Bi Reports?"
    chips = ["0-1 years", "1-3 years", "3-5 years", "5+ years"]
    assert n.pick_option(q, chips, "2") == "1-3 years" and n.pick_option(q, chips, "3") == "1-3 years"   # shared edge -> the LOWER range
    assert n.pick_option(q, ["Less than 1 year", "1 to 2 years", "2 to 4 years", "More than 4 years"], "2") == "1 to 2 years"
    assert n.pick_option(q, ["Yes", "No"], "2") is None                                                     # a number never becomes Yes/No


def test_greeting_bubbles_are_not_chips():  # a company: "Hi Asha Example, thank you..." was collected as an answer option, so the number field was never filled
    import re
    opts = ["Hi Asha Example, thank you for applying", "Thank you for your responses", "1-3 years", "Yes"]
    kept = [o for o in opts if not re.match(r"(?i)(hi|hello|hey|dear|welcome|thank)\b", o.strip())]
    assert kept == ["1-3 years", "Yes"]


def test_stored_procedures_count_as_sql_years_confirmed_2026_11_10():
    from apply import answers_map as am
    assert am.answer_for("How many years of experience do you have in Stored Procedures?") == "3"
    assert am.answer_for("How many years of experience do you have in Liquidity Risk?") is None
    assert am.answer_for("How many years of experience do you have in Semantic Modelling?") is None


def test_current_employer_field_does_not_claim_a_current_job():  # a career break (Cred's form showed 'Current company: Example Retail Co')
    from apply import answers_map as am
    assert am.answer_for("Current company") == "Example Retail Co (until Jan 2025)" and am.answer_for("Current employer") == "Example Retail Co (until Jan 2025)"
    assert am.answer_for("Last employer") == "Example Retail Co" and am.answer_for("Previous company") == "Example Retail Co"   # past-tense fields are unchanged
    assert am.answer_for("Currently working at") == "No"                                                              # a yes/no question keeps its honest No
