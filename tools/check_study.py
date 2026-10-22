#!/usr/bin/env python3
"""python tools/check_study.py study/<exam>.json [...]  — the same rules test_web.py enforces on a study JSON, as a one-file
checker the content agents can run while they write. Exit 1 on the first problem of each file; prints counts when clean."""
import json
import re
import sys
import urllib.request

VID = re.compile(r"[\w-]{11}")


def check(path: str, oembed: bool = False) -> list[str]:
    d = json.load(open(path, encoding="utf-8"))
    bad = []
    for k in ("exam", "intro", "start", "domains"):
        if not d.get(k):
            bad.append(f"missing top-level {k}")
    for l in d.get("start", []):
        if not (l.get("title") and str(l.get("url", "")).startswith(("http", "#"))):
            bad.append(f"start link without title/url: {l}")
    n_notes = n_cards = 0
    for dom in d.get("domains", []):
        name = dom.get("domain", "?")
        v = dom.get("video") or {}
        if not (VID.fullmatch(str(v.get("id", ""))) and v.get("title")):
            bad.append(f"{name}: domain video needs id (11 chars) + title")
        if not str(dom.get("diagram", "")).startswith("flowchart"):
            bad.append(f"{name}: domain diagram must start with 'flowchart'")
        sm = dom.get("summary") or {}
        if not (sm.get("tests") and 4 <= len(sm.get("must_know", [])) <= 8 and sm.get("traps")):
            bad.append(f"{name}: summary needs tests, 4-8 must_know, traps")
        notes = dom.get("notes") or []
        if not notes:
            bad.append(f"{name}: no notes")
        for n in notes:
            n_notes += 1
            if not (n.get("title") and n.get("gist") and 2 <= len(n.get("points", [])) <= 6 and n.get("tip")):
                bad.append(f"{name} / {n.get('title')}: needs title, gist, 2-6 points, tip")
            if not (str(n.get("diagram", "")).startswith("flowchart") and str((n.get("source") or {}).get("url", "")).startswith("http")):
                bad.append(f"{name} / {n.get('title')}: needs a flowchart diagram and a source url")
            if n.get("video") is not None and not VID.fullmatch(str(n["video"].get("id", ""))):
                bad.append(f"{name} / {n.get('title')}: bad video id")
            for lab in re.findall(r"[\[\(\{]+\"?([^\]\)\}]*?)\"?[\]\)\}]+", str(n.get("diagram", ""))):
                if any(ch in lab for ch in '"();'):
                    bad.append(f"{name} / {n.get('title')}: diagram label with quote/paren/semicolon: {lab[:40]}")
        cards = dom.get("cards") or []
        if not 8 <= len(cards) <= 20:
            bad.append(f"{name}: {len(cards)} cards (need 8-20)")
        for cd in cards:
            n_cards += 1
            if not (cd.get("q") and cd.get("a") and len(cd["q"].split()) <= 25 and len(cd["a"].split()) <= 30):
                bad.append(f"{name}: card out of limits: {str(cd)[:80]}")
        if oembed:
            for x in [dom] + notes:
                vid = (x.get("video") or {}).get("id")
                if vid:
                    try:
                        urllib.request.urlopen(f"https://www.youtube.com/oembed?format=json&url=https://youtu.be/{vid}", timeout=20)
                    except Exception as exc:
                        bad.append(f"{name}: video {vid} not embeddable ({exc})")
    print(f"{path}: {len(d.get('domains', []))} domains, {n_notes} notes, {n_cards} cards, {len(bad)} problems")
    return bad


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    rc = 0
    for p in args:
        for b in check(p, oembed="--oembed" in sys.argv):
            print("  -", b)
            rc = 1
    sys.exit(rc)
