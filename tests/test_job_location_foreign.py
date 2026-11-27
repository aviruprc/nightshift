"""A posting is FOREIGN only when its location NAMES a place outside the home country.

The old test was `home not in location.lower()`: a posting written 'City, KA' or 'City, IND-29, IN, 560035' (no word 'India') counted as a foreign job, so work
authorisation was answered 'No' and sponsorship 'Yes' for a citizen applying in her own city - false statements on a real form - and the answer check then
ENFORCED them. Lesson: validate a fact check against the strings REAL data contains, not the string the author had in mind."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import answers_map as am  # noqa: E402

SPONSOR = "Will you now or in the future require sponsorship for an employment visa?"
AUTH = "Are you legally authorized to work in the country where this job is located?"


def test_home_locations_without_the_country_name_are_not_foreign():
    for loc in ("Bengaluru, IND-29, IN, 560035", "Bengaluru, KA", "Bengaluru, Karnataka", "IN - Bengaluru, India", "Remote", ""):
        assert not am.job_is_foreign(loc), loc


def test_places_outside_the_home_country_are_foreign():
    for loc in ("Krakow, Lesser Poland, Poland", "London, UK", "Remote (Japan)", "New York, NY", "Remote-NAMER"):
        assert am.job_is_foreign(loc), loc


def test_answers_follow_the_postings_location(monkeypatch):
    for loc, spons, auth in (("Bengaluru, KA", "No", "Yes"), ("Bengaluru, IND-29, IN, 560035", "No", "Yes"), ("Krakow, Lesser Poland, Poland", "Yes", "No")):
        monkeypatch.setattr(am, "_job_location", lambda jid, loc=loc: loc)
        assert am.answer_for(SPONSOR, "j") == spons, loc
        assert am.answer_for(AUTH, "j") == auth, loc


def test_another_home_country_is_judged_by_its_own_markers(monkeypatch):
    from core import profile
    base = profile.answers()
    monkeypatch.setattr(profile, "answers", lambda: {**base, "current_country": "Germany", "current_city": "Berlin", "current_state": "", "city_aliases": []})
    assert not am.job_is_foreign("Berlin, Germany") and not am.job_is_foreign("Remote")
    assert am.job_is_foreign("London, UK")
