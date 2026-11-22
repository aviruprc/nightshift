"""A job page whose Apply button is a dropdown toggle: the page's own apply link is followed (a company, SAP SuccessFactors RMK,
 - four clicks on 'Apply now' changed nothing). Uses the saved page, served offline at its real address."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402

HTML = (Path(__file__).parent / "fixtures" / "ntt_rmk_job.html").read_text(encoding="utf-8", errors="replace")
URL = "https://careers-inc.nttdata.com/job/Bangalore-Business-Analyst-KA/1451689600/"


def test_the_rmk_menu_item_is_clicked_not_the_talentcommunity_link():
    """Live probe: /talentcommunity/apply/<id>/ redirects to the home page; the menu's 'Apply Now' item opens the form."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.route("**/*", lambda r: r.fulfill(status=200, body=HTML, content_type="text/html") if r.request.url == URL else r.abort())
        pg.goto(URL)
        href = agent.hidden_apply_href(pg)
        clicked = agent.rmk_apply_manually(pg)
        b.close()
    assert href == "" and clicked


def test_the_navigator_follows_it_once_on_a_stuck_job_page():
    src = inspect.getsource(agent.navigate)
    assert "hidden_apply_href(pg)" in src and "apply_href_tried = True" in src and "rmk_apply_manually(pg)" in src


def test_an_apply_anchor_to_a_known_ats_on_another_host_is_followed():
    from playwright.sync_api import sync_playwright
    html = (Path(__file__).parent / "fixtures" / "gs_role_apply.html").read_text(encoding="utf-8", errors="replace")
    url = "https://higher.gs.com/roles/189177"
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.route("**/*", lambda r: r.fulfill(status=200, body=html, content_type="text/html") if r.request.url == url else r.abort())
        pg.goto(url)
        href = agent.hidden_apply_href(pg)
        b.close()
    assert href == "https://hdpc.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/LateralHiring/job/189177/apply/email"
