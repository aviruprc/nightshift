"""Safety gates: one-click boards never clicked, empty JD can't pass 'tailored', no untrue answers, saved answers are used."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright  # noqa: E402

from apply import answers_map as am  # noqa: E402
from apply import engine  # noqa: E402
from cv import ats_check  # noqa: E402

CV = ROOT / "tests" / "fixtures" / "profile" / "Asha_Example_Resume.pdf"   # generated from the fake profile
WRAP = "<!doctype html><meta charset=utf-8><title>Apply</title><form onsubmit=\"event.preventDefault();document.body.innerHTML='<h1>Thank you for applying</h1>'\">{}<button type=submit>Submit application</button></form>"
BASE = '<label for=fn>First name *</label><input id=fn required><label for=em>Email *</label><input id=em type=email required><label for=cv>Resume *</label><input id=cv type=file required>'


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        yield b.new_page()
        b.close()


def form(tmp_path, body, name="f"):
    p = tmp_path / f"{name}.html"
    p.write_text(WRAP.format(BASE + body), encoding="utf-8")
    return p.as_uri()


def run(page, url, dry=False, job="t_gate"):
    return engine.apply_to_url(page, url, CV, dry_run=dry, job_id=job)


def test_one_click_board_is_never_touched(page):
    for host in ("https://www.naukri.com/job-listings-x", "https://www.shine.com/jobs/x", "https://www.apna.co/job/x", "https://in.indeed.com/viewjob?jk=1"):
        r = engine.apply_to_url(page, host, CV, dry_run=True, job_id="t_oc")
        assert r["status"] == "board_direct", (host, r)
    assert page.url == "about:blank"  # nothing was even opened, so nothing could be clicked


def test_empty_or_short_description_fails_the_tailoring_gate():
    r = ats_check.check(CV, "", "Data Analyst")
    assert not r["ok"] and any("description" in p for p in r["problems"]), r
    r2 = ats_check.check(CV, "Power BI and SQL. " * 30, "Data Analyst")
    assert not any("description" in p for p in r2["problems"]), r2


def test_python_years_question_parks_the_job(page, tmp_path):
    r = run(page, form(tmp_path, '<label for=py>Years of experience with Python *</label><input id=py required>'))
    assert r["status"] == "needs_you" and "Python" in r["detail"], r
    assert "Thank you" not in page.inner_text("body")


def test_unverified_tool_experience_parks_but_generic_experience_is_answered(page, tmp_path):
    r = run(page, form(tmp_path, '<label for=x>Years of experience with SAP *</label><input id=x required>'))
    assert r["status"] == "needs_you" and "SAP" in r["detail"], r
    ok = run(page, form(tmp_path, '<label for=x>Total years of experience *</label><input id=x required>', "gen"), dry=True)
    assert ok["status"] == "dry_run_ok" and page.input_value("#x") == "4", ok
    sql = run(page, form(tmp_path, '<label for=x>Years of experience in SQL *</label><input id=x required>', "sql"), dry=True)
    # the user CONFIRMED per-skill years on (answers.yaml skill_years: SQL 3 for the test person). A confirmed skill figure is answered; a skill
    # she has no confirmed figure for (SAP above) still parks. The earlier expectation (park SQL) predates that confirmation.
    assert sql["status"] == "dry_run_ok" and page.input_value("#x") == "3", sql


def test_submit_is_clicked_only_once_even_if_the_form_stays_on_screen(page, tmp_path):
    html = WRAP.format(BASE).replace("document.body.innerHTML='<h1>Thank you for applying</h1>'",
                                     "window.__n=(window.__n||0)+1;document.title='submitted '+window.__n;document.getElementById('msg').textContent='Thanks, we will be in touch'")
    p = tmp_path / "stay.html"
    p.write_text(html + "<p id=msg></p>", encoding="utf-8")
    r = run(page, p.as_uri())
    assert r["status"] == "unconfirmed", r
    assert page.evaluate("window.__n") == 1  # exactly one submission


def test_search_or_alert_box_is_not_an_application_form(page, tmp_path):
    p = tmp_path / "alert.html"
    p.write_text("<!doctype html><title>Jobs</title><form><label for=em>Email *</label><input id=em type=email required>"
                 "<button type=submit>Apply</button></form>", encoding="utf-8")
    r = run(page, p.as_uri())
    assert r["status"] == "no_form", r


def test_qualification_attestation_is_not_ticked(page, tmp_path):
    r = run(page, form(tmp_path, '<label><input type=checkbox required> I confirm I have 5+ years of hands-on Snowflake experience</label>'))
    assert r["status"] == "needs_you", r


def test_foreign_work_authorization_answered_truthfully_no(page, tmp_path):
    r = run(page, form(tmp_path, '<fieldset><legend>Are you legally authorized to work in the United States? *</legend>'
                                 '<label><input type=radio name=w required> Yes</label><label><input type=radio name=w> No</label></fieldset>'), dry=True)
    assert r["status"] == "dry_run_ok", r
    assert page.locator("input[name=w]").nth(1).is_checked()  # 'No': India work permit only


def test_india_work_authorization_still_answered(page, tmp_path):
    r = run(page, form(tmp_path, '<fieldset><legend>Are you legally authorized to work in India? *</legend>'
                                 '<label><input type=radio name=w required> Yes</label><label><input type=radio name=w> No</label></fieldset>'), dry=True)
    assert r["status"] == "dry_run_ok", r
    assert page.locator("input[name=w]").first.is_checked()


def test_date_field_gets_a_date_not_yes(page, tmp_path):
    r = run(page, form(tmp_path, '<label for=sd>Start date *</label><input id=sd type=date required>'), dry=True)
    assert r["status"] == "dry_run_ok", r
    assert re.match(r"\d{4}-\d{2}-\d{2}", page.input_value("#sd"))


def test_resume_goes_only_into_resume_upload(page, tmp_path):
    body = '<label for=cl>Cover letter</label><input id=cl type=file><label for=ph>Profile photo</label><input id=ph type=file>'
    r = run(page, form(tmp_path, body), dry=True)
    assert r["status"] == "dry_run_ok", r
    assert page.eval_on_selector("#cv", "e => e.files.length") == 1
    assert page.eval_on_selector("#cl", "e => e.files.length") == 0 and page.eval_on_selector("#ph", "e => e.files.length") == 0


def test_saved_answers_are_used_and_motivational_ones_are_per_job(page, tmp_path, monkeypatch):
    base = dict(am.profile.answers())
    saved = {**base, "what_is_your_favourite_colour": "Blue", "why_do_you_want_to_join_our_company@job_citi": "Because of Citi's data platform"}
    monkeypatch.setattr(am.profile, "answers", lambda: saved)
    r = run(page, form(tmp_path, '<label for=c>What is your favourite colour? *</label><input id=c required>'), dry=True)
    assert r["status"] == "dry_run_ok" and page.input_value("#c") == "Blue", r
    why = '<label for=w>Why do you want to join our company? *</label><textarea id=w required></textarea>'
    ok = run(page, form(tmp_path, why, "why1"), dry=True, job="job_citi")
    assert ok["status"] == "dry_run_ok" and "Citi" in page.input_value("#w"), ok
    other = run(page, form(tmp_path, why, "why2"), dry=True, job="job_hsbc")  # a different job must NOT reuse a company's answer
    assert other["status"] == "needs_you", other
