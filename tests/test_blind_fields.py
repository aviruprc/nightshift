"""An answer needs its question (a company SmartRecruiters: screening questions in a shadow root were listed as '*'
and the model filled 'Immediate', '0', 'Asha Example' into questions it never saw). Evidence embedded: a company textarea #2 label '*'."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402


def test_a_field_labelled_only_with_a_star_is_blind():
    assert agent.blind_field({"i": 2, "tag": "textarea", "type": "textarea", "label": "*", "req": True})
    assert agent.blind_field({"i": 3, "tag": "input", "type": "text", "label": "", "req": True})


def test_named_fields_and_ticks_are_not_blind():
    assert not agent.blind_field({"tag": "input", "type": "text", "label": "City*"})
    assert not agent.blind_field({"tag": "input", "type": "checkbox", "label": "*"})
    assert not agent.blind_field({"tag": "input", "type": "text", "label": "", "rowctx": "Start date month*"})


def test_the_navigator_refuses_blind_fills():
    src = inspect.getsource(agent.navigate)
    assert "blind_field(el)" in src and "never answer an unseen question" in src
