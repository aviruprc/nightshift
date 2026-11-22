"""Verify the persistent profile is still signed in to each site, headed or headless."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from playwright.sync_api import sync_playwright

from apply import browser

CHECKS = {
    "gmail": ("https://mail.google.com/mail/u/0/#inbox", lambda u, t: "mail.google.com/mail" in u and "accounts.google" not in u),
    "naukri": ("https://www.naukri.com/mnjuser/homepage", lambda u, t: "mnjuser" in u and "nlogin" not in u),
    "indeed": ("https://myjobs.indeed.com/", lambda u, t: "auth" not in u and "secure.indeed.com/auth" not in u),
    "linkedin": ("https://www.linkedin.com/feed/", lambda u, t: "/feed" in u and "login" not in u and "authwall" not in u),
}


def run(headless=True, only=None):
    res = {}
    with sync_playwright() as pw:
        ctx = browser.launch(pw, headless=headless)
        for name, (url, ok) in CHECKS.items():
            if only and name not in only:
                continue
            pg = ctx.new_page()
            try:
                pg.goto(url, wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3500)
                blocked = any(w in pg.title().lower() for w in ("access denied", "just a moment", "attention required"))
                res[name] = (ok(pg.url, pg.title()) and not blocked, pg.url[:70], pg.title()[:50])
            except Exception as e:  # noqa: BLE001
                res[name] = (False, "error", str(e)[:60])
            pg.close()
        ctx.close()
    return res


if __name__ == "__main__":
    headless = "--headed" not in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    import json
    import time
    res = run(headless, only)
    for k, (ok, url, title) in res.items():
        print(f"{'SIGNED IN ' if ok else 'NOT signed '} {k:9} {url} | {title}")
    out = Path(__file__).resolve().parent.parent / "data" / "session_status.json"
    out.write_text(json.dumps({"checked_at": time.time(), "sites": {k: {"ok": bool(v[0]), "detail": v[2]} for k, v in res.items()}}), encoding="utf-8")
