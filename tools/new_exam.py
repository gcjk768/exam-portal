#!/usr/bin/env python3
"""Generate a quiz-bot folder for a Claude certification from the CCAR-P template and build its bank.

python tools/new_exam.py ccaof ccdvf ccarf   -> ../CCAO-F, ../CCDV-F, ../CCAR-F

Each folder gets bot.py (the CCAR-P bot with the exam's name, weights, pass mark, colour), scrape.py (one source:
the exam's claudearchitects.org mock page), Dockerfile, compose.yaml, README.md, test_bot.py, the copied support files,
and data/questions.csv built by scrape.refresh(). Re-running regenerates the code files and leaves data/ alone.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # exam-portal
PROJ = ROOT.parent
TPL = PROJ / "CCAR-P"

EXAMS = {
    "ccaof": dict(folder="CCAO-F", short="CCAO-F", name="Claude Certified Associate - Foundations", n=60, minutes=120, pass_=720,
                  bank=376, accent="#86c17a",
                  weights={"Output Evaluation and Validation": 21, "Workflow Integration and Solution Design": 16,
                           "Governance, Risk, and Responsible Use": 15, "Prompting and Task Execution": 14, "Product and Model Selection": 12,
                           "Configuration and Knowledge Management": 12, "Troubleshooting and Optimization": 10},
                  arch={"Workflow Integration and Solution Design"}),
    "ccdvf": dict(folder="CCDV-F", short="CCDV-F", name="Claude Certified Developer - Foundations", n=53, minutes=120, pass_=720,
                  bank=731, accent="#e58ab4",
                  weights={"Applications and Integration": 33.1, "Model Selection and Optimization": 16.8, "Agents and Workflows": 14.7,
                           "Prompt and Context Engineering": 11, "Tools and MCPs": 10.6, "Security and Safety": 8.1, "Claude Code": 3.1,
                           "Eval, Testing, and Debugging": 2.6},
                  arch={"Applications and Integration", "Agents and Workflows", "Tools and MCPs"}),
    "ccarf": dict(folder="CCAR-F", short="CCAR-F", name="Claude Certified Architect - Foundations", n=60, minutes=120, pass_=720,
                  bank=516, accent="#8fcfe6",
                  weights={"Agentic Architecture & Orchestration": 27, "Claude Code Configuration & Workflows": 20,
                           "Prompt Engineering & Structured Output": 20, "Tool Design & MCP Integration": 18, "Context Management & Reliability": 15},
                  arch={"Agentic Architecture & Orchestration", "Tool Design & MCP Integration"}),
}

WEIGHTS_OLD = '''WEIGHTS = {"Integration": 19, "Solution Design & Architecture": 17, "Evaluation, Testing & Optimization": 16,
           "Governance, Safety & Risk Management": 14, "Stakeholder Communication & Lifecycle Management": 14,
           "Claude Models, Prompting & Context Engineering": 13, "Developer Productivity & Operational Enablement": 7}'''
COACH_OLD = '''COACH = """You are an exam coach for Claude Certified Architect - Professional (CCAR-P: 63 questions, 120 min,
pass 720/1000, domain weights Integration 19%, Solution Design 17%, Evaluation 16%, Governance 14%,
Stakeholder 14%, Models & Prompting 13%, Developer Productivity 7%). Given the student's practice'''

SCRAPE = '''#!/usr/bin/env python3
"""Build and refresh data/questions.csv for {short} from the claudearchitects.org mock exam page: community-written
practice items with explanations, free and without sign-up ("not real exam questions"). refresh() keeps the rows already
in the CSV (ids stay stable, so progress in state.json survives) and appends new ones. Usage: python scrape.py"""
from __future__ import annotations

import csv
import json
import os
import re
import urllib.request
from pathlib import Path

EXAM = "{short}"
URL = f"https://claudearchitects.org/mockexams/{{EXAM}}"
OUT = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data")) / "questions.csv"
FIELDS = ["id", "domain", "question", "options", "correct", "explanation", "source"]
UA = {{"User-Agent": "Mozilla/5.0 ({key}-quiz-bot; personal study use)"}}
LETTERS = "ABCDEFGH"


def _get(url: str) -> str:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")


def _next_payload(html: str) -> str:
    """Next.js RSC payload: join the self.__next_f.push chunks and unescape them once."""
    chunks = re.findall(r'self\\.__next_f\\.push\\(\\[1,"((?:[^"\\\\]|\\\\.)*)"\\]\\)', html)
    return json.loads('"' + "".join(chunks) + '"')


def _json_after(text: str, key: str):
    i = text.find(key)
    if i < 0:
        raise ValueError(f"{{key}} not found; page layout changed?")
    return json.JSONDecoder().raw_decode(text, i + len(key))[0]


def fetch() -> list[dict]:
    rows = []
    for q in _json_after(_next_payload(_get(URL)), '"questions":'):
        opts = sorted(q["options"], key=lambda o: o["id"])            # option ids are letters; keep them in order
        ids = [o["id"] for o in opts]
        correct = sorted(LETTERS[ids.index(c)] for c in q["correct"])
        rows.append({{"id": q["id"], "domain": q["domain"].strip(), "question": q["question"].strip(),
                     "options": "\\n".join(f"{{LETTERS[k]}}. {{o['text'].strip()}}" for k, o in enumerate(opts)),
                     "correct": ",".join(correct), "explanation": q.get("explanation", "").strip(), "source": "claudearchitects.org"}})
    return rows


def load() -> list[dict]:
    if not OUT.exists():
        return []
    with OUT.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def refresh() -> dict:
    """Fetch the page, append rows whose id is new, keep everything else. Same report shape as the CCAR-P bot expects."""
    rows = load()
    ids = {{r["id"] for r in rows}}
    report = {{"before": len(rows), "added": {{}}, "dupes": 0, "failed": {{}}}}
    try:
        fetched = fetch()
    except Exception as exc:                       # network / layout change: keep the bank we have
        report["failed"]["claudearchitects"] = f"{{type(exc).__name__}}: {{str(exc)[:80]}}"
        fetched = []
    added = 0
    for r in fetched:
        if r["id"] in ids or not r["correct"] or r["options"].count("\\n") < 1:
            continue
        ids.add(r["id"])
        rows.append(r)
        added += 1
    report["added"]["claudearchitects"] = added
    OUT.parent.mkdir(exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(OUT)
    report["total"] = len(rows)
    return report


if __name__ == "__main__":
    print(json.dumps(refresh(), indent=1))
'''

README = '''# {short} quiz bot

Telegram quiz bot and question bank for the **{name}** exam ({short}): {n} items in {minutes} minutes, pass {pass_}/1000
(the pass mark is assumed to match the other Claude certifications until the vendor publishes it). The same folder is
mounted by the [Exam Portal](https://github.com/gcjk768/exam-portal), which drives this `bot.py` in a browser (quiz,
timed mock exam, study notes, spaced repetition, decoder).

Question bank: the community-written practice items on [claudearchitects.org]({url}) (free, no sign-up, with
explanations; the site states they are original study items aligned to the published objectives, not real exam
questions). `python scrape.py` builds or refreshes `data/questions.csv`; ids stay stable so progress in
`data/state.json` survives a refresh.

Domains and official weights: {weights}.

Run: fill `bot.env` from `bot.env.example`, then `python bot.py` (or `docker compose up -d --build`). Tests: `python test_bot.py`.

Generated from the CCAR-P bot by `exam-portal/tools/new_exam.py`; the code is the same bot with this exam's name,
domains, weights, pass mark and colour.
'''

TEST = '''"""python test_bot.py — the bank loads, the picker works, an empty state scores 0 (no Telegram, no Claude)."""
import os
from datetime import datetime

os.environ.setdefault("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
import bot  # noqa: E402

assert len(bot.QUESTIONS) > 100 and all(q["letters"] and q["answer"] <= set(q["letters"]) for q in bot.QUESTIONS.values())
doms = {q["domain"] for q in bot.QUESTIONS.values()}
assert doms <= set(bot.WEIGHTS), doms - set(bot.WEIGHTS)
st = {"ok": {}, "bad": {}}
q = bot.pick_question(st)
assert q["id"] in bot.QUESTIONS and bot.question_text(q).startswith(bot.TITLE["q"])
m = bot.metrics(st, datetime.now(bot.SGT))
assert m["score"] == 0 and bot.verdict(m).startswith("⚪")
print(f"ok: {len(bot.QUESTIONS)} questions, {len(doms)} domains")
'''

COPY = ["requirements.txt", "bot.env.example", "LICENSE", ".gitignore", ".dockerignore", "run.ps1", "assets/claude.png"]


def gen_bot(key: str, e: dict) -> str:
    src = (TPL / "bot.py").read_text(encoding="utf-8")
    items = list(e["weights"].items())
    w_lines, line = [], "WEIGHTS = {"
    for i, (d, w) in enumerate(items):
        part = f'{json.dumps(d)}: {w}' + ("," if i < len(items) - 1 else "}")
        if len(line) + len(part) > 118:
            w_lines.append(line.rstrip())
            line = "           "
        line += part + " "
    w_lines.append(line.rstrip())
    weights_new = "\n".join(w_lines)
    short_w = ", ".join(f"{d} {w}%" for d, w in items)
    coach_new = (f'COACH = """You are an exam coach for {e["name"]} ({e["short"]}: {e["n"]} questions, {e["minutes"]} min,\n'
                 f'pass {e["pass_"]}/1000, domain weights {short_w}). Given the student\'s practice')
    reps = [
        ('"""CCAR-P quiz bot: random practice questions in Telegram, graded with inline buttons.',
         f'"""{e["short"]} quiz bot: random practice questions in Telegram, graded with inline buttons.'),
        ('logging.getLogger("ccarp")', f'logging.getLogger("{key}")'),
        ('"ccarp-work"', f'"{key}-work"'),
        ('"ccarp-quiz-bot"', f'"{key}-quiz-bot"'),
        ('"ccarp-diag-"', f'"{key}-diag-"'),
        ('"🎓 <b>CCAR-P QUIZ</b> · 367 practice questions\\n\\n"', f'"🎓 <b>{e["short"]} QUIZ</b> · {e["bank"]} practice questions\\n\\n"'),
        (WEIGHTS_OLD, weights_new),
        ("PASS = 720", f"PASS = {e['pass_']}"),
        ('"🔴 NOT YET: below the 720 pass mark"', f'"🔴 NOT YET: below the {e["pass_"]} pass mark"'),
        (COACH_OLD, coach_new),
        ("You are a tutor for the Claude Certified Architect - Professional (CCAR-P) exam.",
         f"You are a tutor for the {e['name']} ({e['short']}) exam."),
        ('ARCH_DOMAINS = {"Solution Design & Architecture", "Integration"}',
         "ARCH_DOMAINS = {" + ", ".join(json.dumps(d) for d in sorted(e["arch"])) + "}"),
        ('ACCENT = "#d48a63"', f'ACCENT = "{e["accent"]}"'),
    ]
    for old, new in reps:
        assert src.count(old) == 1, (key, old[:70], src.count(old))
        src = src.replace(old, new)
    return src


def generate(key: str) -> None:
    e = EXAMS[key]
    dst = PROJ / e["folder"]
    (dst / "data").mkdir(parents=True, exist_ok=True)
    (dst / "assets").mkdir(exist_ok=True)
    (dst / "bot.py").write_text(gen_bot(key, e), encoding="utf-8")
    (dst / "scrape.py").write_text(SCRAPE.format(short=e["short"], key=key), encoding="utf-8")
    (dst / "Dockerfile").write_text((TPL / "Dockerfile").read_text(encoding="utf-8")
                                    .replace("COPY bot.py scrape.py online_parsers.py ./", "COPY bot.py scrape.py ./"), encoding="utf-8")
    (dst / "compose.yaml").write_text((TPL / "compose.yaml").read_text(encoding="utf-8").replace("ccarp", key), encoding="utf-8")
    (dst / "README.md").write_text(README.format(**{k: v for k, v in e.items() if k != "weights"},
                                                 url=f"https://claudearchitects.org/mockexams/{e['short']}",
                                                 weights=", ".join(f"{d} {w}%" for d, w in e["weights"].items())), encoding="utf-8")
    (dst / "test_bot.py").write_text(TEST, encoding="utf-8")
    for f in COPY:
        if (TPL / f).exists():
            shutil.copy(TPL / f, dst / f)
    if not (dst / "data" / "state.json").exists():
        (dst / "data" / "state.json").write_text('{"ok": {}, "bad": {}}', encoding="utf-8")
    out = subprocess.run([sys.executable, "scrape.py"], cwd=dst, capture_output=True, text=True, timeout=180)
    print(f"{key}: {dst.name} written; scrape -> {out.stdout.strip().replace(chr(10), ' ')[:160]} {out.stderr.strip()[-200:]}")


if __name__ == "__main__":
    for k in sys.argv[1:] or EXAMS:
        generate(k)
