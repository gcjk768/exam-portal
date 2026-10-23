"""Which community-bank questions read like the real exam? TF-IDF cosine against the Udemy/Microsoft Forms/Tutorials Dojo mocks.
Usage: python tools/examlike.py <questions.csv> <out.json> [threshold] [reference.csv]   (stdlib only; ids of the exam-like free questions;
the reference defaults to the same bank, e.g. pass CCAR-P's questions.csv to rate a Foundations bank against the CCAR-P mocks)"""
import csv, json, math, re, sys
from collections import Counter

src, out = sys.argv[1], sys.argv[2]
thr = float(sys.argv[3]) if len(sys.argv) > 3 else 0.45
rows = list(csv.DictReader(open(src, encoding="utf-8")))
extra = list(csv.DictReader(open(sys.argv[4], encoding="utf-8"))) if len(sys.argv) > 4 else []
paid = lambda s: s == "Tutorials Dojo" or "Udemy" in s or s.startswith("Microsoft Forms mock")
tok = lambda r: Counter(re.findall(r"[a-z]{3,}", f"{r['question']} {r['options']}".lower()))
docs = [tok(r) for r in rows + extra]
df = Counter(w for d in docs for w in d)
idf = {w: math.log(len(docs) / c) for w, c in df.items()}
def vec(d):
    v = {w: (1 + math.log(c)) * idf[w] for w, c in d.items()}
    n = math.sqrt(sum(x * x for x in v.values())) or 1
    return {w: x / n for w, x in v.items()}
vecs = [vec(d) for d in docs]
ref = [v for v, r in zip(vecs, rows + extra) if paid(r["source"])]
ids, best = [], []
for v, r in zip(vecs, rows):   # the first len(rows) vectors are the target bank
    if paid(r["source"]):
        continue
    m = max(sum(x * u.get(w, 0) for w, x in v.items()) for u in ref)
    best.append(m)
    if m >= thr:
        ids.append(r["id"])
best.sort()
print(f"{len(best)} free questions, p50={best[len(best)//2]:.2f} p90={best[int(len(best)*.9)]:.2f} max={best[-1]:.2f}; >= {thr}: {len(ids)}")
json.dump(sorted(ids), open(out, "w"))
