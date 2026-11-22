"""a Naukri job that hands off to the employer's own (non-ATS) site is followed, not parked; aggregators never are."""
from apply import naukri_apply as na


class _Ctx:
    def __init__(self, url): self.url = url


def test_aggregator_regex_blocks_job_boards_and_social():
    for h in ("www.ambitionbox.com", "www.linkedin.com", "in.indeed.com", "www.glassdoor.co.in"):
        assert na.AGGREGATOR_RX.search(h)
    assert not na.AGGREGATOR_RX.search("www.example-employer.com")
