"""Employer replies (apply/replies.py): wording taken from real mails in the user's inbox, ."""
import sqlite3
import time
from email.utils import formatdate

import pytest

from apply import replies


# ---- classification -------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("subject,body,kind", [
    ("Update on your Application - Contour Education", "After careful consideration, we regret to inform you that we have decided not to proceed.", "rejected"),
    ("Your IQVIA Application for R1568135", "After careful review, we have decided not to move forward with your application at this time.", "rejected"),
    ("Your Zoom Application: Security Analyst", "For the Security Analyst role, we have identified other candidates who are better aligned to the job requirements.", "rejected"),
    ("Recruitment Update on Business Analyst- 126564 at ANZ", "we regretfully advise that your application has not been successful in progressing to the next stage.", "rejected"),
    ("Thank You!", "After carefully reviewing your application, we regret to inform you that we currently do not have open roles.", "rejected"),
    ("Amazon application: Status update", "Thanks for applying for the position of Financial Analyst (ID: 10560119). Unfortunately, this position is no longer available.", "closed"),
    ("Position Filled - Sr. Analyst", "The position has been filled.", "closed"),
    ("Schedule your interview with JPMorganChase", "Please select a time to interview with our team.", "interview"),
    ("Action Required - Do select test - 2602531 Senior Analyst", "This is a reminder to complete the Do Select technical assessment sent to you.", "assessment"),
    ("Offer of employment", "We are pleased to offer you the position of Analyst.", "offer"),
])
def test_real_wording_is_classified(subject, body, kind):
    assert replies.classify(subject, body) == kind


@pytest.mark.parametrize("subject,body", [
    # acknowledgements carry the same boilerplate as real answers: none of it is an outcome
    ("Thank you for applying to State Street", "Unfortunately, due to the high volume of applications we receive we are unable to provide feedback to everyone who applies."),
    ("Supersub has received your application", "There are several reasons why an application might not move forward: mismatch of skills, volume of applications."),
    ("Thank you for your interest in Citi!", "If you are selected for an interview, a recruiter will contact you to schedule a call. Interview invitations are sent by email."),
    ("Your application was received", "If your profile is shortlisted for the next round we will reach out within two weeks."),
    ("Data Privacy Notice from Capco", "We may not be able to proceed without your consent. Unfortunately we are unable to share your data."),
])
def test_acknowledgements_are_not_outcomes(subject, body):
    assert replies.classify(subject, body) is None


def test_a_rejection_stays_a_rejection_when_it_also_promises_to_keep_the_cv():
    assert replies.classify("Update", "We regret to inform you we will not be moving forward. We will keep your CV on file and may contact you if a role opens up.") == "rejected"


# ---- matching ---------------------------------------------------------------------------------------------------------------------------------
def _job(jid, company, title, url="", applied="2026-11-05T10:00:00"):
    return {"id": jid, "company": company, "title": title, "apply_url": url, "url": url, "applied_at": applied, "status": "applied"}


def _mail(frm, email, subject, body, age_s=3600):
    return {"from": frm, "email": email, "subject": subject, "body": body, "date": formatdate(time.time() - age_s), "ts": time.time() - age_s}


def test_a_reply_is_tied_to_the_job_it_names_by_requisition_number():
    jobs = [_job("a", "Amazon", "Business Analyst Support, AOP Research Science", "https://www.amazon.jobs/en/jobs/10596571/ba"),
            _job("b", "Amazon", "Financial Analyst III", "https://www.amazon.jobs/en/jobs/10585519/fa")]
    m = _mail("", "noreply@mail.amazon.jobs", "Amazon application: Status update", "Thank you for your application for the position of Financial Analyst III (ID: 10585519).")
    job, _score, ambiguous = replies.match_job(m, jobs)
    assert job and job["id"] == "b" and not ambiguous


def test_several_applications_at_one_company_and_no_role_named_stays_with_the_company():
    jobs = [_job("a", "State Street", "Data Operations - Senior Associate"), _job("b", "State Street", "Reference Data Management, Senior Associate")]
    m = _mail("State Street HR", "careers@statestreet.com", "Update on your application", "We regret to inform you we will not be moving forward.")
    job, _score, ambiguous = replies.match_job(m, jobs)
    assert job is None and ambiguous


def test_a_newsletter_that_mentions_a_company_in_its_subject_is_not_that_companys_answer():
    jobs = [_job("a", "Amazon", "Business Analyst")]
    m = _mail("Reddit", "noreply@reddit.com", '"Amazon SDE-1 Interview Experience"', "Amazon interview experience: round 1, round 2. Unfortunately I did not get it.")
    assert replies.match_job(m, jobs)[0] is None
    assert replies.NOT_EMPLOYER.search("noreply@reddit.com Reddit")


def test_a_short_company_name_does_not_match_inside_another_word():
    jobs = [_job("e", "EY", "Senior Regulatory Reporting Analyst")]
    m = _mail("Tradeweb", "recruiting@workflow.email.oraclecloud.com", "Regarding Job Application - RegTech Data Analyst", "they regret to inform you")
    assert replies.match_job(m, jobs)[0] is None


def test_an_application_systems_address_never_names_the_company():
    jobs = [_job("o", "Oracle", "Financial Analyst")]
    m = _mail("JPMorgan Chase & Co. Human Resources", "eino.fa.sender@workflow.mail.us2.cloud.oracle.com", "Your job application status (Job number: 210784814)", "we are moving forward with other candidates")
    assert replies.match_job(m, jobs)[0] is None


def test_a_reply_cannot_predate_the_application():
    from datetime import datetime, timedelta
    applied = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")   # was a fixed date: the test expired 10 days after it was written
    jobs = [_job("a", "Contour Education", "Financial Analyst, India", applied=applied)]
    m = _mail("Contour Education India", "india@contoureducation.com.au", "Update on your Application", "regret to inform", age_s=10 * 86400)
    assert replies.match_job(m, jobs)[0] is None


# ---- storing --------------------------------------------------------------------------------------------------------------------------------
class _DB:
    def __init__(self, jobs):
        self.c = sqlite3.connect(":memory:")
        self.c.row_factory = sqlite3.Row
        self.c.execute("CREATE TABLE jobs (id TEXT, company TEXT, title TEXT, apply_url TEXT, url TEXT, applied_at TEXT, status TEXT, email_proof TEXT, outcome TEXT, outcome_at TEXT)")
        self.c.execute("CREATE TABLE replies (msgkey TEXT PRIMARY KEY, job_id TEXT, company TEXT, kind TEXT, subject TEXT, sender TEXT, ts REAL, received_at TEXT, snippet TEXT, score INTEGER, created_at TEXT, next_step TEXT, deadline TEXT, link TEXT)")
        for j in jobs:
            self.c.execute("INSERT INTO jobs(id,company,title,apply_url,url,applied_at,status) VALUES(?,?,?,?,?,?,?)", (j["id"], j["company"], j["title"], j["apply_url"], j["url"], j["applied_at"], j["status"]))

    def rows(self, where, params=()):
        return [dict(r) for r in self.c.execute(f"SELECT * FROM jobs WHERE {where}", params)]


def test_scan_stores_once_and_sets_the_jobs_outcome():
    jobs = [_job("a", "Contour Education", "Financial Analyst, India", "https://contoureducation.com/job/1")]
    d = _DB(jobs)
    mails = [_mail("Contour Education India", "india@contoureducation.com.au", "Update on your Application - Contour Education",
                   "Thank you for your application for the Financial Analyst. After careful consideration, we regret to inform you that we have decided not to proceed.")]
    first = replies.scan(mails=mails, d=d)
    assert first["stored"] == 1 and first["matched"] == 1
    assert d.c.execute("SELECT outcome FROM jobs WHERE id='a'").fetchone()[0] == "rejected"
    assert replies.scan(mails=mails, d=d)["stored"] == 0   # the same mail is never stored twice


def test_a_later_interview_invite_replaces_an_earlier_rejection_of_the_same_job():
    jobs = [_job("a", "Acme Analytics Private", "Data Analyst", "https://acme.example/jobs/1")]
    d = _DB(jobs)
    d.c.execute("INSERT INTO replies(msgkey,job_id,company,kind,subject,sender,ts,received_at,snippet,score,created_at) VALUES('k1','a','Acme','rejected','s','x',1000,'2026-11-01T10:00:00','',1,'now')")
    d.c.execute("INSERT INTO replies(msgkey,job_id,company,kind,subject,sender,ts,received_at,snippet,score,created_at) VALUES('k2','a','Acme','interview','s','x',2000,'2026-11-02T10:00:00','',1,'now')")
    replies.apply_outcomes(d)
    assert d.c.execute("SELECT outcome FROM jobs WHERE id='a'").fetchone()[0] == "interview"


# ---- the next step an employer asks for -------------------------------------------------------------------------------------------------------
def _ts(y, m, d):
    from datetime import datetime
    return datetime(y, m, d, 10, 0).timestamp()


def test_an_assessment_deadline_written_in_the_mail_is_logged():
    n = replies.next_step("assessment", "Action Required - Do select test", "Please complete the Do Select technical assessment by 14 November 2026. Link: https://app.doselect.com/t/abc",
                          _ts(2026, 11, 9), ["https://app.doselect.com/t/abc", "https://x.com/unsubscribe"])
    assert n["deadline"] == "2026-11-14" and "Complete the assessment" in n["next_step"] and n["link"] == "https://app.doselect.com/t/abc"


def test_a_relative_deadline_is_counted_from_the_mail_date():
    n = replies.next_step("assessment", "Online test", "Kindly complete the test within 3 days.", _ts(2026, 11, 9), [])
    assert n["deadline"] == "2026-11-12"


def test_a_date_without_a_year_means_the_next_one():
    n = replies.next_step("assessment", "Test", "Submit your assignment before Jan 5.", _ts(2026, 12, 20), [])
    assert n["deadline"] == "2027-01-05"


def test_an_interview_date_becomes_the_step_and_a_rejection_needs_no_action():
    n = replies.next_step("interview", "Interview invitation", "Your interview is scheduled on 12 Nov 2026 at 3 PM IST.", _ts(2026, 11, 9), [])
    assert n["deadline"] == "2026-11-12" and "2026-11-12" in n["next_step"]
    r = replies.next_step("rejected", "Update", "We regret to inform you ... by 20 November", _ts(2026, 11, 9), [])
    assert r["deadline"] == "" and r["next_step"].startswith("No action")


def test_no_invented_deadline():
    assert replies.next_step("assessment", "Test", "Please complete the assessment at your earliest.", _ts(2026, 11, 9), [])["deadline"] == ""


def test_scan_backfills_next_step_on_an_old_reply():
    d = _DB([_job("a", "Acme Analytics Private", "Data Analyst", "https://acme.example/jobs/1")])
    m = _mail("Acme HR", "hr@acmeanalytics.example", "Assessment for Data Analyst - Acme Analytics", "Please complete the online assessment by 20 November 2026.")
    replies.scan(mails=[m], d=d)
    assert d.c.execute("SELECT deadline FROM replies").fetchone()[0] == "2026-11-20"


def test_a_recruiters_company_is_read_from_the_brackets_in_the_sender_name():
    assert replies.display_company({"from": "NS, Deepthi (Synchrony, consultant)", "email": "x@synchrony.com", "subject": "", "body": ""}) == "Synchrony"
