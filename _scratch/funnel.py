import sys,collections,re
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0,".")
from core import db as dbm
from apply import runner
from rank import filter as flt, ghost
from urllib.parse import urlparse
d=dbm.DB()
allc=d.rows("status IN ('candidate','dry_run_ok')")
C=collections.Counter
def src(rows): return dict(C(r["source"] for r in rows).most_common(12))
print("all candidates",len(allc))
due=[r for r in allc if not r["next_attempt_at"] or r["next_attempt_at"]<=dbm.now()]
print("due",len(due),"| deferred(next_attempt in future)",len(allc)-len(due), src([r for r in allc if r not in due]))
fr=runner.fresh(due); print("fresh<=7d",len(fr),"| aged out",len(due)-len(fr), src([r for r in due if r not in fr]))
ages=C(min(60,int(ghost.age_or(r["posted_at"],r["first_seen"])//7*7)) for r in due); print("age buckets(days):",sorted(ages.items()))
p=[r for r in fr if not flt.location_conflict(r["location"])]
p2=[r for r in p if r["apply_route"]!="easy_apply" and not runner.one_click_host(r["apply_url"] or r["url"]) and not runner.JUNK_HOSTS.search(r["apply_url"] or r["url"] or "")]
print("after easy/oneclick/junk",len(p2),"| dropped",src([r for r in p if r not in p2]))
sc=[r for r in p2 if r["score"] is not None]; un=[r for r in p2 if r["score"] is None]
print("scored",len(sc),"unscored",len(un),src(un))
print("unscored w/ desc>=400:",sum(len((r['description'] or '').strip())>=400 for r in un), src([r for r in un if len((r['description'] or '').strip())>=400]))
ok=[r for r in sc if r["score"]>=runner.min_score(r)]
print("scored >= bar:",len(ok))
for bar in (40,35,30):
    print(" would pass at flat bar",bar,":",sum(r["score"]>=bar for r in sc), "distinct companies",len({r['company'] for r in sc if r['score']>=bar}))
print("--sample scored 30-44 fresh:")
for r in sorted([r for r in sc if 30<=r["score"]<45],key=lambda r:-r["score"])[:25]:
    print(r["score"],"|",r["source"],"|",(r["company"] or "")[:22],"|",(r["title"] or "")[:45],"|",(r["score_reason"] or "")[:70])
print("--hosts of unscored:",C(urlparse(r["apply_url"] or r["url"] or "").netloc for r in un).most_common(15))
