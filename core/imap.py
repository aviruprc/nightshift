"""Gmail over IMAP with an app password (stored in Windows Credential Manager, service 'nightshift-gmail'). No browser session to expire.
Used for: application receipts (apply/proof.py), email verification codes / links (apply/mail.py), password-reset links.
Search uses Gmail's own query syntax through X-GM-RAW, e.g. 'newer_than:2d from:workday subject:application'."""
import email
import imaplib
import os
import re
import time
from email.header import decode_header, make_header
from email.utils import parseaddr

import keyring

SERVICE = "nightshift-gmail"
try:   # the mailbox the bot reads: cv/facts.yaml personal.email (or JOBHUNT_EMAIL); JOBHUNT_IMAP_HOST for a non-Gmail provider
    from core import profile as _profile
    ADDR = _profile.email()
except Exception:  # noqa: BLE001
    ADDR = ""
HOST = os.environ.get("JOBHUNT_IMAP_HOST") or "imap.gmail.com"
LINK = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)


def password() -> str | None:
    try:
        return keyring.get_password(SERVICE, ADDR)
    except Exception:  # noqa: BLE001
        return None


def available() -> bool:
    return bool(password())


def save_password(pw: str):
    keyring.set_password(SERVICE, ADDR, re.sub(r"\s+", "", pw))  # Google shows it as 4 groups of 4 letters


def _connect() -> imaplib.IMAP4_SSL:
    pw = password()
    if not pw:
        raise RuntimeError("no Gmail app password stored (run: python hunt.py gmail-setup)")
    M = imaplib.IMAP4_SSL(HOST)
    M.login(ADDR, pw)
    M.select('"[Gmail]/All Mail"', readonly=True)
    return M


def check() -> tuple[bool, str]:
    """Health check: can we log in and read the mailbox?"""
    try:
        M = _connect()
        M.logout()
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:100]}"


def _s(h) -> str:
    try:
        return str(make_header(decode_header(h or "")))
    except Exception:  # noqa: BLE001
        return str(h or "")


def _parts(msg) -> tuple[str, str]:
    """(plain text, html) of a message, decoded."""
    plain, htm = [], []
    for part in msg.walk():
        ct = part.get_content_type()
        if ct not in ("text/plain", "text/html"):
            continue
        try:
            payload = part.get_payload(decode=True)
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace") if payload else ""
        except Exception:  # noqa: BLE001
            continue
        (plain if ct == "text/plain" else htm).append(text)
    return "\n".join(plain), "\n".join(htm)


def _strip(htm: str) -> str:
    htm = re.sub(r"(?is)<(script|style).*?</\1>", " ", htm)
    txt = re.sub(r"(?s)<[^>]+>", " ", htm)
    import html
    return re.sub(r"\s+", " ", html.unescape(txt)).strip()


def parse(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw)
    plain, htm = _parts(msg)
    text = re.sub(r"\s+", " ", plain).strip() or _strip(htm)
    name, addr = parseaddr(_s(msg.get("From")))
    # a company/Workday: the reset URL was only VISIBLE TEXT in an HTML-only email (no <a href>, no plain part), so every link list was empty and the
    # bot reported "no password-reset email arrived" while the email sat in the inbox. Also scan the text we derived from the HTML.
    links = list(dict.fromkeys(HREF.findall(htm) + LINK.findall(plain) + LINK.findall(text)))
    return {"from": name or addr, "email": addr, "subject": _s(msg.get("Subject")), "date": msg.get("Date", ""), "snippet": text[:400],
            "body": text[:6000], "links": links}


def search(query: str, limit: int = 30) -> list[dict]:
    """Newest-first messages matching a Gmail query."""
    M = _connect()
    try:
        typ, data = M.search(None, "X-GM-RAW", '"' + query.replace('"', "'") + '"')
        ids = (data[0] or b"").split()[-limit:]
        out = []
        for i in reversed(ids):
            typ, d = M.fetch(i, "(BODY.PEEK[])")
            if typ == "OK" and d and isinstance(d[0], tuple):
                out.append(parse(d[0][1]))
        return out
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


def msg_ts(m: dict) -> float:
    """Unix time of a parsed message's Date header; 0.0 when absent or unparseable."""
    from datetime import timezone
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime((m or {}).get("date") or "")
        if dt.tzinfo is None:   # a Date ending "-0000" means UTC (RFC 5322); Python returns it with no zone and .timestamp then reads it as LOCAL time (5.5 h off in IST)
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def _recent(messages: list, since: float | None, prefer: tuple = (), skew_s: float = 180) -> list:
    """Only mail that arrived AFTER `since` (a small clock-skew allowance), newest first, messages whose SUBJECT matches one of the
    `prefer` patterns first. — an old reset mail
    from yesterday opens an expired link and looks exactly like the portal refusing a correct password."""
    if since:
        messages = [m for m in messages if msg_ts(m) >= since - skew_s]
    return sorted(messages, key=lambda m: (-sum(1 for p in prefer if re.search(p, (m.get("subject") or ""), re.I)), -msg_ts(m)))


def newest(query: str, wait_s: int = 120, poll_s: int = 6, since: float | None = None, prefer: tuple = ()) -> dict | None:
    """Poll until a message matching the query exists (verification emails take a few seconds to arrive).
    `since` keeps only mail that arrived after that unix time; `prefer` puts a matching subject first."""
    deadline = time.time() + wait_s
    while True:
        res = _recent(search(query, limit=10), since, prefer)
        if res:
            return res[0]
        if time.time() >= deadline:
            return None
        time.sleep(poll_s)


OTP_NEAR = re.compile(r"(?:code|otp|passcode|verification)[^0-9]{0,40}\b(\d{6}|\d{4})\b|\b(\d{6}|\d{4})\b[^0-9]{0,30}(?:is your|code|otp)", re.I)


def _code_in(m: dict) -> str | None:
    """The code written in ONE message (digits near the word code/otp, then any 6 digits, then 4 digits, then a letters+digits code)."""
    near = OTP_NEAR.search(m["subject"] + " " + m["body"])
    if near:
        return near.group(1) or near.group(2)
    six = re.search(r"\b(\d{6})\b", m["body"])
    if six:
        return six.group(1)
    four = [x for x in re.findall(r"\b(\d{4})\b", m["body"]) if not re.match(r"(19|20)\d\d$", x)]
    if four:
        return four[0]
    return alphanumeric_code(m["subject"] + " " + m["body"])


# Real subjects seen 2026-11: "One-Time Password for Candidate Account Verification" (SuccessFactors), "Your One-Time Password (OTP) for Profile Creation"
# (a company), "Confirm your identity" (Oracle, companies), "Identity Confirmation", "Your verification code", "Verification Code - <Company>".
CODE_SUBJECT = r"code|otp|one.?time|passcode|verif|confirm your identity|identity (confirmation|verification)"
CODE_VALID_S = 600   # a one-time code lives ~10 minutes (a company / Oracle say so in the mail itself)
FRESH_WAIT_S = 40    # how long to wait for a NEWER code when the newest one in the inbox is already old


def otp_code(sender_hint: str, wait_s: int = 120, since: float | None = None, accept_old: bool = False) -> str | None:
    """The newest valid code from `sender_hint`.

     (a company: "The code isn't valid"): the old version returned the first matching mail it saw, and `since` allowed mail up to
    3 minutes BEFORE our request. a company sends a new code each time one is asked for (today), so a stale code from a
    minute earlier was typed while the fresh one was still in transit. Now: with `since`, only mail from after our request (20 s of clock slack);
    without it, only codes younger than their 10-minute life, and when the newest is older than 90 s we wait up to 40 s for a fresher one."""
    deadline = time.time() + wait_s
    fresh_until = time.time() + FRESH_WAIT_S
    while True:
        msgs = _recent(search(f"newer_than:1d {sender_hint}", limit=10), since, (CODE_SUBJECT,), skew_s=20 if since else 180)
        if not since:
            msgs = [m for m in msgs if time.time() - msg_ts(m) <= CODE_VALID_S]
        if msgs:
            m = msgs[0]
            age = time.time() - msg_ts(m)
            if since or age <= 90 or accept_old or time.time() >= fresh_until:
                code = _code_in(m)
                if code:
                    return code
        if time.time() >= deadline:
            return None
        time.sleep(4)


def alphanumeric_code(text: str) -> str | None:
    """A code made of capital letters AND digits (Toast: "Here is the code required to complete your form ... JYH18").
    Only used when the mail talks about a code and holds no all-digit code. At least TWO capital letters and one digit, so a requisition
    number like R163902 or a year never passes for a code."""
    if not re.search(r"\b(?:code|passcode|otp|verif\w*)\b", text or "", re.I):
        return None
    for tok in re.findall(r"\b([A-Z0-9]{5,8})\b", text or ""):
        if sum(c.isalpha() for c in tok) >= 2 and any(c.isdigit() for c in tok):
            return tok
    return None


def _usable_link(m: dict, allowed: tuple) -> str | None:
    """The best link in ONE message that belongs to this portal (`allowed` substrings), or None."""
    from urllib.parse import urlparse
    links = [h for h in m.get("links", []) if h.startswith("http") and not re.search(r"(^|\.)(google\.com|gmail\.com|googleusercontent\.com)$", urlparse(h).netloc)
             and not re.search(r"unsubscribe|preferences|privacy", urlparse(h).path, re.I) and (not allowed or any(a in h.lower() for a in allowed))]
    ranked = sorted(links, key=lambda h: (not re.search(r"passwordreset|reset|verif|activate|confirm|token|key=", h, re.I), len(h)))
    return ranked[0] if ranked else None


def verification_link(sender_hint: str, wait_s: int = 120, allowed: tuple = (), since: float | None = None,
                      want: str = "") -> str | None:
    """The newest mail from `sender_hint` (after `since`) that holds a link for THIS portal. `want` is an optional subject pattern ('reset' or
    'verif') so a reset request never opens yesterday's account-verification mail, and vice versa.

    this used to take the single newest mail and only THEN filter its links by portal. With two portals' reset mails in the
    inbox (companies) the newest one belonged to the wrong portal, no link survived, and the right mail was never
    looked at. Now every recent mail is tried, newest first."""
    deadline = time.time() + wait_s
    while True:
        for m in _recent(search(f"newer_than:1d {sender_hint}", limit=10), since, ((want,) if want else ())):
            link = _usable_link(m, allowed)
            if link:
                return link
        if time.time() >= deadline:
            return None
        time.sleep(6)
