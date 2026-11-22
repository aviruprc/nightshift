"""python _scratch/one.py <company|job id> [url hint] [--dry] [--show]
Runs ONE job LIVE through the full pipeline (tailor -> ATS gate -> flow -> LLM navigator fallback -> proof) and prints what happened."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from apply import runner
from core import db

arg = sys.argv[1]
hint = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else ""
d = db.DB()
rows = d.rows("id=?", (arg,)) or [r for r in d.rows("company LIKE ?", (arg + "%",))
                                   if r["status"] not in ("applied", "skipped", "closed") and hint in (r["apply_url"] or "")]
if not rows:
    sys.exit("no such job")
r = rows[-1]
print("JOB:", r["id"], r["company"], "|", r["title"][:60], "|", r["status"], "|", (r["apply_url"] or "")[:90])
runner.run(target=1, dry_run="--dry" in sys.argv, max_attempts=1, show="--show" in sys.argv, only="id:" + r["id"])
r = d.rows("id=?", (r["id"],))[0]
print("RESULT:", r["status"], "|", (r["status_detail"] or "")[:300])
print("PROOF :", r["email_proof"])
print("SHOT  :", r["screenshot_path"])
