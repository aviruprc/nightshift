"""Nespresso + a company: postings live in a fresh browser were filed `closed at step 0` from the page AFTER the Apply click."""
from apply import errors


def test_the_post_click_message_is_transient_not_permanent():
    d = ("the page reached by clicking Apply did not show the next step at step 0 (https://x/y); "
         "the posting page itself was open - retried later")
    assert errors.classify("failed", d) == "transient"


def test_a_real_closed_status_stays_permanent():
    assert errors.classify("closed", "closed page at https://x/y") == "permanent"
