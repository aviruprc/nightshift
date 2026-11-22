"""A CV shared with a talent community is not an application (a company: screenshot
data/screenshots//_agent.png reads the text below, and three rows were stored `unconfirmed`)."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402

HITACHI = ("Home Introduce Yourself Thank you for sharing your CV/resume with us! A Recruiter from our Talent Acquisition Team will reach "
           "out to you directly if a role becomes available that matches your skills and qualifications.")


def test_the_hitachi_talent_pool_page_is_recognised():
    assert agent.talent_pool_page({"text": HITACHI})


def test_a_real_confirmation_is_not_a_talent_pool():
    assert not agent.talent_pool_page({"text": "Thank you for sharing your resume. Your application for Data Analyst has been received. "
                                                "Join our talent community for news."})
    assert not agent.talent_pool_page({"text": "Application Submitted. Process Completed!"})


def test_the_navigator_stops_and_forgets_the_route():
    src = inspect.getsource(agent.navigate)
    assert "talent_pool_page(snap)" in src and 'cache.recipe_forget(host, "Introduce Yourself")' in src


def test_a_disabled_save_names_the_empty_required_fields():
    snap = {"els": [{"tag": "input", "type": "text", "label": "How Did You Hear About Us?*", "req": True, "value": ""},
                    {"tag": "input", "type": "text", "label": "Given Name(s)*", "req": True, "value": "Asha"},
                    {"tag": "input", "type": "text", "label": "Country Phone Code*", "req": True, "value": ""}]}
    assert agent.empty_required_names(snap) == "How Did You Hear About Us?*; Country Phone Code*"
    assert "empty_required_names(snap)" in inspect.getsource(agent.navigate)
