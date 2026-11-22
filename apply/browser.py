"""ONE persistent real-Chrome profile shared by every apply step (Gmail, Naukri, Indeed, LinkedIn, ATS accounts)."""
from pathlib import Path

import os

# JOBHUNT_PROFILE lets a second applier run in parallel on a COPY of the signed-in profile (Chrome locks a profile to one process)
PROFILE = Path(os.environ.get("JOBHUNT_PROFILE") or Path(__file__).resolve().parent.parent / "data" / "browser_profile")
UA_HEADLESS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/156.0.0.0 Safari/537.36")
SIGN_IN_PAGES =["https://mail.google.com/", "https://www.naukri.com/nlogin/login",
                 "https://secure.indeed.com/auth", "https://www.linkedin.com/login"]


def _setting(key: str, default: str) -> str:
    """data/settings.json browser_locale / browser_timezone (defaults: en-IN, Asia/Kolkata)."""
    try:
        from core import profile
        return str(profile.settings().get(key) or default)
    except Exception:  # noqa: BLE001
        return default


def launch(pw, headless: bool = False):
    """Persistent context on real Chrome. Sign-ins survive restarts because they live in PROFILE."""
    PROFILE.mkdir(parents=True, exist_ok=True)
    extra = {} if not headless else {"user_agent": UA_HEADLESS}  # drop the 'HeadlessChrome' tag some sites block on
    return pw.chromium.launch_persistent_context(
        str(PROFILE), channel="chrome", headless=headless, viewport={"width": 1366, "height": 900},
        locale=_setting("browser_locale", "en-IN"), timezone_id=_setting("browser_timezone", "Asia/Kolkata"), **extra,
        ignore_default_args=["--enable-automation"],
        # JOBHUNT_OFFSCREEN=1: a REAL headed window parked far off screen. a company's Oracle HCM site serves an empty page to headless Chrome (KB-9,:
        # 408 chars headless vs 5005 headed) but must not pop windows up on the user's desktop.
        args=["--disable-blink-features=AutomationControlled"] + (["--window-position=-32000,-32000", "--window-size=1366,900"]
                                                                   if (os.environ.get("JOBHUNT_OFFSCREEN") and not headless) else []))


def login_session(pages=None):
    """Open the sign-in pages in a visible window; the user signs in by hand, then closes the window."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = launch(pw, headless=False)
        urls = pages or SIGN_IN_PAGES
        first = ctx.pages[0] if ctx.pages else ctx.new_page()
        first.goto(urls[0])
        for u in urls[1:]:
            ctx.new_page().goto(u)
        print("Sign in to each tab yourself, then close the Chrome window. Waiting...", flush=True)
        try:
            while ctx.pages:
                ctx.pages[0].wait_for_timeout(1000)
        except Exception:  # noqa: BLE001 (window closed)
            pass
        print("Window closed; sign-ins saved in", PROFILE)
