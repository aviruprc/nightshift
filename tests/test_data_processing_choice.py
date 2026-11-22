"""asked how her data may be processed, choose the narrowest option (only the roles she applied to)."""
from apply import agent

Q = "Please select how you wish for your data to be processed."


def _snap(checked=None):
    return {"els": [
        {"i": 7, "type": "checkbox", "label": "global_privacy-consent-checkbox", "req": True, "checked": False},
        {"i": 8, "type": "radio", "label": "I would like to join AstraZeneca's Talent Network, and I consent to maintaining my profile", "group": Q, "checked": checked == 8},
        {"i": 9, "type": "radio", "label": "I would like my profile to be considered only for the roles I have applied to.", "group": Q, "checked": checked == 9},
    ]}


def test_the_narrowest_option_is_chosen():
    assert agent.narrow_data_choice(_snap()) == 9


def test_a_choice_already_made_is_left_alone():
    assert agent.narrow_data_choice(_snap(checked=8)) is None


def test_a_page_without_that_question_is_ignored():
    assert agent.narrow_data_choice({"els": [{"i": 1, "type": "radio", "label": "Yes", "group": "Are you over 18?"}]}) is None
