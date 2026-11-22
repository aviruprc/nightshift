""". The answer cache held keys that are
not questions ('yes' => 'No', 'type your response' => her name) and a model's guesses at skill-years. Neither is stored or served."""
from core import cache


def test_a_key_that_is_not_a_question_is_never_cached():
    for q in ("Yes", "Type your response", "Textbox", "Company", "Job Title", "Year", "QA 1"):
        assert not cache.cacheable(q), q


def test_skill_years_are_never_remembered_from_a_guess():
    for q in ("How many years of experience do you have in Surveillance?", "How many years of experience do you have as an MIS Executive?",
              "How many years of solid experience in FP&A, business controlling or IT cost controlling?"):
        assert not cache.cacheable(q), q


def test_ordinary_questions_are_still_cached():
    for q in ("Are you willing to attend a face to face interview in Bengaluru?", "How did you hear about us?", "Notice period", "Phone Device Type",
              "Have you worked at Acme before?"):
        assert cache.cacheable(q, "Acme"), q


def test_put_and_get_refuse_a_junk_key(tmp_path, monkeypatch):
    from core import db as dbm
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "c.db")
    cache.put("Yes", ["Yes", "No"], "No")
    assert cache.get("Yes", ["Yes", "No"]) is None
    cache.put("How did you hear about us?", None, "Career Site")
    assert cache.get("How did you hear about us?", None) == "Career Site"
