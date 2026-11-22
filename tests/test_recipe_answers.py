"""KB-83 / KB-84: a recipe is a route, never an answer - and a page that answers a question for her is checked.

a company "Statistics Analyst - Python SQL" reached "Application Questions 1 of 2" with the required question
"Are you legally authorized to work in the country or jurisdiction where the position to which you are applying is located?*" showing
**No**. That is a false statement about the user (she is legally authorised to work in India). The value was not chosen on this attempt:
on the same page's recipe stored four "No" option picks (sig), and Workday keeps an answer server-side, so
the next application opened with the earlier mistake already filled in. The run only escaped because the model happened to notice; the
deterministic picker "never overwrites a real answer" and would have saved it.

The evidence below is the element list saved with that failure (`a saved failure page`), embedded so the test does not
depend on a failure file that may be pruned (the KB-79 lesson).
"""
import inspect

from apply import agent

# The a company question button exactly as SNAP_JS reported it: the closed dropdown shows its own value as its text.
CITI_ELS = [
    {"i": 0, "tag": "a", "type": "", "label": "", "text": "Skip to main content", "value": "", "req": False},
    {"i": 9, "tag": "button", "type": "submit", "label": "", "text": "Back", "value": "", "req": False, "row": "1/2", "rowctx": "Back"},
    {"i": 10, "tag": "button", "type": "submit", "label": "", "text": "Save and Continue", "value": "", "req": False, "row": "2/2", "rowctx": "Save and Continue"},
    {"i": 7, "tag": "button", "type": "button", "label": "No Required", "text": "No", "value": "e32326e1708d0163849f007ff400fc9a", "req": False,
     "row": "1/6", "rowctx": "e32326e1708d0163849f007ff400fc9a Are you legally authorized to work in the country or jurisdiction where the p",
     "group": "Are you legally authorized to work in the country or jurisdiction where the position to which you are applying is located?*"},
]
QUESTION = CITI_ELS[3]["group"]
RELATIVES = "Do you have any relatives (1) or persons in any other Covered Relationship with a Citi employee?"


def _els_with_shown(text: str) -> list:
    return [dict(e, text=text) if e["i"] == 7 else e for e in CITI_ELS]


# ---- KB-83: an answer is never stored in, or replayed from, a recipe -----------------------------------------------------------
def test_a_question_option_click_is_never_stored():
    """The exact action the step recorded ("click No Required"): clicking an option states a fact, so it is not a route."""
    snap = {"els": CITI_ELS}
    assert agent._storable([{"do": "click", "i": 7, "value": ""}], snap, "pw") is None


def test_a_question_option_pick_is_never_stored():
    snap = {"els": [{"i": 3, "tag": "select", "type": "select", "label": "Do you require sponsorship?", "text": "", "value": "No"}]}
    assert agent._storable([{"do": "select", "i": 3, "value": "No"}], snap, "pw") is None


def test_navigation_and_her_own_details_are_still_stored():
    """The guard must not gut the recipe cache: Continue/Save clicks and fills of her recorded details are routes, not answers."""
    snap = {"els": [{"i": 10, "tag": "button", "type": "submit", "label": "", "text": "Save and Continue", "value": ""},
                    {"i": 11, "tag": "input", "type": "text", "label": "Phone Number*", "text": "", "value": ""}]}
    out = agent._storable([{"do": "click", "i": 10, "value": ""}, {"do": "fill", "i": 11, "value": "9000000001"}], snap, "pw")
    assert out == [{"do": "click", "label": "", "text": "Save and Continue", "tag": "button", "type": "submit", "value": ""},
                   {"do": "fill", "label": "Phone Number*", "text": "", "tag": "input", "type": "text", "value": "9000000001"}]


def test_an_old_answer_recipe_resolves_to_nothing():
    """The rows already in the table (four "No" picks on this page, sig) may never be replayed again."""
    old = [{"do": "select", "label": "Select One Required", "text": "Select One", "tag": "button", "type": "button", "value": "No"},
           {"do": "click", "label": "No Required", "text": "No", "tag": "button", "type": "button", "value": ""}]
    assert agent._resolve(old, {"els": CITI_ELS}, "pw") == []


def test_a_mixed_old_recipe_keeps_only_its_route():
    old = [{"do": "select", "label": "Select One Required", "text": "Select One", "tag": "button", "type": "button", "value": "No"},
           {"do": "click", "label": "", "text": "Save and Continue", "tag": "button", "type": "submit", "value": ""}]
    out = agent._resolve(old, {"els": CITI_ELS}, "pw")
    assert [a["do"] for a in out] == ["click"] and out[0]["i"] == 10


# ---- KB-84: a visible answer that contradicts her record ----------------------------------------------------------------
def test_a_wrong_work_authorisation_answer_is_detected():
    got = agent.question_mismatches(CITI_ELS, None)
    assert got and got[0][0] == QUESTION and got[0][1] == "No" and got[0][2] == "Yes", got


def test_the_right_answer_is_not_a_mismatch():
    assert agent.question_mismatches(_els_with_shown("Yes"), None) == []


def test_two_visible_options_are_ambiguous_and_not_judged():
    pair = _els_with_shown("Yes") + [dict(CITI_ELS[3], i=12, text="No")]
    assert agent.answered_questions(pair) == {}


def test_a_question_her_record_does_not_answer_is_not_judged():
    """Compliance questions she answers "No" where her record has no rule stay untouched (here: relatives in a covered relationship)."""
    els = [{"i": 3, "tag": "button", "type": "button", "label": "No Required", "text": "No", "value": "", "group": RELATIVES}]
    assert agent.answered_questions(els) == {RELATIVES: "No"}
    assert agent.question_mismatches(els, None) == []


def test_only_a_question_shaped_group_is_judged():
    """A paragraph that merely mentions 'authorized' is not a question: a fact is corrected only when the page ASKS."""
    para = "Covered Relationships include persons authorized to act on behalf of a government official and their immediate family"
    els = [{"i": 3, "tag": "button", "type": "button", "label": "No Required", "text": "No", "value": "", "group": para}]
    assert agent.answered_questions(els) == {}


def test_the_navigator_refuses_to_save_past_a_wrong_answer():
    """Source guard: the per-step check must gate the save/continue clicks and the final submit, or a wrong fact is persisted/submitted."""
    src = inspect.getsource(agent.navigate)
    assert "wrong_answers = question_mismatches(" in src
    assert "SAVE_NAV.search(save_text)" in src and "REFUSED" in src
    assert "if is_final and wrong_answers:" in src


def test_pill_buttons_are_answers_never_routes():
    """Oracle HCM: recipes replayed pill-button answers (gender, pronoun, ethnicity, passport, years band, language level,
    salutation) onto later applications while the record said EEO = 'Decline to self-identify'. Any such pill is an answer."""
    for t in ("Male", "He / Him", "Hispanic", "Passport Holder", "5+ years", "Advanced", "Fluent", "Mr"):
        assert agent._is_answer_action("click", {"tag": "button", "type": "button", "text": t}), t
    for t in ("NEXT", "I consent", "ADD LANGUAGE", "APPLY NOW", "Show More", "Save and Continue", "VERIFY"):
        assert not agent._is_answer_action("click", {"tag": "button", "type": "button", "text": t}), t
    assert agent._is_answer_action("select", {"tag": "input", "type": "text", "label": "Language"}, "English")


def test_language_proficiency_and_eeo_follow_her_record():
    facts = agent._facts_compact()
    assert "proficiency levels NOT recorded" in facts and "Decline to self-identify" in facts and "pronouns NOT recorded" in facts
    assert agent._blocked_question("language proficiency required, not recorded").startswith("unanswered required: Language proficiency")


def test_a_tick_is_a_claim_unless_it_is_consent_or_an_opt_out():
    """Review: 'I am fluent in this language.' was stored on a company; fluency is not recorded."""
    def el(t, typ="checkbox"):
        return {"tag": "input", "type": typ, "text": t, "label": t}
    for t in ("I am fluent in this language.", "They/them", "flexible", "None of the above", "LinkedIn"):
        assert agent._is_answer_action("check", el(t)), t
    for t in ("I agree with the terms and conditions", "Yes, I have read and consent to the terms and conditions*", "Terms and Conditions"):
        assert not agent._is_answer_action("check", el(t)), t
    assert not agent._is_answer_action("uncheck", el("I agree to receive marketing communications"))
    assert agent._is_answer_action("fill", el("Please indicate the languages in which you are fluent?*", "text"), "English, Tamil")


def test_the_models_own_wording_of_the_proficiency_block_parks_it_for_her():
    """a company: 'Language dialog is open. Candidate's languages are English and Tamil, but proficiency levels are NOT recorded.'
    was stored as our bug (logic)."""
    why = "Language dialog is open. Candidate's languages are English and Tamil, but proficiency levels are NOT recorded. The form requires them."
    assert agent._blocked_question(why).startswith("unanswered required: Language proficiency")
