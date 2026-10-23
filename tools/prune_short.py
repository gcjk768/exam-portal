"""Move free-bank questions that are below exam standard out of a bot's questions.csv into removed_questions.csv (with a reason).
Usage: python tools/prune_short.py <bot folder> [min_stem_chars=100]
Below standard = a stem shorter than min_stem_chars (the real exam is scenario based), fewer than 4 options, or one-word options.
Paid mocks (Tutorials Dojo, Udemy / Microsoft Forms) are never touched. The bot's scrape.py skips ids and stems listed in removed_questions.csv."""
import csv, shutil, statistics, sys
from pathlib import Path

d = Path(sys.argv[1]) / "data"
min_stem = int(sys.argv[2]) if len(sys.argv) > 2 else 100
paid = lambda s: s == "Tutorials Dojo" or "Udemy" in s or s.startswith("Microsoft Forms mock")
with (d / "questions.csv").open(encoding="utf-8") as f:
    rd = csv.DictReader(f); fields = rd.fieldnames; rows = list(rd)

def why(r):
    opts = [o for o in r["options"].split("\n") if o.strip()]
    if len(r["question"]) < min_stem: return f"stem under {min_stem} chars (not scenario based)"
    if len(opts) < 4: return "fewer than 4 options"
    if statistics.mean(len(o) for o in opts) < 25: return "one-word options"

keep, gone = [], []
for r in rows:
    w = None if paid(r["source"]) else why(r)
    (gone if w else keep).append({**r, "why_removed": w})
rem = d / "removed_questions.csv"
if gone:
    shutil.copy(d / "questions.csv", d / "questions.before-quality-prune.csv")
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
