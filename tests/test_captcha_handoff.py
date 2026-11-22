""""for the JP morgan one, can you please open it in browser and keep everything filled till the point it requires captcha,
I'll fill it in". With JOBHUNT_HANDOFF set, a person is at a visible window: the navigator fills the form, WAITS at the CAPTCHA (it never touches
it) and continues once she has moved the page on. Without the variable nothing changes: the job is parked as before."""
import json

from playwright.sync_api import sync_playwright

from apply import agent
from core import llm as corellm

# a company's first screen in miniature: an e-mail box, an hCaptcha widget, and a NEXT that does nothing for the bot. The "person" acts only
# when she has been told (the notification hook): 1.5 seconds later the next page is there.
PAGE = """<html><body><h1>Apply: Data Analyst</h1>
<label for="em">Email Address</label><input id="em" type="email">
<div class="h-captcha" data-sitekey="x"></div>
<button id="next" type="button">NEXT</button>
<script>
window.personActs = () => setTimeout(() => {
  document.body.innerHTML = '<h1>Step 2 of 4: Your details</h1><label for="ph">Phone</label><input id="ph" type="tel">'; }, 1500);
</script></body></html>"""


def _run(tmp_path, monkeypatch, handoff: str):
    f = tmp_path / "jpm.html"
    f.write_text(PAGE, encoding="utf-8")
    monkeypatch.setattr(agent.engine, "SHOTS", tmp_path)
    monkeypatch.setattr(agent.cache, "recipe_get", lambda *a: None)
    monkeypatch.setattr(agent.cache, "recipe_put", lambda *a, **k: None)
    monkeypatch.setattr(agent.cache, "hints", lambda h: "none")
    told, holder = [], {}

    def toast(title, body):
        told.append(body)
        holder["pg"].evaluate("() => window.personActs()")
    monkeypatch.setattr(agent, "_toast", toast)
    if handoff:
        monkeypatch.setenv("JOBHUNT_HANDOFF", handoff)
    else:
        monkeypatch.delenv("JOBHUNT_HANDOFF", raising=False)
    seen_pages = []

    def llm(prompt, *a, **k):
        snap = json.loads(prompt.split("PAGE: ")[1].split("\nReturn JSON")[0])
        seen_pages.append(snap["text"][:40])
        if "Step 2 of 4" in snap["text"]:
            return {"done": "blocked", "why": "end of the test page"}
        em = next(e["i"] for e in snap["els"] if e.get("type") == "email")
        nx = next(e["i"] for e in snap["els"] if (e.get("text") or "") == "NEXT")
        return {"think": "fill the e-mail and continue", "actions": [{"do": "fill", "i": em, "value": "a@b.co"}, {"do": "click", "i": nx}]}

    monkeypatch.setattr(corellm, "ask_json", llm)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto(f.as_uri())
        holder["pg"] = pg
        res = agent.navigate(pg, ctx, f.as_uri(), tmp_path / "cv.pdf", {"id": "thand", "company": "JPMorgan Chase", "title": "ReEntry"}, dry_run=False)
        captcha_clicked = pg.evaluate("() => window.captchaTouched || 0")
        b.close()
    return res, told, seen_pages, captcha_clicked


def test_with_a_person_at_the_window_the_bot_waits_and_then_carries_on(tmp_path, monkeypatch):
    res, told, seen_pages, _ = _run(tmp_path, monkeypatch, "40")
    assert told and "press the page's Next" in told[0], told                       # she was told what to do
    assert any("Step 2 of 4" in t for t in seen_pages), seen_pages                 # the bot went on to the next page afterwards
    assert "end of the test page" in res["detail"], res                            # ... and its result is about THAT page
    assert "page stopped changing" not in res["detail"] and res["status"] != "captcha", res


def test_an_unattended_run_is_unchanged(tmp_path, monkeypatch):
    res, told, seen_pages, _ = _run(tmp_path, monkeypatch, "")
    assert not told
    assert res["status"] == "failed" and "page stopped changing" in res["detail"], res
    assert not any("Step 2 of 4" in t for t in seen_pages)


def test_the_wait_ends_by_itself_when_nobody_comes(monkeypatch):
    class Pg:
        url = "https://x.example/apply"

        def bring_to_front(self):
            pass

        def evaluate(self, js):
            return "same text"
    monkeypatch.setattr(agent, "_toast", lambda *a: None)
    assert agent.wait_for_human(Pg(), "solve the CAPTCHA", 2, poll_s=0.5) == -1


def test_a_captcha_widget_is_recognised_and_an_ordinary_page_is_not():
    class F:
        def __init__(self, url):
            self.url = url

    class L:
        def count(self):
            return 0

    class Pg:
        def __init__(self, frames):
            self.frames = frames

        def locator(self, sel):
            return L()
    assert agent.captcha_on_page(Pg([F("https://jpmc.fa.oraclecloud.com/x"), F("https://newassets.hcaptcha.com/captcha/v1/abc/static/hcaptcha.html")]))
    assert not agent.captcha_on_page(Pg([F("https://jpmc.fa.oraclecloud.com/x")]))


FLICKER = """<html><body><h1 id="h">Apply</h1><label for="em">Email Address</label><input id="em" type="email">
<div class="h-captcha" data-sitekey="x"></div><button type="button">NEXT</button>
<script>let k = 0; setInterval(() => { k++; document.getElementById('h').textContent = 'Apply ' + k;
  if (k % 2) { const b = document.createElement('button'); b.id = 'ag'; b.textContent = 'AGREE'; document.body.appendChild(b); }
  else { const b = document.getElementById('ag'); if (b) b.remove(); } }, 400);</script></body></html>"""


def test_a_page_that_only_redraws_itself_has_not_moved_on(tmp_path, monkeypatch):
    """ live (a company): text and a terms dialog changing on the SAME screen ended four waits after 2-4 seconds each."""
    monkeypatch.setattr(agent, "_toast", lambda *a: None)
    f = tmp_path / "flicker.html"
    f.write_text(FLICKER, encoding="utf-8")
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.goto(f.as_uri())
        got = agent.wait_for_human(pg, "press Next", 6, poll_s=0.7)
        b.close()
    assert got == -1
