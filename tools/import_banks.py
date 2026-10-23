#!/usr/bin/env python3
"""Import community question banks (the research agent's temp/sources/MANIFEST.md) into the exam folders.

python tools/import_banks.py [--dry] [ccaof ccdvf ccarf ccarp]

Only sources with an open licence (MIT, Apache, BSD, ISC, MPL, CC0, CC BY, Unlicense; never NC / all rights reserved / none
stated) in a structured format (JSON, or JS/TS data modules evaluated with Node) are taken, because the public site shows them.
Each item is normalised (question, lettered options, correct letters, explanation), given an official domain (the item's own
domain text when it matches, else a naive-Bayes guess trained on the exam's current bank), filtered (3+ options, a resolved
answer, 40+ character stem) and de-duplicated against the bank and the other sources (same normalised stem, or 75% of the
content words in common). Rows land in <exam folder>/sources/online/community_2026-10-23.json; the exam's scrape.py merges them.
"""
from __future__ import annotations

import csv
import json
import math
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJ = ROOT.parent
MANIFEST = ROOT / "temp" / "sources" / "MANIFEST.md"
OUT_NAME = "community_2026-10-23.json"
FOLDER = {"ccaof": "CCAO-F", "ccdvf": "CCDV-F", "ccarf": "CCAR-F", "ccarp": "CCAR-P"}
EXAM_LABEL = {"CCAO-F": "ccaof", "CCDV-F": "ccdvf", "CCAR-F": "ccarf", "CCAR-P": "ccarp"}
LET = "ABCDEFGH"
OPEN = re.compile(r"\b(mit|apache|bsd|isc|mpl|cc0|cc[- ]by[- ]4|cc by|unlicense)\b", re.I)
CLOSED = re.compile(r"\b(nc\b|non-?commercial|rights reserved|none stated|proprietary|paid|copyright)", re.I)
Q_KEYS = ("question", "stem", "prompt", "text", "q", "title", "questionText", "question_text")
O_KEYS = ("options", "choices", "answers", "alternatives", "opts")
A_KEYS = ("correct", "answer", "correctAnswer", "correct_answer", "correctAnswers", "correct_answers", "correctIndex", "correct_index",
          "answerIndex", "answer_index", "correctOption", "correct_option", "correctOptions", "solution", "key", "correctLetter")
E_KEYS = ("explanation", "rationale", "why", "reason", "feedback", "explain")
D_KEYS = ("domain", "category", "topic", "section", "area", "tag", "module")
STOP = set("the a an of to and in for is are be that this with on as by or it its which what should most your you from at their they can will would into if not when use using".split())

NODE_EVAL = r'''
const vm = require("vm"), fs = require("fs");
let src = fs.readFileSync(process.argv[2], "utf8");
src = src.replace(/^\s*import\s[^;]*;?\s*$/gm, "")
         .replace(/^\s*export\s+default\s+/gm, "module.exports.__default = ")
         .replace(/^\s*export\s+(const|let|var|function|class)\s/gm, "$1 ")
         .replace(/^\s*export\s*\{[^}]*\};?\s*$/gm, "")
         .replace(/\bas\s+const\b/g, "").replace(/\bsatisfies\s+[\w.<>\[\]]+/g, "")
         .replace(/^\s*(export\s+)?(interface|type)\s+\w+[^{=]*(\{[\s\S]*?\n\}|=[^;]*;)/gm, "")
         .replace(/(const|let|var)\s+(\w+)\s*:\s*[^=\n]+=/g, "$1 $2 =");
const sandbox = { module: { exports: {} }, exports: {}, window: {}, document: {}, globalThis: {}, self: {},
                  console: { log() {}, warn() {}, error() {} }, require: () => ({}), localStorage: { getItem: () => null, setItem() {} } };
sandbox.exports = sandbox.module.exports;
vm.createContext(sandbox);
const names = [...src.matchAll(/^(?:const|let|var)\s+(\w+)\s*=/gm)].map(m => m[1]);
const wrapped = src + "\n;__out = {" + names.map(n => `${JSON.stringify(n)}: (typeof ${n} !== 'undefined' ? ${n} : undefined)`).join(",") + ", __exports: module.exports, __window: window};";
try { vm.runInContext(wrapped, sandbox, { timeout: 8000 }); } catch (e) { console.error("EVAL_FAIL " + String(e.message).slice(0, 120)); process.exit(2); }
process.stdout.write(JSON.stringify(sandbox.__out, (k, v) => typeof v === "function" ? undefined : v));
'''


def manifest_rows() -> list[dict]:
    rows = []
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| CC"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 6 or cells[0] not in EXAM_LABEL:
            continue
        rows.append({"exam": EXAM_LABEL[cells[0]], "path": ROOT / cells[1].strip("`"), "url": cells[2], "lic": cells[3], "qs": cells[4]})
    return rows


def open_licence(lic: str) -> bool:
    return bool(OPEN.search(lic)) and not CLOSED.search(lic)


def load_structure(path: Path):
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8-sig"))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(NODE_EVAL)
        helper = f.name
    r = subprocess.run(["node", helper, str(path)], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not r.stdout.strip():
        raise ValueError(r.stderr.strip()[:120] or "no output")
    return json.loads(r.stdout)


def first(d: dict, keys) -> object:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in low and low[k.lower()] not in (None, ""):
            return low[k.lower()]
    return None


def is_item(x) -> bool:
    return isinstance(x, dict) and first(x, Q_KEYS) is not None and first(x, O_KEYS) is not None


def find_lists(x, out: list) -> None:
    """Every list where most elements look like question items, anywhere in the structure."""
    if isinstance(x, list):
        if x and sum(is_item(e) for e in x) >= max(1, len(x) * 0.5):
            out.append([e for e in x if is_item(e)])
        else:
            for e in x:
                find_lists(e, out)
    elif isinstance(x, dict):
        for v in x.values():
            find_lists(v, out)


def norm_options(raw) -> tuple[list[str], dict[str, str]]:
    """-> (texts in order, letter/id -> text) ; dict-form options keep their own keys."""
    if isinstance(raw, dict):
        keys = sorted(raw, key=lambda k: (len(str(k)), str(k)))
        texts = [str(raw[k]).strip() for k in keys]
        return texts, {str(k).strip().upper(): str(raw[k]).strip() for k in keys}
    if not isinstance(raw, list):
        return [], {}
    texts, keyed = [], {}
    for i, o in enumerate(raw):
        if isinstance(o, dict):
            t = first(o, ("text", "label", "option", "value", "content", "answer", "title"))
            k = first(o, ("key", "id", "letter", "value", "label")) if first(o, ("text", "label", "option", "content", "answer", "title")) else None
            t = str(t).strip() if t is not None else ""
            k = str(k).strip().upper() if k is not None and len(str(k).strip()) <= 2 else LET[i] if i < 8 else ""
        else:
            t, k = str(o).strip(), LET[i] if i < 8 else ""
        m = re.match(r"^\(?([A-Ha-h])[\).:\-]\s+(.*)$", t)         # "A) text" inside the option text
        if m and len(t) > 4:
            k, t = m.group(1).upper(), m.group(2).strip()
        texts.append(t)
        if k:
            keyed[k] = t
    return texts, keyed


def resolve_answer(raw, texts: list[str], keyed: dict[str, str], item: dict) -> list[str]:
    """Correct option letters (A..) in the order of `texts`, from a letter, a list, an index, an option id/text or a flag on the options."""
    pos = {t: LET[i] for i, t in enumerate(texts) if i < 8}
    pos_key = {k: LET[texts.index(v)] for k, v in keyed.items() if v in texts and texts.index(v) < 8}
    if raw is None:                                               # flags on the options themselves
        opts = first(item, O_KEYS)
        if isinstance(opts, list):
            out = [LET[i] for i, o in enumerate(opts) if isinstance(o, dict) and i < 8 and str(first(o, ("correct", "isCorrect", "is_correct", "right"))).lower() in ("true", "1")]
            return out
        return []
    vals = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    for v in vals:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            i = int(v)
            if 0 <= i < len(texts) and i < 8:
                out.append(LET[i])
            continue
        s = str(v).strip()
        if re.fullmatch(r"[A-Ha-h]", s):
            letter = s.upper()
            out.append(pos_key.get(letter, letter))
        elif re.fullmatch(r"[A-Ha-h](\s*[,/&]\s*[A-Ha-h])+", s):
            out += [pos_key.get(c.upper(), c.upper()) for c in re.findall(r"[A-Ha-h]", s)]
        elif s.isdigit() and 0 <= int(s) < len(texts):
            out.append(LET[int(s)])
        elif s in pos:
            out.append(pos[s])
        elif s.upper() in pos_key:
            out.append(pos_key[s.upper()])
        else:
            m = re.match(r"^\(?([A-Ha-h])[\).:\-]?\s", s + " ")
            if m and len(s) <= 3:
                out.append(m.group(1).upper())
    return sorted({l for l in out if l in LET[:len(texts)]})


def norm(stem: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", stem.lower()).strip()


def words(text: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", text.lower()).split() if w not in STOP and len(w) > 2}


class Bayes:
    """Naive Bayes over content words, trained on the exam's own bank; good enough to route a question to a domain."""

    def __init__(self, rows: list[dict]):
        self.docs, self.counts, self.vocab = Counter(), defaultdict(Counter), set()
        for r in rows:
            ws = words(r["question"] + " " + r["options"])
            self.docs[r["domain"]] += 1
            self.counts[r["domain"]].update(ws)
            self.vocab |= ws
        self.total = {d: sum(c.values()) for d, c in self.counts.items()}

    def guess(self, text: str) -> str:
        ws = words(text)
        best, score = None, -math.inf
        n = sum(self.docs.values())
        for d in self.docs:
            s = math.log(self.docs[d] / n)
            for w in ws:
                s += math.log((self.counts[d][w] + 0.5) / (self.total[d] + 0.5 * len(self.vocab)))
            if s > score:
                best, score = d, s
        return best


def domain_for(text, official: list[str], bayes: Bayes, question: str) -> str:
    if text:
        t = norm(str(text))
        for d in official:
            nd = norm(d)
            if t == nd or (len(t) > 6 and (t in nd or nd in t)):
                return d
        tw = words(str(text))
        best = max(official, key=lambda d: len(tw & words(d)) / max(1, len(words(d))))
        if tw and len(tw & words(best)) / max(1, len(words(best))) >= 0.6:
            return best
    return bayes.guess(question)


def import_exam(key: str, rows: list[dict], dry: bool) -> None:
    folder = PROJ / FOLDER[key]
    bank = list(csv.DictReader((folder / "data" / "questions.csv").open(encoding="utf-8")))
    official = sorted({r["domain"] for r in bank})
    bayes = Bayes(bank)
    stems = {norm(r["question"]) for r in bank}
    seen_words = [words(r["question"]) for r in bank]
    out, per_source, rejected = [], Counter(), Counter()
    for src in rows:
        try:
            data = load_structure(src["path"])
        except Exception as exc:
            rejected[f"unparsable: {src['path'].name[:50]} ({str(exc)[:60]})"] += 1
            continue
        lists: list = []
        find_lists(data, lists)
        items = [i for lst in lists for i in lst]
        label = re.sub(r"^https?://github\.com/", "", src["url"]).split("/blob/")[0] + f" ({src['lic'].split('(')[0].strip()[:20]})"
        for it in items:
            q = str(first(it, Q_KEYS) or "").strip()
            texts, keyed = norm_options(first(it, O_KEYS))
            texts = [t for t in texts if t]
            if len(q) < 40 or len(texts) < 3 or len(texts) > 8:
                rejected["shape"] += 1
                continue
            correct = resolve_answer(first(it, A_KEYS), texts, keyed, it)
            if not correct:
                rejected["no answer"] += 1
                continue
            nq = norm(q)
            if nq in stems:
                rejected["duplicate stem"] += 1
                continue
            qw = words(q)
            if len(qw) >= 6 and any(len(qw & w) / len(qw | w) >= 0.75 for w in seen_words):
                rejected["near duplicate"] += 1
                continue
            expl = first(it, E_KEYS)
            if isinstance(expl, dict):
                expl = " ".join(f"{k}: {v}" for k, v in expl.items())
            dom = domain_for(first(it, D_KEYS), official, bayes, q + " " + " ".join(texts))
            stems.add(nq); seen_words.append(qw)
            out.append({"id": f"CM{len(out) + 1:04d}", "domain": dom, "question": q,
                        "options": "\n".join(f"{LET[i]}. {t}" for i, t in enumerate(texts)), "correct": ",".join(correct),
                        "explanation": str(expl or "").strip()[:1500], "source": label})
            per_source[label] += 1
    print(f"== {key}: {len(out)} new rows from {len(per_source)} sources | rejected {dict(rejected.most_common(6))}")
    for s, n in per_source.most_common(12):
        print(f"   {n:4d}  {s}")
    print("   domains:", dict(Counter(r['domain'] for r in out)))
    if not dry:
        dst = folder / "sources" / "online" / OUT_NAME
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        print("   wrote", dst)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    only = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")), "")   # --only=tonydunlop: just those files, written to community_<only>.json (the main file is a full rebuild; a rerun dedupes against it)
    if only:
        OUT_NAME = f"community_{only}.json"
    rows = [r for r in manifest_rows() if open_licence(r["lic"]) and r["path"].suffix.lower() in (".json", ".js", ".ts") and r["path"].exists() and only in str(r["path"])]
    for key in args or FOLDER:
        import_exam(key, [r for r in rows if r["exam"] == key], "--dry" in sys.argv)
