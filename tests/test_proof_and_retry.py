"""P0.1 proof standard v2 + P0.2 failure taxonomy/retries + source hygiene. Each test names the real bug it locks in (see data/lessons_fixed.md)."""
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apply import errors, proof, runner  # noqa: E402
from core import db as dbm  # noqa: E402


@pytest.fixture
def d(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm.DB, "_ledger", lambda *a, **k: None)  # never write test rows into the real applications ledger
    db = dbm.DB(tmp_path / "t.db")
    jid, _ = db.upsert_job({"source": "t", "company": "Genpact", "title": "Sr. Analyst - BI, Reporting", "location": "Bengaluru",
                            "url": "https://x.test/1", "apply_url": "https://x.test/1"})
    db.set_status(jid, "candidate")
    return db, jid


# ---------- P0.1 receipt matching ----------
def test_hsbc_receipt_for_another_role_is_not_proof():  # bug: a company 'Analyst Data Privacy' receipt credited to our TTR job
    job = {"company": "HSBC", "title": "ANALYST - TRADE AND TRANSACTION REGULATORY REPORTING"}
    r = {"from": "HSBC", "email": "noreply@hsbc.com", "subject": "Thank you for applying: Analyst Data Privacy 62978", "snippet": ""}
    assert proof.match_score(job, r) == 0


def test_receipt_naming_the_role_matches():
    job = {"company": "Northern Trust", "title": "Associate Specialist, Data Analytics",
           "apply_url": "https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/R162019"}
    r = {"from": "Workday", "email": "ntrs@myworkday.com", "subject": "Your Northern Trust Application - R162019 Associate Specialist, Data Analytics",
         "snippet": "Thank you for applying"}
    assert proof.match_score(job, r) > 0


def test_linkedin_receipt_names_only_the_company():  # bug: LinkedIn 'application was sent' was excluded as noise
    job = {"company": "Vouchagram India Pvt Ltd", "title": "Risk Analyst Credit Monitoring"}
    r = {"from": "LinkedIn", "email": "jobs-noreply@linkedin.com", "subject": "Asha, your application was sent to Vouchagram India Pvt Ltd", "snippet": ""}
    assert proof.is_receipt(r) and proof.match_score(job, r) > 0


def test_job_alerts_are_not_receipts():
    assert not proof.is_receipt({"subject": "Wells Fargo Business Accountability Associate and 3 other jobs you may be interested in", "snippet": ""})
    assert not proof.is_receipt({"subject": "New jobs for you: Data Analyst", "snippet": "thank you for applying"})


def test_company_word_inside_another_word_is_not_a_match():  # bug: 'care' (a company) matched 'careers' in a company email
    job = {"company": "Fresenius Medical Care", "title": "Specialist Data Analytics"}
    r = {"from": "HSBC careers", "email": "careers@hsbc.com", "subject": "Thank you for your application", "snippet": "Specialist Data Analytics"}
    assert proof.match_score(job, r) == 0


# ---------- accuracy bugs: requisition ids + one weak company token ----------
def test_receipt_for_another_requisition_is_not_proof():  # bug: a company 'Business Analyst' (R158179) credited with the R161076 AML TA receipt
    job = {"company": "Northern Trust", "title": "Business Analyst",
           "apply_url": "https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Pune-India/Business-Analyst_R158179?src=x"}
    r = {"from": "Workday", "email": "ntrs@myworkday.com",
         "subject": "Your Northern Trust Application - R161076 Senior Analyst, AML TA", "snippet": ""}
    assert proof.match_score(job, r) == 0


def test_requisition_in_a_workday_url_with_an_underscore_is_read():  # bug: '\bR158179' never matched '..._R158179' (underscore is a word char)
    job = {"company": "Northern Trust", "title": "Business Analyst",
           "apply_url": "https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/Pune-India/Business-Analyst_R158179?src=x"}
    assert proof.job_req_ids(job) == {"158179"}


def test_receipt_for_the_same_requisition_still_matches():
    job = {"company": "Northern Trust", "title": "Senior Analyst, AML TA", "apply_url": "https://ntrs.wd1.myworkdayjobs.com/northerntrust/job/R161076"}
    r = {"from": "Workday", "email": "ntrs@myworkday.com",
         "subject": "Your Northern Trust Application - R161076 Senior Analyst, AML TA", "snippet": ""}
    assert proof.match_score(job, r) > 0


def test_requisitioned_receipt_does_not_confirm_a_job_without_a_requisition_id():
    job = {"company": "Northern Trust", "title": "Business Analyst"}  # no id in url/title -> cannot verify -> never credit
    r = {"from": "Workday", "email": "ntrs@myworkday.com", "subject": "Your Northern Trust Application - R161076 Senior Analyst", "snippet": ""}
    assert proof.match_score(job, r) == 0


def test_shared_generic_company_word_is_not_a_company_match():  # bug: DTCC matched a company receipt through the word 'trust'
    job = {"company": "The Depository Trust & Clearing Corporation (DTCC)", "title": "Business Intelligence Engineer"}
    r = {"from": "Workday", "email": "ntrs@myworkday.com", "subject": "Your Northern Trust Application - R161076 Senior Analyst, AML TA", "snippet": ""}
    assert proof.match_score(job, r) == 0


def test_ordinary_english_in_a_company_name_is_never_identity():
    """ audit of 56 real receipts x 2688 jobs: 7 pairs scored > 0 on nothing but everyday words. a company receipt
    matched 'Its All About People (ITAAP)' through 'about'/'people', a company receipt matched 'a company India' through 'applied'
    (7 letters, so it even counted as a STRONG token), and three more through 'next', 'team' and 'opportunity'. None had been credited
    because those jobs were not awaiting proof, but each one would have been. Strictness here can only delay a proof; looseness credits
    another company's receipt to her job."""
    stock = {"from": "MasterCard People Services", "email": "mastercard@myworkday.com",
             "subject": "Thank you for your application!", "snippet": "Dear Asha, all the best from our people. Your application is being reviewed."}
    assert proof.match_score({"company": "Its All About People (ITAAP)", "title": "Data Analyst - SQL/Python"}, stock) == 0
    pwc = {"from": "PwC", "email": "pwc@myworkday.com", "subject": "Confirming your PwC job application",
           "snippet": "You applied for a position with PwC. Your application has been received."}
    assert proof.match_score({"company": "Applied Materials India", "title": "Cybersecurity Analyst"}, pwc) == 0
    assert proof.match_score({"company": "NEXT Ventures", "title": "Trading and Risk Advisor"},
                             {"from": "Apex Group", "email": "theapexgroup@myworkday.com", "subject": "Application received",
                              "snippet": "We will be in touch with the next steps shortly."}) == 0
    # ...while a receipt that really names the company still proves it
    real = {"from": "Its All About People", "email": "itaap@myworkday.com", "subject": "Thank you for your application at Its All About People",
            "snippet": "Your application for Data Analyst - SQL/Python has been received."}
    assert proof.match_score({"company": "Its All About People (ITAAP)", "title": "Data Analyst - SQL/Python"}, real) > 0
    real_am = {"from": "Applied Materials", "email": "appliedmaterials@myworkday.com", "subject": "Your application to Applied Materials",
               "snippet": "Thank you for applying to Applied Materials."}
    assert proof.match_score({"company": "Applied Materials India", "title": "Cybersecurity Analyst"}, real_am) > 0


def test_other_company_short_words_do_not_match():  # bugs: Mobineers via 'info' (Argano receipt), a company FIRST via 'first' (TTEC receipt)
    assert proof.match_score({"company": "Mobineers Info Systems", "title": "SQL Developer"},
                             {"from": "Argano Careers", "email": "careers@argano.com", "subject": "Your recent job application for Operations Analyst", "snippet": ""}) == 0
    assert proof.match_score({"company": "IDFC FIRST Bank", "title": "Senior FI Risk Analyst"},
                             {"from": "Talent Acquisition Team", "email": "talent@ttec.com", "subject": "Thank you for applying with TTEC for Senior Financial Analyst", "snippet": ""}) == 0


def test_generic_company_subject_without_role_still_matches():  # a company sends 'Thank you for your interest in a company!' for real applications: one strong/single token is enough
    job = {"company": "Citi", "title": "KYC Operations Analyst", "apply_url": "https://citi.wd5.myworkdayjobs.com/job/123"}
    r = {"from": "Citi Human Resources", "email": "careers@citi.com", "subject": "Thank you for your interest in Citi!", "snippet": ""}
    assert proof.match_score(job, r) > 0


def test_two_token_company_matches_when_both_tokens_appear():
    job = {"company": "Wells Fargo", "title": "Senior Analytics Consultant - HR & BI"}
    r = {"from": "WellsFargoHR", "email": "careers@wellsfargo.com", "subject": "Wells Fargo Careers: Thank you for applying", "snippet": ""}
    assert proof.match_score(job, r) > 0


def test_verification_code_and_naukri_digest_are_never_proof():
    job = {"company": "Purplle.com", "title": "MIS Executive - Logistics CPG"}
    assert not proof.is_receipt({"subject": "TurboHire: 6-digit Code for your application to Purplle.com", "snippet": ""})
    assert proof.match_score(job, {"from": "TurboHire", "subject": "TurboHire: 6-digit Code for your application to Purplle.com", "snippet": ""}) == 0
    nk = {"from": "Naukri", "subject": "Naukri: You applied for 4 jobs on 02 Nov", "snippet": ""}
    assert not proof.is_receipt(nk)
    assert proof.match_score({"company": "Tekskills India", "title": "Tableau Developer"}, nk) == 0


def test_embedded_requisition_in_the_job_url_matches_the_receipt():  # a company URLs glue the id to the prefix: ...EUSR00298886EXTERNAL...
    job = {"company": "Ecolab", "title": "Data Analyst",
           "apply_url": "https://jobs.ecolab.com/global/en/job/EIYEIEUSR00298886EXTERNALENGLOBAL/Data-Analyst"}
    r = {"from": "Ecolab Workday Notification", "email": "ecolab@myworkday.com", "subject": "Ecolab Application Confirmation",
         "snippet": "Requisition R00298886"}
    assert proof.match_score(job, r) > 0


def test_common_business_word_is_not_a_strong_company_match():  # bug: FedEx Express matched a company 'Update on Your Application'
    job = {"company": "Fedex Express Transportation And Supply Chain Services (India)"}
    r = {"from": "Amex Careers", "email": "careers@americanexpress.com", "subject": "Update on Your Application", "snippet": ""}
    assert proof.match_score(job, r) == 0


def test_generic_receipt_cannot_overturn_a_failed_attempt(d, monkeypatch):  # a company 'Job application submitted' would have flipped two failed a company rows
    db, jid = d
    import core.db as core_db
    monkeypatch.setattr(core_db, "DB", lambda *a, **k: db)  # reconcile opens dbm.DB itself: never touch the real database in a test
    db.set_status(jid, "failed", "navigator: National ID number is required")
    monkeypatch.setattr(proof, "read_receipts", lambda days=3: [
        {"from": "hr@hcm-talentkonnect.com", "email": "hr@hcm-talentkonnect.com", "subject": "Job application submitted", "snippet": "Genpact"}])
    monkeypatch.setattr(proof, "verify_naukri", lambda log=print: 0)
    proof.reconcile(days=1, log=lambda *a: None)
    row = db.rows("id=?", (jid,))[0]
    assert row["status"] == "failed" and row["email_proof"] is None


def test_a_receipt_that_predates_the_send_never_proves_it(d, monkeypatch):
    """Live defect (a company): an AmbitionBox notice that arrived at was attached to the job sent at because the only
    agreeing word was the company name. A mail cannot prove a send that happened after it."""
    from datetime import datetime, timedelta
    db, _ = d
    import core.db as core_db
    monkeypatch.setattr(core_db, "DB", lambda *a, **k: db)
    jid, _ = db.upsert_job({"source": "board:linkedin", "company": "Capco", "title": "Business Analyst – Payments", "location": "Bengaluru",
                            "url": "https://x.test/capco", "apply_url": "https://job-boards.greenhouse.io/capco/jobs/8068889"})
    sent = datetime.now()
    db.c.execute("UPDATE jobs SET applied_at=? WHERE id=?", ((sent - timedelta(minutes=5)).isoformat(timespec="seconds"), jid))
    db.set_status(jid, "unconfirmed", "submitted: confirmation page seen, waiting for the receipt email")
    db.c.execute("UPDATE jobs SET applied_at=? WHERE id=?", ((sent - timedelta(minutes=5)).isoformat(timespec="seconds"), jid))
    monkeypatch.setattr(proof, "read_receipts", lambda days=3: [
        {"from": "AmbitionBox", "email": "noreply@ambitionbox.com", "subject": "You applied to Capco Technologies", "snippet": "",
         "ts": (sent - timedelta(hours=14)).timestamp()}])
    monkeypatch.setattr(proof, "verify_naukri", lambda log=print: 0)
    proof.reconcile(days=1, log=lambda *a: None)
    row = db.rows("id=?", (jid,))[0]
    assert row["status"] == "unconfirmed" and row["email_proof"] is None   # the stale notice proves nothing about THIS send
    # the same receipt arriving AFTER the send does prove it (the company-only rule is untouched)
    monkeypatch.setattr(proof, "read_receipts", lambda days=3: [
        {"from": "AmbitionBox", "email": "noreply@ambitionbox.com", "subject": "You applied to Capco Technologies", "snippet": "",
         "ts": (sent + timedelta(minutes=20)).timestamp()}])
    proof.reconcile(days=1, log=lambda *a: None)
    assert db.rows("id=?", (jid,))[0]["status"] == "applied"


def test_role_naming_receipt_does_overturn_a_failed_attempt(d, monkeypatch):  # it really was submitted: the mail names this role
    db, _ = d
    import core.db as core_db
    monkeypatch.setattr(core_db, "DB", lambda *a, **k: db)
    jid, _ = db.upsert_job({"source": "t", "company": "Genpact", "title": "Risk Modeling Analyst", "location": "Bengaluru",
                            "url": "https://x.test/2", "apply_url": "https://x.test/2"})
    db.set_status(jid, "failed", "navigator: page stopped changing")
    monkeypatch.setattr(proof, "read_receipts", lambda days=3: [
        {"from": "Genpact Careers", "email": "careers@genpact.com", "subject": "Genpact: application received for Risk Modeling Analyst", "snippet": ""}])
    monkeypatch.setattr(proof, "verify_naukri", lambda log=print: 0)
    proof.reconcile(days=1, log=lambda *a: None)
    row = db.rows("id=?", (jid,))[0]
    assert row["status"] == "applied" and row["email_proof"]


def test_agreed_requisition_outranks_the_role_word_heuristic():  # live defect: reconcile matched 0 of 55 receipts, two were exact requisition hits
    job = {"company": "Thermo Fisher",  # the live spelling: the receipt's legal name adds the word 'scientific', which no title contains
           "title": "Assoc Finance Systems Analyst", "apply_url": "https://thermofisher.wd5.myworkdayjobs.com/job/R-01369783"}
    r = {"from": "workday.workflow thermofisher", "email": "x@myworkday.com",
         "subject": "Thank you for your interest in Thermo Fisher Scientific!",
         "snippet": "We have successfully received your application for the following position: R-01369783 - Assoc Finance Systems Analyst ."}
    assert proof.match_score(job, r) > 0, "an exactly-agreed requisition was vetoed by the role-word rule"
    other = dict(r, snippet="We have successfully received your application for the following position: R-01369315 - Data Analyst .")
    assert proof.match_score(job, other) == 0, "a receipt naming a different requisition must never match"


def test_two_receipts_with_the_same_subject_each_confirm_their_own_requisition(d, monkeypatch):
    """a company: two genuine receipts share the identical generic subject and the same match time; only the requisition
    in the body tells them apart. Each must prove its own row, and the stored proof must name the requisition so a later audit can see at a
    glance that one receipt was not credited twice. Company is the live spelling 'a company' (the receipt says 'a company Scientific')."""
    db, _ = d
    import core.db as core_db
    monkeypatch.setattr(core_db, "DB", lambda *a, **k: db)
    pairs = [("R-01369315", "Data Analyst - Analytics Engineering & AI"), ("R-01369783", "Assoc Finance Systems Analyst")]
    ids = {}
    for req, title in pairs:
        jid, _ = db.upsert_job({"source": "t", "company": "Thermo Fisher", "title": title, "location": "Bengaluru",
                                "url": f"https://thermofisher.wd5.myworkdayjobs.com/job/{req}",
                                "apply_url": f"https://thermofisher.wd5.myworkdayjobs.com/job/{req}"})
        db.set_status(jid, "unconfirmed", applied_at="2026-11-05T00:52:37")
        ids[req] = jid
    monkeypatch.setattr(proof, "read_receipts", lambda days=3: [
        {"from": "workday.workflow thermofisher", "email": "x@myworkday.com", "subject": "Thank you for your interest in Thermo Fisher Scientific!",
         "snippet": f"We have successfully received your application for the following position: {req} - {title} . Our Talent Acquisition team"}
        for req, title in pairs])
    monkeypatch.setattr(proof, "verify_naukri", lambda log=print: 0)
    proof.reconcile(days=1, log=lambda *a: None)
    for req, jid in ids.items():
        row = db.rows("id=?", (jid,))[0]
        assert row["status"] == "applied", (req, row["status"], row["detail"])
        assert req.split("-")[-1] in (row["email_proof"] or ""), (req, row["email_proof"])


def test_one_receipt_never_confirms_two_jobs(d, monkeypatch):
    """The KB-9 failure mode: one generic 'application received' email credited to every row of that company. A receipt proves exactly one job."""
    db, _ = d
    import core.db as core_db
    monkeypatch.setattr(core_db, "DB", lambda *a, **k: db)
    ids = []
    for n, title in enumerate(("Data Analyst", "Finance Analyst")):
        jid, _ = db.upsert_job({"source": "t", "company": "Thermo Fisher Scientific", "title": title, "location": "Bengaluru",
                                "url": f"https://thermofisher.wd5.myworkdayjobs.com/job/T{n}", "apply_url": f"https://thermofisher.wd5.myworkdayjobs.com/job/T{n}"})
        db.set_status(jid, "unconfirmed", applied_at="2026-11-05T00:52:37")
        ids.append(jid)
    monkeypatch.setattr(proof, "read_receipts", lambda days=3: [
        {"from": "Thermo Fisher Careers", "email": "x@myworkday.com", "subject": "Application received", "snippet": "Thermo Fisher"}])
    monkeypatch.setattr(proof, "verify_naukri", lambda log=print: 0)
    proof.reconcile(days=1, log=lambda *a: None)
    rows = [db.rows("id=?", (jid,))[0] for jid in ids]
    assert sum(1 for r in rows if r["email_proof"]) == 1, [r["email_proof"] for r in rows]
    assert sum(1 for r in rows if r["status"] == "applied") == 1, [r["status"] for r in rows]


# ---------- P0.1 status gate: nothing is 'applied' without conclusive proof ----------
def test_confirmation_page_alone_is_submitted_not_applied(d):
    db, jid = d
    runner.record_submission(db, jid, {"status": "applied", "detail": "Workday confirmed", "proof": "PAGE: Workday 'thank you for applying'",
                                       "proof_shot": "x_confirmed.png"}, dry_run=False, extra={})
    r = db.rows("id=?", (jid,))[0]
    assert r["status"] == "unconfirmed" and r["email_proof"] is None and r["proof_shot"] == "x_confirmed.png" and r["page_proof"]


def test_no_proof_at_all_is_submitted_not_applied(d):
    db, jid = d
    runner.record_submission(db, jid, {"status": "applied", "detail": "clicked submit"}, dry_run=False, extra={})
    assert db.rows("id=?", (jid,))[0]["status"] == "unconfirmed"


def test_career_account_proof_counts_as_applied(d):
    db, jid = d
    runner.record_submission(db, jid, {"status": "applied", "detail": "Naukri confirmed", "proof": "PAGE: Naukri shows 'Applied' after clicking Apply"},
                             dry_run=False, extra={})
    r = db.rows("id=?", (jid,))[0]
    assert r["status"] == "applied" and r["email_proof"]


# ---------- P0.2 failure taxonomy ----------
@pytest.mark.parametrize("status,detail,cls", [
    ("failed", "navigator: {'done': 'blocked', 'why': \"Oracle site shows 'Too Many Attempts. Try Again Later' (30 minute lockout)\"}", "lockout"),
    ("failed", "TimeoutError: Page.goto: Timeout 45000ms exceeded", "transient"),
    ("needs_you", "Workday kept showing 'Something went wrong' after refreshing", "transient"),
    ("failed", "navigator: LLM unavailable (UsageLimit)", "transient"),
    ("captcha", "CAPTCHA on jpmc.fa.oraclecloud.com", "captcha"),
    ("closed", "job page has no Apply button (job closed or removed)", "permanent"),
    ("needs_you", "unanswered required: Were you ever employed by Genpact?*", "logic"),  # the LLM must answer this, never the user
    ("failed", "navigator: page stopped changing () last: step 9", "logic"),
    ("needs_you", "unanswered required: Passport number*", "personal"),
])
def test_classify(status, detail, cls):
    assert errors.classify(status, detail) == cls


def test_backoff_and_retry_limits():
    t0 = datetime(2026, 11, 3, 10, 0, 0)
    assert errors.next_attempt("transient", 0, t0) == "2026-11-03T10:05:00"
    assert errors.next_attempt("transient", 1, t0) == "2026-11-03T10:20:00"
    assert errors.next_attempt("transient", 2, t0) is None  # gave up after ONE retry (each retry redoes an LLM-driven session): becomes 'logic' (our bug)
    assert errors.next_attempt("lockout", 9, t0) == "2026-11-03T10:35:00"
    assert errors.next_attempt("logic", 0, t0) is None


def test_transient_failure_is_requeued_not_parked(d):
    db, jid = d
    r = db.rows("id=?", (jid,))[0]
    cls = runner.handle_failure(db, r, "failed", {"detail": "TimeoutError: Page.goto: Timeout 45000ms exceeded"}, False, {})
    row = db.rows("id=?", (jid,))[0]
    assert cls == "transient" and row["status"] == "candidate" and row["next_attempt_at"] and row["needs_you_reason"] is None


def test_unanswerable_question_is_our_bug_not_hers(d):
    db, jid = d
    r = db.rows("id=?", (jid,))[0]
    runner.handle_failure(db, r, "needs_you", {"detail": "unanswered required: Were you ever employed by Genpact?*"}, False, {})
    row = db.rows("id=?", (jid,))[0]
    assert row["status"] == "failed" and row["fail_class"] == "logic" and not row["needs_you_reason"]


# ---------- false usage-limit + source hygiene ----------
def test_page_text_mentioning_limits_is_not_a_usage_limit(monkeypatch):  # bug: Oracle 'Too many attempts' text looked like a CLI usage limit
    import subprocess

    from core import llm

    class P:
        returncode, stdout, stderr = 0, '{"think": "site says rate limit reached, try later", "done": "blocked"}', ""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P())
    monkeypatch.delenv("JOBHUNT_NO_LLM", raising=False)  # the guard that stops tests reaching the real CLI; subprocess.run is faked above
    assert "rate limit" in llm._ask("x", "haiku", 5, 0)


def test_no_control_characters_in_sources():  # bug: a shell heredoc turned regex \\b into backspace, silently breaking OTP parsing
    bad = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py") if ".venv" not in p.parts and "\x08" in p.read_text(encoding="utf-8", errors="ignore")]
    assert not bad, bad


def test_malformed_llm_json_is_retried_once(monkeypatch):  # bug: BNP failed with 'LLM unavailable (JSONDecodeError)'
    """ask_json must retry a malformed reply with a DIFFERENT provider. Patch `_ask_logged`: it is the seam ask_json actually calls
    (patch `ask` and the test silently reaches the real providers — it 'passed' only by getting a live {"ok": true} back)."""
    from core import llm
    replies = iter([("Sure! here you go: not json", "deepseek-api"), ('{"done": "applied"}', "groq")])
    asked = []

    def fake(prompt, *a, **k):
        asked.append(k.get("exclude"))
        return next(replies)

    monkeypatch.setattr(llm, "_ask_logged", fake)
    assert llm.ask_json("x") == {"done": "applied"}
    assert len(asked) == 2 and "deepseek-api" in (asked[1] or set())  # the model that produced garbage is not asked again


def test_any_activity_is_a_heartbeat(tmp_path, monkeypatch):  # bug: dashboard said 'Stalled' (and the watchdog could kill a healthy run) during long navigator sessions
    import time

    from core import activity, runstate
    monkeypatch.setattr(runstate, "PATH", tmp_path / "run_state.json")
    monkeypatch.setattr(activity, "PATH", tmp_path / "activity.jsonl")
    runstate.update(status="running", finished=False)
    runstate._state["updated_at"] = time.time() - 500
    activity.log("ai", "navigator step 3")
    assert time.time() - runstate._state["updated_at"] < 5


def test_sharding_keeps_a_company_on_one_worker():  # parallel workers: no account-creation races, no shared rate limits
    jobs = [{"company": c} for c in ["Genpact", "Genpact", "Wells Fargo", "UBS", "UBS", "EY", "Oracle", "Oracle"]]
    for n in (2, 3):
        by = {}
        for j in jobs:
            by.setdefault(j["company"], set()).add(runner.shard_of(j, n))
        assert all(len(v) == 1 for v in by.values())  # same company -> always the same worker
    assert {runner.shard_of(j, 2) for j in jobs} == {0, 1}  # and the work actually spreads


def test_supervisor_never_passes_an_empty_argument():  # bug: empty -Only made Start-Process reject the list; no worker ever started
    src = (ROOT / "supervise.ps1").read_text(encoding="utf-8")
    assert "'-Only',\"$Only\"" not in src and "if ($Only)" in src


def test_llm_outage_never_burns_a_jobs_attempts(d):  # bug: a usage-limit window failed ~40 jobs ('LLM unavailable') and burned their retries
    db, jid = d
    r = db.rows("id=?", (jid,))[0]
    assert errors.is_llm_down("navigator: LLM unavailable (RuntimeError)")
    cls = runner.handle_failure(db, r, "failed", {"detail": "navigator: LLM unavailable (RuntimeError)"}, False, {})
    row = db.rows("id=?", (jid,))[0]
    assert cls == "llm_down" and row["status"] == "candidate" and (row["attempts"] or 0) == 0 and runner.LLM_DOWN["until"] > 0
    runner.LLM_DOWN["until"] = 0.0


def test_already_applied_is_not_counted_as_applied_today():  # bug: Trigent 'already applied on Naukri' inflated today's count
    import inspect
    src = inspect.getsource(runner.run)
    # a company: the stored time is re-read first (another worker may have really submitted it), then the EARLIER placeholder
    assert 'applied_at=(live_at or r["applied_at"] or EARLIER)' in src and "SELECT applied_at FROM jobs" in src
    assert runner.EARLIER.startswith("2026-01-01")


def test_naukri_is_no_longer_boosted_over_company_sites():  # superseded: company career sites give the better results
    nk = {"score": 70, "apply_url": "https://www.naukri.com/job-listings-x", "url": "", "family": "data_bi"}
    wd = {"score": 80, "apply_url": "https://x.wd1.myworkdayjobs.com/y", "url": "", "family": "data_bi"}
    assert runner.queue_priority(wd, 10) > runner.queue_priority(nk, 10)
    assert runner.queue_priority(wd, 0) > runner.queue_priority(nk, 0)


def test_returnship_jobs_come_first_with_a_lenient_bar(monkeypatch, tmp_path):  # focus on returnship / career-break programmes
    monkeypatch.setattr(runner, "ROOT", tmp_path)   # code defaults, not the live settings.json (the live bar is a setting: 35 since)
    rj = {"score": 35, "apply_url": "https://x.com/returnship", "url": "", "family": "returnship", "company": "X"}
    other = {"score": 80, "apply_url": "https://y.com/job", "url": "", "family": "data_bi", "company": "Y"}
    assert runner.queue_priority(rj, 5) > runner.queue_priority(other, 5)
    assert runner.min_score(rj) <= 35 and runner.min_score(other) >= 45


def test_interleave_two_fast_per_slow_without_losing_anyone():
    assert runner.interleave(["n1", "n2", "n3", "n4", "n5"], ["w1", "w2"]) == ["n1", "n2", "w1", "n3", "n4", "w2", "n5"]
    assert runner.interleave([], ["w1", "w2"]) == ["w1", "w2"] and runner.interleave(["n1"], []) == ["n1"]


def test_llm_free_routes_keep_working_during_an_outage():
    assert runner.llm_free_route("https://www.naukri.com/job-listings-x") and runner.llm_free_route("https://x.wd1.myworkdayjobs.com/y")
    assert not runner.llm_free_route("https://jobs.ubs.com/x") and not runner.llm_free_route("https://careers.pentair.com/x")


def test_circuit_breaker_after_three_failures_with_no_success(tmp_path, monkeypatch):
    from core import cache
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "cb.db")
    assert not cache.host_blocked("bad.example.com")
    for _ in range(3):
        cache.remember("bad.example.com", False, "page stopped changing")
    assert cache.host_blocked("bad.example.com")
    cache.remember("good.example.com", True)
    for _ in range(5):
        cache.remember("good.example.com", False, "x")
    assert not cache.host_blocked("good.example.com")  # a portal that works at least sometimes is never cut off
    assert errors.MAX_TRANSIENT == 2


def test_company_claims_prevent_two_workers_on_one_company(tmp_path, monkeypatch):  # strict hash sharding left worker B idle; claims let any worker take any company safely
    from core import cache
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "cl.db")
    assert cache.claim("Genpact", "A") and cache.claim("genpact ", "A")        # mine, repeatedly
    assert not cache.claim("Genpact", "B")                                       # taken by A
    assert cache.claim("UST", "B")                                               # a different company is free
    assert cache.claim("Genpact", "B", ttl=-1)                                   # a stale claim (crashed worker) can be taken over


def test_fit_bar_is_realistic_and_lower_for_naukri(tmp_path, monkeypatch):  # bar 60 left 6 of 985 jobs eligible; workers starved
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    nk = {"apply_url": "https://www.naukri.com/job-listings-x", "url": ""}
    other = {"apply_url": "https://careers.acme.com/x", "url": ""}
    assert runner.min_score(nk) == 45 and runner.min_score(other) == 50 and runner.min_score() == 50
    (tmp_path / "data" / "settings.json").write_text('{"min_score": 55, "naukri_min_score": 40}')
    assert runner.min_score(nk) == 40 and runner.min_score(other) == 55


def test_page_proof_without_receipt_is_promoted_only_after_6h_and_only_under_the_tightened_check(d, monkeypatch):
    db, jid = d
    from datetime import datetime, timedelta
    monkeypatch.setattr(proof, "read_receipts", lambda days=3: [])
    monkeypatch.setattr(proof, "verify_naukri", lambda log=print: 0)
    old = (datetime.now() - timedelta(hours=7)).isoformat(timespec="seconds")
    db.c.execute("UPDATE jobs SET status='unconfirmed', page_proof=?, applied_at=? WHERE id=?", ("PAGE: 'successfully applied' after Submit", old, jid))
    monkeypatch.setattr(proof, "STRICT_FROM", "2099-01-01T00:00:00")          # confirmed BEFORE the tightened check existed: not auto-promoted
    monkeypatch.setattr(dbm, "DB", lambda *a, **k: db)  # reconcile opens its own DB handle: point it at the test database
    proof.reconcile(1, log=lambda *a: None)
    assert db.rows("id=?", (jid,))[0]["status"] == "unconfirmed"
    monkeypatch.setattr(proof, "STRICT_FROM", "2000-01-01T00:00:00")
    proof.reconcile(1, log=lambda *a: None)
    assert db.rows("id=?", (jid,))[0]["status"] == "applied"


def test_plan_order_puts_proven_companies_first_and_groups_their_jobs():  # finish every job at a company whose path works
    from apply import runner

    class D:
        def rows(self, q, *a):
            return [{"company": "Wells Fargo", "apply_url": "https://wf.wd1.myworkdayjobs.com/x/job/1"}]
    rows = [{"company": "Acme", "apply_url": "https://acme.com/1"}, {"company": "Wells Fargo", "apply_url": "https://wf.wd1.myworkdayjobs.com/x/job/2"},
            {"company": "Zed", "apply_url": "https://zed.com/1"}, {"company": "Wells Fargo", "apply_url": "https://wf.wd1.myworkdayjobs.com/x/job/3"}]
    out = runner.plan_order(rows, D())
    # the user reversed the rule ("the same ones are repeated again and again"): companies never applied to go first; one company's jobs still stay together
    assert [r["company"] for r in out] == ["Acme", "Zed", "Wells Fargo", "Wells Fargo"]


def test_plan_order_buckets_proven_then_top_tier_then_rest(monkeypatch):  # apply to more top companies
    from apply import runner
    monkeypatch.setattr(runner, "top_companies", lambda: {"goldman sachs", "jpmorgan chase"})

    class D:
        def rows(self, q, *a):
            return [{"company": "Wells Fargo", "apply_url": "https://wf.wd1.myworkdayjobs.com/x/job/1"}]
    rows = [{"company": "Acme", "apply_url": "https://acme.com/1"}, {"company": "Goldman Sachs", "apply_url": "https://higher.gs.com/roles/1"},
            {"company": "Wells Fargo", "apply_url": "https://wf.wd1.myworkdayjobs.com/x/job/2"}, {"company": "Goldman Sachs", "apply_url": "https://higher.gs.com/roles/2"},
            {"company": "JPMorgan Chase", "apply_url": "https://jpmc.fa.oraclecloud.com/job/1"}]
    # no tier buckets any more; new companies first (learned order, apply/learn.py), the already-covered company last, groups kept together
    assert [r["company"] for r in runner.plan_order(rows, D())] == ["Acme", "Goldman Sachs", "Goldman Sachs", "JPMorgan Chase", "Wells Fargo"]


def test_generic_receipts_are_matched_first_in_first_out():  # a company: sends / /, receipts (no role in them) / /
    from datetime import datetime
    from apply import proof
    ep = lambda s: datetime.fromisoformat(s).timestamp()  # noqa: E731
    jobs = [{"id": "a", "applied_at": "2026-11-10T00:23:00"}, {"id": "b", "applied_at": "2026-11-10T00:27:00"}, {"id": "c", "applied_at": "2026-11-10T00:34:00"}]
    assert proof.pick_by_time(jobs, {"ts": ep("2026-11-10T00:30:14")})["id"] == "a"       # the earliest send that precedes it
    assert proof.pick_by_time(jobs[1:], {"ts": ep("2026-11-10T00:35:05")})["id"] == "b"   # a is taken: next in line
    assert proof.pick_by_time(jobs[2:], {"ts": ep("2026-11-10T00:40:09")})["id"] == "c"
    assert proof.pick_by_time(jobs, {"ts": ep("2026-11-10T00:25:00")})["id"] == "a"       # c (sent later) cannot be proven by a receipt that predates it
    assert proof.pick_by_time(jobs, {"ts": ep("2026-11-10T03:30:00")}) is None            # nothing sent within the hour before it: no guess
    assert proof.pick_by_time(jobs, {"ts": None}) is None


def test_top_tier_companies_need_fit_55(monkeypatch):  # fit 55+ at top firms, no per-company cap
    from apply import runner
    monkeypatch.setattr(runner, "ROOT", __import__("pathlib").Path(__file__).parent / "_no_settings_here")   # code defaults, not the live settings.json
    monkeypatch.setattr(runner, "top_companies", lambda: {"goldman sachs"})
    gs = {"company": "Goldman Sachs", "apply_url": "https://higher.gs.com/roles/1", "url": ""}
    other = {"company": "Acme Pvt Ltd", "apply_url": "https://acme.com/1", "url": ""}
    nk = {"company": "Goldman Sachs", "apply_url": "https://www.naukri.com/job-listings-x", "url": ""}
    assert runner.min_score(gs) >= 55 and runner.min_score(nk) >= 55          # whatever the route
    assert runner.min_score(other) < 55                                       # everyone else keeps the normal bar


def test_company_sites_outrank_naukri_and_naukri_is_capped():  # the count rises with Naukri, the results come from company career sites
    from apply import runner
    nk = {"score": 60, "apply_url": "https://www.naukri.com/job-listings-x", "url": ""}
    co = {"score": 60, "apply_url": "https://careers.acme.com/job/1", "url": ""}
    assert runner.queue_priority(co, 5) > runner.queue_priority(nk, 5)
    order = runner.interleave(["c1", "c2", "c3", "c4", "c5", "c6"], ["n1", "n2"], ratio=3)
    assert order[:4] == ["c1", "c2", "c3", "n1"]                      # 3 company jobs for every 1 Naukri job


def test_a_worker_skips_rows_it_will_never_attempt_before_claiming_their_company():
    import inspect
    from apply import runner
    src = inspect.getsource(runner.run)
    assert src.index('JOBHUNT_SKIP_NAUKRI') < src.index('_cc.claim(')
    assert src.index('jpmc.fa.oraclecloud.com') < src.index('_cc.claim(')
