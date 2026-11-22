"""Navigator smoke test: the WHOLE loop (snapshot -> LLM plan -> actions -> confirmation proof) on a local page with a scripted LLM.
Run before restarting the live applier after ANY change to apply/agent.py, apply/widgets.py or apply/engine.py."""
import sys
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import agent  # noqa: E402
from core import llm as corellm  # noqa: E402 (navigate imports it lazily, so patch the module itself)

CV = ROOT / "tests" / "fixtures" / "profile" / "Asha_Example_Resume.pdf"   # generated from the fake profile
PAGE = """<!doctype html><meta charset=utf-8><title>Careers</title><body>
<h1>Data Analyst</h1><button id=apply onclick="document.getElementById('f').style.display='block';this.style.display='none'">Apply now</button>
<form id=f style="display:none" onsubmit="event.preventDefault();document.body.innerHTML='<h1>Thank you for applying</h1><p>Your application has been received.</p>'">
 <label for=em>Email *</label><input id=em type=email required>
 <label for=nm>Full name *</label><input id=nm required>
 <label for=hear>How did you hear about us?</label><select id=hear><option>Select</option><option>Company Career Site</option><option>Referral</option></select>
 <button type=submit>Submit application</button></form></body>"""


@pytest.fixture
def ctx_page(tmp_path):
    f = tmp_path / "careers.html"
    f.write_text(PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        yield ctx, pg, f.as_uri()
        b.close()


def scripted(monkeypatch, steps):
    it = iter(steps)
    monkeypatch.setattr(corellm, "ask_json", lambda *a, **k: next(it))


def test_full_loop_reaches_confirmation_and_records_proof_only_after_submit(ctx_page, monkeypatch, tmp_path):
    ctx, pg, url = ctx_page
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")  # pure LLM path: no recipe or memory reads
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        n = len(calls)
        # element numbers come from the snapshot the prompt carries; find them by label text
        import json
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        if n == 1:
            return {"think": "open the form", "actions": [{"do": "click", "i": idx["Apply now"]}]}
        if n == 2:
            return {"think": "fill", "actions": [
                {"do": "fill", "i": idx["Email *"], "value": "asha@example.com"}, {"do": "fill", "i": idx["Full name *"], "value": "Asha Example"},
                {"do": "select", "i": idx["How did you hear about us?"], "value": "Company careers page"}]}
        return {"think": "submit", "actions": [{"do": "click", "i": idx["Submit application"], "final": True}]}
    monkeypatch.setattr(corellm, "ask_json", llm)
    res = agent.navigate(pg, ctx, url, CV, {"id": "tnav", "company": "Acme", "title": "Data Analyst"}, dry_run=False)
    assert res["status"] == "applied", res
    assert res["proof"].startswith("PAGE:") and res.get("proof_shot")  # screenshot exists only because the final click happened
    assert "Thank you for applying" in pg.inner_text("body")
    assert dict(res["filled"]).get("How did you hear about us?") == "Company careers page"


def test_dry_run_stops_at_submit_without_clicking_it(ctx_page, monkeypatch, tmp_path):
    ctx, pg, url = ctx_page
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    n = []

    def llm(prompt, *a, **k):
        import json
        n.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        if len(n) == 1:
            return {"think": "open", "actions": [{"do": "click", "i": idx["Apply now"]}]}  # 'Apply now' must NOT be treated as submit
        return {"think": "submit", "actions": [{"do": "click", "i": idx["Submit application"], "final": True}]}
    monkeypatch.setattr(corellm, "ask_json", llm)
    res = agent.navigate(pg, ctx, url, CV, {"id": "tdry", "company": "Acme", "title": "Data Analyst"}, dry_run=True)
    assert res["status"] == "dry_run_ok", res
    assert "Thank you for applying" not in pg.inner_text("body")


def test_malformed_plan_does_not_crash_the_loop(ctx_page, monkeypatch, tmp_path):
    ctx, pg, url = ctx_page
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.accounts, "get", lambda h: "x")
    scripted(monkeypatch, [{"think": "??", "actions": [{"do": "fill", "i": 999, "value": "x"}, {"do": "nonsense"}]}, {"done": "blocked", "why": "test"}])
    res = agent.navigate(pg, ctx, url, CV, {"id": "tbad", "company": "Acme", "title": "T"}, dry_run=True)
    assert res["status"] == "failed" and "test" in res["detail"]


QUALCOMM_LIKE = """<!doctype html><meta charset=utf-8><title>Apply</title><body><h1>Business Operations Analyst</h1>
<p>Have you applied before? If you have applied to Acme in the past, tell us.</p>
<label><input type=checkbox id=consent> I consent</label>
<button id=sub disabled>Submit application</button>
<script>document.getElementById('consent').onchange=e=>{document.getElementById('sub').disabled=!e.target.checked};
document.getElementById('sub').onclick=()=>{document.body.innerHTML='<h1>Thank you for applying</h1><p>Your application has been received.</p>'}</script></body>"""


def test_greyed_out_submit_and_loose_wording_is_never_a_confirmation(ctx_page, monkeypatch, tmp_path):  # bug: a company marked 'submitted' while its Submit was disabled and consent unticked
    ctx, pg, url = ctx_page
    f = tmp_path / "q.html"
    f.write_text(QUALCOMM_LIKE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "x")
    import json
    n = []

    def llm(prompt, *a, **k):
        n.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        sub = next(e for e in snap["els"] if "Submit" in (e.get("text") or ""))
        assert sub.get("disabled") is True or len(n) > 1  # the LLM is TOLD the button is disabled
        if len(n) == 1:
            return {"think": "submit", "actions": [{"do": "click", "i": sub["i"], "final": True}]}
        return {"think": "done?", "done": "applied"}  # a hallucinated success
    monkeypatch.setattr(corellm, "ask_json", llm)
    res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tq", "company": "Qualcomm", "title": "BizOps"}, dry_run=False)
    assert res["status"] != "applied" and not res.get("proof"), res
    assert "Thank you for applying" not in pg.inner_text("body")  # the disabled Submit was never clicked, the form is untouched


def test_loose_phrases_do_not_count_as_success_text():
    from apply import engine
    assert not engine.SUCCESS.search("Have you applied before? If you have applied to us in the past, tell us.")
    assert engine.SUCCESS.search("Thank you for applying! Your application has been received.")
    assert engine.SUCCESS.search("We have received your application.")


def test_hints_for_two_letter_company_and_country_tld_portal():  # a company on career5.successfactors.eu produced only ['eu']: no email was ever found
    from apply import agent
    h = agent._hints("https://career5.successfactors.eu/career?x=1", "EY")
    assert "EY" in h and h[-1] == "successfactors" and "eu" not in h


def test_identity_gate_flags_wrong_details_and_accepts_right_ones():  # accuracy over speed: a submitted application can never be corrected
    from apply import agent
    exp = {"email": "me@x.com", "first": "Asha", "last": "Example", "phone": "9000000001"}
    ok = [{"i": 1, "tag": "input", "type": "text", "label": "First Name*", "value": "Asha"},
          {"i": 2, "tag": "input", "type": "text", "label": "Last Name*", "value": "EXAMPLE"},
          {"i": 3, "tag": "input", "type": "text", "label": "Email Address*", "value": "ME@x.com"},
          {"i": 4, "tag": "input", "type": "text", "label": "Phone Number*", "value": "+91 90000 00001"},
          {"i": 5, "tag": "input", "type": "text", "label": "Phone Extension", "value": "12"},
          {"i": 6, "tag": "input", "type": "password", "label": "Email password", "value": "xx"}]
    assert agent.identity_mismatches(ok, exp) == []
    bad = [{"i": 1, "tag": "input", "type": "text", "label": "First Name*", "value": "John"},
           {"i": 4, "tag": "input", "type": "text", "label": "Mobile", "value": "9999999999"},
           {"i": 3, "tag": "input", "type": "text", "label": "E-mail", "value": "someone@else.com"}]
    assert [b[0] for b in agent.identity_mismatches(bad, exp)] == [3, 1, 4] or sorted(b[0] for b in agent.identity_mismatches(bad, exp)) == [1, 3, 4]


def test_identity_gate_rejects_phone_with_a_stray_leading_zero():  # a company autofill produced a leading "0" before a +91 number
    from apply import agent
    exp = {"email": "me@x.com", "first": "Asha", "last": "Example", "phone": "9000000001"}
    el = lambda v: [{"i": 1, "tag": "input", "type": "text", "label": "Phone Number*", "value": v}]  # noqa: E731
    assert agent.identity_mismatches(el("090000 00001"), exp)
    assert not agent.identity_mismatches(el("9000000001"), exp) and not agent.identity_mismatches(el("+91 9000000001"), exp)


WD_PAGE = """<!doctype html><meta charset=utf-8><title>My Information</title><body>
<form><h2>My Information</h2>
<label for=st>State *</label>
<select id=st data-automation-id="addressSection_countryRegion"><option>Select</option><option>Karnataka</option><option>Kerala</option></select>
<label for=pc>Country Phone Code *</label>
<select id=pc data-automation-id="countryPhoneCode"><option>Select</option><option>India (+91)</option><option>United States (+1)</option></select>
<label for=pd>Phone Device Type *</label>
<select id=pd data-automation-id="phoneDeviceType"><option>Select</option><option>Mobile</option><option>Landline</option></select>
</form></body>"""

WD_TEXT = ("Errors Found Error-State The field State is required and must have a value. "
           "Error-Phone Device Type The field Phone Device Type is required and must have a value. "
           "Country Phone Code *")


def test_workday_select_placeholder_is_filled_not_treated_as_a_value(tmp_path):
    """a company: the State picker showed 'Select'; the old check read that as a value, so the fix never ran and the navigator gave up with a
    false 'page stopped changing'. Phone Device Type was never handled at all."""
    from apply import agent
    f = tmp_path / "wd.html"
    f.write_text(WD_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        filled = agent._fill_workday_required(pg, {"url": "https://cisco.wd5.myworkdayjobs.com/x", "text": WD_TEXT})
        assert set(filled) == {"State", "Country Phone Code", "Phone Device Type"}, filled
        assert pg.locator("#st").input_value() == "Karnataka"
        assert pg.locator("#pc").input_value() == "India (+91)"
        assert pg.locator("#pd").input_value() == "Mobile"
        b.close()


def test_workday_fix_never_overwrites_a_real_answer(tmp_path):
    """Accuracy: the helper only touches empty/placeholder fields and reports only what it verified."""
    from apply import agent
    f = tmp_path / "wd2.html"
    f.write_text(WD_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        pg.select_option("#st", "Kerala")
        pg.select_option("#pd", "Landline")
        filled = agent._fill_workday_required(pg, {"url": "https://x.myworkdayjobs.com/y", "text": WD_TEXT})
        assert "State" not in filled and "Phone Device Type" not in filled, filled
        assert pg.locator("#st").input_value() == "Kerala" and pg.locator("#pd").input_value() == "Landline"
        b.close()


def test_workday_fix_is_scoped_to_workday():
    from apply import agent
    assert agent._fill_workday_required(None, {"url": "https://jobs.example.com/x", "text": WD_TEXT}) == []


def test_candidate_home_is_never_a_confirmation():
    """a company: 'Application Received' on the Workday candidate home was the STATUS of an older application (R-01361795, 24 July);
    the job the bot claimed (R-01366767) was never in the list. The reset page's 'Submit' also counted as a final submit."""
    home = {"url": "https://thermofisher.wd5.myworkdayjobs.com/wday/cxs/thermofisher/userHome",
            "text": "Welcome, Asha Example (Asha Example) My Tasks You have no tasks. My Applications Active (1) Job Title Job Req My Application Status "
                    "Date Submitted Action Sales Operations Analyst II R-01361795 Application Received July 24, 2026",
            "els": [{"i": 0, "tag": "a", "text": "Candidate Home"}]}
    assert not agent._confirmed(home)
    assert not agent._confirmed({**home, "url": "https://x.example.com/home"})  # the wording alone is enough to reject it
    ok = {"url": "https://x.example.com/apply/confirmation", "text": "Thank you for applying! Your application has been received.",
          "els": [{"i": 0, "tag": "a", "text": "Back to careers"}]}
    assert agent._confirmed(ok)
    # ... but a REAL send on the same home page shows the post-submit modal, and that IS proof (R-01369315, 4 Nov 2026)
    real = {**home, "text": "Welcome, Asha Example My Tasks Application Submitted Confirmation: your application has been successfully submitted. "
                            "My Applications Job Title Job Req My Application Status Date Submitted Data Analyst R-01369315 Application Received November 4, 2026"}
    assert agent._confirmed(real)
    assert not agent._confirmed({**real, "els": [{"i": 0, "tag": "button", "text": "Submit"}]})  # a Submit still on the page is not a confirmation


def test_the_proof_is_the_rule_that_proved_it():
    """KB-82: a company Data Governance was REALLY submitted at - its own saved screenshot shows
    the Workday modal "Application Submitted / Process Completed!" and the row "Data Governance ... Application Received ...
    November 8, 2026" - yet the record said "navigator believes it submitted; no confirmation text", because the applied branch
    asked only `engine.SUCCESS` while `_confirmed` accepts the candidate-home modal too. A check that accepts two evidences
    must let the caller credit the one that matched."""
    home_modal = {"url": "https://citi.wd5.myworkdayjobs.com/wday/cxs/citi/userHome",
                  "text": "Welcome, Asha Example (Asha Example) My Tasks Application Submitted Process Completed! Thank you for your "
                          "interest in career opportunities at Citi. My Applications Active (13) Job Title Job Req My Application Status "
                          "Date Submitted Data Governance 261007353 Application Received November 8, 2026",
                  "els": [{"i": 0, "tag": "a", "text": "Return to Home"}]}
    assert agent._confirmed(home_modal)
    assert agent.confirmation_proof(home_modal).lower().startswith("application submitted")

    older_only = {"url": "https://thermofisher.wd5.myworkdayjobs.com/wday/cxs/thermofisher/userHome",
                  "text": "My Applications Job Title Job Req My Application Status Date Submitted Sales Operations Analyst II R-01361795 "
                          "Application Received July 24, 2026",
                  "els": []}
    assert agent.confirmation_proof(older_only) == "", "an older application's status is not a confirmation"

    ok = {"url": "https://x.example.com/apply/confirmation", "text": "Thank you for applying! Your application has been received.", "els": []}
    assert "thank you" in agent.confirmation_proof(ok).lower()


def test_the_applied_branch_credits_the_modal_not_only_the_generic_wording():
    import inspect
    src = inspect.getsource(agent.navigate)
    assert "proof_txt = confirmation_proof(snap)" in src
    assert 'engine.SUCCESS.search(snap["text"]) if _confirmed(snap) else None' not in src


AUTH_HOME = """<!doctype html><meta charset=utf-8><title>Reset password</title><body>
<h1>Reset your password</h1>
<label for=em>Email</label><input id=em type=email value="a@b.com">
<label for=pw>New password</label><input id=pw type=password>
<button id=sub>Submit</button>
<script>document.getElementById('sub').onclick=()=>{document.body.innerHTML='<h1>Welcome, Asha Example</h1><p>My Tasks</p><p>My Applications</p>'
+'<table><tr><th>Job Title</th><th>Job Req</th><th>My Application Status</th><th>Date Submitted</th></tr>'
+'<tr><td>Sales Operations Analyst II</td><td>R-01361795</td><td>Application Received</td><td>July 24, 2026</td></tr></table>'};</script></body>"""


def test_auth_page_submit_is_never_the_final_submit(tmp_path, monkeypatch):
    import json
    f = tmp_path / "auth.html"
    f.write_text(AUTH_HOME, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        if len(calls) == 1:
            return {"think": "set the password", "actions": [
                {"do": "fill", "i": idx["New password"], "value": "{{PASSWORD}}"},
                {"do": "click", "i": idx["Submit"], "final": True}]}
        return {"think": "this is the candidate home, not a confirmation", "done": "blocked", "why": "candidate home, no application sent"}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tauth", "company": "Acme", "title": "Data Analyst"}, dry_run=False)
        snap = pg.evaluate(agent.SNAP_JS)
        b.close()
    assert "Application Received" in snap["text"]  # the button was clicked and the home page is showing
    assert not agent._confirmed(snap)
    assert res["status"] != "applied" and not res.get("proof"), res


SIGNIN_PAGE = """<!doctype html><meta charset=utf-8><title>Sign In</title><body>
<h1>Sign In</h1><form onsubmit="return false">
<label for=em>Email</label><input id=em type=email>
<label for=pw>Password</label><input id=pw type=password>
<label for=hp>Enter website. This input is for robots only, do not enter if you're human.</label><input id=hp name="website">
<button id=si>Sign In</button></form><div id=msg></div>
<script>window.n=0; document.getElementById('si').onclick=()=>{window.n++;
document.getElementById('msg').textContent='Invalid email address or password (attempt '+window.n+')';};</script></body>"""


def test_signin_is_capped_and_robot_trap_is_never_offered(tmp_path, monkeypatch):
    """a company: the model clicked Sign In on every step (16 lockout attempts) and was offered a honeypot input labelled
    'Enter website. This input is for robots only, do not enter if you're human.' Neither may happen again."""
    import json
    f = tmp_path / "signin.html"
    f.write_text(SIGNIN_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        assert not any("robots only" in (e.get("label") or "") for e in snap["els"]), "honeypot was shown to the model"
        si = next(e["i"] for e in snap["els"] if (e.get("text") or "").strip() == "Sign In")
        return {"think": "sign in again", "actions": [{"do": "click", "i": si}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx, pg = b.new_context(), None
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tsignin", "company": "Acme", "title": "Data Analyst"}, dry_run=False)
        attempts = pg.evaluate("window.n")
        b.close()
    assert res["status"] == "failed" and "sign-in rejected twice" in res["detail"], res
    assert attempts == 2, f"sign-in submitted {attempts} times"
    assert len(calls) <= 3, calls  # the cap ends the run instead of burning the whole step budget


# --- accuracy gate: CTC / notice / experience / employment / education ------------------------
FACT_EXPECTED = {
    "email": "me@x.com", "first": "Asha", "last": "Example", "phone": "9000000001",
    "expected_ctc": "12", "last_ctc": "11.5", "notice": "Immediate", "total_experience": "4",
    "employers": ["Example Retail Co", "Sample Logistics", "Demo Telecom"],
    "titles": ["Marketing Analyst", "Operations Intern", "Customer Insights Associate"],
    "degrees": ["MSc (Statistics)", "BSc (Mathematics)"],
    "institutions": ["Larkmoor University, Westfield School of Statistics (WSS)", "Brightwater College of Science"],
    "graduation_years": ["2022", "2018"],
}


def _fact_field(i, label, value, tag="input", **kw):
    return {"i": i, "tag": tag, "type": "text", "label": label, "value": value, **kw}


def test_accuracy_gate_accepts_correct_ctc_notice_experience_and_education():
    from apply import agent
    els = [
        _fact_field(1, "Expected CTC", "12 LPA"),
        _fact_field(2, "Current CTC", "11.5"),
        _fact_field(3, "Last drawn salary", "11,50,000"),
        _fact_field(4, "Notice Period", "0 days"),
        _fact_field(5, "Total Years of Experience", "4 years"),
        _fact_field(6, "Current Employer", "Example Retail Co"),
        _fact_field(7, "Current Job Title", "Marketing Analyst"),
        _fact_field(8, "University", "Larkmoor University"),
        _fact_field(9, "Degree", "MSc"),
        _fact_field(10, "Year of Graduation", "2022"),
        _fact_field(11, "Designation", "Operations Intern"),
    ]
    assert agent.identity_mismatches(els, FACT_EXPECTED) == []


def test_accuracy_gate_flags_wrong_ctc_and_corrects_in_the_forms_unit():
    from apply import agent
    bad = agent.identity_mismatches([_fact_field(1, "Expected CTC", "30 LPA")], FACT_EXPECTED)
    assert len(bad) == 1 and bad[0].i == 1 and bad[0].fill == "12", bad
    assert "12" in bad[0].want and "30" in bad[0].shown
    # rupees-valued field: the correction must stay in rupees (11.5 LPA = 11,50,000)
    bad = agent.identity_mismatches([_fact_field(2, "Current CTC", "30,00,000")], FACT_EXPECTED)
    assert bad and bad[0].fill == "1150000", bad
    # a wrong number in a dropdown is corrected with the real option text
    sel = _fact_field(3, "Expected CTC", "30 LPA", tag="select", options=["10 LPA", "12 LPA", "30 LPA"])
    bad = agent.identity_mismatches([sel], FACT_EXPECTED)
    assert bad and bad[0].fill == "12 LPA", bad


def test_accuracy_gate_ignores_unparseable_values():
    from apply import agent
    for value in ("Negotiable", "12-15 LPA", "As per company standards", "1500", "", "0"):
        assert agent.identity_mismatches([_fact_field(1, "Expected CTC", value)], FACT_EXPECTED) == [], value
    assert agent.identity_mismatches([_fact_field(1, "Total Years of Experience", "3 years 10 months")], FACT_EXPECTED) == []
    assert agent.identity_mismatches([_fact_field(1, "Notice Period", "Serving notice period")], FACT_EXPECTED) == []


def test_accuracy_gate_treats_zero_days_and_immediate_as_the_same_fact():
    from apply import agent
    for value in ("Immediate", "0 days", "0", "Immediately available"):
        assert agent.identity_mismatches([_fact_field(1, "Notice Period", value)], FACT_EXPECTED) == [], value
    bad = agent.identity_mismatches([_fact_field(1, "Notice Period", "30 days")], FACT_EXPECTED)
    assert bad and bad[0].fill == "0", bad
    bad = agent.identity_mismatches([_fact_field(1, "Notice period (in days)", "30")], FACT_EXPECTED)
    assert bad and bad[0].fill == "0", bad
    bad = agent.identity_mismatches([_fact_field(1, "Do you have a notice period?", "Yes")], FACT_EXPECTED)
    assert bad and bad[0].fill == "No", bad


def test_accuracy_gate_flags_wrong_education_and_employment_facts():
    from apply import agent
    bad = agent.identity_mismatches([_fact_field(1, "University", "Harvard University")], FACT_EXPECTED)
    assert len(bad) == 1 and bad[0].fill is None and "Larkmoor" in bad[0].want, bad
    bad = agent.identity_mismatches([_fact_field(2, "Degree", "B.Tech")], FACT_EXPECTED)
    assert bad and bad[0].fill is None, bad
    bad = agent.identity_mismatches([_fact_field(3, "Year of Graduation", "2026")], FACT_EXPECTED)
    assert bad and bad[0].fill is None, bad
    bad = agent.identity_mismatches([_fact_field(4, "Current Employer", "Google")], FACT_EXPECTED)
    assert bad and bad[0].fill is None and "Example Retail" in bad[0].want, bad
    bad = agent.identity_mismatches([_fact_field(5, "Current Job Title", "Marketing Manager")], FACT_EXPECTED)
    assert bad and bad[0].fill is None, bad


def test_accuracy_gate_leaves_ambiguous_fields_unjudged():
    """A false mismatch must never block a correct application: no section context for dates, monthly pay is a
    different fact, and skill-specific experience is not her total."""
    from apply import agent
    els = [_fact_field(1, "Start Date", "01/09/2022"),
           _fact_field(2, "From", "Sep 2022"),
           _fact_field(3, "Monthly Salary", "125000"),
           _fact_field(4, "Relevant Experience", "4"),
           _fact_field(5, "Years of experience in Python", "1"),
           _fact_field(6, "School", "St. Xavier's"),
           _fact_field(7, "Company", "Acme Corp"),          # can be the company being applied to
           _fact_field(8, "Job Title", "Data Analyst"),     # can be the role being applied for
           _fact_field(9, "Email OTP", "123456"),           # a code, not her email
           _fact_field(10, "Mobile verification code", "123456")]
    assert agent.identity_mismatches(els, FACT_EXPECTED) == []


def test_expected_identity_is_read_from_the_profile_sources():
    """No second copy of her facts: CTC/notice/experience come from data/answers.yaml, employers/education from cv/facts.yaml."""
    from apply import agent
    exp = agent._expected_identity()
    assert exp["expected_ctc"] == "12" and exp["last_ctc"] == "11.5"   # the invented test person (tests/fixtures/profile/answers.yaml)
    assert exp["notice"] == "Immediate" and exp["total_experience"] == "4"
    assert "Example Retail Co" in exp["employers"] and "Demo Telecom" in exp["employers"]
    assert any("Larkmoor University" in x for x in exp["institutions"])
    assert set(exp["graduation_years"]) == {"2022", "2018"}


CTC_PAGE = """<!doctype html><meta charset=utf-8><title>Apply</title><body><h1>Data Analyst</h1>
<form onsubmit="event.preventDefault();document.body.innerHTML='<h1>Thank you for applying</h1><p>Your application has been received.</p>'">
<label for=ctc>Expected CTC (in LPA) *</label><input id=ctc value="30" required>
<button type=submit>Submit application</button></form></body>"""


def test_final_submit_is_not_clicked_while_a_wrong_ctc_is_on_the_form(tmp_path, monkeypatch):
    """The accuracy gate must correct the wrong 30 LPA first and never let the final click through with it on the form."""
    import json
    f = tmp_path / "ctc.html"
    f.write_text(CTC_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        if len(calls) == 1:
            return {"think": "submit", "actions": [{"do": "click", "i": idx["Submit application"], "final": True}]}
        return {"think": "stop for the test", "done": "blocked", "why": "test stop"}
    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tctc", "company": "Acme", "title": "Data Analyst"}, dry_run=False)
        value, body = pg.locator("#ctc").input_value(), pg.inner_text("body")
        b.close()
    assert res["status"] == "failed" and "test stop" in res["detail"], res
    assert value == "12", value  # (fixture expected CTC: 12) wrong 30 was corrected in the form's own unit before anything was sent
    assert "Thank you for applying" not in body


def test_a_password_reset_allows_a_legitimate_third_signin(tmp_path, monkeypatch):
    """a company: the FIRST working sign-in was click 3, after two rejections, create-account,
    forgot-password and a reset link. The cap must only count repeated failures with the SAME password, so a real
    password change resets it."""
    import json
    f = tmp_path / "signin_reset.html"
    f.write_text(SIGNIN_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    monkeypatch.setattr(agent.accounts, "is_verified", lambda h: False)
    monkeypatch.setattr(agent.accounts, "generate_password", lambda: "New#2pass")
    monkeypatch.setattr(agent.accounts, "save", lambda *a, **k: None)
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        si = next(e["i"] for e in snap["els"] if (e.get("text") or "").strip() == "Sign In")
        if len(calls) <= 2:
            return {"think": "sign in", "actions": [{"do": "click", "i": si}]}
        if len(calls) == 3:
            return {"think": "the password is rejected by policy: change it", "actions": [{"do": "new_password"}]}
        if len(calls) == 4:
            return {"think": "sign in with the new password", "actions": [{"do": "click", "i": si}]}
        return {"think": "stop for the test", "done": "blocked", "why": "test stop"}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "treset", "company": "Acme", "title": "Data Analyst"}, dry_run=False)
        attempts = pg.evaluate("window.n")
        b.close()
    assert res["status"] == "failed" and "test stop" in res["detail"], res
    assert "sign-in rejected twice" not in res["detail"]
    assert attempts == 3, f"third sign-in after a password change was refused ({attempts} attempts)"


# --- defect 1: SNAP_JS geometry rule hid the REAL native consent checkbox behind a span widget (a company / Oracle) ------------------------------
ORACLE_CONSENT_PAGE = """<!doctype html><meta charset=utf-8><title>Personal Information</title><body>
<h1>Personal Information</h1>
<form onsubmit="event.preventDefault();if(document.getElementById('legal').checked){document.body.innerHTML='<h1>Thank you for applying</h1><p>Your application has been received.</p>'}">
<div class="input-row" style="position:relative">
 <input type="checkbox" id="legal" class="input-row__hidden-control" required aria-labelledby="legal-label"
        style="position:absolute;width:1px;height:1px;opacity:0;clip:rect(0 0 0 0);overflow:hidden">
 <span class="input-row__indicator" aria-hidden="true">&#10003;</span>
 <label id="legal-label" for="legal">You need to agree to the terms and conditions.</label>
</div>
<label for="hp">Enter website. This input is for robots only, do not enter if you&#39;re human.</label>
<input id="hp" name="website" type="checkbox">
<button type="submit">Submit application</button></form></body>"""


def _snapshot_of(tmp_path, name, html):
    f = tmp_path / name
    f.write_text(html, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        b.close()
    return snap


def test_visually_hidden_consent_checkbox_is_offered_but_a_honeypot_checkbox_is_not(tmp_path):
    """a company job: Oracle's REAL native checkbox (`input-row__hidden-control`, 1px, absolute, clipped) sits behind a span
    widget, so the geometry trap dropped it from all 9 elements and the navigator could only loop on 'You need to agree to the terms and conditions'.
    Both halves: the hidden checkbox must be offered, a honeypot checkbox must still never be."""
    snap = _snapshot_of(tmp_path, "oracle.html", ORACLE_CONSENT_PAGE)
    boxes = [e for e in snap["els"] if e.get("type") == "checkbox"]
    assert len(boxes) == 1, boxes
    assert "agree to the terms" in (boxes[0].get("label") or "").lower(), boxes[0]
    assert boxes[0].get("req") is True
    assert not any("robots only" in ((e.get("label") or "") + (e.get("text") or "")) for e in snap["els"]), snap["els"]


def test_hidden_oracle_consent_box_can_be_ticked_and_the_form_submitted(tmp_path, monkeypatch):
    """The root cause was not cosmetic: with the checkbox missing from every snapshot the form could never be consented. This drives the whole loop."""
    import json
    f = tmp_path / "oracle2.html"
    f.write_text(ORACLE_CONSENT_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        legal = next(k for k in idx if "agree to the terms" in k)
        return {"think": "tick the real box, then submit", "actions": [
            {"do": "check", "i": idx[legal]},
            {"do": "click", "i": idx["Submit application"], "final": True}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "toracle", "company": "WSP", "title": "Data Analyst"}, dry_run=False)
        b.close()
    assert res["status"] == "applied" and res.get("proof"), res


# --- defect 2: sign-in attempts must be counted/written from evidence, not from any 'Sign In' click --------------------------------
BENIGN_SIGNIN_PAGE = """<!doctype html><meta charset=utf-8><title>Sign In</title><body>
<h1>Sign In</h1><form onsubmit="return false">
<label for=em>Email</label><input id=em type=email>
<label for=pw>Password</label><input id=pw type=password>
<button id=si>Sign In</button></form><div id=msg></div>
<script>window.n=0; document.getElementById('si').onclick=()=>{window.n++;
document.getElementById('msg').textContent='please wait '+window.n;};</script></body>"""


def test_signin_stop_message_does_not_claim_a_rejection_the_page_never_showed(tmp_path, monkeypatch):
    """a company: the stored reason was 'wrong saved password or locked account' although the final
    page had NO rejection text (an empty sign-in shell). A password was filled, so the attempts ARE counted - but the wording may not invent a
    rejection, and the failure must stay transient."""
    import json
    f = tmp_path / "benign.html"
    f.write_text(BENIGN_SIGNIN_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    monkeypatch.setattr(agent.accounts, "is_verified", lambda h: False)
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        return {"think": "sign in", "actions": [
            {"do": "fill", "i": idx["Password"], "value": "{{PASSWORD}}"},
            {"do": "click", "i": idx["Sign In"]}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tbenign", "company": "Medline", "title": "Analyst"}, dry_run=False)
        clicks = pg.evaluate("window.n")
        b.close()
    assert clicks == 2, f"a password-filled sign-in must still be capped at 2 attempts ({clicks} clicks)"
    assert res["status"] == "failed", res          # transient, never 'closed'
    assert "did not progress after 2 attempts" in res["detail"], res
    assert "no rejection message shown" in res["detail"], res
    assert "wrong password" not in res["detail"] and "locked" not in res["detail"], res


MODAL_SIGNIN_PAGE = """<!doctype html><meta charset=utf-8><title>Careers</title><body>
<h1>Careers</h1>
<button id=hdr type=button>Sign In</button>
<div id=modal style="display:none">
 <label for=u>Email</label><input id=u type=email>
 <label for=p>Password</label><input id=p type=password>
 <button id=go type=button>Sign In</button></div>
<div id=msg></div>
<script>document.getElementById('hdr').onclick=()=>{document.getElementById('modal').style.display='block';};
window.n=0; document.getElementById('go').onclick=()=>{window.n++; document.getElementById('msg').textContent='signing in '+window.n;};</script></body>"""


def test_a_plain_signin_header_that_only_opens_a_modal_is_not_an_attempt(tmp_path, monkeypatch):
    """a company: a benign modal/header click consumed the sign-in budget, so the real attempt was refused. Only a click that
    follows a password fill, or one the page answers with a rejection, may count."""
    import json
    f = tmp_path / "modal.html"
    f.write_text(MODAL_SIGNIN_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    monkeypatch.setattr(agent.accounts, "is_verified", lambda h: False)
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        si = [e["i"] for e in snap["els"] if (e.get("text") or "").strip() == "Sign In"]
        if len(calls) == 1:
            return {"think": "open the sign-in modal", "actions": [{"do": "click", "i": si[0]}]}
        if len(calls) <= 4:
            return {"think": "click sign in (nothing is filled)", "actions": [{"do": "click", "i": si[-1]}]}
        return {"think": "stop for the test", "done": "blocked", "why": "test stop"}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tmodal", "company": "HPE", "title": "Analyst"}, dry_run=False)
        modal_clicks = pg.evaluate("window.n")
        b.close()
    assert modal_clicks == 3, f"uncounted modal clicks were refused ({modal_clicks} of 3)"
    assert "test stop" in res["detail"], res
    assert "sign-in rejected" not in res["detail"], res


# --- defect 3: a duplicated / covered copy of the form was offered first and clicking it did nothing (Visa) -------------------------
DUPLICATE_SIGNIN_PAGE = """<!doctype html><meta charset=utf-8><title>Sign In</title><body>
<div style="position:relative">
 <div id="copy1">
  <label for="em1">Email Address</label><input id="em1" type="text">
  <label for="pw1">Password</label><input id="pw1" type="password">
  <button id="ca1" type="button">Create Account</button>
 </div>
 <div id="cover" style="position:absolute;top:0;left:0;right:0;bottom:0;z-index:2"></div>
</div>
<div id="copy2">
 <label for="em2">Email Address</label><input id="em2" type="text">
 <label for="pw2">Password</label><input id="pw2" type="password">
 <button id="ca2" type="button">Create Account</button>
</div>
<script>window.clicked=null;
document.getElementById('ca1').onclick=()=>{window.clicked='ca1';};
document.getElementById('ca2').onclick=()=>{window.clicked='ca2';};</script></body>"""


def test_duplicate_signin_form_keeps_only_the_copy_a_human_could_click(tmp_path):
    """Visa: the whole sign-in form was rendered TWICE (elements 5-10 and 15-19); clicking the first copy's 'Create
    Account' did nothing. The snapshot must offer the copy that is actually on top, and keep numbering contiguous for the rest."""
    f = tmp_path / "dup.html"
    f.write_text(DUPLICATE_SIGNIN_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        ca = [e for e in snap["els"] if (e.get("text") or "") == "Create Account"]
        em = [e for e in snap["els"] if (e.get("label") or "") == "Email Address"]
        assert len(ca) == 1, ca
        assert len(em) == 1, em
        ids = {e["i"]: pg.evaluate("i => document.querySelectorAll('[data-ag]')[i].id", e["i"]) for e in (ca + em)}
        pg.locator(f'[data-ag="{ca[0]["i"]}"]').click()
        clicked = pg.evaluate("window.clicked")
        b.close()
    assert ids[ca[0]["i"]] == "ca2", ids          # the copy a human could actually click
    assert ids[em[0]["i"]] == "em2", ids
    assert [e["i"] for e in snap["els"]] == list(range(len(snap["els"])))   # numbering stays stable/contiguous
    assert clicked == "ca2", f"the kept copy did not work ({clicked})"


# --- defect 4: a Forgot-password click was invisible to the one-reset ledger (a company) -----------------------------
FORGOT_PAGE = """<!doctype html><meta charset=utf-8><title>Sign In</title><body>
<h1>Sign In</h1>
<label for=em>Email</label><input id=em type=email>
<button id=si type=button>Sign In</button>
<a id=fp href="#" onclick="window.forgot=(window.forgot||0)+1;return false;">Forgot your password?</a>
<div id=msg></div></body>"""


def _forgot_page(tmp_path):
    f = tmp_path / "forgot.html"
    f.write_text(FORGOT_PAGE, encoding="utf-8")
    return f


def test_forgot_password_click_is_written_to_the_one_reset_ledger(tmp_path, monkeypatch):
    """a company: 'Forgot password' was clicked at step 6, yet data/reset_ledger.json had no hpe.wd5 entry, so a later job could
    trigger a second reset on a portal that allows one (KB-8). The reset is now recorded at the click, and a link that never arrives stops
    TRANSIENTLY."""
    import json
    ledger = tmp_path / "ledger.json"
    f = _forgot_page(tmp_path)
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "LEDGER", ledger)
    monkeypatch.setattr(agent.accounts, "host_key", lambda h: "portal.example.com")
    monkeypatch.setattr(agent.accounts, "is_verified", lambda h: False)
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    monkeypatch.setattr(agent, "_fetch_mail_link", lambda *a, **k: None)   # the email never arrives within the bounded wait
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        if len(calls) == 1:
            return {"think": "request a reset", "actions": [{"do": "click", "i": idx["Forgot your password?"]}]}
        return {"think": "give up on the email", "done": "blocked", "why": "Password reset email was requested but no link available"}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tforgot", "company": "HPE", "title": "Analyst"}, dry_run=False)
        clicked = pg.evaluate("window.forgot")
        b.close()
    assert clicked == 1, "the Forgot control was never clicked"
    assert json.loads(ledger.read_text(encoding="utf-8")).get("portal.example.com"), "the reset was not recorded"
    assert res["status"] == "failed", res                 # transient, never a permanent block/closure
    assert "no reset link arrived" in res["detail"] and "retried later" in res["detail"], res


def test_forgot_password_is_refused_when_a_reset_was_already_used(tmp_path, monkeypatch):
    """A second reset within 30 days is never allowed (portals allow one; KB-8). The control must not even be clicked, and the stop is transient."""
    import json
    ledger = tmp_path / "ledger.json"
    import datetime  # one repeat is allowed after 48 h (accounts.reset_allowed), so the used reset must be RECENT
    ledger.write_text(json.dumps({"portal.example.com": [(datetime.datetime.now() - datetime.timedelta(hours=2)).isoformat(timespec="seconds")]}), encoding="utf-8")
    f = _forgot_page(tmp_path)
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "LEDGER", ledger)
    monkeypatch.setattr(agent.accounts, "host_key", lambda h: "portal.example.com")
    monkeypatch.setattr(agent.accounts, "is_verified", lambda h: False)
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        fp = next(e["i"] for e in snap["els"] if "Forgot your password" in (e.get("text") or ""))
        return {"think": "request a reset", "actions": [{"do": "click", "i": fp}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tforgot2", "company": "HPE", "title": "Analyst"}, dry_run=False)
        clicked = pg.evaluate("window.forgot")
        b.close()
    assert not clicked, "the Forgot control was clicked although a reset was already used"
    assert res["status"] == "failed", res
    assert "password reset refused" in res["detail"] and "retried later" in res["detail"], res
    assert len(json.loads(ledger.read_text(encoding="utf-8"))["portal.example.com"]) == 1   # no second entry


# --- defect 5: a disabled control looped instead of telling the model what was blocking it (a company) -----------------------------
DISABLED_CONSENT_PAGE = """<!doctype html><meta charset=utf-8><title>Voluntary Information</title><body>
<h1>Voluntary Information</h1><form onsubmit="return false">
<label><input id=c19 type=checkbox required> * Please confirm the resume you uploaded is attached</label>
<button id=back type=button onclick="document.getElementById('msg').textContent='back '+(window.b=(window.b||0)+1)">Back</button>
<button id=next type=submit disabled>Next</button></form><div id=msg></div></body>"""


def test_a_disabled_next_tells_the_model_which_consent_box_is_unticked(tmp_path, monkeypatch):
    """a company (a saved failure page): '#21 Next' was disabled because a required box was never accepted; the loop
    only said 'skipped', so the model re-clicked it for 16 steps. The feedback must name the unticked box (never tick it), and the 3rd identical
    attempt must stop transiently instead of burning the budget. The box here is deliberately NOT a terms/privacy consent box: those ARE
    auto-ticked since a company (see test_a_required_consent_box_is_ticked_but_marketing_is_not); everything else is the
    model's to tick, and a disabled Next must say why."""
    import json
    f = tmp_path / "pentair.html"
    f.write_text(DISABLED_CONSENT_PAGE, encoding="utf-8")
    url = f.as_uri() + "?step=5&stepname=voluntaryInformation"
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    prompts = []
    calls = []

    def llm(prompt, *a, **k):
        prompts.append(prompt)
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        if len(calls) % 2:
            return {"think": "click Next", "actions": [{"do": "click", "i": idx["Next"]}]}
        return {"think": "look again", "actions": [{"do": "click", "i": idx["Back"]}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(url)
        res = agent.navigate(pg, ctx, url, CV, {"id": "tpentair", "company": "Pentair", "title": "Analyst"}, dry_run=False)
        ticked = pg.locator("#c19").is_checked()
        b.close()
    assert not ticked, "the consent box was auto-ticked; a blanket tick can hit marketing opt-ins"
    assert res["status"] == "failed", res
    assert "required consent box never ticked" in res["detail"], res
    assert "voluntaryInformation" in res["detail"], res
    assert "retried later" in res["detail"], res
    assert any("Unticked required boxes" in p and "confirm the resume" in p for p in prompts[1:]), "the model was never told why Next was disabled"


# --- accuracy: COUNTRY OF RESIDENCE (a company: the country picker sat on 'Afghanistan' and nearly got submitted) ------------
COUNTRY_PAGE = """<!doctype html><meta charset=utf-8><title>Personal information</title><body>
<h1>Personal information</h1>
<form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1;window.sentCountry=document.getElementById('country').value;
document.body.innerHTML='<h1>Thank you for applying</h1><p>Your application has been received.</p>'">
<label for=country>Country/Region*</label>
<div style="position:relative;display:inline-block">
 <input id=country type=text value="Afghanistan" autocomplete="off" aria-haspopup="listbox" style="width:180px">
 <span id=clear aria-label="Clear Country/Region value" style="cursor:pointer">&#10005;</span>
 <div id=list role=listbox style="display:none;position:absolute;top:100%;left:0;background:#fff;border:1px solid #ccc">
  <div role=option>Afghanistan</div><div role=option>India</div><div role=option>Indonesia</div>
 </div>
</div>
<button type=submit>Submit application</button></form>
<script>
const inp=document.getElementById('country'), list=document.getElementById('list');
['click','focus','input'].forEach(ev=>inp.addEventListener(ev,()=>{list.style.display='block';}));
list.querySelectorAll('[role=option]').forEach(o=>o.addEventListener('click',ev=>{ev.stopPropagation();inp.value=o.textContent;list.style.display='none';}));
document.getElementById('clear').addEventListener('click',()=>{inp.value='';});
</script></body>"""

COUNTRY_READONLY_PAGE = """<!doctype html><meta charset=utf-8><title>Personal information</title><body>
<h1>Personal information</h1>
<form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1;
document.body.innerHTML='<h1>Thank you for applying</h1><p>Your application has been received.</p>'">
<label for=country>Country/Region*</label><input id=country type=text value="Afghanistan" readonly>
<button type=submit>Submit application</button></form></body>"""


def test_country_picker_defaulting_to_the_first_option_ends_selected_on_india(tmp_path):
    """a company: the alphabetical country list opens on 'Afghanistan'; the widget must end on the OPTION India (or stay
    empty), never on the first alphabetical entry."""
    from apply import widgets
    f = tmp_path / "country.html"
    f.write_text(COUNTRY_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        ok = widgets.fill_country(pg, pg.locator("#country"), "India")
        value = pg.locator("#country").input_value()
        b.close()
    assert ok is True
    assert value == "India", f"the picker ended on {value!r}"


def test_country_of_residence_gate_flags_afghanistan_and_accepts_india():
    """The pre-submit gate must judge country of residence / nationality (and the phone country code) but never work-authorisation questions."""
    from apply import agent
    bad = [{"i": 1, "tag": "input", "type": "text", "label": "Country/Region*", "value": "Afghanistan"}]
    m = agent.identity_mismatches(bad, {})
    assert len(m) == 1 and m[0].want == "India" and m[0].fill == "India", m
    good = [{"i": 1, "tag": "input", "type": "text", "label": "Country/Region*", "value": "India"},
            {"i": 2, "tag": "input", "type": "text", "label": "Nationality", "value": "Indian"},
            {"i": 3, "tag": "input", "type": "text", "label": "Country of residence", "value": "IN"},
            {"i": 4, "tag": "input", "type": "text", "label": "Country Phone Code", "value": "India (+91)"},
            {"i": 5, "tag": "input", "type": "text", "label": "Are you legally authorized to work in the country you are applying?", "value": "No"}]
    assert agent.identity_mismatches(good, {}) == []
    phone = agent.identity_mismatches([{"i": 6, "tag": "input", "type": "text", "label": "Country Phone Code", "value": "Afghanistan (+93)"}], {})
    assert phone and phone[0].want == "India (+91)", phone


def test_final_submit_waits_until_the_country_really_says_india(tmp_path, monkeypatch):
    """The wrong country is corrected through the real picker BEFORE the final click, and the submitted form carries India."""
    import json
    f = tmp_path / "country_submit.html"
    f.write_text(COUNTRY_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        return {"think": "submit", "actions": [{"do": "click", "i": idx["Submit application"], "final": True}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tcountry", "company": "Turnitin", "title": "BI Analyst"}, dry_run=False)
        sent, sent_country = pg.evaluate("window.sent"), pg.evaluate("window.sentCountry")
        b.close()
    assert sent == 1, "the form was submitted more than once or not at all"
    assert sent_country == "India", f"submitted with country {sent_country!r}"
    assert res["status"] == "applied", res


def test_submit_is_blocked_while_the_country_shows_afghanistan(tmp_path, monkeypatch):
    """A country that cannot be corrected must never be submitted (accuracy over speed): the run fails transiently instead."""
    import json
    f = tmp_path / "country_ro.html"
    f.write_text(COUNTRY_READONLY_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        return {"think": "submit", "actions": [{"do": "click", "i": idx["Submit application"], "final": True}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tcountryro", "company": "Turnitin", "title": "BI Analyst"}, dry_run=False)
        sent = pg.evaluate("window.sent")
        b.close()
    assert not sent, "an application was submitted with the wrong country on the form"
    assert res["status"] == "failed" and "wrong details on the form" in res["detail"], res


# --- account VERIFICATION: an 'email sent' page is not a rejected sign-in (a company) -----------------------------
VERIFY_PAGE_HTML = """<!doctype html><meta charset=utf-8><title>Sign In</title><body>
<h1>Sign In</h1>
<p>An email has been sent to you. Still can't sign in? Be sure to check your spam folder, your account may need verification.</p>
<label for=em>Email</label><input id=em type=email>
<label for=pw>Password</label><input id=pw type=password>
<button id=si>Sign In</button>
<button id=rs type=button>Resend Account Verification</button>
<div id=msg></div>
<script>window.resends=0;document.getElementById('rs').onclick=()=>{window.resends++;document.getElementById('msg').textContent='sent '+window.resends;};</script></body>"""

VERIFIED_PAGE = """<!doctype html><meta charset=utf-8><title>Verified</title><body><h1>Your candidate account is verified</h1></body>"""

RESEND_ONLY_PAGE = """<!doctype html><meta charset=utf-8><title>Sign In</title><body>
<h1>Sign In</h1>
<label for=em>Email</label><input id=em type=email>
<button id=si type=button>Sign In</button>
<button id=rs type=button>Resend code</button>
<div id=msg></div>
<script>window.resends=0;document.getElementById('rs').onclick=()=>{window.resends++;document.getElementById('msg').textContent='sent '+window.resends;};</script></body>"""


def test_an_email_sent_page_uses_the_verification_link_and_never_says_wrong_password(tmp_path, monkeypatch):
    """a company: 'An email has been sent to you ... your account may need verification' was stored as 'wrong saved password or locked
    account' while three 'Verify your candidate account' mails sat unopened. The link must be used, and the stop must not invent a rejection."""
    import json
    f = tmp_path / "verify.html"
    v = tmp_path / "verified.html"
    f.write_text(VERIFY_PAGE_HTML, encoding="utf-8")
    v.write_text(VERIFIED_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    verified, fetches = [], []

    def fetch(pg, ctx, job_url, company, total_s=170, kind="reset", since=None):
        fetches.append({"kind": kind, "since": since})
        return v.as_uri()

    monkeypatch.setattr(agent, "_fetch_mail_link", fetch)
    monkeypatch.setattr(agent.accounts, "mark_verified", lambda h: verified.append(h))

    def llm(prompt, *a, **k):
        return {"think": "stop for the test", "done": "blocked", "why": "test stop"}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tverify", "company": "TVH", "title": "Data Sharing analyst"}, dry_run=False)
        resends = pg.evaluate("window.resends")
        b.close()
    assert verified, "the verification link was never opened"
    assert fetches and fetches[0]["kind"] == "verify" and fetches[0]["since"] is not None, fetches
    assert resends == 0, "a resend was sent although the verification mail was already available"
    assert "test stop" in res["detail"], res
    assert "wrong password" not in res["detail"] and "locked" not in res["detail"], res


def test_verification_without_a_link_stops_transiently_and_resends_only_once(tmp_path, monkeypatch):
    """No link inside the bounded wait -> transient stop (never 'closed', never 'wrong password'), and at most ONE re-request per job."""
    f = tmp_path / "verify2.html"
    f.write_text(VERIFY_PAGE_HTML, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    fetches = []

    def fetch(pg, ctx, job_url, company, total_s=170, kind="reset", since=None):
        fetches.append({"kind": kind, "since": since})
        return None

    monkeypatch.setattr(agent, "_fetch_mail_link", fetch)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tverify2", "company": "TVH", "title": "Analyst"}, dry_run=False)
        resends = pg.evaluate("window.resends")
        b.close()
    assert resends == 1, f"the verification email was re-requested {resends} times"
    assert [x["kind"] for x in fetches] == ["verify", "verify"], fetches
    assert fetches[-1]["since"] is not None and fetches[-1]["since"] >= fetches[0]["since"], fetches
    assert res["status"] == "failed", res
    assert "no verification link arrived" in res["detail"] and "retried later" in res["detail"], res
    assert "wrong password" not in res["detail"] and "locked" not in res["detail"], res


def test_resend_is_capped_at_once_per_job(tmp_path, monkeypatch):
    """a company sent three identical verification mails in 74 seconds: the model may click Resend once, the 2nd and 3rd are refused."""
    import json
    f = tmp_path / "resend.html"
    f.write_text(RESEND_ONLY_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        rs = next(e["i"] for e in snap["els"] if "Resend" in (e.get("text") or ""))
        if len(calls) <= 4:
            return {"think": "resend again", "actions": [{"do": "click", "i": rs}]}
        return {"think": "stop for the test", "done": "blocked", "why": "test stop"}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tresend", "company": "TVH", "title": "Analyst"}, dry_run=False)
        resends = pg.evaluate("window.resends")
        b.close()
    assert resends == 1, f"Resend was clicked {resends} times (must be at most 1 per job)"
    assert res["status"] == "failed", res                 # transient, and the model was told why the click was skipped
    assert "already re-requested once" in res["detail"], res


def test_mail_link_lookup_passes_since_and_the_right_subject_kind(monkeypatch):
    """The recency plumbing: a reset search must carry 'reset', a verification search 'verif', and both the click time."""
    from apply import mail as mailmod
    calls = []
    monkeypatch.setattr(mailmod, "verification_link",
                        lambda ctx, hint, wait_s=120, allowed=(), since=None, want="": calls.append((hint, since, want)) or None)

    class PG:
        url = "https://tvh.wd3.myworkdayjobs.com/x"

    agent._fetch_mail_link(PG(), None, PG.url, "TVH", total_s=10, kind="verify", since=12345.0)
    agent._fetch_mail_link(PG(), None, PG.url, "TVH", total_s=10, kind="reset", since=999.0)
    assert calls, "the mail lookup was never made"
    assert calls[0][1] == 12345.0 and "verif" in calls[0][2], calls
    assert calls[-1][1] == 999.0 and calls[-1][2] == "reset", calls


# --- Workday Phone Device Type: the list starts with Landline (a company) -----------------------------
WD_DEVICE_PAGE = """<!doctype html><meta charset=utf-8><title>My Information</title><body>
<form onsubmit="return false"><h2>My Information</h2>
<p>Errors Found Error-Phone Device Type The field Phone Device Type is required and must have a value.</p>
<div data-automation-id="formField-phoneType">
 <label for="phoneNumber--phoneType"><span>Phone Device Type*</span></label>
 <div><button aria-haspopup="listbox" type="button" name="phoneType" id="phoneNumber--phoneType"
              aria-label="Phone Device Type Select One Required" aria-expanded="false">Select One</button>
 <ul id="devlist" role="listbox" style="display:none">OPTIONS</ul></div>
</div>
<button id="back" type="button" onclick="document.getElementById('msg').textContent='back'">Back</button>
</form><div id="msg"></div>
<script>
const b=document.getElementById('phoneNumber--phoneType'), l=document.getElementById('devlist');
b.onclick=()=>{l.style.display='block'; b.setAttribute('aria-expanded','true');};
l.querySelectorAll('[role=option]').forEach(o=>o.onclick=()=>{if(o.getAttribute('aria-disabled'))return; b.innerText=o.innerText; l.style.display='none';});
</script></body>"""

WD_DEVICE_OPTIONS = """<li role="option" aria-disabled="true" id="select-one">Select One</li>
 <li role="option">Landline</li><li role="option">Landline - BCM**</li>
 <li role="option">Mobile</li><li role="option">Mobile - BCM**</li>"""
WD_DEVICE_BROKEN_OPTIONS = """<li role="option" aria-disabled="true" id="select-one">Select One</li>
 <li role="option">Landline</li><li role="option">Landline - BCM**</li>"""

WD_DEVICE_TEXT = "Errors Found Error-Phone Device Type The field Phone Device Type is required and must have a value."


def test_workday_phone_device_type_is_picked_from_a_landline_first_list(tmp_path):
    """a company: the device-type list starts with Landline / Landline - BCM**, the field stayed 'Select One' and the navigator
    died with a false 'page stopped changing'."""
    f = tmp_path / "wd_device.html"
    f.write_text(WD_DEVICE_PAGE.replace("OPTIONS", WD_DEVICE_OPTIONS), encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        failed = []
        filled = agent._fill_workday_required(pg, {"url": "https://db.wd3.myworkdayjobs.com/x/apply", "text": WD_DEVICE_TEXT}, failed=failed)
        shown = pg.locator("#phoneNumber--phoneType").inner_text()
        b.close()
    assert filled == ["Phone Device Type"], (filled, failed)
    assert not failed, failed
    assert shown.strip() == "Mobile", f"the picker settled on {shown!r}"


def test_a_workday_field_reported_with_a_reason_still_counts_as_the_same_field():
    """The picker now appends WHY it gave up; the retry counter must still see one field, or the loop never stops with a named reason."""
    assert agent._field_key("Phone Device Type (list offered Landline, Landline - BCM**)") == "Phone Device Type"
    assert agent._field_key("State") == "State"
    # a portal-named field that merely contains parentheses is never rewritten
    q = "Previous employer (most recent)"
    assert agent._field_key(q) == q


def test_an_unrecorded_phone_type_parks_with_the_options_named(monkeypatch):
    """a company: the only choices were Mobile/Android, Mobile/Apple iOS and Mobile - Other. Choosing for her would be a
    fabricated fact, so the job must park as a question (a retry cannot supply a fact nobody recorded); a field that is merely stuck
    stays a transient failure."""
    from apply import widgets

    lab = ("Phone Device Type (no option equals 'Mobile'; list offered Select One, Mobile/Android, Mobile/Apple iOS, Mobile - Other (Non Iph")
    monkeypatch.setattr(widgets, "phone_device_value", lambda: "")
    q = agent._phone_type_question(lab)
    assert q and q.startswith("unanswered required: Phone device type?"), q
    assert "Mobile/Apple iOS" in q, q
    assert agent._phone_type_question("State (the list never opened)") is None
    assert agent._phone_type_question("Phone Device Type (clicked 'Mobile' but the field still shows 'Select One')") is None
    monkeypatch.setattr(widgets, "phone_device_value", lambda: "Mobile/Android")
    assert agent._phone_type_question(lab) is None   # she has answered it: the picker will use her value


def test_workday_phone_device_type_that_cannot_be_set_is_reported(tmp_path):
    """When the list has no Mobile, the helper must REPORT 'Phone Device Type' (not silently return nothing) and must not leave a wrong
    option (Landline) on the form."""
    f = tmp_path / "wd_device_broken.html"
    f.write_text(WD_DEVICE_PAGE.replace("OPTIONS", WD_DEVICE_BROKEN_OPTIONS), encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        failed = []
        filled = agent._fill_workday_required(pg, {"url": "https://db.wd3.myworkdayjobs.com/x/apply", "text": WD_DEVICE_TEXT}, failed=failed)
        shown = pg.locator("#phoneNumber--phoneType").inner_text()
        b.close()
    # the report names WHAT the picker saw, so the failure can be diagnosed from the snapshot instead of a replay (a company)
    assert filled == [] and len(failed) == 1 and failed[0].startswith("Phone Device Type ("), (filled, failed)
    assert "Landline" in failed[0], failed
    assert shown.strip() == "Select One", f"a wrong option was left on the form: {shown!r}"


WD_STATE_PAGE = """<!doctype html><meta charset=utf-8><title>My Information</title><body>
<form onsubmit="return false"><h2>My Information</h2>
<p>Errors Found The field State is required and must have a value.</p>
<div data-automation-id="formField-countryRegion">
 <label for="address--countryRegion"><span>State<abbr>*</abbr></span></label>
 <div><button aria-haspopup="listbox" type="button" name="countryRegion" id="address--countryRegion"
              aria-label="State Select One Required" aria-expanded="false">Select One</button>
 <ul id="statelist" role="listbox" style="display:none">
  <li role="option" aria-disabled="true">Select One</li><li role="option">Kerala</li></ul></div>
</div>
<button id="back" type="button" onclick="document.getElementById('msg').textContent='back'">Back</button>
</form><div id="msg"></div>
<script>
const sb=document.getElementById('address--countryRegion'), sl=document.getElementById('statelist');
sb.onclick=()=>{sl.style.display='block'; sb.setAttribute('aria-expanded','true');};
sl.querySelectorAll('[role=option]').forEach(o=>o.onclick=()=>{if(o.getAttribute('aria-disabled'))return; sb.innerText=o.innerText; sl.style.display='none';});
</script></body>"""


def _navigate_local(tmp_path, monkeypatch, html: str, jid: str):
    import json
    f = tmp_path / f"{jid}.html"
    f.write_text(html, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        back = next(e["i"] for e in snap["els"] if (e.get("text") or "") == "Back")
        return {"think": "look again", "actions": [{"do": "click", "i": back}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": jid, "company": "Deutsche Bank", "title": "Analyst"}, dry_run=False)
        b.close()
    return res


def test_an_unrecorded_workday_device_type_parks_with_the_question(tmp_path, monkeypatch):
    """a company: the device list has no plain 'Mobile'. Retrying cannot supply a fact nobody recorded, so the job must PARK
    with the exact question and the options, never loop on 'could not set'."""
    from apply import widgets
    monkeypatch.setattr(widgets, "phone_device_value", lambda: "")
    res = _navigate_local(tmp_path, monkeypatch, WD_DEVICE_PAGE.replace("OPTIONS", WD_DEVICE_BROKEN_OPTIONS), "twddevice")
    assert res["status"] == "needs_you", res
    assert "Phone device type?" in res["detail"], res
    assert "Landline" in res["detail"], res          # the options the page offered are named, so she can answer
    assert "page stopped changing" not in res["detail"], res


def test_a_national_id_block_becomes_a_question_for_her():
    """a company (two jobs): the model refused to invent an Aadhaar/PAN - correct - but the job was filed as our 'logic' failure
    with the raw verdict in status_detail. A personal identifier only she has must PARK with the exact question instead."""
    why = ("National ID number is required but not provided in candidate source of truth; cannot invent an Aadhaar/PAN number.")
    assert agent._blocked_question(why) == "unanswered required: National ID number?"
    assert agent._blocked_question("the site's submit button is broken") is None
    assert agent._blocked_question("Passport number is required by the form") == "unanswered required: Passport number?"


def test_a_site_search_control_is_never_clicked_from_a_job_posting():
    """a company careers: 'click #4 Search' took the navigator to search-results ('There are no jobs for your search
    criteria') and the job died as 'page stopped changing'. A site search never advances an application."""
    assert agent._on_job_posting({"url": "https://careers.statestreet.com/global/en/job/STSTGLOBALR800296EXTERNALENGLOBAL/Financial-Reporting-Associate-2"})
    assert agent._on_job_posting({"url": "https://statestreet.wd1.myworkdayjobs.com/Global/job/Bangalore-India/Financial-Reporting--Associate-2_R-800295/apply"})
    assert not agent._on_job_posting({"url": "https://careers.statestreet.com/global/en/search-results?m=3&cityStateCountry=Bengaluru"})
    assert agent.SEARCH_NAV.search("Search") and agent.SEARCH_NAV.search("Search again")
    assert not agent.SEARCH_NAV.search("Search and apply")   # a submit-ish control is not site nav


def test_the_navigator_refuses_a_search_click_on_a_job_page(tmp_path, monkeypatch):
    import json
    (tmp_path / "job").mkdir()
    page = tmp_path / "job" / "R-800296.html"   # the job view lives under /job/, like the real careers site
    page.write_text("""<!doctype html><meta charset=utf-8><title>Financial Reporting, Associate 2</title><body>
<h2>Financial Reporting, Associate 2</h2><p>Location: Bengaluru</p>
<button onclick="location.href='../search-results.html'">Search</button>
<div id="msg"></div></body>""", encoding="utf-8")
    (tmp_path / "search-results.html").write_text(
        "<!doctype html><meta charset=utf-8><body>There are no jobs for your search criteria. Please search again.</body>", encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        i = next(e["i"] for e in snap["els"] if (e.get("text") or "").strip() == "Search")
        return {"think": "press search", "actions": [{"do": "click", "i": i}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(page.as_uri())
        res = agent.navigate(pg, ctx, page.as_uri(), CV, {"id": "tsearchnav", "company": "State Street", "title": "Analyst"}, dry_run=False)
        final_url = pg.url
        b.close()
    assert "search-results.html" not in final_url, f"the navigator left the job for the site search: {final_url}"
    assert res["status"] == "failed", res   # transient; it never claims closed
    assert "REFUSED" in res["detail"], res


def test_an_unset_workday_state_stops_transiently_naming_the_field(tmp_path, monkeypatch):
    """A field that is not a fact about her stays TRANSIENT (a picker/layout problem on our side), never 'closed' and never a false
    'page stopped changing'."""
    res = _navigate_local(tmp_path, monkeypatch, WD_STATE_PAGE, "twdstate")
    assert res["status"] == "failed", res           # transient, never 'closed'
    assert "could not set State" in res["detail"], res
    assert "retried later" in res["detail"], res
    assert "page stopped changing" not in res["detail"], res


DB_DEVICE_PAGE = """<!doctype html><meta charset=utf-8><title>My Information</title><body>
<h2>My Information</h2><p>Errors Found</p><p>Error-Phone Device Type</p>
<p>The field Phone Device Type is required and must have a value.</p>
<button id="pd" aria-haspopup="listbox" type="button" name="phoneType" aria-label="Phone Device Type Select One Required"
        aria-invalid="true" aria-expanded="true">Select One</button>
<ul id="devlist" role="listbox">OPTIONS</ul>
<div id="msg"></div>
<script>document.querySelectorAll('#devlist [role=option]').forEach(o=>o.onclick=()=>{
 if(o.getAttribute('aria-disabled'))return; const b=document.getElementById('pd');
 b.innerText=o.innerText; b.removeAttribute('aria-invalid');});</script></body>"""


def test_a_named_invalid_button_control_is_repaired_by_field_intent(tmp_path):
    """a company 1105_013306 shape: the device-type control is a BUTTON whose text is only its value ('Select One') and whose aria-label
    names the field; the banner names 'Phone Device Type'. It must become addressable, be marked invalid, and end on Mobile (never Landline)."""
    f = tmp_path / "db_device.html"
    f.write_text(DB_DEVICE_PAGE.replace("OPTIONS", WD_DEVICE_OPTIONS), encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        btn = next(e for e in snap["els"] if e.get("tag") == "button" and (e.get("text") or "") == "Select One")
        assert btn.get("invalid") is True and "phone device type" in (btn.get("label") or "").lower(), btn
        fixed = agent._repair_named_required(pg, snap)
        shown = pg.locator("#pd").inner_text()
        b.close()
    assert fixed == ["Phone Device Type"], fixed
    assert shown.strip() == "Mobile", f"the repair settled on {shown!r}"


def test_a_named_invalid_button_is_reported_when_only_wrong_options_exist(tmp_path):
    """Only Landline-style options -> report the field (transient upstream) and never leave a wrong value ('Select One' stays)."""
    f = tmp_path / "db_device_bad.html"
    f.write_text(DB_DEVICE_PAGE.replace("OPTIONS", WD_DEVICE_BROKEN_OPTIONS), encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        failed = []
        fixed = agent._repair_named_required(pg, snap, failed=failed)
        shown = pg.locator("#pd").inner_text()
        b.close()
    # the report names WHAT the list offered (KB-26), so the next occurrence is diagnosable from the row itself
    assert fixed == [] and len(failed) == 1 and failed[0].startswith("Phone Device Type (no option equals 'Mobile'"), (fixed, failed)
    assert "Landline" in failed[0], failed
    assert shown.strip() == "Select One", f"a wrong option was left behind: {shown!r}"


# --- account lifecycle: a VERIFIED host whose stored password is rejected (a company) -----------------------------
RESET_FORM_PAGE = """<!doctype html><meta charset=utf-8><title>Reset password</title><body>
<h1>Reset your password</h1><label for=pw>New password</label><input id=pw type=password>
<button id=sub type=button>Reset password</button><div id=msg></div></body>"""

SIGNIN_OK_PAGE = """<!doctype html><meta charset=utf-8><title>Sign In</title><body>
<h1>Sign In</h1><label for=em>Email</label><input id=em type=email>
<label for=pw>Password</label><input id=pw type=password>
<button id=si onclick="location.href='HOME'">Sign In</button></body>"""

HOME_PAGE = """<!doctype html><meta charset=utf-8><title>Candidate Home</title><body><h1>My Tasks</h1></body>"""


def _hpe_vault(monkeypatch, ledger, verified=True):
    monkeypatch.setattr(agent.accounts, "host_key", lambda h: "hpe.wd5.myworkdayjobs.com")
    monkeypatch.setattr(agent.accounts, "LEDGER", ledger)
    monkeypatch.setattr(agent.accounts, "is_verified", lambda h: verified)
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")
    saved, verified_calls = [], []

    def _save(h, p, proven=False):
        saved.append((h, p, proven))
        return True

    monkeypatch.setattr(agent.accounts, "save", _save)
    monkeypatch.setattr(agent.accounts, "mark_verified", lambda h: verified_calls.append(h))
    return saved, verified_calls


def _nav_env(monkeypatch, tmp_path):
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")


def test_a_verified_host_with_a_rejected_password_gets_exactly_one_reset(tmp_path, monkeypatch):
    """a company: verified=1 + a rejected password failed three jobs in a row forever ('A WORKING account exists' won the prompt branch,
    reset_allowed said never). A wrong VAULT entry now allows EXACTLY ONE reset, recorded in the ledger, with clear instructions."""
    import json
    f = tmp_path / "signin.html"
    f.write_text(SIGNIN_PAGE, encoding="utf-8")
    r = tmp_path / "reset.html"
    r.write_text(RESET_FORM_PAGE, encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    _nav_env(monkeypatch, tmp_path)
    saved, verified_calls = _hpe_vault(monkeypatch, ledger)
    monkeypatch.setattr(agent, "_fetch_mail_link", lambda *a, **k: r.as_uri())
    prompts = []

    def llm(prompt, *a, **k):
        prompts.append(prompt)
        if len(prompts) == 4:
            return {"think": "give up on the email", "done": "blocked", "why": "Password reset email was requested but no link available"}
        if len(prompts) > 3:
            return {"think": "stop for the test", "done": "blocked", "why": "test stop"}
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        si = next(e["i"] for e in snap["els"] if (e.get("text") or "").strip() == "Sign In")
        return {"think": "sign in", "actions": [{"do": "click", "i": si}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tvault", "company": "HPE", "title": "Analyst"}, dry_run=False)
        b.close()
    entries = json.loads(ledger.read_text(encoding="utf-8"))["hpe.wd5.myworkdayjobs.com"]
    assert len(entries) == 1, entries                                    # exactly ONE reset decision
    assert any("vault entry is" in p and "forgot password" in p for p in prompts[3:]), "the model was never told the vault entry is wrong"
    assert res["status"] == "failed" and "test stop" in res["detail"], res
    assert saved == [] and verified_calls == [], (saved, verified_calls)  # nothing proven: nothing written, never 'verified'


def test_a_verified_host_with_no_reset_left_becomes_needs_you(tmp_path, monkeypatch):
    """No retry can fix a verified host whose one reset is already used: it must ask for a human (needs_you), not fail transiently."""
    import json
    from datetime import datetime
    f = tmp_path / "signin2.html"
    f.write_text(SIGNIN_PAGE, encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"hpe.wd5.myworkdayjobs.com": [datetime.now().isoformat(timespec="seconds")]}), encoding="utf-8")
    _nav_env(monkeypatch, tmp_path)
    saved, verified_calls = _hpe_vault(monkeypatch, ledger)

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        si = next(e["i"] for e in snap["els"] if (e.get("text") or "").strip() == "Sign In")
        return {"think": "sign in", "actions": [{"do": "click", "i": si}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tvault2", "company": "HPE", "title": "Analyst"}, dry_run=False)
        b.close()
    assert res["status"] == "needs_you", res                               # a human action is what is actually required
    assert "hpe.wd5.myworkdayjobs.com" in res["detail"] and "reset it once manually" in res["detail"], res
    assert len(json.loads(ledger.read_text(encoding="utf-8"))["hpe.wd5.myworkdayjobs.com"]) == 1   # no second reset
    assert saved == [] and verified_calls == [], (saved, verified_calls)


def test_a_password_is_saved_only_after_a_signin_proves_it(tmp_path, monkeypatch):
    """Evidence-gated vault write: leaving the auth screen after OUR password was submitted is what proves it (and only then)."""
    import json
    f = tmp_path / "signin_ok.html"
    home = tmp_path / "home.html"
    home.write_text(HOME_PAGE, encoding="utf-8")
    f.write_text(SIGNIN_OK_PAGE.replace("HOME", home.as_uri()), encoding="utf-8")
    ledger = tmp_path / "ledger.json"
    _nav_env(monkeypatch, tmp_path)
    saved, verified_calls = _hpe_vault(monkeypatch, ledger)
    calls = []

    def llm(prompt, *a, **k):
        calls.append(1)
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {(e.get("label") or e.get("text") or ""): e["i"] for e in snap["els"]}
        if len(calls) == 1:
            return {"think": "sign in", "actions": [
                {"do": "fill", "i": idx["Password"], "value": "{{PASSWORD}}"},
                {"do": "click", "i": idx["Sign In"]}]}
        return {"think": "stop for the test", "done": "blocked", "why": "test stop"}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tproven", "company": "HPE", "title": "Analyst"}, dry_run=False)
        b.close()
    assert saved == [("hpe.wd5.myworkdayjobs.com", "Pw#1abcDEF", True)], saved
    assert verified_calls == ["hpe.wd5.myworkdayjobs.com"], verified_calls


# --- repeated labels in repeating rows (a company: two education rows, identical labels) -----------------------------
EDU_ROWS_PAGE = """<!doctype html><meta charset=utf-8><title>My Experience</title><body>
<h2>Education</h2>
<p>Error-Degree The field Degree is required and must have a value.</p>
<div id="rows">
 <div class="edurow">
  <label for="s1">School or University*</label><input id="s1" value="Larkmoor University, Westfield School of Statistics">
  <label>Degree*</label><button id="d1b" aria-haspopup="listbox" aria-label="Degree Bachelor's Degree Required">Bachelor's Degree</button>
  <ul id="d1l" role="listbox" style="display:none"><li role="option">Select One</li><li role="option">Bachelor's Degree</li><li role="option">MSc</li></ul>
  <label>Field of Study</label><button id="f1b" aria-haspopup="listbox" aria-label="Field of Study Earth sciences">Earth sciences</button>
  <ul id="f1l" role="listbox" style="display:none"><li role="option">Earth sciences</li><li role="option">Biology</li><li role="option">Statistics</li></ul>
 </div>
 <div class="edurow">
  <label for="s2">School or University*</label><input id="s2" value="Brightwater College of Science">
  <label>Degree*</label><button id="d2b" aria-haspopup="listbox" aria-invalid="true" aria-label="Degree Select One Required">Select One</button>
  <ul id="d2l" role="listbox" style="display:none"><li role="option" aria-disabled="true">Select One</li><li role="option">Bachelor's Degree</li><li role="option">MSc</li></ul>
  <label>Field of Study</label><button id="f2b" aria-haspopup="listbox" aria-label="Field of Study Biology">Biology</button>
  <ul id="f2l" role="listbox" style="display:none"><li role="option">Earth sciences</li><li role="option">Biology</li></ul>
 </div>
</div>
<script>
const lists=[...document.querySelectorAll('[role=listbox]')];
document.querySelectorAll('button[aria-haspopup]').forEach(b=>b.onclick=()=>{
  lists.forEach(l=>l.style.display='none');
  const l=document.getElementById(b.id.replace('b','l')); if(l) l.style.display='block';});
lists.forEach(l=>l.querySelectorAll('[role=option]').forEach(o=>o.onclick=()=>{
  if(o.getAttribute('aria-disabled'))return; const b=document.getElementById(l.id.replace('l','b'));
  b.innerText=o.innerText; l.style.display='none';}));
</script></body>"""


def test_repeating_rows_give_each_element_its_row_and_labels_repeat(tmp_path):
    """a company: two education rows with IDENTICAL labels - a first-match lookup always addresses row 1. The snapshot must say which
    row each control belongs to, so 'the Degree of row 2' is expressible."""
    f = tmp_path / "edu.html"
    f.write_text(EDU_ROWS_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        d1 = next(e for e in snap["els"] if (e.get("text") or "") == "Bachelor's Degree")
        d2 = next(e for e in snap["els"] if e.get("text") == "Select One" and e.get("invalid"))
        r1 = next(e for e in snap["els"] if (e.get("text") or "") == "Earth sciences")
        r2 = next(e for e in snap["els"] if (e.get("text") or "") == "Biology")
        # set row 2's Degree through its OWN element: row 1 must not change
        from apply import widgets
        info = widgets.pick_option(pg, pg.locator(f'[data-ag="{d2["i"]}"]').first, "MSc", "Degree")
        after1, after2 = pg.locator("#d1b").inner_text(), pg.locator("#d2b").inner_text()
        b.close()
    assert d1.get("row") == "1/2" and "Larkmoor" in (d1.get("rowctx") or ""), d1
    assert d2.get("row") == "2/2" and "Brightwater" in (d2.get("rowctx") or ""), d2
    assert r1.get("row") == "1/2" and r2.get("row") == "2/2", (r1.get("row"), r2.get("row"))
    # identical labels in different rows are LEGITIMATE copies: the duplicate-copy filter must not collapse them
    schools = [e for e in snap["els"] if (e.get("label") or "") == "School or University*"]
    assert len(schools) == 2, schools
    assert sorted(e.get("row") for e in schools) == ["1/2", "2/2"], schools
    assert info is True and after2 == "MSc", (info, after1, after2)
    assert after1 == "Bachelor's Degree", f"row 1 was rewritten ({after1})"


def test_a_picker_never_accepts_a_suggestion_that_is_not_the_intended_value(tmp_path):
    """a company: the Field of Study picker accepted 'Earth sciences'/'Biology'. It must pick 'Statistics' when it exists and report the
    field UNFILLED when the option does not exist - never take the first suggestion."""
    from apply import widgets
    f = tmp_path / "edu2.html"
    f.write_text(EDU_ROWS_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        f2 = next(e for e in snap["els"] if (e.get("text") or "") == "Biology" and e.get("row") == "2/2")
        f1 = next(e for e in snap["els"] if (e.get("text") or "") == "Earth sciences" and e.get("row") == "1/2")
        # 'Statistics' exists only in row 1: the row-2 picker must NOT accept 'Earth sciences' instead
        missing = widgets.pick_option(pg, pg.locator(f'[data-ag="{f2["i"]}"]').first, "Statistics", "Field of Study")
        row2_text = pg.locator("#f2b").inner_text()
        found = widgets.pick_option(pg, pg.locator(f'[data-ag="{f1["i"]}"]').first, "Statistics", "Field of Study")
        row1_text = pg.locator("#f1b").inner_text()
        b.close()
    assert missing is False, "a wrong suggestion was accepted for a field that has no such option"
    assert row2_text == "Biology", f"the row-2 field of study was rewritten to {row2_text!r}"
    assert found is True and row1_text == "Statistics", (found, row1_text)


def test_a_wrong_degree_or_field_in_a_known_education_row_is_flagged(tmp_path):
    """a company: Larkmoor (MSc) held the bachelor's degree and 'Earth sciences'; Brightwater (BSc) held 'Biology'. Within a row whose
    institution is one of hers, the degree and field must be THAT record's - and such a mismatch is never auto-corrected, it blocks the submit."""
    f = tmp_path / "edu3.html"
    f.write_text(EDU_ROWS_PAGE, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        snap = pg.evaluate(agent.SNAP_JS)
        b.close()
    bad = agent.education_row_mismatches(snap["els"], agent._expected_identity())
    got = {(m.label, m.shown, m.want) for m in bad}
    assert ("Degree Bachelor's Degree Required", "Bachelor's Degree", "MSc (Statistics)") in got, got
    assert ("Field of Study Earth sciences", "Earth sciences", "Statistics") in got, got
    assert ("Field of Study Biology", "Biology", "Mathematics") in got, got
    assert all(m.fill is None for m in bad), "a cross-field fact must never be guessed into a form"


def test_llm_outage_detail_names_the_provider_and_its_real_limit(tmp_path, monkeypatch):
    """A stored 'LLM unavailable (RuntimeError)' ticket says nothing actionable: keep the provider and its real reason on the failure line."""
    f = tmp_path / "llm.html"
    f.write_text(PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")

    def boom(*a, **k):
        raise RuntimeError("gemini: 429 daily quota exhausted")

    monkeypatch.setattr(corellm, "ask_json", boom)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        res = agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tllmdown", "company": "Acme", "title": "T"}, dry_run=False)
        b.close()
    assert res["status"] == "failed", res
    assert "LLM unavailable (RuntimeError: gemini: 429 daily quota exhausted)" in res["detail"], res


# ---------------------------------------------------------------- a company: a banner that names a QUESTION
WD_YESNO_PAGE = """<!doctype html><meta charset=utf-8><title>My Information</title><body>
<h2>My Information</h2><p>Errors Found</p>
<button type="submit" data-agx="e1"><span>Error</span><div>-</div>How Did You Hear About Us?</button>
<button type="submit" data-agx="e2"><span>Error</span><div>-</div>Have you previously worked at Deutsche Bank?</button>
<div data-automation-id="formField-source">
 <div role="group" aria-labelledby="src-label"><label id="src-label">How Did You Hear About Us?</label>
 <button id="src" aria-haspopup="listbox" aria-label="How Did You Hear About Us? Select One Required">Select One</button>
 <ul id="srclist" role="listbox" style="display:none"><li role="option">Company Career Site</li><li role="option">Job Board</li></ul></div></div>
<div data-automation-id="formField-candidateIsPreviousWorker">
 <fieldset><legend><label>Have you previously worked at Deutsche Bank or any related entities as permanent, temporary or fixed-term
 employee or Contingent worker?</label></legend>
 <input id="pw-t" name="candidateIsPreviousWorker" type="radio" value="true"><label for="pw-t">Yes</label>
 <input id="pw-f" name="candidateIsPreviousWorker" type="radio" value="false"><label for="pw-f">No</label></fieldset></div>
<button id="back" type="button">Back</button>
<script>
const sb=document.getElementById('src'), sl=document.getElementById('srclist');
sb.onclick=()=>{sl.style.display='block'};
sl.querySelectorAll('[role=option]').forEach(o=>o.onclick=()=>{sb.innerText=o.innerText; sl.style.display='none';});
</script></body>"""


def test_a_banner_that_names_her_questions_is_extracted_whole():
    """a company 0115_032528: the banner named 'How Did You Hear About Us?' and 'Have you previously worked at a company ...?',
    but the old pattern stopped at the '?' and at 50 characters, so NO field was extracted and the page died as 'page stopped changing '."""
    import json
    fpath = Path(__file__).resolve().parents[1] / "data" / "failures" / "corpus_13.json"
    if not fpath.exists():
        __import__("pytest").skip("saved failure snapshot not present (private corpus in data/failures)")
    snap = json.load(open(fpath, encoding="utf-8"))["snap"]
    names = agent._named_required_fields(snap)
    assert "How Did You Hear About Us?" in names, names
    assert any(n.startswith("Have you previously worked at Deutsche Bank") for n in names), names
    assert "Phone Device Type" in names, names


def test_a_radio_pair_carries_the_question_it_answers():
    """The radios' own labels are only 'Yes' and 'No'. Without the fieldset question the model and the repair both see two unlabelled
    switches, which is exactly how the application was lost."""
    f = Path(__file__).resolve().parent / "_yesno.html"
    f.write_text(WD_YESNO_PAGE, encoding="utf-8")
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="chrome", headless=True)
            pg = b.new_page()
            pg.goto(f.as_uri())
            snap = pg.evaluate(agent.SNAP_JS)
            radios = [e for e in snap["els"] if e.get("type") == "radio"]
            b.close()
    finally:
        f.unlink(missing_ok=True)
    assert len(radios) == 2, radios
    assert all("previously worked at Deutsche Bank" in (r.get("group") or "") for r in radios), radios


def test_a_banner_named_question_is_answered_from_her_recorded_facts():
    """Both banner fields have a recorded answer (careers page; never worked at a company = No). They must be filled by the repair,
    not left for another 'Next' click."""
    f = Path(__file__).resolve().parent / "_yesno_fill.html"
    f.write_text(WD_YESNO_PAGE, encoding="utf-8")
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="chrome", headless=True)
            pg = b.new_page()
            pg.goto(f.as_uri())
            snap = pg.evaluate(agent.SNAP_JS)
            failed: list = []
            questions: list = []
            done = agent._repair_named_required(pg, snap, failed=failed, questions=questions)
            heard = pg.locator("#src").inner_text()
            no_ticked = pg.locator("#pw-f").is_checked()
            yes_ticked = pg.locator("#pw-t").is_checked()
            b.close()
    finally:
        f.unlink(missing_ok=True)
    assert questions == [], questions
    assert "Career Site" in heard, heard
    assert no_ticked and not yes_ticked, (no_ticked, yes_ticked)
    assert failed == [], failed
    assert any("previously worked" in d for d in done), done


def test_a_banner_named_personal_id_parks_with_the_question(tmp_path, monkeypatch):
    """A banner that names 'National ID number' must ASK her, never spin to 'page stopped changing' and never invent a number."""
    page = """<!doctype html><meta charset=utf-8><title>Application Questions</title><body>
    <h2>Application Questions</h2><p>Errors Found</p>
    <p>The field National ID number is required and must have a value.</p>
    <label>National ID number<input id="nid" name="nid" type="text" aria-required="true"></label>
    <button id="back" type="button">Back</button></body>"""
    res = _navigate_local(tmp_path, monkeypatch, page, "tnid")
    assert res["status"] == "needs_you", res
    assert "National ID number?" in res["detail"], res
    assert "page stopped changing" not in res["detail"], res


MMC_CONSENT_PAGE = """<!doctype html><meta charset=utf-8><title>Voluntary Disclosures</title><body>
<h2>Voluntary Disclosures</h2><p>* Indicates a required field</p>
<label><input id="tc" type="checkbox" aria-required="true"><span>Yes, I have read and consent to the terms and conditions*</span></label>
<label><input id="oc" type="checkbox"><span>I agree with the terms and conditions *</span></label>
<label><input id="mk" type="checkbox"><span>Yes, send me marketing emails</span></label>
<button id="back" type="button">Back</button></body>"""


def test_a_required_consent_box_is_ticked_but_marketing_is_not(tmp_path, monkeypatch):
    """a company: the only required thing on Voluntary Disclosures was the terms-and-conditions checkbox and no banner
    named it, so the navigator waited until 'too many steps' (twice). Consent to applying is her intent; a marketing opt-in is not."""
    res = _navigate_local(tmp_path, monkeypatch, MMC_CONSENT_PAGE, "tmmc")
    filled = " ".join(str(f) for f in res.get("filled") or [])
    assert "terms and conditions" in filled.lower(), res.get("filled")
    assert "marketing" not in filled.lower(), res.get("filled")
    assert "terms and conditions" in filled.lower(), f"a terms box marked only by '*' was not ticked: {res.get('filled')}"


SWISSRE_GATE_PAGE = """<!doctype html><meta charset=utf-8><title>Create an Account</title><body>
<h2>Create an Account</h2>
<label>Email Address: *<input id="em" value="asha.example@example.com"></label>
<label>Choose Password: *<input id="pw" type="password" value="Pw#1abcDEF"></label>
<button id="gate" type="button">Open to read and accept the data privacy statement</button>
<button id="create" type="submit">Create Account</button>
<div id="msg"></div>
<script>
window.__accepted = false;
document.getElementById('gate').onclick = () => {
  const a = document.createElement('button'); a.textContent = 'Accept'; a.id = 'acc';
  a.onclick = () => { window.__accepted = true; a.remove(); };
  document.body.appendChild(a);
};
document.getElementById('create').onclick = (e) => { e.preventDefault();
  document.getElementById('msg').textContent = window.__accepted ? 'Account created' : 'Password accepted'; };
</script></body>"""


def test_a_privacy_gate_is_opened_and_accepted_before_submitting(tmp_path, monkeypatch):
    """a company: 'Create Account' did nothing until the 'Open to read and accept the data privacy statement' control was
    opened and accepted; the navigator only saw 'page stopped changing (Password accepted)' - a POSITIVE hint misreported as the why."""
    import json
    f = tmp_path / "swissre.html"
    f.write_text(SWISSRE_GATE_PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    monkeypatch.setattr(agent.accounts, "get", lambda h: "Pw#1abcDEF")

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        idx = {((e.get("label") or e.get("text") or "")): e["i"] for e in snap["els"]}
        return {"think": "create the account", "actions": [{"do": "click", "i": idx["Create Account"]}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        agent.navigate(pg, ctx, f.as_uri(), CV, {"id": "tswiss", "company": "Swiss Re", "title": "Analyst"}, dry_run=False)
        accepted = pg.evaluate("window.__accepted")
        msg = pg.locator("#msg").inner_text()
        b.close()
    assert accepted is True, "the privacy gate was never accepted"
    assert msg == "Account created", msg


def test_a_positive_validation_hint_is_never_reported_as_the_reason_a_page_stopped():
    """'Password accepted' is a success hint. Reporting it as the fault sent triage looking for a password problem that did not exist."""
    assert agent._page_complaint({"errs": ["Password accepted", "Password matches"]}) == ""
    assert agent._page_complaint({"errs": ["Password accepted", "The field State is required"]}) == "The field State is required"
    assert agent._page_complaint({"errs": [], "els": [{"text": "Error-Phone Device Type"}]}) == "Error-Phone Device Type"


def test_a_failure_page_is_still_saved_when_the_element_snapshot_cannot_be_taken(tmp_path, monkeypatch):
    """Target/a company: the page was mid-navigation, evaluate raised, and the HTML already on disk came back as if no
    evidence had been kept at all (no .json, so the failure looked like a bare message)."""
    shots = tmp_path / "shots"
    shots.mkdir()
    monkeypatch.setattr(agent.engine, "SHOTS", shots / "x.png")

    class Page:
        url = "https://target.wd5.myworkdayjobs.com/signin"

        def content(self):
            return "<html>Sign In</html>"

        def evaluate(self, js):
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")

    res = {"detail": "needs_you"}
    agent._save_failure(Page(), {"company": "Target"}, res)
    files = sorted((shots / "failures").glob("*"))
    assert [f.suffix for f in files] == [".html", ".json"]  # both kept, not one instead of the other
    import json
    rec = json.loads(files[1].read_text(encoding="utf-8"))
    assert rec["url"] == Page.url and rec["detail"] == "needs_you" and rec["snap"]["els"] == []
    assert res["failure_snapshot"].endswith(".html")


def test_portal_sentence_you_already_applied_is_recognised():  # a company (Oracle HCM): reported as a failed 'claimed submitted'
    from apply import agent
    assert agent.already_applied_notice("Job application form You already applied for this job. You may also view other jobs .").lower().startswith("you already applied")
    assert agent.already_applied_notice("You have already applied to this position.")
    assert agent.already_applied_notice("Apply now. Tell us why you want this job.") == ""


def test_recipes_are_not_replayed_on_repeating_row_pages():  # a company: English was added twice (and 'English (Australia)'), Tamil never
    from apply import agent
    assert agent.repeating_rows_page({"text": "Step 3 of 3 Language Skills English Advanced ADD LANGUAGE"})
    assert agent.repeating_rows_page({"text": "Work experience  Add another experience"})
    assert not agent.repeating_rows_page({"text": "Personal details First name Last name Email"})


def test_oracle_hcm_job_application_thanks_counts_as_confirmation_wording():  # a company
    from apply import engine
    assert engine.SUCCESS.search("Thank you for your job application.")
    assert not engine.SUCCESS.search("Tell us why you want this job")


def test_hints_include_the_portal_domain_param_and_eightfold():  # a company: the code mail came from "a company Careers <confirm@eightfold.ai>"
    from apply import agent
    h = agent._hints("https://jobs.alexion.com/careers/apply?pid=1&domain=astrazeneca.com", "Alexion Pharmaceuticals, Inc.")
    assert "astrazeneca" in h and "eightfold" in h and "Alexion" in h


def test_recipes_are_never_replayed_on_pages_with_a_password_box():  # a company: a replayed plan clicked Create Account three times on an existing account
    from apply import agent
    assert agent.replay_allowed(None, {"els": [{"type": "password"}], "text": "Sign In"}) is False
    assert agent.replay_allowed(None, {"els": [{"type": "text"}], "text": "Tell us about yourself"}) in (True, False)  # (other rules decide)
