"""Bengaluru (or remote-India) only - a remote SEARCH is not proof that a posting is remote (KB-63).

. `a company - Accounts & Finance Analyst`
(stored location "Pune Division, Maharashtra, India") reached `needs_you`; all 147 non-Bengaluru rows in play were
`board:linkedin`, the only source that marks every card from its "India" remote search `_remote=True`. `location_ok`
then accepted any of them because INDIA_HINT matched the "India" in the address. A named non-Bengaluru city/state
must refute that flag; only the posting's own "Remote"/"Work from home" wording (or a location naming no other
city, i.e. just "India") may stand in for it.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apply import runner  # noqa: E402
from rank import filter as flt  # noqa: E402


def test_a_pune_job_stays_out_even_when_a_remote_search_found_it():
    assert flt.location_ok("Pune Division, Maharashtra, India", remote_flag=True) is False
    fam, why = flt.check({"title": "Accounts & Finance Analyst",
                          "location": "Pune Division, Maharashtra, India",
                          "description": "finance analyst with excel", "_remote": True})
    assert (fam, why) == (None, "location")


def test_every_other_indian_city_in_play_is_rejected():
    for loc in ("Hyderabad, Telangana, India", "Mumbai, Maharashtra, India", "New Delhi, Delhi, India",
                "Chennai, Tamil Nadu, India", "Noida, Uttar Pradesh, India", "Gurugram, Haryana, India",
                "Mumbai Metropolitan Region", "Maharashtra, India", "Delhi, India", "Pune City, Maharashtra"):
        assert flt.location_ok(loc, remote_flag=True) is False, loc


def test_bengaluru_and_multi_city_rows_stay_in():
    for loc in ("Bengaluru", "Bangalore, India", "Bangalore Rural", "Greater Bengaluru Area", "Karnataka, India",
                "Bengaluru, Hyderabad, Pune", "Hybrid - Bengaluru, Pune", "Pune, Bangalore Rural, Gurugram"):
        assert flt.location_ok(loc, remote_flag=False) is True, loc


def test_a_remote_india_row_that_names_no_other_city_stays_in():
    assert flt.location_ok("India", remote_flag=True) is True
    assert flt.location_ok("Remote - India") is True
    assert flt.location_ok("Work from home", remote_flag=False) is True
    assert flt.location_ok("Anywhere in India") is True


def test_a_foreign_remote_is_still_rejected():
    assert flt.location_ok("Remote - United States") is False
    assert flt.location_ok("London, United Kingdom", remote_flag=True) is False


def test_the_stored_row_text_is_enough_to_retire_it_at_queue_time():
    assert flt.location_conflict("Pune Division, Maharashtra, India") is True
    assert flt.location_conflict("Bengaluru, Karnataka") is False
    assert flt.location_conflict("Bengaluru, Hyderabad, Pune") is False
    assert flt.location_conflict("Remote - India") is False
    assert flt.location_conflict("") is False


def test_the_queue_checks_the_stored_location_before_it_prepares_anything():
    src = inspect.getsource(runner.prepare_queue)
    assert "flt.location_conflict(r[\"location\"])" in src
    assert src.index("location_conflict") < src.index("flt.check({**r")
