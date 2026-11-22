"""A final Submit that does nothing on a page with an hCaptcha is a CAPTCHA for her (Lever: 14 jobs 'page stopped changing ', 0 sent)."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402


def test_the_lever_hcaptcha_widget_is_seen():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content("<form><div class='h-captcha' data-sitekey='x'></div><button type='submit'>Submit application</button></form>")
        assert agent.captcha_on_page(pg)
        pg.set_content("<form><button type='submit'>Submit application</button></form>")
        assert not agent.captcha_on_page(pg)
        b.close()


def test_the_stuck_submit_branch_reports_a_captcha_not_page_stopped_changing():
    src = inspect.getsource(agent.navigate)
    assert "final_step is not None and captcha_on_page(pg)" in src and "the Submit button does nothing until it is solved" in src
