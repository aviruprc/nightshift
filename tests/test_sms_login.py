"""A sign-in by SMS code to her phone is hers (ADM via jobhai.com: her number was typed into 'Enter mobile number to get OTP'
and four model calls waited for a text message the tool cannot read)."""
import inspect
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import agent  # noqa: E402


def test_the_jobhai_page_is_an_sms_login():
    assert agent.sms_login_page({"els": [{"tag": "input", "type": "tel", "label": "Enter mobile number to get OTP"}], "text": ""})
    assert agent.sms_login_page({"els": [], "text": "OTP has been sent to 9000000001. Enter the 4 digit code"})


def test_an_emailed_code_is_not_an_sms_login():
    assert not agent.sms_login_page({"els": [{"tag": "input", "label": "Enter verification code digit 1 of six."}],
                                     "text": "We sent a verification code to your email asha...@example.com"})
    assert not agent.sms_login_page({"els": [{"tag": "input", "type": "tel", "label": "Phone number"}], "text": "Apply for this job"})
    assert "sms_login_page(snap)" in inspect.getsource(agent.navigate)
