"""a company / SuccessFactors (three live runs): after account creation + emailed passcode, a remembered one-action plan "click Sign In"
was replayed on a login page whose inputs had not rendered yet. The empty submit was rejected, counted as a sign-in attempt, and the real sign-in
was then refused by the two-attempt cap. A sign-in click without credentials is never replayed, and a typed code resets the cap."""
import inspect

from apply import agent


def snap(*els):
    return {"els": [dict(e, i=i) for i, e in enumerate(els)]}


def test_a_lone_sign_in_click_is_not_replayed():
    s = snap({"tag": "a", "text": "Home"}, {"tag": "button", "type": "submit", "text": "Sign In"})
    assert agent.bare_signin_replay([{"do": "click", "i": 1}], s)


def test_a_sign_in_that_fills_the_password_first_is_a_real_plan():
    s = snap({"tag": "input", "type": "text", "label": "Email"}, {"tag": "input", "type": "password", "label": "Password"},
             {"tag": "button", "type": "submit", "text": "Sign In"})
    assert not agent.bare_signin_replay([{"do": "fill", "i": 0, "value": "x"}, {"do": "fill", "i": 1, "value": "{{PASSWORD}}"}, {"do": "click", "i": 2}], s)


def test_other_remembered_clicks_are_untouched():
    s = snap({"tag": "a", "text": "Apply now"}, {"tag": "button", "text": "Next"})
    assert not agent.bare_signin_replay([{"do": "click", "i": 0}], s) and not agent.bare_signin_replay([{"do": "click", "i": 1}], s)


def test_the_navigator_uses_the_guard_and_a_typed_code_resets_the_sign_in_cap():
    src = inspect.getsource(agent.navigate)
    assert "bare_signin_replay(resolved, snap)" in src
    i = src.index('what += " (code typed)"')
    assert "signin_clicks = 0" in src[i:i + 700] and "signin_rejected = False" in src[i:i + 700]


def test_a_reset_we_just_requested_is_finished_not_forbidden():
    src = inspect.getsource(agent.navigate)
    i, j = src.index("elif reset_requested:"), src.index("elif new_pw:")
    assert i < j and "verify_link" in src[i:j] and "Do NOT sign in yet" in src[i:j]      # checked BEFORE the 'a reset was already used' branch
    assert src.index("elif reset_requested:") < src.index("a password reset was ALREADY used")


def test_education_dates_reach_the_form_filler_with_her_recorded_months():
    """a company: the form asks exact education dates; the model invented days and months because only the year was sent."""
    c = agent._facts_compact()
    assert "Aug 2020 to Jun 2022" in c and "Jul 2015 to Jun 2018" in c and "must not be guessed" in c
