""": 'LG Ad Solutions', 'a company', 'a company India', 'a company', 'a company Ltd', '3M India' reduced to no identity words, so no receipt could ever match."""
from apply import proof


def test_short_all_caps_names_are_identity_words():
    assert proof.tokens("LG Ad Solutions") == ["lg"]
    assert proof.tokens("EY") == ["ey"] and proof.tokens("HPE") == ["hpe"] and proof.tokens("ABB Ltd") == ["abb"]
    assert proof.tokens("PwC India") == ["pwc"] and proof.tokens("3M India") == ["3m"]


def test_legal_suffixes_and_everyday_short_words_are_not_identity():
    assert proof.tokens("Hours Inc") == ["hours"]
    assert proof.tokens("Antal HR") == ["antal"]
    assert "inc" not in proof.tokens("Acme INC")


def test_the_lg_receipt_matches_the_lg_job_but_not_another_company():
    job = {"company": "LG Ad Solutions", "title": "Senior Analyst, Business Intelligence", "apply_url": "", "url": ""}
    receipt = {"from": "LG Ad Solutions Hiring Team", "email": "no-reply@ashbyhq.com", "subject": "Thanks for applying to LG Ad Solutions!",
               "snippet": "Thank you for applying for the Senior Analyst, Business Intelligence role at LG Ad Solutions!"}
    assert proof.match_score(job, receipt) > 0
    other = {"company": "Goldman Sachs", "title": "Senior Analyst, Business Intelligence", "apply_url": "", "url": ""}
    assert proof.match_score(other, receipt) == 0
