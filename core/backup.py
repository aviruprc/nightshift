"""Database safety net: consistent snapshots (SQLite online backup API) into data/backups, newest 30 kept; restore with a safety copy first."""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from core.db import DB_PATH

BACKUPS = DB_PATH.parent / "backups"
KEEP = 30


def snapshot(reason: str = "manual") -> Path | None:
    if not DB_PATH.exists():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS / f"jobhunt-{datetime.now():%Y%m%d-%H%M%S}-{reason}.db"
    src = sqlite3.connect(DB_PATH, timeout=15)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)  # consistent copy even while another process is writing
        dst.close()
    finally:
        src.close()
    for old in sorted(BACKUPS.glob("jobhunt-*.db"))[:-KEEP]:
        old.unlink(missing_ok=True)
    return dest


def list_backups() -> list[Path]:
    return sorted(BACKUPS.glob("jobhunt-*.db"), reverse=True)


def check(path: Path = DB_PATH) -> str:
    c = sqlite3.connect(path, timeout=15)
    try:
        return c.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        c.close()


def restore(backup: Path) -> Path:
    """Replace the live database with a backup. A safety snapshot of the current one is taken first."""
    if check(backup) != "ok":
        raise ValueError(f"{backup.name} failed its integrity check; refusing to restore")
    safety = snapshot("before-restore")
    for ext in ("-wal", "-shm"):
        Path(str(DB_PATH) + ext).unlink(missing_ok=True)
    shutil.copy2(backup, DB_PATH)
    return safety
