"""Read verification links / OTP codes from Gmail using the signed-in Gmail tab of the persistent profile. Never prints codes."""
import re
import time

LINK = re.compile(r"https?://[^\s\"'<>)]+", re.I)
VERIFY_HINT = re.compile(r"verif|confirm|activate|validate|account|token|key=|code=", re.I)
OTP = re.compile(r"\b(\d{6}|\d{4})\b")


def _open_latest(pg, query: str, wait_s: int, newer: str = "10m") -> bool:
    """Search Gmail for a very recent message and open the newest. Polls until it arrives or wait_s runs out."""
    deadline = time.time() + wait_s
    while time.time() < deadline:
        from urllib.parse import quote
        pg.goto(f"https://mail.google.com/mail/u/0/#search/{quote(query + ' newer_than:' + newer, safe='')}", wait_until="domcontentloaded")
        pg.wait_for_timeout(3500)
        rows = pg.locator("tr.zA")
        if rows.count():
            rows.first.click()
            pg.wait_for_timeout(2500)
            return True
        pg.wait_for_timeout(6000)
    return False


def verification_link(ctx, sender_hint: str, wait_s: int = 120, allowed: tuple = (), newer: str = "20m",
                      since: float | None = None, want: str = ""):
    """`since` = the unix time we clicked Forgot-password / Resend: only mail that arrived AFTER that click is used, so an old
    reset/verification mail (expired link) can never be mistaken for this one. `want` = 'reset' or 'verif' subject preference."""
    from core import imap
    if imap.available():  # IMAP + app password: no browser session to expire
        try:
            return imap.verification_link(sender_hint, wait_s, allowed, since=since, want=want)
        except Exception:  # noqa: BLE001
            pass
    """Newest recent email whose sender/subject matches sender_hint -> the most likely verification URL, or None.
    `allowed` restricts candidate links to those containing one of these substrings (e.g. 'myworkday'), so we never pick unsubscribe/manage links."""
    pg = ctx.new_page()
    try:
        if not _open_latest(pg, f"{sender_hint}", wait_s, newer=newer):
            return None
        from urllib.parse import urlparse
        links = [h for h in pg.locator("div[role=main] a[href]").evaluate_all("els => els.map(e => e.href)")
                 if h.startswith("http") and not re.search(r"(^|\.)(google\.com|gmail\.com|googleusercontent\.com)$", urlparse(h).netloc)
                 and not re.search(r"unsubscribe|preferences|privacy", urlparse(h).path, re.I)  # match the path only: reset links embed her gmail address in the query
                 and (not allowed or any(a in h.lower() for a in allowed))]
        ranked = sorted(links, key=lambda h: (not re.search(r"passwordreset|reset|verif|activate|confirm|token|key=", h, re.I), len(h)))
        return ranked[0] if ranked else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        pg.close()


def otp_code(ctx, sender_hint: str, wait_s: int = 120, since: float | None = None, accept_old: bool = False):
    from core import imap
    if imap.available():
        try:
            return imap.otp_code(sender_hint, wait_s, since=since, accept_old=accept_old)
        except Exception:  # noqa: BLE001
            pass
    """Newest recent email from sender_hint -> a 6 (or 4) digit code, or None. The code is returned to the caller only."""
    pg = ctx.new_page()
    try:
        if not _open_latest(pg, f"{sender_hint}", wait_s, newer="5m"):
            return None
        body = pg.inner_text("div[role=main]")
        # prefer a code next to the words code/OTP/verification; never a year (2025/2026) or a phone fragment
        near = re.search(r"(?:code|otp|passcode|verification)[^0-9]{0,40}\b(\d{6}|\d{4})\b", body, re.I) or             re.search(r"\b(\d{6}|\d{4})\b[^0-9]{0,30}(?:is your|code|otp)", body, re.I)
        if near:
            return near.group(1)
        six = re.search(r"\b(\d{6})\b", body)
        if six:
            return six.group(1)
        four = [x for x in re.findall(r"\b(\d{4})\b", body) if not re.match(r"(19|20)\d\d$", x)]
        return four[0] if four else None
    except Exception:  # noqa: BLE001
        return None
    finally:
        pg.close()
