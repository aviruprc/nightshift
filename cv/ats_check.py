"""ATS gate for the PDF actually being uploaded. Extracts text the way an ATS parser would and checks it."""
import re
from pathlib import Path

from pypdf import PdfReader

from core import profile
from cv import vocab

HEADINGS = ["professional summary", "skills", "professional experience", "education"]


def extract(pdf: Path) -> str:
    return "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)


def check(pdf: Path, jd: str, title: str = "") -> dict:
    """Returns {ok, coverage, problems, gaps}. ok=False means DO NOT SUBMIT this CV."""
    f = profile.facts()
    text = extract(pdf)
    low = text.lower()
    problems = []
    if len((jd or "").strip()) < 300:  # an empty/short JD would trivially pass keyword coverage: refuse to call it 'tailored'
        problems.append("job description missing or too short to tailor against")
    if len(text) < 1200:
        problems.append("text too short / not extractable (image-only PDF?)")
    for h in HEADINGS:
        if h not in low:
            problems.append(f"missing standard heading: {h}")
    if f["personal"]["email"].lower() not in low:
        problems.append("email not found in extracted text")
    if re.sub(r"\D", "", f["personal"]["phone"])[-10:] not in re.sub(r"\D", "", text):
        problems.append("phone not found in extracted text")
    if not text.lstrip().lower().startswith(f["personal"]["name"].lower()):
        problems.append("name is not the first line")
    if len(PdfReader(str(pdf)).pages) > 2:
        problems.append("more than 2 pages")
    if re.search(r"[•●]{3,}|[�]", text):
        problems.append("garbled characters in extracted text")
    # truthfulness: nothing she lacks may be claimed on the CV
    for skill in f["not_claimable"]:
        if re.search(rf"\b{re.escape(skill)}\b", text, re.I):
            problems.append(f"CV claims a skill she does not have: {skill}")
    owned, lacked = vocab.jd_terms(f"{title}\n{jd}")
    present = [s for s in owned if s.lower() in low or vocab.OWNED_RE[s].search(text)]
    coverage = len(present) / len(owned) if owned else 1.0
    if owned and coverage < 0.8:
        problems.append(f"keyword coverage {coverage:.0%} < 80% of JD terms she has")
    return {"ok": not problems, "coverage": round(coverage, 2), "problems": problems, "gaps": lacked,
            "matched": sorted(present), "chars": len(text)}
