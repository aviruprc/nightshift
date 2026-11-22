"""Append-only live activity log (data/activity.jsonl) for the dashboard: what the script / LLM / portal is doing right now.
kind: step | portal | llm | ai | fill | result | park | error | info. Never raises."""
import json
import os
import time
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "data" / "activity.jsonl"
RUN = os.environ.get("JOBHUNT_LABEL") or ("B" if os.environ.get("JOBHUNT_PROFILE") else "A")  # which process wrote the line
_job: dict = {}


def set_job(company: str = "", title: str = "", portal: str = ""):
    _job.update(company=company, title=title, portal=portal)


def log(kind: str, msg: str, portal: str | None = None, model: str | None = None, **extra):
    try:
        PATH.parent.mkdir(exist_ok=True)
        line = {"t": time.time(), "run": RUN, "kind": kind, "msg": str(msg)[:300], "company": _job.get("company", ""),
                "title": _job.get("title", ""), "portal": portal or _job.get("portal", ""), **({"model": model} if model else {}), **extra}
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        from core import runstate  # any activity proves the applier is alive: feeds the dashboard status and the stall watchdog
        runstate.touch()
    except Exception:  # noqa: BLE001
        pass


def tail(n: int = 120) -> list[dict]:
    try:
        with open(PATH, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 96_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.startswith("{")]
    except Exception:  # noqa: BLE001
        return []


def portal_of(url: str, route: str | None = None) -> str:
    from urllib.parse import urlparse
    h = urlparse(url or "").netloc.lower()
    if route == "easy_apply":
        return "LinkedIn Easy Apply"
    table = [("myworkdayjobs", "Workday"), ("naukri.com", "Naukri"), ("linkedin.com", "LinkedIn"), ("oraclecloud", "Oracle HCM"),
             ("greenhouse", "Greenhouse"), ("lever.co", "Lever"), ("ashbyhq", "Ashby"), ("smartrecruiters", "SmartRecruiters"),
             ("successfactors", "SuccessFactors"), ("sapsf", "SuccessFactors"), ("icims", "iCIMS"), ("taleo", "Taleo"),
             ("eightfold", "Eightfold"), ("phenom", "Phenom"), ("jobvite", "Jobvite"), ("workable", "Workable"),
             ("indeed.", "Indeed"), ("shine.com", "Shine"), ("foundit", "Foundit"), ("instahyre", "Instahyre"), ("hirist", "Hirist"),
             ("apna.co", "Apna"), ("ripplehire", "RippleHire"), ("amazon.jobs", "Amazon Jobs"), ("higher.gs.com", "Goldman Sachs")]
    for k, v in table:
        if k in h:
            return v
    return h.removeprefix("www.").split(":")[0] or "unknown"
