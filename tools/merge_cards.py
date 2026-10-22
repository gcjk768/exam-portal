#!/usr/bin/env python3
"""python tools/merge_cards.py [exam ...] — write the agents' temp/agents/cards-<exam>.json into study/<exam>.json
(domains[].cards). Domain names must match exactly; the file keeps its 1-space indent and raw UTF-8."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AG = ROOT / "temp" / "agents"

for key in sys.argv[1:] or sorted(p.stem[6:] for p in AG.glob("cards-*.json")):
    src, sj = AG / f"cards-{key}.json", ROOT / "study" / f"{key}.json"
    cards = json.loads(src.read_text(encoding="utf-8"))
    study = json.loads(sj.read_text(encoding="utf-8"))
    names = [d["domain"] for d in study["domains"]]
    missing, extra = [n for n in names if n not in cards], [n for n in cards if n not in names]
    assert not missing and not extra, (key, "missing", missing, "extra", extra)
    for d in study["domains"]:
        d["cards"] = [{"q": " ".join(c["q"].split()), "a": " ".join(c["a"].split())} for c in cards[d["domain"]]]
    sj.write_text(json.dumps(study, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(key, {d["domain"][:28]: len(d["cards"]) for d in study["domains"]})
