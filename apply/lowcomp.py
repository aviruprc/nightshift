"""LinkedIn low-competition scan log.

Every LinkedIn job the worker opens while resolving its real apply page is logged here with what the page said about
competition and what we did about it, so the dashboard shows the evidence rather than a count."""
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "li_scan.jsonl"
DEFAULT_LIMIT = 10


def li_max_applicants() -> int | None:
    """settings.json li_max_applicants (default 10; 0 or null turns the filter off): a LinkedIn job is only applied to when its page
    shows an EXACT applicant count below this."""
    try:
        st = json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return DEFAULT_LIMIT
    if "li_max_applicants" not in st:
        return DEFAULT_LIMIT
    v = st["li_max_applicants"]
    return int(v) if v else None


def record(job: dict, reading: dict, decision: str, detail: str = "") -> None:
    """decision: qualified | crowded | easy_apply | closed | error. Never raises: a log line must not cost an application."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        row = {"t": time.time(), "id": job.get("id"), "company": job.get("company"), "title": (job.get("title") or "")[:90],
               "url": job.get("url"), "kind": reading.get("kind"), "applicants": reading.get("applicants"),
               "text": reading.get("text"), "decision": decision, "detail": detail[:200]}
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def recent(limit: int = 60) -> list:
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001
            continue
    return out
