"""Loads `.env` from the project folder into os.environ (only variables that are not already set). See .env.example."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path | None = None) -> int:
    p = path or ROOT / ".env"
    n = 0
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001 (no .env is the normal case)
        return 0
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.split(" #")[0].strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n
