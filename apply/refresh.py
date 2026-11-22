"""Daily Naukri profile refresh: re-upload the approved resume so 'Profile last updated' is today.
Runs headless in the persistent Chrome profile. Logs to data/refresh.log and shows a Windows toast."""
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOG = ROOT / "data" / "refresh.log"
from core import profile as _profile  # noqa: E402
RESUME = _profile.base_cv_pdf()   # settings.json base_cv_pdf (your own CV PDF for the job-board profile)


def log(msg: str):
    LOG.parent.mkdir(exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def toast(title: str, body: str):
    try:
        from win11toast import notify
        notify(title, body)
    except Exception:  # noqa: BLE001
        pass


def refresh_naukri() -> bool:
    from playwright.sync_api import sync_playwright

    from apply import browser
    today = datetime.now().strftime("%b %d, %Y")
    with sync_playwright() as pw:
        ctx = browser.launch(pw, headless=True)
        pg = ctx.new_page()
        pg.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(5000)
        if "nlogin" in pg.url or "Access Denied" in pg.title():
            log("Naukri NOT signed in / blocked - run: python hunt.py login")
            ctx.close()
            return False
        pg.locator("input[type=file]").first.set_input_files(str(RESUME))
        pg.wait_for_timeout(8000)
        text = re.sub(r"\n\s*\n+", "\n", pg.inner_text("body"))
        ok = f"Uploaded on {today}" in text
        log(f"Naukri resume re-upload {'OK' if ok else 'UNCONFIRMED'} ({today})")
        ctx.close()
        return ok


if __name__ == "__main__":
    try:
        ok = refresh_naukri()
    except Exception as e:  # noqa: BLE001
        log(f"Naukri refresh ERROR: {str(e)[:160]}")
        ok = False
    toast("Job Hunt", "Naukri profile refreshed" if ok else "Naukri refresh FAILED - check data/refresh.log")
    sys.exit(0 if ok else 1)
