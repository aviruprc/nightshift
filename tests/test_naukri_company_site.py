"""KB-23: a Naukri job that hands off to the company's own site must record WHERE it went.

The signed-out public page exposes no apply control, so this can only be read in the signed-in context (the worker). These
tests drive the pure DOM reader with synthetic pages; they never touch the live site.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import naukri_apply  # noqa: E402


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()  # the apply flow CLOSES the page it is given: sibling pages need a real context
        pg = ctx.new_page()
        yield pg
        b.close()


def test_a_workday_link_is_kept_as_the_company_site_target(page):
    page.set_content("""<!doctype html><meta charset=utf-8><body>
      <a href="https://www.naukri.com/job-listings-x">Other jobs</a>
      <a href="https://acme.wd5.myworkdayjobs.com/en-US/Acme/job/Bangalore/Data-Analyst_R123">Apply on company site</a>
      <a href="https://twitter.com/acme">Follow us</a></body>""")
    url, host = naukri_apply.company_site_target(page)
    assert url == "https://acme.wd5.myworkdayjobs.com/en-US/Acme/job/Bangalore/Data-Analyst_R123"
    assert host == "acme.wd5.myworkdayjobs.com"


def test_an_ordinary_external_link_is_never_named_as_the_apply_target(page):
    # two live rows read "Naukri sends this to the company site (www.ambitionbox.com)" because the old reader named the
    # FIRST external anchor on the page. A recommendation / ad / social link is not the hand-off control: say nothing instead.
    page.set_content("""<!doctype html><meta charset=utf-8><body>
      <a href="https://www.naukri.com/job-listings-y">Similar jobs</a>
      <a href="https://www.ambitionbox.com/reviews/acme">Acme reviews</a>
      <a href="https://www.linkedin.com/company/acme">Acme on LinkedIn</a>
      <button>Apply on company site</button></body>""")
    assert naukri_apply.company_site_target(page) == (None, None)


def test_the_controls_own_non_ats_link_is_named_but_not_stored(page):
    # the control really goes somewhere that is not an ATS we can drive: name it so she knows, never store it as an apply URL
    page.set_content("""<!doctype html><meta charset=utf-8><body>
      <a href="https://www.ambitionbox.com/reviews/acme">Acme reviews</a>
      <a href="https://careers.acme.com/apply/123">Apply on company site</a></body>""")
    url, host = naukri_apply.company_site_target(page)
    assert url is None
    assert host == "careers.acme.com"


def test_a_controls_url_in_a_data_attribute_is_read(page):
    page.set_content("""<!doctype html><meta charset=utf-8><body>
      <button data-apply-url="https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/42">Apply on company site</button></body>""")
    url, host = naukri_apply.company_site_target(page)
    assert url == "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/42"
    assert host == "acme.fa.oraclecloud.com"


def test_a_page_with_no_external_link_reports_nothing_rather_than_a_guess(page):
    page.set_content("""<!doctype html><meta charset=utf-8><body>
      <a href="https://www.naukri.com/job-listings-z">Other jobs</a></body>""")
    assert naukri_apply.company_site_target(page) == (None, None)


def test_a_handoff_to_a_known_ats_is_handed_to_the_runner_as_an_apply_url(monkeypatch, page, tmp_path):
    """KB-23 step 2: the captured Workday URL must survive the call as res['apply_url'], or the row dies at 'deferred_board' again."""
    monkeypatch.setattr(naukri_apply, "company_site_target",
                        lambda pg: ("https://acme.wd5.myworkdayjobs.com/en-US/Acme/job/R1", "acme.wd5.myworkdayjobs.com"))
    f = tmp_path / "job.html"
    f.write_text("<!doctype html><meta charset=utf-8><body><button>Apply on company site</button></body>", encoding="utf-8")

    class Ctx:  # apply_naukri closes the page it opens, so every call gets its own
        def new_page(self):
            return page.context.new_page()

    res = naukri_apply.apply_naukri(Ctx(), f.as_uri(), None, True, "t_handoff")
    assert res["status"] == "board_direct"
    assert res["apply_url"] == "https://acme.wd5.myworkdayjobs.com/en-US/Acme/job/R1"


def test_a_handoff_with_no_target_carries_no_apply_url(monkeypatch, page, tmp_path):
    monkeypatch.setattr(naukri_apply, "company_site_target", lambda pg: (None, None))
    f = tmp_path / "job.html"
    f.write_text("<!doctype html><meta charset=utf-8><body><button>Apply on company site</button></body>", encoding="utf-8")

    class Ctx:
        def new_page(self):
            return page.context.new_page()

    res = naukri_apply.apply_naukri(Ctx(), f.as_uri(), None, True, "t_handoff2")
    assert res["status"] == "board_direct" and "apply_url" not in res
