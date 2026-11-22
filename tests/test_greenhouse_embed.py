"""A Greenhouse job shown on the company's own page is applied to through Greenhouse's own form (a company)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from discover.ats_api import greenhouse_apply_url  # noqa: E402


def test_a_company_page_gets_the_greenhouse_form():
    assert greenhouse_apply_url("coupang", "https://www.coupang.jobs/en/jobs/?gh_jid=8398431", 8398431) == \
        "https://job-boards.greenhouse.io/embed/job_app?for=coupang&token=8398431"


def test_a_greenhouse_page_or_missing_data_is_left_alone():
    u = "https://boards.greenhouse.io/zuora/jobs/8312134?gh_jid=8312134"
    assert greenhouse_apply_url("zuora", u, 8312134) == u
    assert greenhouse_apply_url("", "https://x.com/?gh_jid=1", 1) == "https://x.com/?gh_jid=1"
