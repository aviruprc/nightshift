"""claude -p wrapper. Haiku for bulk scoring, Sonnet for writing. Returns text or parsed JSON."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

CLAUDE = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude.exe")
MODELS = {"score": "haiku", "write": "sonnet"}


class UsageLimit(Exception):
    pass


class LLMUnavailable(UsageLimit, RuntimeError):
    """Nothing could answer. The message NAMES every provider that was tried and its real reason
    ("gemini: HTTPError 429 daily quota, retry in 85 min; codex: plan limit resets; claude: rate_limit_error").
    ~50 jobs sat with "navigator: LLM unavailable (RuntimeError)" and nobody could tell which provider was tried or why —
    the old code re-raised whichever exception happened to be last, so the type name was all that survived.

    Subclasses UsageLimit because callers already pause cleanly on that (runner.prepare_queue, rank/score) and RuntimeError so
    existing callers/handlers that treated a hard LLM failure as a RuntimeError keep working."""

    def __init__(self, reasons: str = "", attempts: list | None = None):
        super().__init__(reasons or "no AI could answer")
        self.reasons = reasons
        self.attempts = list(attempts or [])


# ---- provider fallback: when Claude is out of usage, any OpenAI-compatible provider (DeepSeek, OpenAI, ...) configured in data/llm_providers.json
# takes over. Keys live in Windows Credential Manager (service 'nightshift-llm'), never in that file. Claude is probed again after the cool-down.
DATA = Path(__file__).resolve().parent.parent / "data"
DOWN_FILE = DATA / "claude_down.json"
CLAUDE_COOLDOWN_S = 900
USAGE_FILE = DATA / "llm_usage.jsonl"


def _env_key(name: str) -> str:
    """JOBHUNT_KEY_<NAME> (provider name upper-cased, non-letters -> '_'): for machines without an OS credential store."""
    import re as _re
    return os.environ.get("JOBHUNT_KEY_" + _re.sub(r"[^A-Z0-9]+", "_", str(name).upper()).strip("_"), "")


def get_key(name: str) -> str | None:
    """The API key for a provider: the OS credential store (service 'nightshift-llm') first, then the environment."""
    try:
        import keyring
        k = keyring.get_password("nightshift-llm", name)
    except Exception:  # noqa: BLE001
        k = None
    return k or _env_key(name) or None


def _has_key(name: str) -> bool:
    """A provider without a key is silently dropped from the chain, so a transient Credential Manager error must not look like 'no key':
    one retry, then give up (: 'no AI is configured' is exactly the kind of message that must never be wrong)."""
    if _env_key(name):
        return True
    for attempt in range(2):
        try:
            import keyring
            return bool(keyring.get_password("nightshift-llm", name))
        except Exception:  # noqa: BLE001
            if attempt:
                return False
            time.sleep(0.2)
    return False


COOLDOWN_FILE = DATA / "provider_cooldown.json"


def key_name(p: dict) -> str:
    return p.get("key") or p["name"]


def cooldown_reason(name: str):
    """(reason, until) if this provider is resting after a quota / credit / auth problem, else None."""
    try:
        e = json.loads(COOLDOWN_FILE.read_text(encoding="utf-8")).get(name)
        return (e["reason"], e["until"]) if e and e["until"] > time.time() else None
    except Exception:  # noqa: BLE001
        return None


def _read_cooldowns() -> dict:
    try:
        data = json.loads(COOLDOWN_FILE.read_text(encoding="utf-8")) if COOLDOWN_FILE.exists() else {}
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write_cooldowns(data: dict):
    """Atomic (temp + replace): two workers set cooldowns at the same time and a half-written file used to silence a provider."""
    try:
        tmp = COOLDOWN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(COOLDOWN_FILE)
    except Exception:  # noqa: BLE001
        try:  # last resort: the old non-atomic write (readable file beats no file)
            COOLDOWN_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass


def _rest(name: str, reason: str, secs: float, escalate: bool = False, cap: float = 3600.0):
    """Write a rest entry. `escalate` = the limit's reset time is unknown, so back off 15 -> 30 -> 60 min (NEVER a day, never
    permanent: the user 'I hope all the llms keep checking when the limit is reset and come back online by themselves')."""
    prev = _read_cooldowns().get(name) or {}
    streak = int(prev.get("streak") or 0) + 1
    secs = max(30.0, float(secs))
    if escalate:
        secs = min(secs * (2 ** max(0, streak - 1)), cap)
    data = _read_cooldowns()
    data[name] = {"reason": reason, "until": time.time() + secs, "at": time.strftime("%H:%M:%S"),
                  "streak": streak, "secs": int(secs)}
    _write_cooldowns(data)


def _exc_body(exc: Exception) -> str:
    """The provider's own words (Google/DeepSeek send a JSON body naming the quota and a retryDelay). Never a secret: it is an error body."""
    body = getattr(exc, "body", None)
    if isinstance(body, str) and body:
        return body[:4000]
    try:
        raw = exc.read()  # urllib.error.HTTPError
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")[:4000]
    except Exception:  # noqa: BLE001
        pass
    return str(exc)[:1000]


def _exc_code(exc: Exception):
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    m = re.search(r"\b(4\d\d|5\d\d)\b", f"{exc} {_exc_body(exc)}")
    return int(m.group(1)) if m else None


def _retry_delay(body: str):
    """Google's 429 body carries 'retryDelay': '31s' (and a bucket with retry_delay.seconds)."""
    for rx in (r'"retryDelay"\s*:\s*"([0-9.]+)s"', r'"seconds"\s*:\s*(\d+)'):
        m = re.search(rx, body)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


def _next_pacific_midnight() -> float:
    """Google's free-tier DAILY quotas reset at midnight Pacific (not at midnight IST)."""
    import datetime as _dt
    pst = _dt.timezone(_dt.timedelta(hours=-8))
    now_utc = _dt.datetime.now(_dt.timezone.utc)
    p = now_utc.astimezone(pst)
    nxt = p.replace(hour=0, minute=0, second=0, microsecond=0) + _dt.timedelta(days=1)
    return nxt.timestamp()


RESET_AT_RX = re.compile(
    r"(?:try again|resets?|available again)\s*(?:at|on|in)?\s*"
    r"([A-Z][a-z]{2,8}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}\s+\d{1,2}:\d{2}\s*(?:[AP]\.?M\.?)?)", re.I)


def _reset_at_from_text(txt: str):
    """Codex prints its plan limit as prose: '... you've hit your limit ... or try again at Dec 4th, 2026 6:31 PM'."""
    m = RESET_AT_RX.search(txt)
    if not m:
        return None
    s = re.sub(r"(\d)(st|nd|rd|th)", r"\1", m.group(1)).replace(".", "")
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%b %d %Y %I:%M %p", "%B %d %Y %I:%M %p",
                "%b %d, %Y", "%B %d, %Y"):
        try:
            return time.mktime(time.strptime(s, fmt))
        except ValueError:
            continue
    return None


LIMIT_RX = re.compile(
    r"usage[_ -]?limit|rate[_ -]?limit|limit (?:has been )?(?:reached|exceeded)|hit your limit|you(?:'ve| have) (?:reached|hit)"
    r"|too many requests|\b429\b|\b529\b|exceeded your (?:current )?quota|quota (?:exceeded|reached)|overloaded"
    r"|credit balance is too low|insufficient (?:credit|balance|quota)|out of (?:extra )?usage", re.I)

BALANCE_RX = re.compile(r"insufficient balance|out of credit|no credit|credit balance is too low|billing|payment required|\b402\b", re.I)
AUTH_RX = re.compile(r"invalid api key|invalid_api_key|unauthori[sz]ed|forbidden|authentication|\b401\b|\b403\b", re.I)


def classify_limit(exc: Exception) -> tuple[str, float, bool]:
    """(human reason, seconds to rest, escalate) keyed to the REAL limit type, not just the HTTP code.
    `escalate` = the reset time is unknown, so the rest must grow (15 -> 30 -> 60 min) instead of guessing a long one.

    any Gemini 429 was written off as 'daily free quota (2h)' although the earlier one was a per-minute limit, and a Codex
    'plan usage' limit (resets Dec 4) was rested for 20 minutes — so the chain kept hammering a provider that could not work for weeks."""
    body = _exc_body(exc)
    txt = f"{type(exc).__name__} {exc} {body}"
    code = _exc_code(exc)
    low = txt.lower()
    retry_s = _retry_delay(body)

    if code == 402 or (BALANCE_RX.search(txt) and code in (400, None)):
        return "out of credits: recharge the account or replace the key", 3600, False
    if code in (401, 403) or (code == 400 and AUTH_RX.search(txt)):
        return f"key or permission problem (HTTP {code}): replace the key", 3600, False
    if code in (500, 502, 503, 504) or re.search(r"service unavailable|bad gateway|gateway timeout|internal server error", low):
        return f"provider outage (HTTP {code or '5xx'}): temporary, retrying soon", 300, False

    reset_at = _reset_at_from_text(txt)  # Codex prints '... or try again at Dec 4th, 2026 6:31 PM'
    is_limit = code in (429, 529) or bool(reset_at) or bool(LIMIT_RX.search(txt))
    if is_limit:
        name_is_gemini = "gemini" in (getattr(exc, "provider", "") or "")
        quotas = getattr(exc, "quotas", None)  # attached by _ask_provider for gemini: per model, code, body
        if not quotas and body:
            quotas = [("", code, body)]
        if quotas:
            bodies = [b.lower() for (_m, _c, b) in quotas]
            per_day = any("perday" in b or "per day" in b or "daily" in b for b in bodies)
            per_min = any("perminute" in b or "per minute" in b for b in bodies)
            if per_day and not per_min:
                if name_is_gemini:
                    # Google's own 'retryDelay' is the time until the bucket refills (live: 41502s); fall back to the
                    # daily boundary at midnight Pacific only when it is missing.
                    until = (time.time() + retry_s) if (retry_s and retry_s > 300) else _next_pacific_midnight()
                    secs = max(600.0, min(until - time.time(), 86400.0))
                    when = time.strftime("%d %b %H:%M", time.localtime(time.time() + secs))
                    return f"daily free quota used up (Google resets it at {when} IST)", secs, False
                secs = retry_s or 12 * 3600  # another provider's daily bucket: we do not know its reset hour, so rest long
                return f"daily quota used up (retrying in about {max(1, int(secs / 3600))}h)", secs, False
            if per_min:
                secs = retry_s or 90.0
                return f"per-minute rate limit (retry in {int(secs)}s)", secs, False
        until = reset_at
        if until and until > time.time():
            secs = min(until - time.time(), 40 * 86400)  # honour the date it names; a guard only against a nonsense parse
            when = time.strftime("%d %b %H:%M", time.localtime(until))
            return f"plan usage limit: the provider says it resets {when}", secs, False
        # An unnamed 429 (no body / no bucket): the reset time is UNKNOWN, so rest 15 min and escalate 30 -> 60 (capped) on repeat
        # probes. Naming the bucket is what makes a daily quota rest for hours and a per-minute limit rest for seconds.
        who = "Google did not name which quota" if name_is_gemini else "the provider did not name the limit"
        return f"rate limit / quota (HTTP {code or '429'}; {who}): retrying with backoff", 900, True

    if retry_s and retry_s > 0:  # a provider that says 'retry in Ns' is not broken
        return f"asked us to retry in {int(retry_s)}s", retry_s, False

    if re.search(r"timed? ?out|timeout|urlopen error|connection (?:reset|refused|aborted)|temporary failure|ssl|network", low):
        return f"temporary network/timeout problem ({type(exc).__name__})", 180, False
    if re.search(r"no api key|signed out|needslogin|browserbusy", low):
        return f"{type(exc).__name__}: {str(exc)[:120]}", 900, False
    return f"{type(exc).__name__}: {str(exc)[:120]}", 180, False


def set_cooldown(name: str, exc: Exception):
    """Rest a provider for the time that matches its REAL limit type (per-minute vs daily quota vs balance vs plan reset vs
    transient glitch). The reason is shown on the dashboard and returned in 'no AI could answer' messages."""
    try:
        getattr(exc, "provider", None) or setattr(exc, "provider", name)
    except Exception:  # noqa: BLE001
        pass
    reason, secs, escalate = classify_limit(exc)
    _rest(name, reason, secs, escalate=escalate)
    start_prober()  # a resting provider must be re-tested by itself when its time is up (never needs the user)


# ---- self-healing: a provider that is resting comes back on its own -------------------------------------------------------------
# Two independent paths, both required ("I hope all the llms keep on checking when limit is reset and comes back
# online themselves without me interfering"):
#   (a) LAZY: providers already ignores an entry whose 'until' has passed, so the very next call asks it again — no clear_cooldown
#       is ever needed. A normal call is therefore always the fastest way back in.
#   (b) ACTIVE: a daemon thread that exists ONLY while some provider is resting sleeps until the earliest retry time, sends one tiny
#       probe to that provider, clears its rest on success and re-rests it (with capped backoff) on failure. It never holds a lock
#       that a job needs, so it can never be the reason a job waits.
PROBE_FILE = DATA / "provider_probe.json"
PROBE_TIMEOUT = 45
PROBE_PROMPT = 'Reply with exactly this JSON and nothing else: {"ok": true}'
_probe_thread = None
_probe_lock = threading.Lock()


def probe_log() -> dict:
    """When each provider was last re-tested by the prober (epoch 'at' + human 'when' + result). The dashboard shows it."""
    try:
        d = json.loads(PROBE_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _probe_record(name: str, ok: bool, detail: str = ""):
    data = probe_log()
    data[name] = {"at": time.time(), "ok": bool(ok), "detail": (detail or "")[:200],
                  "when": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        tmp = PROBE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(PROBE_FILE)
    except Exception:  # noqa: BLE001
        pass


def due_probes(now: float | None = None) -> list[dict]:
    """PURE decision (no I/O beyond reading the two JSON files): providers whose rest time has passed and must be re-tested."""
    now = time.time() if now is None else now
    cool = _read_cooldowns()
    out = []
    for p in all_providers():
        if not p.get("enabled", True):
            continue
        e = cool.get(p.get("name"))
        if not e:
            continue
        try:
            if float(e.get("until") or 0) > now:
                continue
        except (TypeError, ValueError):
            pass  # an unreadable entry must never keep a provider out of rotation
        out.append(p)
    return out


def probe_due(now: float | None = None) -> list[str]:
    """One shot: re-test every provider whose retry time has arrived. Success clears the rest, failure classifies a new (capped) one.
    Returns the names that came back. Costs one tiny call per provider and touches no lock a job needs."""
    back = []
    for p in due_probes(now):
        name = p.get("name") or "?"
        try:
            if p.get("type") == "browser":  # a chat site: only its SESSION can be checked quietly (no prompt, no history)
                from core import llm_browser
                if llm_browser.check_session(p):
                    clear_cooldown(name)
                    _probe_record(name, True, "session valid")
                    back.append(name)
                else:
                    _rest(name, "signed out: press Sign in on the dashboard (DeepSeek blocks automated logins)", 900)
                    _probe_record(name, False, "signed out")
            else:
                _ask_provider(PROBE_PROMPT, p, "score", PROBE_TIMEOUT)
                clear_cooldown(name)
                _probe_record(name, True, "answered")
                back.append(name)
            _say(f"AI prober: {name} is back in rotation (it answered by itself, no action needed)")
        except Exception as e:  # noqa: BLE001
            set_cooldown(name, e)  # re-rest with the real reason (and capped backoff when the reset time is unknown)
            _probe_record(name, False, f"{type(e).__name__}: {str(e)[:120]}")
            cd = cooldown_reason(name)
            _say(f"AI prober: {name} is still unavailable ({(cd[0] if cd else str(e))[:90]}); it will be re-tested automatically")
    return back


def _say(msg: str):
    try:
        from core import activity
        activity.log("info", msg)
    except Exception:  # noqa: BLE001
        pass


def _probe_loop():
    while True:
        cool = _read_cooldowns()
        if not cool:
            return  # nothing resting: the thread ends (the next set_cooldown/ask/status starts a new one)
        now = time.time()
        try:
            untils = [float(e.get("until") or 0) for e in cool.values()]
        except (TypeError, ValueError):
            untils = [now]
        wait = min(untils) - now
        if wait > 0:
            time.sleep(min(wait + 1, 60))  # wake at the earliest retry time, at most once a minute
            continue
        try:
            probe_due()
        except Exception:  # noqa: BLE001 (a probe must never kill the thread)
            time.sleep(30)


def start_prober():
    """Start the background prober once, if anything is resting. No-op while JOBHUNT_NO_LLM is set (tests must never dial out)."""
    global _probe_thread
    if os.environ.get("JOBHUNT_NO_LLM"):
        return None
    if not _read_cooldowns():
        return None
    with _probe_lock:
        if _probe_thread is not None and _probe_thread.is_alive():
            return _probe_thread
        _probe_thread = threading.Thread(target=_probe_loop, name="llm-prober", daemon=True)
        _probe_thread.start()
        return _probe_thread


def providers() -> list[dict]:
    """Enabled providers; an API provider (type 'openai') only counts once its key is stored (`python hunt.py llm-key <name>`)."""
    try:
        return [p for p in json.loads((DATA / "llm_providers.json").read_text(encoding="utf-8"))
                if p.get("enabled", True) and (p.get("type") not in ("openai", "gemini") or _has_key(key_name(p))) and not cooldown_reason(p["name"])]
    except Exception:  # noqa: BLE001
        return []


PROV_FILE_NAME = "llm_providers.json"


def all_providers() -> list[dict]:
    try:
        d = json.loads((DATA / PROV_FILE_NAME).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:  # noqa: BLE001
        return []


def save_providers(lst: list[dict]):
    """Atomic write (temp file + replace) and refuse anything that is not a list of named providers: a half-written config would silence every AI."""
    if not isinstance(lst, list) or not all(isinstance(p, dict) and p.get("name") for p in lst):
        raise ValueError("invalid provider list")
    import os
    tmp = DATA / (PROV_FILE_NAME + ".tmp")
    tmp.write_text(json.dumps(lst, indent=1), encoding="utf-8")
    os.replace(tmp, DATA / PROV_FILE_NAME)


def set_enabled(name: str, on: bool):
    lst = all_providers()
    for p in lst:
        if p["name"] == name:
            p["enabled"] = bool(on)
    save_providers(lst)


def move(name: str, delta: int):
    """Priority = position in the file. delta -1 = earlier (asked first), +1 = later."""
    lst = all_providers()
    i = next((k for k, p in enumerate(lst) if p["name"] == name), None)
    if i is None:
        return
    j = max(0, min(len(lst) - 1, i + delta))
    lst.insert(j, lst.pop(i))
    save_providers(lst)


def clear_cooldown(name: str):
    """A provider that answered is healthy again: drop its rest entry (and its failure streak) immediately."""
    try:
        data = _read_cooldowns()
        data.pop(name, None)
        _write_cooldowns(data)
    except Exception:  # noqa: BLE001
        pass


def status() -> dict:
    """Honest picture for the dashboard: per provider ready / resting (why + seconds to retry) / no key / off, when it was last
    probed, plus whether ANY AI can answer right now. An entry whose retry time has passed is NEVER reported as resting."""
    start_prober()  # the dashboard polls this every 2 s: it is what keeps the background prober alive (no-op when nothing is resting)
    rows = []
    probes = probe_log()
    now = time.time()
    for p in all_providers():
        needs_key = p.get("type") in ("openai", "gemini")
        cd = cooldown_reason(p["name"])
        if not p.get("enabled", True):
            state, why = "off", "switched off"
        elif needs_key and not _has_key(key_name(p)):
            state, why = "nokey", "no API key yet"
        elif cd:
            left = max(0, int(cd[1] - now))
            state, why = "resting", f"{cd[0]} (retry in {max(1, left // 60)} min)"
        else:
            state, why = "ready", ""
        pb = probes.get(p["name"]) or {}
        rows.append({"name": p["name"], "model": p.get("model", ""), "type": p.get("type", ""), "state": state, "why": why,
                     "retry_in": max(0, int(cd[1] - now)) if cd else 0,
                     "last_probe": pb.get("when", ""), "last_probe_ok": pb.get("ok")})
    ready = [r["name"] for r in rows if r["state"] == "ready"]
    claude_ok = claude_for_bot() and not claude_is_down()
    return {"providers": rows, "ready": ready, "claude_ok": claude_ok, "any": bool(ready) or claude_ok, "claude_reserved": not claude_for_bot()}


def claude_for_bot() -> bool:
    """settings.json {"claude_for_bot": false}: the user's Claude plan is reserved; the bot must not spend it (jobs that need an AI wait for another provider)."""
    try:
        return bool(json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("claude_for_bot", True))
    except Exception:  # noqa: BLE001
        return True


def claude_is_down() -> bool:
    try:
        return time.time() < json.loads(DOWN_FILE.read_text(encoding="utf-8")).get("until", 0)
    except Exception:  # noqa: BLE001
        return False


def claude_down_reason() -> str:
    try:
        return str(json.loads(DOWN_FILE.read_text(encoding="utf-8")).get("reason") or "")
    except Exception:  # noqa: BLE001
        return ""


def mark_claude_down(reason: str = ""):
    """Rest Claude for 15 min after a hard failure, and RECORD WHY (the dashboard/incident report used to say only 'RuntimeError')."""
    try:
        DOWN_FILE.write_text(json.dumps({"until": time.time() + CLAUDE_COOLDOWN_S, "at": time.strftime("%H:%M:%S"),
                                         "reason": (reason or "")[:200]}), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _ask_provider(prompt: str, prov: dict, task: str, timeout: int) -> str:
    """One OpenAI-compatible chat completion (works for DeepSeek, OpenAI, Groq, OpenRouter, ...), or a logged-in chat website (type 'browser')."""
    if prov.get("type") == "browser":
        # The caller (an applier worker) is ALREADY inside a sync Playwright session on this thread: a second sync_playwright here raises
        # "Playwright Sync API inside the asyncio loop". Run the backup-AI browser on its own thread (own event loop).
        from concurrent.futures import ThreadPoolExecutor

        from core import llm_browser
        with ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(llm_browser.ask, prompt, prov, timeout).result(timeout=timeout + 60)
    if prov.get("type") == "codex":  # the installed Codex CLI, signed in with her ChatGPT account (no API key); read-only sandbox, no files touched
        import tempfile
        exe = prov.get("path") or str(Path.home() / ".codex" / "packages" / "standalone" / "current" / "bin" / "codex.exe")
        import os
        fd, name = tempfile.mkstemp(suffix=".txt")
        os.close(fd)  # (an open handle made Windows refuse codex's write)
        out = Path(name)
        try:
            extra = (["-m", prov["codex_model"]] if prov.get("codex_model") else []) + (["-c", f'model_reasoning_effort="{prov["effort"]}"'] if prov.get("effort") else [])
            # (data/llm_providers.json: "codex_model": "<name>" and/or "effort": "low|medium|high" on the codex entry switch the model without code changes)
            p = subprocess.run([exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", *extra, "-o", str(out), "-"],
                               input=prompt, capture_output=True, text=True, timeout=timeout, encoding="utf-8")
            txt = out.read_text(encoding="utf-8").strip() if out.exists() else ""
            if p.returncode != 0 or not txt:
                # keep enough of the CLI's own words: 'try again at Dec 4th, 2026 6:31 PM' sits at the END of the message and is what
                # tells us this is a plan limit for weeks, not a 20-minute quota
                err = ((p.stderr or "") + " " + (p.stdout or ""))[-600:].strip()
                raise RuntimeError(f"codex failed: {err}")
            return txt
        finally:
            out.unlink(missing_ok=True)
    import urllib.request

    key = get_key(key_name(prov))
    if not key:
        raise RuntimeError(f"no API key stored for provider {prov['name']}")
    model = (prov.get("models") or {}).get(task) or prov["model"]
    if prov.get("type") == "gemini":  # Google's native API (the call the user tested with curl): models/<model>:generateContent, key in X-goog-api-key
        # The free tier gives each MODEL its own small daily quota (gemini-flash-latest: 20 requests/day; flash-lite: hundreds). When the main model says 429,
        # the same provider tries its "fallback_model" before it counts as out of quota (: Gemini looked dead for hours while lite was unused).
        last_exc = None
        quotas = []  # (model, http code, body): the body names WHICH quota ran out (PerMinute vs PerDay) and when it resets
        for mdl in [model] + ([prov["fallback_model"]] if prov.get("fallback_model") and prov["fallback_model"] != model else []):
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent",
                                         data=json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0}}).encode(),
                                         headers={"Content-Type": "application/json", "X-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return "".join(p.get("text", "") for p in json.loads(r.read().decode())["candidates"][0]["content"]["parts"]).strip()
            except urllib.error.HTTPError as e:
                last_exc = e
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:2000]
                except Exception:  # noqa: BLE001
                    pass
                quotas.append((mdl, e.code, body))
                # KB-64: only a 429 fell through to the fallback model, so a MODEL-SPECIFIC outage wrote the
                # whole provider off. Live evidence: gemini-flash-latest answered 503 "This model is currently experiencing high
                # demand" while gemini-flash-lite-latest answered "ok" in 1.7 s - and `set_cooldown` then rested gemini for 300 s
                # ("provider outage"), i.e. the applier ran without an AI for no reason. 5xx falls through too; a 4xx (bad key,
                # bad request) still fails fast because retrying the same key with another model cannot fix it.
                if e.code != 429 and e.code < 500:
                    raise
        try:  # evidence for set_cooldown (it must not guess per-minute vs daily)
            last_exc.quotas = quotas
            last_exc.body = quotas[-1][2] if quotas else ""
        except Exception:  # noqa: BLE001
            pass
        raise last_exc
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0}).encode()
    req = urllib.request.Request(prov["base_url"].rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": "jobhunt/1.0"})  # (Cloudflare in front of Groq blocks urllib's default agent with 403)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["choices"][0]["message"]["content"].strip()


def backup_first() -> bool:
    """settings.json {"llm_primary": "backup"}: spend the backup AI first and keep Claude as the fallback."""
    try:
        return json.loads((DATA / "settings.json").read_text(encoding="utf-8")).get("llm_primary") == "backup" and bool(providers())
    except Exception:  # noqa: BLE001
        return False


def _ask_with_fallback(prompt: str, task: str, model: str, timeout: int, retries: int,
                       exclude=None) -> tuple[str, str]:
    """Claude first (unless it is in its usage-limit cool-down), then each configured provider. Returns (text, who answered).
    With llm_primary=backup the order is reversed: providers first, Claude only if they all fail.

    `exclude`: names that already gave an unusable reply for THIS question (ask_json), so the next attempt asks someone else.
    When nothing answers it raises LLMUnavailable whose message lists every provider tried and its real reason — never the bare
    type of whichever exception happened to be last (that is what made ~50 jobs say 'LLM unavailable (RuntimeError)' with no diagnosis)."""
    import os
    force = bool(os.environ.get("JOBHUNT_FORCE_BACKUP"))
    skip = {e for e in (exclude or ()) if e}
    tried: list[tuple[str, str]] = []
    last = None
    claude_asked = False

    def try_providers():
        nonlocal last
        for prov in providers():
            if prov["name"] in skip:
                continue
            try:
                txt = _ask_provider(prompt, prov, task, min(timeout, 120))
                clear_cooldown(prov["name"])  # it ANSWERED: any leftover rest entry is stale (a stale cooldown is a silently dead provider)
                return txt, prov["name"]
            except Exception as e:  # noqa: BLE001
                last = e
                set_cooldown(prov["name"], e)
                cd = cooldown_reason(prov["name"])
                tried.append((prov["name"], f"{type(e).__name__}: {str(e)[:80]}" + (f" -> resting: {cd[0]}" if cd else "")))
                try:
                    from core import activity
                    # the old line said 'falling back to Claude' even when the next thing tried was ANOTHER provider (logs read
                    # as if Claude had answered every fallback). Say what really happens.
                    activity.log("info", f"{prov['name']} failed ({type(e).__name__}: {str(e)[:100]}): trying the next AI")
                except Exception:  # noqa: BLE001
                    pass
        return None

    if backup_first() and not force:
        got = try_providers()
        if got:
            return got
    if claude_for_bot() and not force and not claude_is_down() and model not in skip:
        claude_asked = True
        try:
            return _ask(prompt, model, timeout, retries), model
        except UsageLimit as e:
            # 'claude -p' kept failing with a limit message the old detector did not recognise (rate_limit_error / 429),
            # so it raised RuntimeError, no cool-down was written, and EVERY job paid 3 CLI round-trips and then failed.
            mark_claude_down(f"usage limit: {str(e)[:160]}")
            last = e
            tried.append((model, f"usage limit: {str(e)[:100]}"))
        except Exception as e:  # noqa: BLE001
            # Any hard CLI failure means Claude cannot answer right now: rest it for 15 min instead of retrying it for every job.
            mark_claude_down(f"{type(e).__name__}: {str(e)[:160]}")
            last = e
            tried.append((model, f"{type(e).__name__}: {str(e)[:100]}"))
    got = try_providers()
    if got:
        return got
    if not claude_asked:
        if not claude_for_bot():
            tried.append(("claude", "switched off for the bot (settings.json claude_for_bot=false)"))
        elif claude_is_down():
            tried.append(("claude", f"resting after a failure ({claude_down_reason() or 'cool-down'})"))
        elif model in skip:
            tried.append((model, "already gave an unusable reply"))
    if skip:
        tried.insert(0, ("skipped", ", ".join(sorted(skip)) + " (already gave an unusable reply for this same question)"))
    reasons = "; ".join(f"{n}: {r}" for n, r in tried) or (
        "no AI is configured (data/llm_providers.json has no enabled provider with a key, and Claude is reserved for the user)")
    try:
        from core import activity
        activity.log("error", f"no AI could answer -> {reasons[:400]}")
    except Exception:  # noqa: BLE001
        pass
    err = LLMUnavailable(reasons, tried)
    if last is not None:
        raise err from last
    raise err


_LAST_ANSWERER = {"who": ""}


def ask(prompt: str, task: str = "score", timeout: int = 240, retries: int = 2, purpose: str = "",
        exclude=None) -> str:
    """Public entry point. ask_json goes through HERE (not _ask_logged) so a test that patches llm.ask still intercepts it —
    the lesson: a seam that tests do not use is a seam that quietly dials a live provider."""
    txt, who = _ask_logged(prompt, task, timeout, retries, purpose, exclude=exclude)
    _LAST_ANSWERER["who"] = who
    return txt


def _ask_logged(prompt: str, task: str = "score", timeout: int = 240, retries: int = 2, purpose: str = "",
                exclude=None) -> tuple[str, str]:
    """ask plus WHO answered (ask_json needs the name so a model that returns garbage is not asked the same question again)."""
    from core import activity
    start_prober()  # any live LLM traffic also keeps the self-healing prober alive
    model = MODELS.get(task, task)
    t0 = time.time()
    ps = providers()
    first = (ps[0]["name"] if ps else model) if backup_first() else model  # who is asked FIRST (Claude only if the backup fails)
    activity.log("llm", f"{purpose or task} -> asking {first}", model=first, state="start")
    try:
        out, model = _ask_with_fallback(prompt, task, model, timeout, retries, exclude=exclude)
        activity.log("llm", f"{purpose or task}: {model} answered in {time.time() - t0:.1f}s", model=model, state="done",
                     secs=round(time.time() - t0, 1), tin=len(prompt) // 4, tout=len(out) // 4)  # rough token estimates (chars/4) for the Bills card
        try:  # one tiny line per call: the dashboard sums these into tokens used today / this month (estimates: chars / 4)
            with open(USAGE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": int(time.time()), "m": model, "i": len(prompt) // 4, "o": len(out) // 4, "p": (purpose or task)[:20]}) + "\n")
        except Exception:  # noqa: BLE001
            pass
        return out, model
    except Exception as e:
        # the reason, not just the type: this line is what an incident review reads
        activity.log("error", f"{purpose or task}: no AI answered ({type(e).__name__}: {str(e)[:220]})", model=model, state="fail")
        raise


def _ask(prompt: str, model: str, timeout: int, retries: int) -> str:
    if os.environ.get("JOBHUNT_NO_LLM"):
        # tests/conftest.py sets this: a test that forgets to patch the seam must FAIL, never quietly spend the user's Claude budget
        # (: a test called _ask_with_fallback unpatched and got a live reply from her Claude session)
        raise RuntimeError("JOBHUNT_NO_LLM is set: tests never run claude -p")
    last = ""
    for i in range(retries + 1):
        try:
            p = subprocess.run([CLAUDE, "-p", "--model", model, "--output-format", "text"],
                               input=prompt, capture_output=True, text=True, timeout=timeout, encoding="utf-8")
        except subprocess.TimeoutExpired:
            last = "timeout"
            continue
        out = (p.stdout or "").strip()
        # only the CLI's own short error message counts: a normal answer may quote page text that mentions a 'limit'.
        # /05: the old pattern missed the API's own 429 wording ("rate_limit_error"), so a real limit surfaced as
        # RuntimeError, no cool-down was written and every job retried a Claude that could not answer.
        err_text = (p.stderr or "") + (out if (p.returncode != 0 or len(out) < 300) and "{" not in out else "")
        if LIMIT_RX.search(err_text):
            raise UsageLimit((err_text or out)[:200])
        if p.returncode == 0 and out:
            return out
        last = (p.stderr or out)[:200]
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"claude -p failed: {last}")


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    body = m.group(1) if m else text
    start = min([i for i in (body.find("{"), body.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON in response")
    return json.loads(body[start:body.rfind("}" if body[start] == "{" else "]") + 1])


def ask_json(prompt: str, task: str = "score", purpose: str = "", **kw):
    """A malformed reply is a transient glitch. Retry stricter, and ask a DIFFERENT provider each time: one model that cannot produce
    JSON used to burn the whole job ("LLM unavailable (JSONDecodeError)") while three healthy providers sat idle ."""
    exclude = set(kw.pop("exclude", ()) or ())
    last = None
    who = ""
    attempts = 0
    try:
        for attempts in range(1, 4):
            _LAST_ANSWERER["who"] = ""
            txt = ask(prompt + "\n\nReturn ONLY valid JSON, no prose, no code fences."
                      + ("\nYour previous reply was not valid JSON. Output the JSON object only." if last else ""),
                      task, purpose=purpose, exclude=exclude, **kw)
            who = _LAST_ANSWERER.get("who") or ""
            try:
                return extract_json(txt)
            except ValueError as e:  # includes json.JSONDecodeError
                last = e
                if who:  # (a patched ask in tests reports no provider: then just retry)
                    exclude.add(who)
                try:
                    from core import activity
                    activity.log("info", f"{who} answered without usable JSON ({str(e)[:80]}): asking the next provider")
                except Exception:  # noqa: BLE001
                    pass
    except LLMUnavailable as e:  # every remaining provider is excluded or down: say why the replies were unusable too
        if exclude and last is not None:
            raise LLMUnavailable(f"no AI gave usable JSON (last reply from {who}: {str(last)[:100]}); {e}",
                                 [("json", f"{who}: {str(last)[:100]}")] + e.attempts) from e
        raise
    raise LLMUnavailable(f"{attempts} AI replies in a row were not usable JSON (last from {who}: {str(last)[:120]})",
                         [("json", f"{who}: {str(last)[:100]}")]) from last
