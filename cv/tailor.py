"""Deterministic CV tailoring: reorder + emphasise only. Never adds a fact that is not in facts.yaml. Zero LLM tokens."""
import html
import re
from pathlib import Path

from core import profile
from cv import vocab

ROOT = Path(__file__).resolve().parent.parent


def family_summaries() -> dict:
    """facts.yaml `family_summaries` {role family: summary text}: alternative openings per kind of job, written by YOU from claims already
    on your CV. A family without one uses the plain `summary`."""
    try:
        return dict(profile.facts().get("family_summaries") or {})
    except Exception:  # noqa: BLE001
        return {}


FAMILY_SUMMARY = family_summaries()


def _score(text: str, weights: dict) -> int:
    """How strongly a bullet/project matches the JD (sum of JD hit counts of the skills it mentions)."""
    return sum(w for skill, w in weights.items() if vocab.OWNED_RE[skill].search(text))


def tailor(jd: str, family: str, title: str = "") -> dict:
    f = profile.facts()
    weights, lacked = vocab.jd_terms(f"{title}\n{jd}")
    cv = {"personal": f["personal"], "education": f["education"], "certifications": f.get("certifications") or []}
    cv["summary"] = " ".join(str((f.get("family_summaries") or {}).get(family) or f["summary"]).split())
    # skills: matched first within each category; categories with most matches first; nothing added
    cats = []
    for cat, items in f["skills"].items():
        ordered = sorted(items, key=lambda s: -weights.get(s, 0))
        cats.append((sum(1 for s in items if weights.get(s)), cat, ordered))
    cats.sort(key=lambda c: -c[0])
    cv["skills"] = {cat: items for _, cat, items in cats}
    # headline: facts.yaml headline_title (default: your most recent job title) + the 3 of your tools this JD names most + headline_tags.
    tools = f["skills"].get("Tools") or next(iter(f["skills"].values()), [])
    tools_hit = [s for s in weights if s in tools]
    top = sorted(tools_hit, key=lambda s: -weights[s])[:3] or list(tools[:3])
    title0 = f.get("headline_title") or ((f.get("experience") or [{}])[0].get("title") or "")
    tags = [str(t) for t in (f.get("headline_tags") or [])]
    cv["headline"] = " | ".join(x for x in [title0, " • ".join(top)] + tags if x) or str(f.get("headline") or "")
    cv["experience"] = []
    for e in f["experience"]:
        bullets = sorted(e["bullets"], key=lambda b: -_score(b, weights))  # stable: ties keep original order
        cv["experience"].append({**e, "bullets": bullets})
    cv["projects"] = sorted(f.get("projects") or [], key=lambda p: -_score(p["tech"] + " " + " ".join(p["bullets"]), weights))
    cv["_weights"], cv["_gaps"] = weights, lacked
    return cv


CSS = ("body{font-family:Arial,Helvetica,sans-serif;font-size:8.8pt;line-height:1.2;color:#000;margin:0}"
       "h1{font-size:17pt;margin:0 0 2px;text-align:center}.sub{text-align:center;font-size:9.5pt;margin:0}"
       "h2{font-size:10pt;border-bottom:1px solid #000;margin:8px 0 3px;padding-bottom:1px;text-transform:uppercase}"
       ".role{margin:6px 0 1px;font-weight:bold}.dates{font-weight:normal}ul{margin:2px 0 4px 18px;padding:0}li{margin:1px 0}p{margin:2px 0}")


def render_html(cv: dict) -> str:
    e = html.escape
    p = cv["personal"]
    out = [f"<h1>{e(p['name'].upper())}</h1><p class=sub>{e(cv['headline'])}</p>",
           "<p class=sub>" + " | ".join(e(str(p[k])) for k in ("phone", "email", "location", "linkedin", "github") if p.get(k)) + "</p>",
           f"<h2>Professional Summary</h2><p>{e(cv['summary'])}</p><h2>Skills</h2>"]
    out += [f"<p><b>{e(c)}:</b> {e(', '.join(items))}</p>" for c, items in cv["skills"].items()]
    out.append("<h2>Professional Experience</h2>")
    for x in cv["experience"]:
        out.append(f"<p class=role>{e(x['title'])} | {e(x['company'])}, {e(x['location'])} "
                   f"<span class=dates>| {e(x['start'])} – {e(x['end'])}</span></p><ul>"
                   + "".join(f"<li>{e(b)}</li>" for b in x["bullets"]) + "</ul>")
    if cv["projects"]:
        out.append("<h2>Projects</h2>")
    for x in cv["projects"]:
        link = f" | {e(x['link'])}" if x.get("link") else ""
        out.append(f"<p class=role>{e(x['name'])} | {e(x['tech'])} <span class=dates>| {e(x['year'])}{link}</span></p><ul>"
                   + "".join(f"<li>{e(b)}</li>" for b in x["bullets"]) + "</ul>")
    if cv["certifications"]:
        out.append("<h2>Certifications</h2><ul>" + "".join(f"<li>{e(c)}</li>" for c in cv["certifications"]) + "</ul>")
    out.append("<h2>Education</h2>" + "".join(
        f"<p><b>{e(d['degree'])}</b> — {e(d['institution'])}, {e(d['year'])}</p>" for d in cv["education"]))
    return f"<!doctype html><meta charset=utf-8><title>{e(p['name'])} Resume</title><style>{CSS}</style><body>{''.join(out)}</body>"


def render_pdf(cv: dict, out_path: Path):
    from playwright.sync_api import sync_playwright
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content(render_html(cv))
        pg.pdf(path=str(out_path), format="A4", margin={"top": "9mm", "bottom": "9mm", "left": "11mm", "right": "11mm"})
        b.close()
    return out_path


def build(job_id: str, jd: str, family: str, title: str = "") -> tuple[Path, dict]:
    from concurrent.futures import ThreadPoolExecutor
    cv = tailor(jd, family, title)
    from core import profile
    out = ROOT / "data" / "resumes" / job_id / profile.resume_filename()
    # Playwright's sync API cannot nest inside another sync session on the same thread: render on a worker thread.
    with ThreadPoolExecutor(1) as ex:
        pdf = ex.submit(render_pdf, cv, out).result(timeout=90)
    return pdf, cv


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
