"""CAPTCHA hand-off with a person at the screen.

    python -m apply.handoff <job id> [<job id> ...] (run from the project root)

Each job goes through the NORMAL pipeline (tailored CV, rule-based flow, LLM navigator, every accuracy gate, proof) in a VISIBLE Chrome window on
a copy of the signed-in profile, so the unattended worker keeps running next to it. At a CAPTCHA the bot stops, brings the window forward and waits
for her (agent.wait_for_human); it never touches the CAPTCHA. When she has passed it, the bot continues and submits as it would for any other job.
If she does not come within WAIT_S the job is parked exactly as an unattended run would park it."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAIT_S = 1800   # how long one CAPTCHA may wait for her


def main(ids: list) -> int:
    if not ids:
        print(__doc__)
        return 2
    sys.path.insert(0, str(ROOT))
    from apply import assist
    if assist._alive():
        print("the older CAPTCHA helper window (hunt.py assist) is using the same browser copy: close it first")
        return 1
    # set BEFORE the modules that read them are imported (browser.PROFILE and runstate._LABEL are fixed at import)
    os.environ["JOBHUNT_PROFILE"] = str(assist.PROFILE)
    os.environ["JOBHUNT_LABEL"] = "H"
    os.environ["JOBHUNT_HANDOFF"] = str(WAIT_S)
    os.environ.pop("JOBHUNT_OFFSCREEN", None)      # the window must be on HER screen
    assist._refresh_profile()
    from apply import runner
    from core import db as dbm
    d = dbm.DB()
    for jid in ids:
        rows = d.rows("id=?", (jid,))
        if not rows:
            print("no such job:", jid)
            continue
        r = rows[0]
        if r["status"] in ("applied", "unconfirmed", "closed"):
            print(f"{r['company']} | {r['title'][:60]}: already {r['status']}, not opened")
            continue
        if r["status"] != "candidate":   # parked earlier (needs_you / deferred_captcha / failed): a person is here now, so it may be tried
            d.set_status(jid, "candidate", "hand-off run: a person is at the window", needs_you_reason=None)
            d.c.execute("UPDATE jobs SET next_attempt_at=NULL WHERE id=?", (jid,))
            d.c.commit()
        print(f"OPENING {r['company']} | {r['title'][:70]}", flush=True)
        runner.run(target=1, dry_run=False, max_attempts=1, show=True, only="id:" + jid)
        r = d.rows("id=?", (jid,))[0]
        print(f"RESULT  {r['status']} | {(r['status_detail'] or '')[:220]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
