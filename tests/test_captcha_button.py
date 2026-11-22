"""a company + a company: `.g-recaptcha` on a plain submit button ("Join Talent Community") is not a challenge."""
import pytest
from playwright.sync_api import sync_playwright

from apply import engine

BUTTON = '<html><body><h1>Role</h1><button type="submit" class="g-recaptcha" data-sitekey="k" data-callback="cb">Join Talent Community</button></body></html>'
CHALLENGE = ('<html><body><h1>Role</h1><div class="g-recaptcha" style="width:304px;height:78px">'
             '<iframe src="https://www.google.com/recaptcha/api2/anchor?size=normal" style="width:304px;height:78px"></iframe></div></body></html>')


@pytest.fixture()
def page():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_page()
        yield pg
        b.close()


def test_a_recaptcha_submit_button_is_not_a_challenge(page):
    page.set_content(BUTTON)
    assert engine.page_state(page) == "ok"


def test_a_visible_challenge_iframe_still_counts(page):
    page.set_content(CHALLENGE)
    assert engine.page_state(page) == "captcha"


def test_talentbrew_hosts_with_the_invisible_recaptcha_apply_button_are_parked_for_her():
    from apply import runner
    for u in ("https://jobs.cargill.com/job/Bangalore-x/1368860657/", "https://careers.cargill.com/en/job/bengaluru/x",
              "https://careers.moodys.com/en/job/bengaluru/sr-credit-associate/49841/1"):
        assert runner.INVISIBLE_RECAPTCHA_HOSTS.search(u), u
    assert not runner.INVISIBLE_RECAPTCHA_HOSTS.search("https://careers.example.com/job/1")


def test_a_referral_marketplace_is_a_junk_host():
    from apply import runner
    assert runner.JUNK_HOSTS.search("https://getmereferred.com/candidate/edit-profile")
