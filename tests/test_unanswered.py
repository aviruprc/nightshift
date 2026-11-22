"""Unanswered form questions: answer ONLY recorded facts, route the rest to the user as ONE stable question.
Every test names the real bug it locks in. Browser tests are headless and use an inline Naukri-drawer fixture (no live site)."""
import html
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import answers_map as am  # noqa: E402
from apply import errors, naukri_apply, runner  # noqa: E402
from core import db as dbm  # noqa: E402

DRAWER = "chatbot_Drawer"


# ---------- facts she has recorded: answered, with the source named ----------
def test_bengaluru_residence_is_answered_from_her_recorded_location():
    # Miles Education / LKQ / A Client of Next Learn / a company were parked on this exact wording
    for q in ("Are you currently residing in Bengaluru, Karnataka or willing to relocate to Bengaluru?",
              "Are you currently residing in Bengaluru or willing to relocate to Bengaluru?",
              "Are you currently residing in Bengaluru, Karnataka or willing to relocate to Bengaluru, Karnataka?"):
        assert am.answer_for(q) == "Yes", q
        assert "current_location" in am.source_of(q, "Yes")


def test_india_work_authorisation_is_answered_from_answers_yaml():
    q = "Are you legally authorized to work in the country you are applying for?"  # UST / RippleHire
    assert am.answer_for(q) == "Yes"
    assert "work_authorization_home" in am.source_of(q, "Yes")


def test_relocation_willingness_comes_from_the_recorded_answer():
    assert am.answer_for("Are you willing to relocate to Bengaluru?") == "Yes"
    assert "willing_to_relocate" in am.source_of("Are you willing to relocate to Bengaluru?", "Yes")


def test_total_and_relevant_experience_use_the_recorded_figures():
    for q in ("How many years of total work experience do you have?",
              "How many years of relevant experience do you have?",
              "How many years of experience do you have in total?"):
        assert am.answer_for(q) == "4", q
        assert not am.needs_her(q), q  # career-level, not a skill figure


# ---------- former-employee questions: No for a company she never worked for, never a blanket No ----------
def test_an_employee_id_question_is_answered_no_not_with_an_email_address():
    """a company (all four jobs): the question contains "email address", so the plain EMAIL rule won and the answer to a
    Yes/No radio would have been her email address. It is the former-employee question: she has never worked at a company."""
    q = ("Have you ever been issued a Cisco Employee ID or Cisco email address? This includes individuals who were Interns, "
         "Co-Ops, Temporary, and Contractors at Cisco.")
    assert am.answer_for(q) == "No"


@pytest.mark.parametrize("q", [
    "Were you previously employed by this company?",
    "Have you worked for our organisation before?",
    "Are you a current employee of the company?",
])
def test_former_employee_questions_are_answered_no_for_a_company_she_never_worked_for(q):
    assert am.answer_for(q) == "No", q


def test_the_former_employee_rule_never_says_no_about_one_of_her_own_employers():
    """The user worked at Demo Telecom 2019-2021 and may apply there again: a blanket No would be a false statement on a real form."""
    q = "Have you ever been issued a Demo Telecom Employee ID or Demo Telecom email address?"
    assert am.answer_for(q) != "No", q


# ---------- skill-specific figures are HER fact, never a guess ----------
@pytest.mark.parametrize("q", [
    "How many years of experience do you have in VBA Macros?",        # a company: was answered "4"
    "How many years of experience do you have in Macros?",            # a company
    "How many years of experience do you have in Program Coordinator?",  # a company
    "Have you any experience in Call Report, FR Y-9C, CCAR, TIC Reports, FR 2900 and FR 2052a ?",  # a company
    "Experience in dashboards (years)",
])
def test_skill_specific_experience_is_never_guessed_and_routes_to_her(q, monkeypatch):
    # the user confirmed SQL / Excel / Power BI / regulatory etc. from her CV dates (they now answer; see test_confirmed_skill_years...), and
    # answers she saved from the dashboard live in answers.yaml. This test is about a skill with NO recorded fact, so it runs without those.
    from core import profile
    real = profile.answers()
    monkeypatch.setattr(profile, "answers", lambda: {k: v for k, v in real.items() if not k.startswith(("how_many_years_of_experience_do_you_have_in_", "experience_"))})
    assert am.answer_for(q) is None, q
    assert am.needs_her(q), q
    assert am.canonical_question(q).endswith("?")


# ---------- commercial preferences: no recorded fact, so ask her ----------
@pytest.mark.parametrize("q", [
    "Are you ok with Third party Payroll?",  # CIEL HR; the old rule invented "Yes" on
    "Do you have experience in Quick commerce ?",  # Shadowfax
    "Are you open to a contract-to-hire role?",
])
def test_commercial_and_skill_preferences_need_her_decision(q):
    assert am.answer_for(q) is None, q
    assert am.needs_her(q), q


def test_which_industry_her_experience_is_in_is_her_question_not_our_bug():
    """IT consulting (Naukri drawer): 'Which industry experience you are having (i.e Banking, healthcare,
    Ecommerce, Retail?' was classified `logic` - our defect, retried three times - although only she can say which
    sectors her experience covers. A question she must answer parks the job (needs_you), it never retries blindly."""
    q = ("unanswered required: Which industry experience you are having( i.e Banking , healthcare , Ecommerce, Retail? "
         "(after clicking, the drawer did not advance)")
    assert am.answer_for(q) is None, q
    assert am.needs_her(q), q
    assert errors.classify("failed", q) == "personal"
    assert errors.status_for(errors.classify("failed", q)) == "needs_you"


def test_a_national_id_question_is_never_answered_with_a_guess():
    """a company: 'National ID number' is a personal identifier (PAN/Aadhaar) that is in none of her records. The rule matches
    (so no generic rule can speak) and yields nothing, which parks the job with the exact question."""
    for q in ("National ID number", "Aadhaar number", "PAN card number", "Social Security Number (SSN)"):
        assert am.answer_for(q) is None, q


def test_identical_questions_collapse_to_one_stable_reason():
    # the dashboard groups needs_you rows by this string: 14 copies of one question must be ONE entry
    a = am.canonical_question("Q1. Do you have experience in Quick commerce ?")
    b = am.canonical_question("  Do you have experience in Quick commerce ?   ")
    c = am.canonical_question("2) Do you have experience in Quick commerce?")
    assert a == b == c == "Do you have experience in Quick commerce?"


def test_an_answer_she_already_gave_for_the_same_question_is_reused(tmp_path, monkeypatch):
    # dashboard 'Save and retry' writes answers.yaml; the identical question must then be answered without asking again
    from core import profile
    p = tmp_path / "answers.yaml"
    p.write_text(yaml.safe_dump({"how_many_years_of_experience_do_you_have_in_macros": "3"}), encoding="utf-8")
    monkeypatch.setattr(profile, "ANSWERS", p)
    assert am.answer_for("How many years of experience do you have in Macros?") == "3"


def test_reading_failure_is_our_bug_not_a_question_for_her():
    detail = "unanswered required: required field 'Years of experience' (Naukri's question text was not legible)"
    assert not am.needs_her(detail)
    assert errors.classify("needs_you", detail) == "logic"


# ---------- runner: a real question is parked as needs_you with the question ----------
@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never touch the real ledger
    db = dbm.DB(tmp_path / "t.db")
    jid, _ = db.upsert_job({"source": "t", "company": "Narayana Health", "title": "MIS Analyst", "location": "Bengaluru",
                            "url": "https://x.test/1", "apply_url": "https://x.test/1"})
    db.set_status(jid, "candidate")
    return db, jid


def test_a_skill_question_is_parked_as_needs_you_with_the_exact_question(d):
    db, jid = d
    r = db.rows("id=?", (jid,))[0]
    detail = "unanswered required: " + am.canonical_question("How many years of experience do you have in Macros?")
    runner.handle_failure(db, r, "needs_you", {"detail": detail}, False, {})
    row = db.rows("id=?", (jid,))[0]
    assert row["status"] == "needs_you" and row["needs_you_reason"] == detail


def test_an_unreadable_required_question_stays_our_bug(d):
    db, jid = d
    r = db.rows("id=?", (jid,))[0]
    detail = "unanswered required: required field 'Years of experience' (Naukri's question text was not legible)"
    runner.handle_failure(db, r, "needs_you", {"detail": detail}, False, {})
    row = db.rows("id=?", (jid,))[0]
    assert row["status"] == "failed" and row["fail_class"] == "logic" and not row["needs_you_reason"]


# ---------- headless Naukri drawer fixtures ----------
@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright  # function-scoped: module scope breaks later sync_playwright calls
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        yield pg
        b.close()


def _drawer(page, question, options=("Yes", "No"), extra=""):
    # The option text is carried in a data attribute, not inside an inline onclick: "Bachelor's Degree" contains an
    # apostrophe, and repr turned it into answer("...") inside a double-quoted attribute - invalid HTML, unclickable
    # chip, and the test then "proved" a failure that did not exist.
    buttons = "".join('<button data-a="%s">%s</button>' % (html.escape(o, quote=True), html.escape(o)) for o in options)
    page.set_content(f"""<!doctype html><meta charset=utf-8>
      <div class="{DRAWER}" id="d"><div>{question}</div>{buttons}{extra}</div>
      <script>window.__answer = null;  /* set_content reuses the window in Chromium: clear the previous answer */
      document.getElementById('d').addEventListener('click', (e) => {{
        const b = e.target.closest('button[data-a]'); if (!b) return;
        window.__answer = b.dataset.a; document.getElementById('d').style.display = 'none'; }});</script>""")


def test_drawer_answers_a_recorded_fact_question(page):
    _drawer(page, "Are you currently residing in Bengaluru, Karnataka or willing to relocate to Bengaluru?")
    done, q = naukri_apply.answer_chat(page, "j_fact")
    assert done is True and q is None
    assert page.evaluate("window.__answer") == "Yes"


def test_drawer_parks_a_skill_years_question_without_clicking_an_answer(page):
    _drawer(page, "How many years of experience do you have in Macros?", options=("0", "1", "2", "3", "4"))
    done, q = naukri_apply.answer_chat(page, "j_macros")
    assert done is False
    assert q == "How many years of experience do you have in Macros?"
    assert page.evaluate("window.__answer") is None  # never guess a figure


def test_a_degree_question_is_yes_when_the_drawer_offers_yes_no():
    """a company: the honest answer 'MSc (Statistics)' matched no chip and the job was lost."""
    assert am.answer_for("Do you have a bachelor's degree or higher qualification?") == "Yes"


def test_the_degree_rule_never_answers_a_visa_question_yes():
    """'Do you hold a postgraduate work visa?' contains the word postgraduate; a degree rule must not claim it (she has an
    India work permit only). Unanswered is the correct outcome - the job is parked for her."""
    assert am.answer_for("Do you hold a postgraduate work visa?") is None


def test_drawer_picks_her_real_degree_level_when_the_drawer_offers_levels(page):
    _drawer(page, "Do you have a bachelor's degree or higher qualification?",
            options=("Bachelor's Degree", "Master's Degree", "Doctorate"))
    done, q = naukri_apply.answer_chat(page, "j_degree")
    assert done is True and q is None
    assert page.evaluate("window.__answer") == "Master's Degree"   # her facts: BSc + MSc


def test_drawer_answers_a_degree_question_with_yes_when_the_chips_are_yes_no(page):
    _drawer(page, "Do you have a bachelor's degree or higher qualification?")
    done, q = naukri_apply.answer_chat(page, "j_degree2")
    assert done is True and q is None
    assert page.evaluate("window.__answer") == "Yes"


def test_a_date_question_with_a_text_box_is_typed_even_when_chat_text_looks_like_options(page):
    """a company: 'What is your Date of Birth (dd/mm/yyyy)?' was parked with 'no option matching 17/09/1993' - the
    drawer showed a text box AND non-matching chat lines, and the option branch swallowed the field."""
    page.set_content(f"""<!doctype html><meta charset=utf-8>
      <div class="{DRAWER}" id="d"><div>What is your Date of Birth (dd/mm/yyyy)?</div>
        <input id="dob"><button onclick="document.getElementById('d').style.display='none'">Save</button>
        <div>Yes</div><div>No</div></div>""")
    done, q = naukri_apply.answer_chat(page, "j_dob")
    assert done is True and q is None, q
    assert page.eval_on_selector("#dob", "e => e.value") == "17/09/1993"


def test_a_location_chip_shorter_than_her_recorded_location_is_still_answered(page):
    """a company: the answer 'Bengaluru, Karnataka, India' matched neither 'Bengaluru' nor 'Bangalore'."""
    for opts, want in ((("Bengaluru", "Hyderabad", "Pune"), "Bengaluru"), (("Bangalore", "Chennai"), "Bangalore"),
                       (("Karnataka", "Tamil Nadu"), None)):
        _drawer(page, "Current Location?", options=opts)
        done, q = naukri_apply.answer_chat(page, "j_loc")
        got = page.evaluate("window.__answer")
        assert got == want, (opts, got)
        assert done is (want is not None), (opts, done, q)


def test_drawer_parks_a_commercial_preference_with_the_exact_wording(page):
    _drawer(page, "Do you have experience in Quick commerce ?")
    done, q = naukri_apply.answer_chat(page, "j_qc")
    assert done is False
    assert q == "Do you have experience in Quick commerce?"


def test_unreadable_optional_question_is_skipped_not_blocking(page):
    # the portal's own Skip is offered -> the question is optional, so the application must not be parked on it
    page.set_content(f"""<!doctype html><meta charset=utf-8>
      <div class="{DRAWER}" id="d"><div>...</div>
      <button onclick="document.getElementById('d').style.display='none'">Skip</button></div>""")
    done, q = naukri_apply.answer_chat(page, "j_skip")
    assert done is True and q is None


def test_unreadable_required_question_names_the_field(page):
    page.set_content(f"""<!doctype html><meta charset=utf-8>
      <div class="{DRAWER}" id="d"><div>...</div>
      <input type="text" aria-label="Years of experience">
      <button>Save</button></div>""")
    done, q = naukri_apply.answer_chat(page, "j_unreadable")
    assert done is False
    assert "Years of experience" in q
    assert "could not read the question" not in q.lower()


def test_engine_never_asks_the_llm_a_question_that_is_the_users_to_decide(monkeypatch):
    """Accuracy: the generic engine used to hand EVERY unanswered form question to the LLM, including 'How many years of
    experience do you have in VBA Macros?' - the model could answer with her total career figure, an overclaim on a submitted form. Skill-year
    and commercial-preference questions now stay parked (they surface on the dashboard with the exact wording) and are never sent to the LLM."""
    from apply import engine, llm_answer

    fields = [{"id": 1, "type": "text", "tag": "input", "label": "How many years of experience do you have in VBA Macros?", "q": "", "options": None,
               "value": "", "name": "vba", "placeholder": "", "group": None},
              {"id": 2, "type": "text", "tag": "input", "label": "Years of experience in SQL", "q": "", "options": None,
               "value": "", "name": "sql", "placeholder": "", "group": None},
              {"id": 3, "type": "text", "tag": "input", "label": "Full name", "q": "", "options": None,
               "value": "", "name": "name", "placeholder": "", "group": None}]

    class FakeLoc:  # a field the rules cannot fill: every interaction is a no-op that reports failure
        first = None

        def __init__(self):
            self.first = self

        def __getattr__(self, _name):
            def call(*_a, **_k):
                return False if _name in ("is_checked", "count", "is_visible") else FakeLoc()
            return call

    class PG:
        def evaluate(self, *_a, **_k):
            return fields

        def locator(self, *_a, **_k):
            return FakeLoc()

    asked = []

    def fake_answer(qs, job=None, **kw):
        asked.extend(q["q"] for q in qs)
        return {}

    monkeypatch.setattr(llm_answer, "answer", fake_answer)
    monkeypatch.setattr(engine, "CURRENT_JOB", {"id": "t", "company": "Acme", "title": "Analyst"})
    unknown, filled = [], []
    engine.fill_page(PG(), __import__("pathlib").Path("cv.pdf"), unknown, filled)
    assert "How many years of experience do you have in VBA Macros?" not in asked
    assert "Years of experience in SQL" not in asked
    assert any(u.startswith("How many years of experience do you have in VBA Macros?") for u in unknown)  # parked, not guessed


def test_a_degree_question_never_picks_a_degree_she_does_not_hold():
    """a company (again): 'I have a bachelor's or higher' parked although her facts contain BSc + MSc. The chip list
    can name degrees directly; a chip she HOLDS is a true answer, a chip she does not (B.Com/B.Sc) or a doctorate is never clicked."""
    q = "Do you have a bachelor's degree or higher qualification?"
    assert naukri_apply.pick_option(q, ["Bachelor of Engineering"], "Yes") == "Bachelor of Engineering"
    assert naukri_apply.pick_option(q, ["B.E/B.Tech"], "Yes") == "B.E/B.Tech"
    assert naukri_apply.pick_option(q, ["Doctorate", "Bachelor's Degree"], "Yes") == "Bachelor's Degree"
    assert naukri_apply.pick_option(q, ["Bachelor's Degree", "Master's Degree", "Doctorate"], "Yes") == "Master's Degree"  # her MSc
    assert naukri_apply.pick_option(q, ["B.Com", "B.Sc"], "Yes") is None, "a degree she does not hold was picked"


def test_a_question_that_does_not_advance_names_what_was_clicked(page):
    """a company: the drawer was parked with just the question, so nothing said whether the chip click landed,
    what was clicked, or what the drawer still showed. The reason must carry that evidence."""
    page.set_content(f"""<!doctype html><meta charset=utf-8>
      <div class="{DRAWER}" id="d"><div>Do you have a bachelor's degree or higher qualification?</div>
      <button>Master's Degree</button><button>Bachelor's Degree</button></div>
      <script>window.__clicks = 0; document.getElementById('d').addEventListener('click', () => {{ window.__clicks++; }});</script>""")
    done, q = naukri_apply.answer_chat(page, "j_stuck")
    assert done is False
    assert "after clicking" in q, q
    assert "Master's Degree" in q, q


def test_confirmed_skill_years_answer_and_vba_still_parks():
    """SQL, Excel, Power BI ... VBA was never on the list (it stopped riding on Excel)."""
    assert am.answer_for("How many years of experience do you have in SQL?") == "3"
    assert am.answer_for("How many years of experience do you have in Power BI?") == "2"
    assert am.answer_for("How many years of experience do you have in VBA Macros?") is None
