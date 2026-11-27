"""A closure claimed inside a BLOCKED verdict is checked against the page and the HTTP status (a company,:
the model said '404 PAGE NOT FOUND ... no longer online'; curl answered HTTP 410). A paused deployment is transient (Skan.ai)."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent, errors  # noqa: E402


class _R:
    def __init__(self, status):
        self.status = status


class _Req:
    def __init__(self, status):
        self.s = status

    def get(self, url, **kw):
        return _R(self.s if not isinstance(self.s, dict) else self.s.get(url, 200))


class _Pg:
    url = "https://careers.example.com/job/1"

    def __init__(self, status):
        self.request = _Req(status)


def test_closure_wording_is_recognised():
    assert agent.CLOSURE_CLAIM.search("Job posting page returns 404 PAGE NOT FOUND - the position is no longer online")
    assert not agent.CLOSURE_CLAIM.search("saved password rejected: needs a manual reset")


def test_only_404_or_410_counts_as_gone():
    job = "https://careers.example.com/job/1"
    assert agent.http_gone(_Pg(410), job) == 410 and agent.http_gone(_Pg(404), job) == 404
    assert agent.http_gone(_Pg(200), job) == 0 and agent.http_gone(_Pg(403), job) == 0
    assert agent.http_gone(_Pg(404), "") == 0   # no job URL: never judged


def test_a_404_on_a_helper_page_does_not_close_a_live_posting():
    pg = _Pg({"https://www.linkedin.com/talentwidgets/apply-with-linkedin": 404, "https://careers.example.com/job/1": 200})
    pg.url = "https://www.linkedin.com/talentwidgets/apply-with-linkedin"
    assert agent.http_gone(pg, "https://careers.example.com/job/1") == 0


def test_the_blocked_branch_checks_before_closing():
    src = inspect.getsource(agent.navigate)
    assert 'done == "blocked" and CLOSURE_CLAIM.search(' in src and "http_gone(pg, job_url_)" in src and "same_host_" in src


def test_a_paused_deployment_is_transient():
    assert errors.classify("failed", "navigator: Site shows 'This deployment is temporarily paused' with zero interactive elements") == "transient"


def test_a_bare_blocked_verdict_keeps_the_models_reason():
    # Magna International: stored "navigator: blocked" while the plan's think said why
    # ("no 'Apply' or 'Apply Now' button visible") - which would also have classified it transient (KB-58).
    src = inspect.getsource(agent.navigate)
    assert "why_txt or ('blocked: ' + think if think else '') or _why_text(done)" in src   # ALLEN Digital: _why_text('blocked') won first
    assert 'CLOSURE_CLAIM.search(_why_text(plan.get("why")) + " " + think)' in src
    assert agent.CLOSURE_CLAIM.search("The target job posting is expired ('Oops, This Job is currently expired')")
    assert errors.classify("failed", "navigator: blocked: The current page is the job description page on Saarthi, but there is no 'Apply' or 'Apply Now' button visible in the el") == "transient"


def test_a_cloudflare_human_check_is_a_captcha_for_her():
    assert errors.classify("failed", "navigator: Cloudflare bot verification block encountered") == "captcha"


def test_a_cloudflare_wait_that_never_clears_is_a_human_check_for_her():
    # a company Senior Data Management Analyst: "page stopped changing last: step 4: wait #0 Cloudflare, ..."
    assert errors.classify("failed", "navigator: page stopped changing () last: step 4: wait #0 Cloudflare, Inc. = 5000") == "captcha"


def test_a_linkedin_apply_that_opened_nothing_is_a_page_state_not_hers():
    assert errors.classify("needs_you", "Apply did not open an external page") == "transient"


def test_a_models_lacks_required_skill_verdict_is_a_skip_not_a_retry():
    from apply import agent
    assert agent.lacks_required_skill("Candidate lacks required skills (SAP FI S/4HANA Accounting)")
    assert not agent.lacks_required_skill("saved password rejected: needs a manual reset")
    assert "lacks_required_skill(why_txt)" in inspect.getsource(agent.navigate)


def test_a_duplicate_rejection_after_our_submit_is_already_applied_not_unconfirmed():
    from apply import agent
    assert agent.already_applied_notice("Error Oops! You have already applied for this job opening")
    src = inspect.getsource(agent.navigate)
    assert 'elif already_applied_notice(snap.get("text", "")):' in src and 'elif final_step is None and already_applied_notice' not in src
