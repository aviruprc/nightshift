"""Provider fallback: Claude first; on a usage limit any configured OpenAI-compatible provider answers; Claude is retried after a cool-down."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import llm  # noqa: E402


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "DATA", tmp_path)
    monkeypatch.setattr(llm, "DOWN_FILE", tmp_path / "claude_down.json")
    monkeypatch.setattr(llm, "PROBE_FILE", tmp_path / "provider_probe.json")  # tests never write the live probe log


def providers_file(tmp_path, on=True):
    (tmp_path / "llm_providers.json").write_text(json.dumps([{"name": "deepseek", "base_url": "https://x.test/v1", "model": "deepseek-chat", "enabled": on}]))


def test_usage_limit_switches_to_the_provider_and_remembers_claude_is_down(tmp_path, monkeypatch):
    providers_file(tmp_path)
    calls = []
    monkeypatch.setattr(llm, "_ask", lambda *a: (_ for _ in ()).throw(llm.UsageLimit("limit")))
    monkeypatch.setattr(llm, "_ask_provider", lambda p, prov, task, t: calls.append(prov["name"]) or "from deepseek")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0) == ("from deepseek", "deepseek")
    assert llm.claude_is_down()  # next calls skip Claude straight away (no wasted 'claude -p' round trip while it is out)
    monkeypatch.setattr(llm, "_ask", lambda *a: pytest.fail("Claude must not be called during its cool-down"))
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0)[1] == "deepseek"


def test_claude_is_tried_again_after_the_cooldown(tmp_path, monkeypatch):
    providers_file(tmp_path)
    (tmp_path / "claude_down.json").write_text(json.dumps({"until": time.time() - 1}))
    monkeypatch.setattr(llm, "_ask", lambda *a: "from claude")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0) == ("from claude", "haiku")


def test_no_provider_configured_keeps_the_old_behaviour(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "_ask", lambda *a: (_ for _ in ()).throw(llm.UsageLimit("limit")))
    with pytest.raises(llm.UsageLimit):
        llm._ask_with_fallback("hi", "score", "haiku", 5, 0)


def test_disabled_or_keyless_provider_is_not_used(tmp_path, monkeypatch):
    providers_file(tmp_path, on=False)
    monkeypatch.setattr(llm, "_ask", lambda *a: (_ for _ in ()).throw(llm.UsageLimit("limit")))
    with pytest.raises(llm.UsageLimit):
        llm._ask_with_fallback("hi", "score", "haiku", 5, 0)
    providers_file(tmp_path)
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: (_ for _ in ()).throw(RuntimeError("no API key stored for provider deepseek")))
    with pytest.raises(RuntimeError):
        llm._ask_with_fallback("hi", "score", "haiku", 5, 0)


def test_browser_provider_is_dispatched_to_the_logged_in_site(tmp_path, monkeypatch):
    (tmp_path / "llm_providers.json").write_text(json.dumps([{"name": "deepseek-web", "type": "browser", "enabled": True, "model": "x"}]))
    from core import llm_browser
    monkeypatch.setattr(llm_browser, "ask", lambda prompt, prov, timeout=150: f"browser:{prov['name']}")
    monkeypatch.setattr(llm, "_ask", lambda *a: (_ for _ in ()).throw(llm.UsageLimit("limit")))
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0) == ("browser:deepseek-web", "deepseek-web")


def test_force_backup_switch_skips_claude(tmp_path, monkeypatch):
    providers_file(tmp_path)
    monkeypatch.setenv("JOBHUNT_FORCE_BACKUP", "1")
    monkeypatch.setattr(llm, "_ask", lambda *a: pytest.fail("Claude must be skipped"))
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: "backup")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0)[1] == "deepseek"


def test_browser_lock_serialises_two_users_of_one_profile(tmp_path, monkeypatch):
    from core import llm_browser
    monkeypatch.setattr(llm_browser, "LOCKS", tmp_path)
    with llm_browser._Lock("p", wait_s=5):
        with pytest.raises(TimeoutError):  # a second holder cannot enter while the first is inside
            with llm_browser._Lock("p", wait_s=2):
                pass
    with llm_browser._Lock("p", wait_s=2):  # free again afterwards
        pass


def test_backup_first_setting_spares_claude_but_still_falls_back_to_it(tmp_path, monkeypatch):
    providers_file(tmp_path)
    (tmp_path / "settings.json").write_text('{"llm_primary": "backup"}')
    order = []
    monkeypatch.setattr(llm, "_ask_provider", lambda p, prov, task, t: order.append("backup") or "from backup")
    monkeypatch.setattr(llm, "_ask", lambda *a: order.append("claude") or "from claude")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0)[1] == "deepseek" and order == ["backup"]  # Claude untouched
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: (_ for _ in ()).throw(RuntimeError("backup down")))
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0) == ("from claude", "haiku")  # backup failed -> Claude is the safety net


def test_signed_out_backup_site_is_flagged_and_falls_back(tmp_path, monkeypatch):  # DeepSeek blocks automated logins; a human signs in via the dashboard button
    import types

    from core import llm_browser
    monkeypatch.setattr(llm_browser, "LOGIN_FLAG", tmp_path / "flag.json")
    monkeypatch.setattr(llm_browser, "LOCKS", tmp_path)
    monkeypatch.setitem(sys.modules, "win11toast", types.SimpleNamespace(notify=lambda *a, **k: None))
    page = tmp_path / "signin.html"
    page.write_text("<html><body><h1>Sign in</h1><p>Log in with Google</p></body></html>", encoding="utf-8")
    prov = {"name": "deepseek-web", "url": page.as_uri(), "channel": "chrome", "profile": "tmp_llm_profile_test", "headless": True, "input": "textarea"}
    monkeypatch.setattr(llm_browser, "ROOT", tmp_path)
    with pytest.raises(llm_browser.NeedsLogin):
        llm_browser.ask("hi", prov, timeout=5)
    assert (tmp_path / "flag.json").exists()
    t0 = time.time()
    with pytest.raises(llm_browser.NeedsLogin):  # the fresh flag short-circuits: no browser launch at all
        llm_browser.ask("hi", prov, timeout=5)
    assert time.time() - t0 < 2


def test_provider_needing_login_makes_claude_the_fallback(tmp_path, monkeypatch):
    providers_file(tmp_path)
    (tmp_path / "settings.json").write_text('{"llm_primary": "backup"}')
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: (_ for _ in ()).throw(RuntimeError("deepseek-web is signed out")))
    monkeypatch.setattr(llm, "_ask", lambda *a: "from claude")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0) == ("from claude", "haiku")


def test_session_check_flags_a_signed_out_site_and_clears_when_signed_in(tmp_path, monkeypatch):
    import types

    from core import llm_browser
    monkeypatch.setattr(llm_browser, "LOGIN_FLAG", tmp_path / "flag.json")
    monkeypatch.setattr(llm_browser, "LOCKS", tmp_path)
    monkeypatch.setattr(llm_browser, "ROOT", tmp_path)
    monkeypatch.setitem(sys.modules, "win11toast", types.SimpleNamespace(notify=lambda *a, **k: None))
    out, inn = tmp_path / "out.html", tmp_path / "in.html"
    out.write_text("<html><body>Sign in</body></html>", encoding="utf-8")
    inn.write_text("<html><body><textarea></textarea></body></html>", encoding="utf-8")
    prov = {"name": "deepseek-web", "channel": "chrome", "profile": "sess_test_profile", "headless": True, "input": "textarea"}
    assert llm_browser.check_session({**prov, "url": out.as_uri()}) is False and (tmp_path / "flag.json").exists()
    assert llm_browser.check_session({**prov, "url": inn.as_uri()}) is True and not (tmp_path / "flag.json").exists()  # signed in again -> flag cleared


def test_limit_protect_mode_when_the_backup_ai_is_signed_out(tmp_path, monkeypatch):  # 95 Claude calls in 30 min while DeepSeek was signed out
    from apply import runner
    from core import llm_browser
    providers_file(tmp_path)
    (tmp_path / "settings.json").write_text('{"llm_primary": "backup"}')
    monkeypatch.setattr(llm_browser, "LOGIN_FLAG", tmp_path / "flag.json")
    assert not runner.protect_claude_limit()                        # signed in: normal operation
    (tmp_path / "flag.json").write_text("{}")
    assert runner.protect_claude_limit()                            # signed out: protect the limit
    (tmp_path / "settings.json").write_text('{"llm_primary": "claude"}')
    assert not runner.protect_claude_limit()                        # she chose Claude first: nothing to protect


def test_browser_that_cannot_start_is_busy_not_signed_out(tmp_path, monkeypatch):  # a stuck Edge window held the profile; launches failed and it was reported as 'signed out'
    from core import llm_browser
    monkeypatch.setattr(llm_browser, "LOGIN_FLAG", tmp_path / "flag.json")
    monkeypatch.setattr(llm_browser.time, "sleep", lambda s: None)

    class PW:
        class chromium:
            @staticmethod
            def launch_persistent_context(*a, **k):
                raise RuntimeError("Browser closed: exitCode=21")
    with pytest.raises(llm_browser.BrowserBusy):
        llm_browser._launch(PW, "p", {"channel": "msedge", "headless": True})
    assert not (tmp_path / "flag.json").exists()  # not a sign-out: the red 'sign in' state must not be raised for it


ECHO_PAGE = """<!doctype html><body><textarea id=t></textarea><script>
document.getElementById('t').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault();
  const d = document.createElement('div'); d.className = 'ds-markdown'; d.textContent = 'pong'; document.body.appendChild(d); } });
</script></body>"""


def test_backup_browser_works_from_inside_a_running_playwright_session(tmp_path, monkeypatch):
    """: in the live workers (which already run sync Playwright) every DeepSeek call failed with 'Playwright Sync API inside the asyncio loop'
    and silently fell back to Claude. Standalone tests passed, so the test must call it from INSIDE a sync_playwright block."""
    from playwright.sync_api import sync_playwright

    from core import llm_browser
    monkeypatch.setattr(llm_browser, "ROOT", tmp_path)
    monkeypatch.setattr(llm_browser, "LOCKS", tmp_path)
    monkeypatch.setattr(llm_browser, "LOGIN_FLAG", tmp_path / "flag.json")
    page = tmp_path / "echo.html"
    page.write_text(ECHO_PAGE, encoding="utf-8")
    (tmp_path / "llm_providers.json").write_text(json.dumps([{"name": "deepseek-web", "type": "browser", "enabled": True, "model": "x"}]))
    monkeypatch.setattr(llm, "DATA", tmp_path)
    prov = {"name": "deepseek-web", "type": "browser", "url": page.as_uri(), "channel": "chrome", "profile": "echo_profile", "headless": True,
            "input": "textarea", "reply": ".ds-markdown", "model": "x"}
    with sync_playwright() as pw:  # exactly the live situation: the caller is already inside a sync Playwright session
        b = pw.chromium.launch(channel="chrome", headless=True)
        b.new_page()
        assert llm._ask_provider("ping", prov, "score", 60) == "pong"
        b.close()


def test_api_provider_needs_a_stored_key_and_claude_can_be_reserved(tmp_path, monkeypatch):  # DeepSeek suspended the account; official APIs (Gemini/Groq) replace the web route
    from apply import runner
    (tmp_path / "llm_providers.json").write_text(json.dumps([
        {"name": "gemini", "type": "openai", "base_url": "https://x.test/v1", "model": "m", "enabled": True},
        {"name": "deepseek-web", "type": "browser", "enabled": False, "model": "x"}]))
    (tmp_path / "settings.json").write_text('{"llm_primary": "backup", "claude_for_bot": false}')
    from core import llm_browser
    monkeypatch.setattr(llm_browser, "LOGIN_FLAG", tmp_path / "no_flag.json")  # never read the real sign-out flag
    keys = {}
    monkeypatch.setattr(llm, "_has_key", lambda name: name in keys)
    assert llm.providers() == []                                     # no key stored: the API provider is inactive, the disabled web one is off
    assert runner.protect_claude_limit()                              # nothing else available and Claude is reserved: LLM-free routes only
    keys["gemini"] = "k"
    assert [p["name"] for p in llm.providers()] == ["gemini"]         # storing the key activates it by itself
    assert not runner.protect_claude_limit()                          # a provider exists: normal operation


def _two(tmp_path):
    (tmp_path / "llm_providers.json").write_text(json.dumps([
        {"name": "a", "type": "openai", "base_url": "https://a.test", "model": "m", "enabled": True},
        {"name": "b", "type": "openai", "base_url": "https://b.test", "model": "m", "enabled": True}]))


def test_dashboard_status_is_red_when_nothing_can_answer(tmp_path, monkeypatch):  # light stayed green with DeepSeek suspended and Claude reserved
    _two(tmp_path)
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: False)
    (tmp_path / "settings.json").write_text(json.dumps({"claude_for_bot": False}))
    s = llm.status()
    assert not s["any"] and [p["state"] for p in s["providers"]] == ["nokey", "nokey"]
    monkeypatch.setattr(llm, "_has_key", lambda n: n == "b")
    s = llm.status()
    assert s["any"] and s["ready"] == ["b"]


def test_switching_order_off_and_cooldown_reasons(tmp_path, monkeypatch):
    _two(tmp_path)
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    llm.move("b", -1)
    assert [p["name"] for p in llm.providers()] == ["b", "a"]            # b is now asked first
    llm.set_enabled("b", False)
    assert [p["name"] for p in llm.providers()] == ["a"]                 # off = never asked
    llm.set_enabled("b", True)
    err = type("E", (Exception,), {"code": 402})()
    llm.set_cooldown("b", err)                                           # out of credits -> rests, other provider takes over
    assert [p["name"] for p in llm.providers()] == ["a"]
    assert "recharge" in [p for p in llm.status()["providers"] if p["name"] == "b"][0]["why"]
    llm.clear_cooldown("b")                                              # recharged + 'retry' button
    assert [p["name"] for p in llm.providers()] == ["b", "a"]


def test_broken_config_never_replaces_a_good_one(tmp_path):
    _two(tmp_path)
    with pytest.raises(ValueError):
        llm.save_providers([{"nonsense": 1}])
    assert len(llm.all_providers()) == 2
    (tmp_path / "llm_providers.json").write_text("{not json")
    assert llm.all_providers() == [] and llm.providers() == []           # a corrupt file degrades safely instead of crashing the workers


def test_claude_is_asked_only_after_every_other_provider_failed(tmp_path, monkeypatch):  # Claude only as the very last measure
    _two(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"llm_primary": "backup", "claude_for_bot": True}))
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    order = []
    monkeypatch.setattr(llm, "_ask_provider", lambda p, prov, task, t: order.append(prov["name"]) or (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(llm, "_ask", lambda *a: order.append("claude") or "claude answered")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0) == ("claude answered", "haiku")
    assert order == ["a", "b", "claude"]                     # both providers tried first, Claude last
    order.clear()
    llm.clear_cooldown("a"), llm.clear_cooldown("b")          # (the failures above put them to rest)
    monkeypatch.setattr(llm, "_ask_provider", lambda p, prov, task, t: order.append(prov["name"]) or "provider answered")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0)[0] == "provider answered" and "claude" not in order   # a healthy provider means no Claude call


def test_gemini_falls_back_to_its_lite_model_on_429(monkeypatch):  # flash-latest has a 20/day free quota, lite has hundreds
    import io
    import urllib.error
    import urllib.request
    calls = []

    class Resp:
        def __init__(self, body):
            self.b = body

        def read(self):
            return self.b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=0):
        calls.append(req.full_url.split("/models/")[1].split(":")[0])
        if "lite" not in calls[-1]:
            raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b"{}"))
        return Resp(b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}')
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda s, n: "k")
    prov = {"name": "gemini", "type": "gemini", "model": "gemini-flash-latest", "fallback_model": "gemini-flash-lite-latest"}
    assert llm._ask_provider("hi", prov, "score", 5) == "ok" and calls == ["gemini-flash-latest", "gemini-flash-lite-latest"]


def test_gemini_falls_back_to_its_lite_model_when_the_main_one_is_5xx(monkeypatch):
    """KB-64: gemini-flash-latest answered 503 'This model is currently experiencing high demand' at while
    gemini-flash-lite-latest answered 'ok' in 1.7 s. Only a 429 used to fall through, so the provider was rested for 300 s."""
    import io
    import urllib.error
    import urllib.request
    calls = []

    class Resp:
        def read(self):
            return b'{"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout=0):
        calls.append(req.full_url.split("/models/")[1].split(":")[0])
        if "lite" not in calls[-1]:
            raise urllib.error.HTTPError(req.full_url, 503, "high demand", {}, io.BytesIO(b'{"error":{"code":503}}'))
        return Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda s, n: "k")
    prov = {"name": "gemini", "type": "gemini", "model": "gemini-flash-latest", "fallback_model": "gemini-flash-lite-latest"}
    assert llm._ask_provider("hi", prov, "score", 5) == "ok" and calls == ["gemini-flash-latest", "gemini-flash-lite-latest"]


def test_a_4xx_from_gemini_still_fails_fast_without_the_fallback(monkeypatch):
    """A bad key / bad request is not model-specific: asking the lite model with the same key cannot fix it."""
    import io
    import urllib.error
    import urllib.request
    calls = []

    def fake_open(req, timeout=0):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 400, "bad request", {}, io.BytesIO(b'{"error":{"code":400}}'))
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda s, n: "k")
    prov = {"name": "gemini", "type": "gemini", "model": "gemini-flash-latest", "fallback_model": "gemini-flash-lite-latest"}
    import pytest
    with pytest.raises(urllib.error.HTTPError):
        llm._ask_provider("hi", prov, "score", 5)
    assert len(calls) == 1


class HttpErr(Exception):
    """An urllib HTTPError look-alike: the code as an attribute and the provider's JSON body (which names the quota)."""

    def __init__(self, code, body="", provider=""):
        super().__init__(f"HTTP Error {code}: rate limited")
        self.code = code
        self.body = body
        self.provider = provider


def _quota_body(bucket, delay="31s"):
    return json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": f"Quota exceeded for {bucket}",
                                 "details": [{"metadata": {"quotaId": f"{bucket}PerProjectPerModel-FreeTier"}},
                                             {"retryDelay": delay}]}})


def test_gemini_cooldown_follows_the_quota_bucket_not_the_status_code(tmp_path, monkeypatch):
    """: EVERY Gemini 429 was rested for 2 hours as 'daily free quota' — a per-minute limit therefore took the provider out
    for the rest of the working day, and the chain fell through to a paid provider (or failed the job) for no reason."""
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    now = time.time()

    per_min = HttpErr(429, _quota_body("GenerateRequestsPerMinute"), "gemini")
    llm.set_cooldown("gemini", per_min)
    why, until = llm.cooldown_reason("gemini")
    assert "per-minute" in why and until - now < 300                       # a minute-scale limit rests seconds/minutes, not 2 hours

    # (Google's own retryDelay for a spent daily bucket: the live answer was 41502 s)
    per_day = HttpErr(429, _quota_body("GenerateRequestsPerDay", delay="41502s"), "gemini")
    llm.set_cooldown("gemini", per_day)
    why, until = llm.cooldown_reason("gemini")
    assert "daily free quota" in why and 40000 < until - now <= 86400      # rests until the reset Google names, not a guessed 2 h

    # main model out for the day, lite only per-minute limited: lite can still work, so do NOT write the provider off for a day
    mixed = HttpErr(429, _quota_body("GenerateRequestsPerMinute"), "gemini")
    mixed.quotas = [("gemini-flash-latest", 429, _quota_body("GenerateRequestsPerDay")),
                    ("gemini-flash-lite-latest", 429, _quota_body("GenerateRequestsPerMinute"))]
    llm.set_cooldown("gemini", mixed)
    why, until = llm.cooldown_reason("gemini")
    assert "per-minute" in why and until - now < 300


def test_codex_plan_limit_rests_until_the_date_the_cli_names(tmp_path, monkeypatch):
    """The real Codex message was '... or try again at Dec 4th, 2026 6:31 PM' and the code rested it for 20 minutes: every call then
    wasted ~5 s on a provider that could not answer for weeks."""
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    import datetime
    d = (datetime.date.today().replace(day=1) + datetime.timedelta(days=62)).replace(day=4)   # always ~5-9 weeks ahead (was a fixed date)
    err = RuntimeError(f"codex failed: You've hit your usage limit. Upgrade to Pro/Plus, or try again at {d:%b} 4th, {d.year} 6:31 PM")
    llm.set_cooldown("codex", err)
    why, until = llm.cooldown_reason("codex")
    assert "plan usage" in why and until > time.time() + 14 * 86400        # weeks away, not 20 minutes
    assert datetime.datetime.fromtimestamp(until).month == d.month


def test_cooldown_reasons_name_the_real_limit_type(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    now = time.time()
    llm.set_cooldown("deepseek-api", HttpErr(402, '{"error":{"message":"Insufficient Balance"}}', "deepseek-api"))
    why, until = llm.cooldown_reason("deepseek-api")
    assert "recharge" in why and 3000 < until - now < 4000                 # balance/credit: an hour, then try again
    llm.set_cooldown("groq", HttpErr(401, '{"error":{"message":"Invalid API Key"}}', "groq"))
    assert "replace the key" in llm.cooldown_reason("groq")[0]
    llm.set_cooldown("groq", TimeoutError("The read operation timed out"))
    why, until = llm.cooldown_reason("groq")
    assert "timeout" in why.lower() and until - now < 400                  # a glitch rests 3 minutes, not 20


def test_a_provider_that_answers_clears_its_stale_cooldown(tmp_path, monkeypatch):
    """A cooldown entry that has expired is still in the file; if the provider then answers, the entry must go (the dashboard must not
    keep showing an old reason, and a later reader must not see a stale rest for a healthy provider)."""
    providers_file(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"llm_primary": "backup"}))
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    (tmp_path / "cd.json").write_text(json.dumps({"deepseek": {"reason": "old", "until": time.time() - 5, "at": "00:00"}}))
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: "answered")
    assert llm._ask_with_fallback("hi", "score", "haiku", 5, 0) == ("answered", "deepseek")
    assert "deepseek" not in json.loads((tmp_path / "cd.json").read_text())


def test_all_down_raises_a_diagnosis_naming_every_provider_and_its_reason(tmp_path, monkeypatch):
    """: ~50 jobs carried 'navigator: LLM unavailable (RuntimeError)'. The old code re-raised whichever exception was LAST,
    so the only thing that survived was a type name: no provider, no reason, no idea whether to rotate a key or wait."""
    _two(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"llm_primary": "backup", "claude_for_bot": True}))
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    monkeypatch.setattr(llm, "_ask_provider", lambda p, prov, task, t: (_ for _ in ()).throw(RuntimeError(f"{prov['name']} exploded")))
    monkeypatch.setattr(llm, "_ask", lambda *a: (_ for _ in ()).throw(RuntimeError("claude -p failed: rate_limit_error")))
    with pytest.raises(llm.LLMUnavailable) as ei:
        llm._ask_with_fallback("hi", "score", "haiku", 5, 0)
    msg = str(ei.value)
    assert "a: RuntimeError: a exploded" in msg and "b: RuntimeError: b exploded" in msg and "rate_limit_error" in msg
    assert isinstance(ei.value, llm.UsageLimit) and isinstance(ei.value, RuntimeError)  # callers that pause on a limit still do
    assert ei.value.attempts                                     # structured, for the dashboard/incident report


def test_claude_is_rested_after_any_hard_failure_so_the_next_job_does_not_retry_it(tmp_path, monkeypatch):
    """: Claude's CLI failed with an API 429 ("rate_limit_error") the detector did not recognise, so no cool-down was written;
    every job then ran 'claude -p' three times (with sleeps) before failing. One hard failure must rest Claude for a while."""
    providers_file(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"llm_primary": "backup"}))
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: "provider answered")
    monkeypatch.setattr(llm, "_ask", lambda *a: (_ for _ in ()).throw(RuntimeError("claude -p failed: boom")))
    llm._ask_with_fallback("hi", "score", "haiku", 5, 0)          # provider answers, Claude is not reached
    assert not llm.claude_is_down()
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: (_ for _ in ()).throw(RuntimeError("provider down")))
    with pytest.raises(llm.LLMUnavailable):
        llm._ask_with_fallback("hi", "score", "haiku", 5, 0)
    assert llm.claude_is_down() and "boom" in llm.claude_down_reason()


def test_claude_limit_wording_with_an_underscore_is_recognised(tmp_path, monkeypatch):
    """'rate_limit_error' (the API's own 429 type) did NOT match the old 'rate limit' pattern."""
    import subprocess
    monkeypatch.delenv("JOBHUNT_NO_LLM", raising=False)  # (subprocess.run is patched right below: nothing leaves the machine)
    class P:
        returncode = 1
        stdout = ""
        stderr = 'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":"Number of requests exceeded"}}'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: P())
    with pytest.raises(llm.UsageLimit):
        llm._ask("hi", "haiku", 5, 0)


def test_a_model_that_cannot_produce_json_does_not_cost_the_job(tmp_path, monkeypatch):
    """a company: 'LLM unavailable (JSONDecodeError)' — one model answered prose, the SAME model was asked again, and the
    whole job failed although other providers were healthy. Each retry must ask a different provider."""
    _two(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"llm_primary": "backup", "claude_for_bot": True}))
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    seen = []

    def fake(prompt, prov, task, t):
        seen.append(prov["name"])
        return "I think the answer is probably yes." if prov["name"] == "a" else '{"ok": true}'
    monkeypatch.setattr(llm, "_ask_provider", fake)
    assert llm.ask_json("q", "score") == {"ok": True} and seen == ["a", "b"]


def test_tests_can_never_run_the_claude_cli(tmp_path, monkeypatch):
    """A test that forgets to patch the seam used to make a REAL claude -p call (it 'passed' on a live reply). Fail loudly instead."""
    with pytest.raises(RuntimeError, match="JOBHUNT_NO_LLM"):
        llm._ask("hi", "haiku", 5, 0)


def _resting(path, name, in_secs, reason="quota reached", streak=0):
    path.write_text(json.dumps({name: {"reason": reason, "until": time.time() + in_secs, "at": "00:00", "streak": streak}}))


def test_a_provider_comes_back_by_itself_when_its_rest_has_passed(tmp_path, monkeypatch):
    """'I hope all the llms keep on checking when limit is reset and comes back online themselves without me
    interfering.' The lazy path: providers must drop an expired rest on its own — no clear_cooldown, no restart."""
    _two(tmp_path)
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    _resting(tmp_path / "cd.json", "a", -5)
    assert llm.cooldown_reason("a") is None                                 # expired = not resting any more
    assert [p["name"] for p in llm.providers()] == ["a", "b"]               # back in rotation with no manual step
    assert not [p for p in llm.status()["providers"] if p["name"] == "a"][0]["why"]


def test_status_shows_why_how_long_and_when_it_was_last_probed(tmp_path, monkeypatch):
    """A stale 'resting' after the retry time has passed is a bug; the dashboard also needs the last probe time."""
    _two(tmp_path)
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    _resting(tmp_path / "cd.json", "a", 600, reason="daily free quota used up")
    row = [p for p in llm.status()["providers"] if p["name"] == "a"][0]
    assert row["state"] == "resting" and "daily free quota" in row["why"] and 500 < row["retry_in"] <= 600
    assert row["last_probe"] == "" and row["last_probe_ok"] is None
    _resting(tmp_path / "cd.json", "a", -1)                                 # the retry time passes
    row = [p for p in llm.status()["providers"] if p["name"] == "a"][0]
    assert row["state"] == "ready" and row["retry_in"] == 0                  # never a stale 'resting'


def test_prober_re_tests_a_due_provider_and_clears_its_rest(tmp_path, monkeypatch):
    """The active path: the background prober re-tests a provider whose time is up, off the critical path, and clears the rest on success."""
    _two(tmp_path)
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    _resting(tmp_path / "cd.json", "a", -5)
    seen = []
    monkeypatch.setattr(llm, "_ask_provider", lambda prompt, prov, task, t: seen.append(prov["name"]) or '{"ok": true}')
    assert [p["name"] for p in llm.due_probes()] == ["a"]                    # pure decision, no I/O to a provider
    assert llm.probe_due() == ["a"] and seen == ["a"]
    assert llm.cooldown_reason("a") is None                                  # healthy again, immediately
    log = llm.probe_log()["a"]
    assert log["ok"] is True and log["when"]                                # the dashboard can show when it ran
    assert llm.due_probes() == []                                           # nothing left to probe


def test_a_failing_probe_backs_off_and_caps_but_never_parks_the_provider(tmp_path, monkeypatch):
    """An unknown reset time must not park a provider for a day: 15 min, then 30, then 60 (capped) — always a next try."""
    _two(tmp_path)
    cd = tmp_path / "cd.json"
    monkeypatch.setattr(llm, "COOLDOWN_FILE", cd)
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: (_ for _ in ()).throw(HttpErr(429, "")))  # a bare 429: no reset time given
    for prior_streak, expected in ((0, 900), (1, 1800), (2, 3600), (3, 3600)):
        _resting(cd, "a", -5, streak=prior_streak)
        llm.probe_due()
        why, until = llm.cooldown_reason("a")
        left = until - time.time()
        assert expected - 60 <= left <= expected + 5, (prior_streak, left)
        assert "rate limit" in why.lower()
    assert llm.probe_log()["a"]["ok"] is False


def test_the_prober_never_starts_in_a_test_process(tmp_path, monkeypatch):
    """No hidden thread may dial the network from a test run: JOBHUNT_NO_LLM (tests/conftest.py) switches the prober off."""
    assert __import__("os").environ.get("JOBHUNT_NO_LLM") == "1"
    _two(tmp_path)
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    _resting(tmp_path / "cd.json", "a", -5)
    assert llm.start_prober() is None
    assert llm._probe_thread is None or not llm._probe_thread.is_alive()


def test_every_model_returning_prose_is_reported_as_such(tmp_path, monkeypatch):
    providers_file(tmp_path)
    (tmp_path / "settings.json").write_text(json.dumps({"llm_primary": "backup", "claude_for_bot": False}))
    monkeypatch.setattr(llm, "COOLDOWN_FILE", tmp_path / "cd.json")
    monkeypatch.setattr(llm, "_has_key", lambda n: True)
    monkeypatch.setattr(llm, "_ask_provider", lambda *a: "prose, not JSON")
    with pytest.raises(llm.LLMUnavailable) as ei:
        llm.ask_json("q", "score")
    assert "gave usable JSON" in str(ei.value) and "deepseek" in str(ei.value)
