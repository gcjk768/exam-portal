"""Move the questions a review marked keep=false out of a bot's questions.csv into removed_questions.csv.
Usage: python tools/apply_review.py <bot folder> <verdict.json> [more verdict.json ...]
A verdict file is a list of {"id", "keep", "reason"}. A backup of the bank is kept as data/questions.before-review.csv."""
import csv, json, shutil, sys
from pathlib import Path

d = Path(sys.argv[1]) / "data"
drop = {x["id"]: x["reason"] for f in sys.argv[2:] for x in json.loads(Path(f).read_text(encoding="utf-8")) if not x["keep"]}
with (d / "questions.csv").open(encoding="utf-8") as f:
    rd = csv.DictReader(f); fields = rd.fieldnames; rows = list(rd)
gone = [{**r, "why_removed": f"review: {drop[r['id']]}"} for r in rows if r["id"] in drop]
keep = [r for r in rows if r["id"] not in drop]
if gone:
    shutil.copy(d / "questions.csv", d / "questions.before-review.csv")
    rem = d / "removed_questions.csv"
    new = not rem.exists()
    with rem.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["why_removed"], extrasaction="ignore")
        if new: w.writeheader()
        w.writerows(gone)
    tmp = d / "questions.tmp"
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(keep)
    tmp.replace(d / "questions.csv")
print(f"{d.parent.name}: {len(rows)} -> {len(keep)} (removed {len(gone)})")
