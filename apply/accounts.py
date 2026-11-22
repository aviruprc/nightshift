"""Career-site accounts. Passwords live in Windows Credential Manager (keyring), keyed by site host, mirrored in data/accounts.json (base64)
as a backup on the user's own machine (her choice), so a password is never lost and no site needs a reset twice. Never in logs or the DB."""
import base64
import json
import re
from pathlib import Path
import secrets
import string
from datetime import datetime, timedelta

import keyring

from core import db as dbm

SERVICE = "nightshift-sites"
MIRROR = Path(__file__).resolve().parent.parent / "data" / "accounts.json"


def _mirror_read() -> dict:
    try:
        return json.loads(MIRROR.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def mirror_enabled() -> bool:
    """data/settings.json accounts_mirror (default false): also keep passwords base64-encoded in data/accounts.json.
    base64 is NOT encryption, so the plain-file backup is off unless you turn it on."""
    try:
        from core import profile
        return bool(profile.settings().get("accounts_mirror", False))
    except Exception:  # noqa: BLE001
        return False


def _mirror_write(host: str, password: str | None):
    if password is not None and not mirror_enabled():
        return   # removals always apply; new copies only when the mirror is switched on
    m = _mirror_read()
    if password is None:
        m.pop(host, None)
    else:
        prev = m.get(host) or {}
        hist = [x for x in ([prev.get("password_b64")] if prev.get("password_b64") else []) + list(prev.get("history") or [])
                if x and x != base64.b64encode(password.encode()).decode()][:4]   # the last 4 passwords stay available: a reset that never took effect must not lose the one that worked
        m[host] = {"email": EMAIL, "password_b64": base64.b64encode(password.encode()).decode(), "saved": datetime.now().isoformat(timespec="seconds"), "history": hist}
    MIRROR.write_text(json.dumps(m, indent=1), encoding="utf-8")
# The login email for every portal account: cv/facts.yaml personal.email (or the JOBHUNT_EMAIL environment variable).
try:
    from core import profile as _profile
    EMAIL = _profile.email()
except Exception:  # noqa: BLE001 (no facts.yaml yet: `python hunt.py doctor` says so)
    EMAIL = ""


# One company = ONE account: a company's public careers site and its Workday tenant share the same login, so they must share one saved password and one
# "verified" flag. (a company had two hosts with two different saved passwords, so every job looked like an unknown account and triggered another reset.)
ALIASES = {"wellsfargojobs.com": "wf.wd1.myworkdayjobs.com", "careers.wellsfargo.com": "wf.wd1.myworkdayjobs.com"}
LEDGER = Path(__file__).resolve().parent.parent / "data" / "reset_ledger.json"
# A reset/verification link can live on a DIFFERENT web address than the portal the bot browses (a company: the page is
# wf.wd1.myworkdayjobs.com, the reset mail links to wd1.myworkdaysite.com/recruiting/wf/WellsFargoJobs/passwordreset/ and the host filter dropped it).
LINK_ALIASES = {"wf.wd1.myworkdayjobs.com": ("wd1.myworkdaysite.com/recruiting/wf/",)}


def host_key(url_or_host: str) -> str:
    from urllib.parse import urlparse
    h = (urlparse(url_or_host).netloc or url_or_host).lower().removeprefix("www.")
    return ALIASES.get(h, h)


def record_reset(host: str):
    """Every password reset is written down: a reset is the LAST resort, never a routine step."""
    try:
        led = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
        led.setdefault(host_key(host), []).append(datetime.now().isoformat(timespec="seconds"))
        LEDGER.write_text(json.dumps(led, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def reset_used(host: str, days: int = 30, minutes: float | None = None) -> bool:
    """True when the LEDGER has a reset for this host inside the window (ledger only: unlike reset_allowed it ignores the verified flag).
    a company: a verified host held a password the site rejected, so reset_allowed said 'never' and every job failed forever."""
    try:
        times = json.loads(LEDGER.read_text(encoding="utf-8")).get(host_key(host), []) if LEDGER.exists() else []
    except Exception:  # noqa: BLE001
        times = []
    if minutes is not None:
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    else:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    return any(t >= cutoff for t in times)


SECOND_RESET_AFTER_H = 48


def reset_allowed(host: str, days: int = 30) -> bool:
    """A reset is allowed only when we have NO working login (not verified) and none was done in the last `days` days (portals allow one; a second one is a bug)."""
    if is_verified(host):
        return False
    # 13 portals had ONE reset in the ledger and still no
    # working login - the reset mail was never found (the mail search starved its matching hint), so the reset bought nothing and every job there was
    # dead for 30 days. With the mail search fixed, a reset that did not lead to a working login may be repeated ONCE, not sooner than 48 hours later.
    # Never more than two in the window, never while a login works: this is still not a routine step.
    try:
        times = json.loads(LEDGER.read_text(encoding="utf-8")).get(host_key(host), []) if LEDGER.exists() else []
    except Exception:  # noqa: BLE001
        times = []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    recent = sorted(t for t in times if t >= cutoff)
    if not recent:
        return True
    if len(recent) >= 2:
        return False
    return recent[-1] <= (datetime.now() - timedelta(hours=SECOND_RESET_AFTER_H)).isoformat(timespec="seconds")


def generate_password() -> str:
    """Meets typical Workday/Oracle/SuccessFactors rules: 14 chars, upper, lower, digit, symbol, no look-alike or ambiguous characters."""
    sets = [string.ascii_uppercase.replace("O", "").replace("I", ""), string.ascii_lowercase.replace("l", "").replace("o", ""),
            "23456789", "@#$%"]  # only the symbols every portal accepts (a company rejected '=+?-' style symbols as 'no special character')
    chars = [secrets.choice(s) for s in sets]
    pool = "".join(sets)
    chars += [secrets.choice(pool) for _ in range(10)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def get(host: str):
    h = host_key(host)
    try:
        pw = keyring.get_password(SERVICE, h)
    except Exception:  # noqa: BLE001
        pw = None
    if pw:
        return pw
    e = _mirror_read().get(h)
    return base64.b64decode(e["password_b64"]).decode() if e else None



def candidate_passwords(host: str, limit: int = 3) -> list[str]:
    """Passwords worth ONE careful try on this portal, most likely first: the saved one, its recorded predecessors, then the saved passwords of other hosts of the
    same company. Never more than `limit`."""
    h = host_key(host)
    out: list[str] = []
    cur = get(h)
    if cur:
        out.append(cur)
    m = _mirror_read()
    for b in (m.get(h) or {}).get("history", []):
        try:
            pw = base64.b64decode(b).decode()
        except Exception:  # noqa: BLE001
            continue
        if pw and pw not in out:
            out.append(pw)
    token = next((x for x in re.findall(r"[a-z]{4,}", h) if x not in ("jobs", "careers", "career", "myworkdayjobs", "myworkdaysite", "workday", "oraclecloud", "successfactors")), "")
    if token:
        for k, v in m.items():
            if k != h and token in k:
                try:
                    pw = base64.b64decode(v.get("password_b64", "")).decode()
                except Exception:  # noqa: BLE001
                    continue
                if pw and pw not in out:
                    out.append(pw)
    return out[:limit]

def save(host: str, password: str, proven: bool = False) -> bool:
    """Store a portal password. An EXISTING entry is replaced only when `proven` - a sign-in actually worked, an account creation was
    confirmed, or the password comes from the reset we just completed (ledger entry minutes old, which is how the Workday reset flow saves).

    a company: the bot replaced the working password with the guess it had just generated after a policy-rejection, and every later job
    failed with 'wrong password' against a host still marked verified. An unproven password now stays in memory for THIS attempt only.
    Returns True when the password was stored."""
    h = host_key(host)
    if h.endswith(".invalid"):  # test fixtures never touch the real store
        return False
    if get(h) is not None and not proven and not reset_used(h, minutes=15):
        return False
    try:
        keyring.set_password(SERVICE, h, password)
    except Exception:  # noqa: BLE001
        pass
    _mirror_write(h, password)
    d = dbm.DB()
    d.c.execute("INSERT OR REPLACE INTO accounts(site,username,created_at,verified) VALUES(?,?,?,COALESCE((SELECT verified FROM accounts WHERE site=?),0))",
                (h, EMAIL, datetime.now().isoformat(timespec="seconds"), h))
    d.c.commit()
    return True


def is_verified(host: str) -> bool:
    """True once a sign-in or submission actually worked on this host (a saved password alone only proves we TRIED to register)."""
    d = dbm.DB()
    r = d.c.execute("SELECT verified FROM accounts WHERE site=?", (host_key(host),)).fetchone()
    return bool(r and r[0])


def mark_verified(host: str):
    d = dbm.DB()
    d.c.execute("UPDATE accounts SET verified=1 WHERE site=?", (host_key(host),))
    d.c.commit()


def forget(host: str):
    try:
        keyring.delete_password(SERVICE, host_key(host))
    except Exception:  # noqa: BLE001
        pass
    if not host_key(host).endswith(".invalid"):
        _mirror_write(host_key(host), None)
