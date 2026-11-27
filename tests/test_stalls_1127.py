"""Stalls that cost a whole run: a human-check wall filed as a rendering bug, an SMS-code login filed as ours, a site's own confirmation thrown away, a code
shorter than its boxes typed and submitted."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent, errors, runner  # noqa: E402


def test_a_security_check_wall_is_a_captcha_not_a_rendering_bug():
    snap = {"heads": ["Quick security check"], "text": "Quick security check Please confirm you're human to continue. Create Job Alert Language Loading.."}
    assert agent.human_wall_page(snap)
    assert not agent.human_wall_page({"heads": ["Senior Data Analyst"], "text": "Apply now. Security and privacy"})
    src = inspect.getsource(agent.navigate)
    assert src.index("human_wall_page(snap)") < src.index("if page_loading(snap):")


def test_an_sms_code_login_is_the_users_whatever_the_models_wording():
    assert errors.classify("failed", "navigator: careers.example-bank.test requires SMS code verification and the tool cannot read the phone") == "personal"


def test_account_walled_job_sites_are_aggregators_without_a_form():
    for u in ("https://mycareernet.co/mycareernet/jobs/Risk-Underwriting-Senior-Analyst-199696", "https://www.iitjobs.com/job/senior-data-analytics-643127"):
        assert runner.JUNK_HOSTS.search(u), u


def test_a_site_confirmation_after_a_one_step_apply_is_kept_as_unconfirmed():
    """'Application Submitted! You applied to <role>' with no final-labelled click was thrown away as 'nothing was sent'."""
    src = inspect.getsource(agent.navigate)
    assert "elif final_step is None and proof_txt:" in src and 'status="unconfirmed"' in src
    assert src.index("elif final_step is None and proof_txt:") < src.index("claimed 'submitted' but never clicked a final Submit")


def test_a_code_shorter_than_the_boxes_is_never_typed():
    """Eight 'Digit k of 8' boxes and a mail reader that returned a 4-digit year: the year was typed and submitted."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content("".join(f"<input type='text' maxlength='1' aria-label='Digit {k} of 8'>" for k in range(1, 9)))
        assert agent.fill_code(pg, "2026") is False
        assert pg.locator("input").first.input_value() == ""
        b.close()
