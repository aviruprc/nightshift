"""Guided first-run setup: python hunt.py setup

Walks a new user through everything that must be customised: disclaimers, profile basics (facts.yaml + answers.yaml),
job preferences (locations, kinds of job, score bar), the CV PDF, settings, mailbox access, AI providers and keys, and
browser sign-ins. It asks before writing anything, never overwrites a file without asking, and never prints a secret.
Every step can be skipped and the whole tool re-run later. Finish by editing cv/facts.yaml by hand (docs/CONFIGURING_FACTS.md).
"""
import getpass
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CV_EX, CV = ROOT / "cv" / "facts.example.yaml", ROOT / "cv" / "facts.yaml"
ANS_EX, ANS = ROOT / "data" / "answers.example.yaml", ROOT / "data" / "answers.yaml"
SET_EX, SET = ROOT / "data" / "settings.example.json", ROOT / "data" / "settings.json"
LLM_EX, LLM = ROOT / "data" / "llm_providers.example.json", ROOT / "data" / "llm_providers.json"

# Values that only exist in the shipped example profile. If any is still present, the profile is not finished.
EXAMPLE_MARKERS = ("Asha Example", "asha.example@example.com", "asha-example", "9000000001", "Example Retail Co", "Demo Telecom",
                   "Sample Logistics", "Larkmoor University", "Brightwater College", "12 Example Street")

KEY_URLS = {"gemini": "https://aistudio.google.com/apikey", "groq": "https://console.groq.com/keys",
            "deepseek-api": "https://platform.deepseek.com/api_keys"}

DISCLAIMER = """
==========================================================================================
 NIGHTSHIFT SETUP - please read
==========================================================================================
 * Nightshift fills in and SUBMITS job applications in your name. A submitted application
   cannot be edited, withdrawn or re-sent. You are responsible for everything it submits.
 * It only states what you record in cv/facts.yaml and data/answers.yaml. Record only true
   facts. A missing fact is safe (the job waits for you); a wrong fact is not.
 * It NEVER solves or bypasses CAPTCHAs. Those jobs are parked for you.
 * Automating websites can break their terms of service (LinkedIn forbids automated use of
   accounts). Whether you use a site with automation is your decision.
 * Passwords and API keys are stored in your operating system's credential store, never in
   files. This setup never prints them.
 * Live applying starts only when YOU run `hunt.py run --live` or start_all.ps1. Everything
   before that is a dry run.
==========================================================================================
"""


def ask(prompt: str, default: str = "", required: bool = False) -> str:
    while True:
        d = f" [{default}]" if default else ""
        v = input(f"  {prompt}{d}: ").strip()
        v = v or default
        if v or not required:
            return v
        print("    (required)")


def yes(prompt: str, default: bool = False) -> bool:
    v = input(f"  {prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not v else v in ("y", "yes")


def step(n: int, title: str) -> bool:
    print(f"\n--- Step {n}: {title} " + "-" * max(3, 70 - len(title)))
    return yes("Do this step now?", True)


def _load_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _dump_yaml(p: Path, data: dict, header: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=140), encoding="utf-8")


def _start_from(target: Path, example: Path, what: str) -> dict | None:
    """The current file (to update) or a fresh copy of the example. None = user declined."""
    if target.exists():
        if yes(f"{target.relative_to(ROOT)} exists. Update it (your other values are kept)?", True):
            return _load_yaml(target) if target.suffix in (".yaml", ".yml") else json.loads(target.read_text(encoding="utf-8"))
        return None
    print(f"    creating {target.relative_to(ROOT)} from {example.relative_to(ROOT)} ({what})")
    return _load_yaml(example) if example.suffix in (".yaml", ".yml") else json.loads(example.read_text(encoding="utf-8"))


def profile_step():
    facts = _start_from(CV, CV_EX, "your CV as data")
    if facts is None:
        return
    p = facts.setdefault("personal", {})
    ex = bool(example_markers_in(CV)) or not CV.exists()   # still the example person: offer no example value as a default

    def cur(k):
        return "" if ex else str(p.get(k) or "")

    print("  Your basic details (they appear on every application):")
    p["name"] = ask("Full name", cur("name"), required=True)
    parts = p["name"].split()
    p["first_name"] = ask("First name", parts[0] if parts else "")
    p["last_name"] = ask("Last name", parts[-1] if len(parts) > 1 else "")
    p["email"] = ask("Email (portal logins + the mailbox the bot reads)", cur("email"), required=True)
    p["phone_country_code"] = ask("Phone country code", p.get("phone_country_code") or "+91")
    p["phone"] = ask("Phone number", cur("phone"), required=True)
    p["city"] = ask("City you live in", cur("city"), required=True)
    country = ask("Country you live in", "India")
    p["location"] = f"{p['city']}, {country}"
    li = ask("LinkedIn URL (blank if none)", cur("linkedin"))
    p["linkedin"] = re.sub(r"^https?://(www\.)?", "", li)
    p["github"] = ask("GitHub / portfolio URL (blank if none)", cur("github"))
    _dump_yaml(CV, facts, "# Your facts. Every value here can be submitted on an application: keep it TRUE. Field guide: docs/CONFIGURING_FACTS.md\n"
                          "# IMPORTANT: replace every remaining example value (experience, education, skills, summary) with your own.\n")

    ans_ex = not ANS.exists() or bool(example_markers_in(ANS))
    if ANS.exists():
        ans = _start_from(ANS, ANS_EX, "answers to recurring form questions")
        if ans is None:
            return
    else:   # never start from the example's invented answers: only what you type below is recorded
        print(f"    creating {ANS.relative_to(ROOT)} with only the answers you give now (all keys: {ANS_EX.relative_to(ROOT)})")
        ans = {}
    print("  Common form answers (leave blank to be asked per job instead):")
    ans["current_city"] = p["city"]
    ans["current_country"] = country
    ans["current_state"] = ask("State / region", "" if ans_ex else str(ans.get("current_state") or ""))
    ans["current_location"] = ", ".join(x for x in (p["city"], ans["current_state"], country) if x)
    ans["city_aliases"] = [x.strip() for x in ask("Spellings of your city (comma separated)", p["city"] if ans_ex else ", ".join(ans.get("city_aliases") or [p["city"]])).split(",") if x.strip()]
    ans["linkedin"] = ("https://www." + p["linkedin"]) if p["linkedin"] else None
    ans["total_experience_years"] = ask("Total years of work experience (whole number you can defend)", "")
    ans["relevant_experience_years"] = ask("Relevant years of experience", ans["total_experience_years"])
    ans["notice_period"] = ask("Notice period (e.g. Immediate, 30 days)", "")
    ans["available_immediately"] = "Yes" if ans["notice_period"].lower().startswith("immediate") else "No"
    ans["has_notice_period"] = "No" if ans["available_immediately"] == "Yes" else "Yes"
    ans["currently_employed"] = "Yes" if yes("Are you currently employed?", False) else "No"
    ans["expected_ctc_lpa"] = ask("Expected pay per year, in the unit forms use (e.g. LPA)", "")
    ans["last_ctc_lpa"] = ans["current_ctc_lpa"] = ask("Current / last pay per year, same unit", "")
    ans["work_authorization_home"] = "Yes" if yes(f"Are you authorised to work in {country}?", True) else "No"
    ans["requires_sponsorship"] = "Yes" if yes(f"Do you need visa sponsorship to work in {country}?", False) else "No"
    ans["willing_to_relocate"] = "Yes" if yes("Willing to relocate?", False) else "No"
    ans["open_to_hybrid"] = "Yes" if yes("Open to hybrid / on-site work?", True) else "No"
    dob = ask("Date of birth DD/MM/YYYY (blank: forms asking for it are parked for you)", "")
    if dob:
        ans["date_of_birth"] = dob
    else:
        ans.pop("date_of_birth", None)
        ans.pop("date_of_birth_long", None)
    for k in ("over_18", "knows_no_insider", "no_compliance_flags"):
        ans.pop(k, None)
    if yes("Are you 18 or older?", True):
        ans["over_18"] = "Yes"
    if yes("Confirm: you know no director/employee at the companies you will apply to (referral/relative questions -> No)?", False):
        ans["knows_no_insider"] = "Yes"
    if yes("Confirm: no criminal record, sanctions, conflicts of interest, non-competes, PEP relatives or disciplinary history?", False):
        ans["no_compliance_flags"] = "Yes"
    for k in [k for k, v in list(ans.items()) if isinstance(v, str) and any(m in v for m in EXAMPLE_MARKERS + ("Sampletown", "560001"))]:
        ans.pop(k)   # never keep an example value the user was not asked about
    ans = {k: v for k, v in ans.items() if v not in (None, "")}
    _dump_yaml(ANS, ans, "# Your answers to recurring form questions. Missing = asked per job. Field guide: docs/CONFIGURING_FACTS.md\n")
    print("    saved. Open cv/facts.yaml now and replace the example experience, education, skills and summary with your own.")


def preferences_step():
    if not CV.exists():
        print("    run the profile step first")
        return
    facts = _load_yaml(CV)
    pr = facts.setdefault("preferences", {})
    city = (facts.get("personal") or {}).get("city", "")
    try:
        aliases = list((_load_yaml(ANS) if ANS.exists() else {}).get("city_aliases") or [])
    except Exception:  # noqa: BLE001
        aliases = []
    own = ", ".join(dict.fromkeys([city] + aliases)) if city else ""
    cur = "" if example_markers_in(CV) else ", ".join(x.replace("\\", "") for x in str(pr.get("home_location_regex") or "").split("|") if x)
    locs = ask("Locations you accept (comma separated spellings)", cur or own)
    pr["home_location_regex"] = "|".join(re.escape(x.strip().lower()) for x in locs.split(",") if x.strip())
    q = ask("Job titles to search for (comma separated; blank = the built-in analyst list)", ", ".join(pr.get("search_queries") or []))
    if q:
        pr["search_queries"] = [x.strip() for x in q.split(",") if x.strip()]
        print("    note: titles are also filtered by role families (rank/filter.py). For a different field, set preferences.job_families"
              " and replace_job_families (docs/CONFIGURING_FACTS.md).")
    pr["include_returnships"] = yes("Also search returnship / career-break and contract programmes?", bool(pr.get("include_returnships", True)))
    y = ask("Skip jobs whose stated MINIMUM experience is at least N years (0 = never skip)", str(pr.get("max_stated_min_years", 0)))
    pr["max_stated_min_years"] = int(y) if y.isdigit() else 0
    _dump_yaml(CV, facts, "# Your facts. Every value here can be submitted on an application: keep it TRUE. Field guide: docs/CONFIGURING_FACTS.md\n")
    st = _start_from(SET, SET_EX, "settings") or (json.loads(SET.read_text(encoding="utf-8")) if SET.exists() else {})
    s = ask("Minimum AI fit score 0-100 to apply (lower = more, broader applications)", str(st.get("min_score", 45)))
    st["min_score"] = int(s) if s.isdigit() else 45
    st["naukri_min_score"] = st["min_score"]
    SET.write_text(json.dumps(st, indent=1), encoding="utf-8")
    print("    saved")


def cv_step():
    st = json.loads(SET.read_text(encoding="utf-8")) if SET.exists() else json.loads(SET_EX.read_text(encoding="utf-8"))
    src = ask("Path to your own CV PDF (used for job-board profile uploads and outreach; blank to skip)", "")
    if not src:
        return
    sp = Path(src.strip('"')).expanduser()
    if not sp.exists() or sp.suffix.lower() != ".pdf":
        print("    not a PDF file, skipped")
        return
    dst = ROOT / "data" / "resumes" / "_latest" / "resume.pdf"
    if yes(f"Copy it into the project ({dst.relative_to(ROOT)})? No = point to it where it is", True):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sp, dst)
        st["base_cv_pdf"] = "data/resumes/_latest/resume.pdf"
    else:
        st["base_cv_pdf"] = str(sp)
    SET.write_text(json.dumps(st, indent=1), encoding="utf-8")
    print("    saved. Per-job CVs are generated from cv/facts.yaml (cv/tailor.py).")


def settings_step():
    st = _start_from(SET, SET_EX, "settings")
    if st is None:
        return
    g = ask("Daily goal shown on the dashboard", str(st.get("daily_goal", 30)))
    st["daily_goal"] = int(g) if g.isdigit() else 30
    c = ask("Max applications per company per day", str(st.get("company_daily_cap", 3)))
    st["company_daily_cap"] = int(c) if c.isdigit() else 3
    skip = ask("Companies never to apply to (comma separated words, blank = none)", ", ".join(st.get("skip_companies") or []))
    st["skip_companies"] = [x.strip().lower() for x in skip.split(",") if x.strip()]
    st["outreach_send"] = yes("Allow the daily startup outreach to SEND emails it drafts? (No = drafts only)", bool(st.get("outreach_send", False)))
    st["accounts_mirror"] = yes("Also keep portal passwords in data/accounts.json (base64, NOT encrypted)? Recommended: No",
                                bool(st.get("accounts_mirror", False)))
    st["claude_for_bot"] = yes("Let the bot use the Claude Code CLI if it is installed?", bool(st.get("claude_for_bot", False)))
    SET.write_text(json.dumps(st, indent=1), encoding="utf-8")
    print("    saved")


def mailbox_step():
    from core import imap
    print("  The bot reads your mailbox over IMAP for verification codes, password-reset links, receipts and replies.")
    print("  Gmail: turn on 2-Step Verification, then create an app password at https://myaccount.google.com/apppasswords")
    print(f"  Mailbox: {imap.ADDR or '(no email in cv/facts.yaml yet)'} on {imap.HOST}  (another server: set JOBHUNT_IMAP_HOST in .env)")
    if not imap.ADDR:
        return
    pw = getpass.getpass("  App password (hidden, blank to skip): ").strip()
    if not pw:
        return
    imap.save_password(pw)
    ok, why = imap.check()
    print("    login test:", "OK" if ok else f"FAILED ({why})")


def ai_step():
    import keyring

    from core import llm
    provs = json.loads(LLM.read_text(encoding="utf-8")) if LLM.exists() else json.loads(LLM_EX.read_text(encoding="utf-8"))
    if not LLM.exists():
        print(f"    creating {LLM.relative_to(ROOT)} from the example")
    print("  AI scores jobs and fills unfamiliar forms (only from your facts). Free keys: Gemini and Groq. Keys go to the credential store.")
    for p in provs:
        if p.get("type") not in ("openai", "gemini"):
            continue
        if not yes(f"Do you have an API key for {p['name']}? ({KEY_URLS.get(p['name'], p.get('base_url', ''))})", False):
            continue
        k = getpass.getpass(f"  {p['name']} API key (hidden): ").strip()
        if not k:
            continue
        keyring.set_password("nightshift-llm", llm.key_name(p), k)
        p["enabled"] = True
        try:
            out = llm._ask_provider("Reply with the single word: ok", p, "score", 60)
            print("    test reply:", out[:30], "-> working")
            llm.clear_cooldown(p["name"])
        except Exception as e:  # noqa: BLE001
            print("    key stored, but the test call failed:", type(e).__name__, str(e)[:120])
    for p in provs:
        if p.get("type") in ("openai", "gemini") and not llm._has_key(llm.key_name(p)):
            p["enabled"] = False   # a provider without a key is simply skipped; switched off so the dashboard does not list it as broken
    if shutil.which("codex") and yes("The Codex CLI is installed. Use it as a provider (signed in with your own account)?", False):
        for p in provs:
            if p.get("type") == "codex":
                p["enabled"] = True
    LLM.write_text(json.dumps(provs, indent=1), encoding="utf-8")
    print("    providers saved. Check any time: python hunt.py llm-test")


def browser_step():
    print("  A Chrome window opens on the bot's OWN profile (data/browser_profile), with Gmail, Naukri, Indeed and LinkedIn sign-in tabs.")
    print("  Sign in to the ones you use, then close the window. Sessions stay in that profile (treat it like a password).")
    if yes("Open it now?", True):
        from apply import browser
        browser.login_session()


def run():
    if not sys.stdin.isatty():
        print("setup is interactive: run `python hunt.py setup` in a terminal")
        return 2
    print(DISCLAIMER)
    if input("  Type I AGREE to continue: ").strip().upper() != "I AGREE":
        print("  Setup stopped. Nothing was changed.")
        return 1
    steps = [("Your profile (facts.yaml + answers.yaml)", profile_step),
             ("Job preferences (locations, kinds of job, fit score)", preferences_step),
             ("Your CV PDF", cv_step),
             ("Settings (caps, do-not-apply list, outreach, password mirror)", settings_step),
             ("Mailbox access (verification codes, receipts)", mailbox_step),
             ("AI providers and keys", ai_step),
             ("Browser sign-ins", browser_step)]
    for i, (title, fn) in enumerate(steps, 1):
        if step(i, title):
            try:
                fn()
            except (KeyboardInterrupt, EOFError):
                print("\n    step cancelled")
            except Exception as e:  # noqa: BLE001 (one failed step must not lose the others)
                print(f"    step failed: {type(e).__name__}: {str(e)[:160]}")
    print("\n--- Check " + "-" * 60)
    subprocess.run([sys.executable, str(ROOT / "hunt.py"), "doctor"], cwd=str(ROOT))
    print("\nNext: edit cv/facts.yaml by hand (experience, education, skills, not_claimable), then:\n"
          "  python hunt.py discover\n  python hunt.py run --target 3 --show     (dry run: nothing is submitted)\n"
          "Guides: docs/SETUP.md, docs/CONFIGURING_FACTS.md, docs/SAFETY_AND_ACCURACY.md")
    return 0


def example_markers_in(*paths: Path) -> list:
    """Example-profile values still present in the given files (the profile is not finished while any remain)."""
    hits = []
    for p in paths:
        try:
            txt = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        hits += [f"{p.name}: '{m}'" for m in EXAMPLE_MARKERS if m in txt]
    return hits


if __name__ == "__main__":
    raise SystemExit(run())
