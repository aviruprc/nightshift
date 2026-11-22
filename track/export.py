"""tracker.xlsx + dashboard.html from the DB."""
import html
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
XLSX = ROOT / "data" / "tracker.xlsx"
HTML = ROOT / "data" / "dashboard.html"
COLS = ["score", "family", "company", "title", "location", "posted_at", "status", "status_detail", "needs_you_reason",
        "ghost_flags", "source", "apply_url", "applied_at", "resume_path", "score_reason"]


def _sheet(wb, name, rows, cols=COLS):
    ws = wb.create_sheet(name[:31])
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2F4F6F")
    for r in rows:
        ws.append([r.get(c) for c in cols])
        cell = ws.cell(row=ws.max_row, column=cols.index("apply_url") + 1) if "apply_url" in cols else None
        if cell and cell.value:
            cell.hyperlink = cell.value
            cell.font = Font(color="0563C1", underline="single")
    for i, c in enumerate(cols, 1):
        ws.column_dimensions[get_column_letter(i)].width = 60 if c in ("title", "apply_url", "score_reason", "status_detail") else 18
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def export(db, returnships=None):
    q = lambda w: db.rows(w)  # noqa: E731
    needs = q("status IN ('needs_you','unconfirmed','failed')")
    applied = sorted(q("status='applied'"), key=lambda r: (r.get("family") or "", r["company"]))
    cands = sorted(q("status IN ('candidate','planned')"), key=lambda r: -(r.get("score") or 0))
    skipped = q("status='skipped'")
    wb = Workbook()
    wb.remove(wb.active)
    _sheet(wb, "Needs you", needs)
    _sheet(wb, "Candidates (ranked)", cands)
    _sheet(wb, "Applied", applied)
    _sheet(wb, "Skipped", skipped)
    if returnships:
        _sheet(wb, "Returnships", returnships, ["name", "status", "relevant_for_analyst", "url", "notes"])
    _sheet(wb, "All jobs", q("1=1"))
    XLSX.parent.mkdir(exist_ok=True)
    wb.save(XLSX)

    counts = {r["status"]: r["n"] for r in db.c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    fam = {r["family"] or "-": r["n"] for r in db.c.execute("SELECT family, COUNT(*) n FROM jobs WHERE status IN ('candidate','planned','applied') GROUP BY family")}

    def table(rows, cols):
        h = "".join(f"<th>{c}</th>" for c in cols)
        b = ""
        for r in rows[:150]:
            tds = ""
            for c in cols:
                v = html.escape(str(r.get(c) if r.get(c) is not None else ""))
                tds += f'<td><a href="{v}" target="_blank">open</a></td>' if c == "apply_url" and v else f"<td>{v}</td>"
            b += f"<tr>{tds}</tr>"
        return f"<table><tr>{h}</tr>{b}</table>"

    show = ["score", "company", "title", "location", "status", "needs_you_reason", "apply_url"]
    page = f"""<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=300><title>Job Hunt</title>
<style>body{{font:14px system-ui;margin:24px;background:#fafafa;color:#222}}table{{border-collapse:collapse;width:100%;margin:8px 0 28px}}
td,th{{border:1px solid #ddd;padding:5px 8px;text-align:left}}th{{background:#2f4f6f;color:#fff}}.k{{display:inline-block;margin:0 14px 10px 0;padding:8px 14px;background:#fff;border:1px solid #ddd;border-radius:6px}}</style>
<h1>Job Hunt — {datetime.now():%d %b %Y %H:%M}</h1>
{''.join(f'<span class=k><b>{v}</b> {html.escape(k)}</span>' for k, v in counts.items())}
<p>By family: {html.escape(str(fam))}</p>
<h2>Needs you ({len(needs)})</h2>{table(needs, show)}
<h2>Top candidates ({len(cands)})</h2>{table(cands, show)}
<h2>Applied ({len(applied)})</h2>{table(applied, show + ['applied_at'])}"""
    HTML.write_text(page, encoding="utf-8")
    return XLSX, HTML
