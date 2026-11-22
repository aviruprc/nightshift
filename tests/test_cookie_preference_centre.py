"""a company / SmartRecruiters: the Privacy Preference Center covered the form; its only button was 'Confirm My Choices', which dismiss_cookies ignored."""
import inspect
from apply import engine


def test_confirm_my_choices_is_a_dismissable_cookie_button():
    assert "confirm my choices" in inspect.getsource(engine.dismiss_cookies)
