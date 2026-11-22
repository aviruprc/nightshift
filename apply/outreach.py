"""Startup outreach: find hiring email addresses that companies PUBLISH on their own site, and save an honest
application email for each as a Gmail DRAFT. Nothing is sent from here: she reviews the drafts (mode 'drafts'); sending happens ONLY
through send_drafted, which she must have asked for explicitly (: "go on and send these drafts"), and sends the exact
draft she could read in Gmail.

Rules: an address is only used if it appears literally on the company's own pages (mailto: or visible text) and we record the page it
came from. We never construct or guess an address. One email per company, ever. Facts come from cv/facts.yaml only."""
import email
import imaplib
import json
import re
import sqlite3
import smtplib
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "outreach.db"
try:
    from core import profile as _profile
    CV = _profile.base_cv_pdf()   # settings.json base_cv_pdf: your current CV PDF for outreach emails (see docs/SETUP.md)
except Exception:  # noqa: BLE001
    CV = ROOT / "data" / "resumes" / "_latest" / "resume.pdf"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
PATHS = ["", "/careers", "/careers/", "/jobs", "/join-us", "/contact", "/contact-us", "/about", "/about-us", "/company/careers"]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
HIRING = re.compile(r"^(?:.*(?:career|recruit|hiring|talent)s?.*|jobs?(?:[._-].*)?|hr(?:[._-].*)?|people|join|apply|work|resume|cv|ta)$", re.I)
BAD_TLD = re.compile(r"\.(png|jpg|jpeg|gif|svg|webp|css|js|woff2?)$", re.I)
DAILY_DRAFT_CAP = 15

# Data / analytics firms first. Domains are verified against the live
# site before anything is read from it; a domain whose homepage does not name the company is dropped.
ANALYTICS_SEEDS = [
    ("Mu Sigma", "mu-sigma.com"), ("Fractal Analytics", "fractal.ai"), ("LatentView Analytics", "latentview.com"),
    ("Tredence", "tredence.com"), ("Tiger Analytics", "tigeranalytics.com"), ("Course5 Intelligence", "course5i.com"),
    ("Sigmoid", "sigmoid.com"), ("Gramener", "gramener.com"), ("Affine", "affine.ai"), ("Quantiphi", "quantiphi.com"),
    ("Ugam", "ugam.com"), ("Evalueserve", "evalueserve.com"), ("Axtria", "axtria.com"), ("MathCo", "themathcompany.com"),
    ("Hevo Data", "hevodata.com"), ("Atlan", "atlan.com"), ("Datazymes", "datazymes.com"), ("Arcana Analytics", "arcanaanalytics.com"),
    ("Subex", "subex.com"), ("Crisil", "crisil.com"), ("Indium Software", "indiumsoftware.com"), ("Bridgei2i", "bridgei2i.com"),
    #  second batch: more Bengaluru-area data / analytics / BI firms; each domain is still verified on the live site
    ("Dataweave", "dataweave.com"), ("Zeotap", "zeotap.com"), ("Perceptive Analytics", "perceptive-analytics.com"), ("Ganit", "ganitinc.com"),
    ("Aays Analytics", "aays.com"), ("Kaleidofin", "kaleidofin.com"), ("Leena AI", "leena.ai"), ("Clootrack", "clootrack.com"),
    ("Mad Street Den", "madstreetden.com"), ("Fluent Grid", "fluentgrid.com"), ("Absolutdata", "absolutdata.com"), ("Manthan", "manthan.com"),
    ("Latent AI", "latentai.com"), ("InMobi", "inmobi.com"), ("Innovaccer", "innovaccer.com"), ("Pubmatic", "pubmatic.com"),
    ("Capillary Technologies", "capillarytech.com"), ("Wingify", "wingify.com"), ("Netradyne", "netradyne.com"), ("Yellow.ai", "yellow.ai"),
    ("Sisense", "sisense.com"), ("Zycus", "zycus.com"), ("Tookitaki", "tookitaki.com"), ("Fibe", "myfibe.com"),
]
EXTRA_TIERS = ("startup", "analytics", "analytics-bpo", "fintech", "midsize", "data-ratings", "fs-data")


def _db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS outreach (
        id INTEGER PRIMARY KEY, company TEXT, domain TEXT UNIQUE, email TEXT, kind TEXT, source_page TEXT,
        priority INTEGER, status TEXT, note TEXT, found_at TEXT, drafted_at TEXT, subject TEXT, draft_id TEXT)""")
    return c


def targets() -> list:
    """(company, domain|None, priority). Analytics firms are priority 0, other registry startups priority 1."""
    out, seen = [], set()
    for n, dom in ANALYTICS_SEEDS:
        out.append((n, dom, 0))
        seen.add(n.lower())
    import glob
    for f in glob.glob(str(ROOT / "discover" / "companies_*.yaml")):
        for c in (yaml.safe_load(open(f, encoding="utf-8")) or {}).get("companies", []) or []:
            n = (c.get("name") or "").strip()
            if n and n.lower() not in seen and c.get("tier") in EXTRA_TIERS:
                seen.add(n.lower())
                out.append((n, None, 1))
    return out


def _guess_domains(name: str) -> list:
    s = re.sub(r"[^a-z0-9]", "", name.lower().replace("&", "and"))
    return [f"{s}.com", f"{s}.ai", f"{s}.io", f"{s}.in", f"{s}.co"] if s else []


def _get(url: str, final: list | None = None) -> str | None:
    """Page text, or None. `final` (a list) receives the URL after redirects (c5i.ai for course5i.com)."""
    try:
        r = requests.get(url, headers=UA, timeout=10, allow_redirects=True)
        if r.status_code == 200 and "text" in (r.headers.get("content-type") or ""):
            if final is not None:
                final.append(r.url)
            return r.text[:600_000]
    except Exception:  # noqa: BLE001
        pass
    return None


def _token(name: str) -> str:
    """The distinctive word a site must contain for us to believe it is this company."""
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in {"the", "and", "inc", "ltd", "limited", "private", "pvt", "analytics", "data", "software", "india", "technologies", "labs"}]
    return max(words or re.findall(r"[a-z0-9]+", name.lower()) or [""], key=len)   # 'a company' -> 'sigma', never the 2-letter 'mu'


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def verify_domain(name: str, domain: str | None) -> tuple[str, str] | None:
    """(host, homepage_html) when the homepage plainly belongs to `name`; else None. A domain we supplied (curated list) is accepted when the
    company word is in the page <title> or repeated in the body (titles are often a tagline); a domain we only GUESSED needs the word in the
    <title>. A host match proves nothing: a guess contains the name by construction."""
    tok = _token(name)
    for dom in ([domain] if domain else _guess_domains(name)):
        fin: list = []
        html = _get(f"https://{dom}", fin)
        if not html or not tok:
            continue
        host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", fin[0] if fin else dom).split("/")[0])
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        in_title = bool(m and tok in _norm(m.group(1)))
        in_body = _norm(html[:200_000]).count(tok) >= 3
        if in_title or (domain and in_body):
            return host, html
    return None


def find_emails(domain: str, home_html: str) -> list:
    """[(email, kind, source_url)] for addresses literally printed on the company's own pages. kind: 'hiring' or 'general'."""
    found, seen = [], set()
    base = f"https://{domain}"
    root = ".".join(domain.split(".")[-2:])
    for p in PATHS:
        url = base + p
        html = home_html if p == "" else _get(url)
        if not html:
            continue
        for m in EMAIL.findall(html):
            e = m.strip(".,;:").lower()
            if e in seen or BAD_TLD.search(e) or not e.split("@")[1].endswith(root):
                continue
            seen.add(e)
            found.append((e, "hiring" if HIRING.match(e.split("@")[0]) else "general", url))
        time.sleep(0.7)
    return found


def scan(limit: int = 25, priority: int | None = None, log=print) -> int:
    """Discover contacts for up to `limit` not-yet-seen companies, analytics firms first. Returns how many hiring addresses were found."""
    c = _db()
    c.execute("DELETE FROM outreach WHERE status='no_site'")   # "no website found" is a look, not a verdict: try again on the next scan
    c.commit()
    done = {r["company"].lower() for r in c.execute("SELECT company FROM outreach")}
    n_new = hiring = 0
    for name, dom, pr in sorted(targets(), key=lambda t: t[2]):
        if n_new >= limit:
            break
        if name.lower() in done or (priority is not None and pr != priority):
            continue
        n_new += 1
        v = verify_domain(name, dom)
        if not v:
            c.execute("INSERT OR IGNORE INTO outreach(company,domain,priority,status,note,found_at) VALUES (?,?,?,?,?,datetime('now','localtime'))",
                      (name, f"unverified:{name.lower()}", pr, "no_site", "no homepage that names the company"))
            c.commit()
            log(f"  {name}: no verified website")
            continue
        domain, html = v
        em = find_emails(domain, html)
        best = [x for x in em if x[1] == "hiring"]
        if best:
            e, kind, src = best[0]
            hiring += 1
            c.execute("INSERT OR IGNORE INTO outreach(company,domain,email,kind,source_page,priority,status,found_at) VALUES (?,?,?,?,?,?,?,datetime('now','localtime'))",
                      (name, domain, e, kind, src, pr, "found"))
            log(f"  {name}: {e}  (from {src})")
        else:
            gen = em[0][0] if em else ""
            c.execute("INSERT OR IGNORE INTO outreach(company,domain,email,kind,source_page,priority,status,note,found_at) VALUES (?,?,?,?,?,?,?,?,datetime('now','localtime'))",
                      (name, domain, None, None, None, pr, "no_hiring_email", f"only a general address published: {gen}" if gen else "no email published", ))
            log(f"  {name}: no hiring address published" + (f" (only {gen})" if gen else ""))
        c.commit()
    c.close()
    return hiring


def compose(company: str, to: str) -> EmailMessage:
    """An honest email from cv/facts.yaml only (`outreach` block: subject, pitch, ask). No claim about the company beyond its name."""
    from core import profile
    facts = yaml.safe_load(open(profile.FACTS, encoding="utf-8"))
    p = facts["personal"]
    o = facts.get("outreach") or {}
    if not o.get("pitch"):
        raise RuntimeError("cv/facts.yaml has no outreach.pitch: outreach emails are off until you write one (see docs/CONFIGURING_FACTS.md)")
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = p["email"]
    msg["Subject"] = str(o.get("subject") or f"Application - {p['name']}").replace("{company}", company)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(p["email"].split("@")[-1] if "@" in str(p.get("email")) else None))
    pitch = " ".join(str(o["pitch"]).split()).replace("{company}", company)
    ask = str(o.get("ask") or "relevant")
    contact = chr(10).join(x for x in (p.get("phone"), p.get("email")) if x)
    link = f", and my profile is at {p['linkedin']}" if p.get("linkedin") else ""
    nl = chr(10)
    msg.set_content(
        f"Hello {company} team,{nl}{nl}{pitch}{nl}{nl}"
        f"I would like to be considered for any current or upcoming {ask} openings at {company}. My CV is attached{link}.{nl}{nl}"
        f"Thank you for your time.{nl}{nl}Regards,{nl}{p['name']}{nl}{contact}{nl}")
    with open(CV, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=profile.resume_filename())
    return msg


def save_draft(msg: EmailMessage) -> str:
    """APPEND to Gmail's Drafts over IMAP (the same app-password login the receipts reader uses). Returns the Message-ID."""
    from core import imap
    pw = imap.password()
    if not pw:
        raise RuntimeError("no Gmail app password stored")
    M = imaplib.IMAP4_SSL(imap.HOST)
    try:
        M.login(imap.ADDR, pw)
        typ, _ = M.append('"[Gmail]/Drafts"', "\\Draft", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(f"Gmail refused the draft: {typ}")
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    return msg["Message-ID"]


def draft_pending(cap: int = DAILY_DRAFT_CAP, log=print) -> int:
    """Save a Gmail draft for each 'found' company, analytics first, at most `cap` per day. Never sends."""
    c = _db()
    today = time.strftime("%Y-%m-%d")
    used = c.execute("SELECT COUNT(*) FROM outreach WHERE drafted_at LIKE ?", (today + "%",)).fetchone()[0]
    made = 0
    for r in c.execute("SELECT * FROM outreach WHERE status='found' AND email IS NOT NULL ORDER BY priority, id").fetchall():
        if used + made >= cap:
            break
        msg = compose(r["company"], r["email"])
        mid = save_draft(msg)
        c.execute("UPDATE outreach SET status='drafted', drafted_at=datetime('now','localtime'), subject=?, draft_id=? WHERE id=?",
                  (msg["Subject"], mid, r["id"]))
        c.commit()
        made += 1
        log(f"  drafted for {r['company']} <{r['email']}>")
    c.close()
    return made


def rows(limit: int = 200) -> list:
    c = _db()
    out = [dict(r) for r in c.execute("SELECT company,domain,email,kind,source_page,priority,status,note,found_at,drafted_at,subject FROM outreach "
                                      "ORDER BY CASE status WHEN 'drafted' THEN 0 WHEN 'found' THEN 1 ELSE 2 END, priority, id LIMIT ?", (limit,))]
    c.close()
    return out


def _fetch_draft(M, message_id: str):
    M.select('"[Gmail]/Drafts"')
    typ, d = M.search(None, "HEADER", "Message-ID", message_id)
    ids = d[0].split() if typ == "OK" else []
    if len(ids) != 1:
        return None, ids
    typ, m = M.fetch(ids[0], "(RFC822)")
    return email.message_from_bytes(m[0][1]), ids


def send_drafted(limit: int = 15, gap_s: float = 20, log=print) -> int:
    """Send each 'drafted' row's EXACT Gmail draft over Gmail SMTP (same app password), once. Only called when the user has asked for the
    drafts to go out. A draft that is missing or ambiguous is skipped, never rebuilt. A row leaves 'drafted' as soon as SMTP
    accepts it, so it cannot be sent twice; the draft itself is left in Gmail (it is the only copy Gmail keeps)."""
    from core import imap
    pw = imap.password()
    if not pw:
        raise RuntimeError("no Gmail app password stored")
    c = _db()
    try:
        c.execute("ALTER TABLE outreach ADD COLUMN sent_at TEXT")
    except sqlite3.OperationalError:
        pass
    sent = 0
    M = imaplib.IMAP4_SSL(imap.HOST)
    M.login(imap.ADDR, pw)
    try:
        for r in c.execute("SELECT * FROM outreach WHERE status='drafted' AND email IS NOT NULL AND draft_id IS NOT NULL ORDER BY priority, id LIMIT ?", (limit,)).fetchall():
            msg, ids = _fetch_draft(M, r["draft_id"])
            if msg is None:
                log(f"  {r['company']}: draft not found exactly once in Gmail ({len(ids)}) - skipped")
                continue
            if msg["To"].strip().lower() != r["email"].lower():
                log(f"  {r['company']}: draft is addressed to {msg['To']}, not {r['email']} - skipped")
                continue
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=40) as S:
                S.login(imap.ADDR, pw)
                S.send_message(msg)
            # Gmail merges an SMTP send with the draft that has the same Message-ID, so NO separate copy appears in Sent. The
            # only independent evidence available is the absence of a bounce, so this is 'sent' (accepted) and check_bounces can still demote it.
            c.execute("UPDATE outreach SET status='sent', sent_at=datetime('now','localtime'), note='accepted by Gmail SMTP; delivery not confirmed' WHERE id=?", (r["id"],))
            c.commit()
            sent += 1
            log(f"  SENT {r['company']} <{r['email']}> (accepted by Gmail; the draft stays in Drafts, do not resend)")
            time.sleep(gap_s)
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
        c.close()
    return sent


def check_bounces(days: int = 2, log=print) -> int:
    """Mark 'sent' rows 'bounced' when a Delivery Status Notification names their address. Returns how many were demoted."""
    from core import imap
    M = imaplib.IMAP4_SSL(imap.HOST)
    M.login(imap.ADDR, imap.password())
    c = _db()
    n = 0
    try:
        M.select('"[Gmail]/All Mail"', readonly=True)
        typ, d = M.search(None, "X-GM-RAW", f'"newer_than:{days}d from:mailer-daemon"')
        blobs = []
        for i in d[0].split():
            t, m = M.fetch(i, "(RFC822)")
            msg = email.message_from_bytes(m[0][1])
            for p in msg.walk():
                if p.get_content_type() == "text/plain":
                    blobs.append((p.get_payload(decode=True) or b"").decode(errors="replace").lower())
        for r in c.execute("SELECT id,company,email FROM outreach WHERE status='sent'").fetchall():
            if any(f"delivered to {r['email'].lower()}" in b or r["email"].lower() in b for b in blobs):
                c.execute("UPDATE outreach SET status='bounced', note='the address does not exist (Gmail bounce)' WHERE id=?", (r["id"],))
                n += 1
                log(f"  BOUNCED {r['company']} <{r['email']}>")
        c.commit()
    finally:
        c.close()
        M.logout()
    return n


def daily(log=print) -> dict:
    """the user ("send any emails to startups" every day): scan the next unseen startups (analytics firms first), draft an honest
    email for each published hiring address, and send what was drafted. Bounded by DAILY_DRAFT_CAP; addresses come only from a company's own pages."""
    n_found = scan(limit=30, log=log)
    n_draft = draft_pending(log=log)
    try:   # sending an email in the user's name is THEIR decision: settings.json outreach_send (default false = drafts only)
        from core import profile
        allowed = bool(profile.settings().get("outreach_send", False))
    except Exception:  # noqa: BLE001
        allowed = False
    n_sent = send_drafted(limit=DAILY_DRAFT_CAP, log=log) if allowed else 0
    if not allowed:
        log("outreach: sending is off (settings.json outreach_send=false): drafts are in your mailbox's Drafts folder")
    log(f"outreach daily: {n_found} new hiring addresses, {n_draft} drafted, {n_sent} sent")
    return {"found": n_found, "drafted": n_draft, "sent": n_sent}
