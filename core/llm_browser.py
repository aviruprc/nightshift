"""Backup AI through a logged-in chat website (DeepSeek in the Edge profile, ChatGPT in the Chrome backup profile). Used only when Claude is out of
usage. One browser per profile at a time, so calls are serialised with a cross-process file lock. A fresh chat is opened for every prompt."""
import msvcrt
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCKS = ROOT / "data"
LOGIN_FLAG = ROOT / "data" / "llm_login_needed.json"
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


class NeedsLogin(Exception):
    """The backup site is signed out: only a human can sign back in (DeepSeek blocks automated logins and shows a CAPTCHA)."""


def flag_login_needed(name: str):
    import json
    first = not LOGIN_FLAG.exists()
    LOGIN_FLAG.write_text(json.dumps({"provider": name, "at": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")
    if first:  # one Windows notification per expiry, not one per call
        try:
            from win11toast import notify
            notify("Job Hunt: backup AI signed out", "Open the dashboard and press 'Sign in' on the Backup AI chip (about a minute).")
        except Exception:  # noqa: BLE001
            pass


def clear_login_flag():
    LOGIN_FLAG.unlink(missing_ok=True)


def open_login_window(name: str = "deepseek-web"):
    """A plain (non-automated) Edge window on the backup profile: CAPTCHAs work normally. Sign in, close the window, and the bot carries on."""
    import subprocess
    exe = next((p for p in EDGE if Path(p).exists()), None)
    if not exe:
        raise RuntimeError("Microsoft Edge not found")
    cfg = SITES[name]
    subprocess.Popen([exe, f"--user-data-dir={ROOT / 'data' / cfg['profile']}", "--no-first-run", "--no-default-browser-check", cfg["url"]])


class _Lock:
    """Cross-process lock on a file (Windows msvcrt): two workers must never open the same browser profile at once."""

    def __init__(self, name: str, wait_s: int = 240):
        self.path, self.wait_s, self.fh = LOCKS / f"llm_browser_{name}.lock", wait_s, None

    def __enter__(self):
        self.fh = open(self.path, "a+")
        deadline = time.time() + self.wait_s
        while True:
            try:
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if time.time() > deadline:
                    raise TimeoutError("backup-AI browser busy")
                time.sleep(1.5)

    def __exit__(self, *a):
        try:
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.fh.close()


SITES = {
    "deepseek-web": {"channel": "msedge", "profile": "browser_profile_llm_edge", "url": "https://chat.deepseek.com/", "input": "textarea",
                     "reply": ".ds-markdown", "headless": True},
    "chatgpt-web": {"channel": "chrome", "profile": "browser_profile_llm", "url": "https://chatgpt.com/", "input": "div[contenteditable=true]",
                    "reply": "[data-message-author-role=assistant]", "headless": False},
}

REPLY_JS = "(sel) => { const e = [...document.querySelectorAll(sel)]; return {n: e.length, text: e.length ? e[e.length - 1].innerText : ''}; }"


class BrowserBusy(Exception):
    """The browser could not start on this profile (another process holds it). NOT the same as being signed out: never flagged as a sign-out."""


def _launch(pw, profile: str, cfg: dict):
    last = None
    for attempt in range(2):  # one retry: a window closing a moment ago may still hold the profile
        try:
            return pw.chromium.launch_persistent_context(
                profile, channel=cfg["channel"], headless=cfg["headless"], viewport={"width": 1280, "height": 850},
                ignore_default_args=["--enable-automation"],
                args=["--disable-blink-features=AutomationControlled"] + ([] if cfg["headless"] else ["--window-position=-2400,0"]))  # visible-mode sites stay off-screen
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(6)
    try:
        from core import activity
        activity.log("alert", "backup AI browser could not start (its profile is in use by another window): using the next AI instead")
    except Exception:  # noqa: BLE001
        pass
    raise BrowserBusy(str(last)[:120])


def ask(prompt: str, prov: dict, timeout: int = 150) -> str:
    from playwright.sync_api import sync_playwright
    cfg = {**SITES[prov["name"]], **{k: v for k, v in prov.items() if k in SITES[prov["name"]]}}
    profile = str(ROOT / "data" / cfg["profile"])
    try:  # signed out recently: do not waste 25s per call on a login page; retry every 5 minutes to notice a fresh sign-in
        if LOGIN_FLAG.exists() and time.time() - LOGIN_FLAG.stat().st_mtime < 300:
            raise NeedsLogin(f"{prov['name']} is signed out (waiting for the dashboard Sign in)")
    except OSError:
        pass
    with _Lock(cfg["profile"]):
        with sync_playwright() as pw:
            ctx = _launch(pw, profile, cfg)
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
                box = pg.locator(cfg["input"]).first
                try:
                    box.wait_for(state="visible", timeout=25000)
                except Exception:  # noqa: BLE001
                    body = ""
                    try:
                        body = pg.inner_text("body")[:600].lower()
                    except Exception:  # noqa: BLE001
                        pass
                    if "sign_in" in pg.url or "login" in pg.url or "log in" in body or "sign in" in body:
                        flag_login_needed(prov["name"])
                        raise NeedsLogin(f"{prov['name']} is signed out")
                    raise
                clear_login_flag()
                before = pg.evaluate(REPLY_JS, cfg["reply"])["n"]
                box.click()
                if cfg["input"].startswith("textarea"):
                    box.fill(prompt)
                else:  # contenteditable (ChatGPT): paste the text in one go instead of typing it
                    pg.keyboard.insert_text(prompt)
                pg.keyboard.press("Enter")
                deadline, last, stable = time.time() + timeout, "", 0
                while time.time() < deadline:
                    pg.wait_for_timeout(2000)
                    r = pg.evaluate(REPLY_JS, cfg["reply"])
                    if r["n"] > before and r["text"]:
                        stable = stable + 1 if r["text"] == last else 0
                        last = r["text"]
                        if stable >= 2:  # unchanged for ~4s: the reply has finished streaming
                            return last.strip()
                raise TimeoutError("backup AI did not finish replying")
            finally:
                ctx.close()


def check_session(prov: dict) -> bool:
    """Is this backup site still signed in? Loads the page headless (no prompt sent, so no tokens or chat history) and raises/clears the login flag.
    The guardian calls this every 30 minutes so an expiry is caught BEFORE the bot needs the AI."""
    from playwright.sync_api import sync_playwright
    cfg = {**SITES[prov["name"]], **{k: v for k, v in prov.items() if k in SITES[prov["name"]]}}
    if not cfg["headless"]:
        return True  # visible-only sites cannot be probed quietly; they are checked when used
    with _Lock(cfg["profile"], wait_s=60):
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(ROOT / "data" / cfg["profile"]), channel=cfg["channel"], headless=True, viewport={"width": 1280, "height": 850},
                                                        ignore_default_args=["--enable-automation"], args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = ctx.pages[0] if ctx.pages else ctx.new_page()
                pg.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
                try:
                    pg.locator(cfg["input"]).first.wait_for(state="visible", timeout=20000)
                except Exception:  # noqa: BLE001
                    flag_login_needed(prov["name"])
                    return False
                clear_login_flag()
                return True
            finally:
                ctx.close()
