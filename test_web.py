"""python test_web.py — recorder streaming, answer grading, home/study, no Claude calls.

Runs against copies of the sibling bot folders so the real state.json is never touched.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp(prefix="portal-test-"))
specs = []
for key, src in (("ccarp", ROOT.parent / "CCAR-P"), ("sapc03", ROOT.parent / "AWS Certified Solutions Architect - Professional"), ("aipc01", ROOT.parent / "AWS Certified Generative AI Developer - Professional AIP-01")):
    dst = tmp / key
    (dst / "data").mkdir(parents=True)
    for f in ("bot.py", "scrape.py"):
        if (src / f).exists():
            shutil.copy(src / f, dst / f)
    shutil.copytree(src / "assets", dst / "assets")
    for f in ("questions.csv", "study_notes.csv"):
        if (src / "data" / f).exists():
            shutil.copy(src / "data" / f, dst / "data" / f)
    specs.append(f"{key}:{dst}:{tmp / 'vault' / key}")
os.environ["EXAMS"] = ",".join(specs)

import web  # noqa: E402

web.EXAMS.update(web.load_exams())
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(web.app)


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


# exams + home
ex = c.get("/api/exams").json()
assert {e["key"] for e in ex} == {"ccarp", "sapc03", "aipc01"}, ex
assert all(e["total"] > 50 for e in ex)
home = c.get("/api/ccarp/home").json()
assert home["pending"] is None and home["coverage"]["seen"] == 0 and home["pass"] == 720
assert c.get("/api/sapc03/home").json()["has_study"] is True
assert c.get("/api/nope/home").status_code == 404

# next -> one question event with full options + buttons; state.pending set
ev = events(c.post("/api/ccarp/act", json={"data": "next"}))
assert len(ev) == 1 and ev[0]["op"] == "add" and ev[0]["question"]["options"], ev
qid = ev[0]["question"]["id"]
assert ev[0]["buttons"][0][0]["data"] == f"a|{qid}|{ev[0]['question']['options'][0]['l']}"
assert c.get("/api/ccarp/home").json()["pending"]["question"]["id"] == qid

# answer correctly (no Claude call on a right answer)
q = web.EXAMS["ccarp"].mod.QUESTIONS[qid]
ev = events(c.post("/api/ccarp/answer", json={"id": qid, "picked": sorted(q["answer"])}))
assert ev[0]["op"] == "meta" and ev[0]["graded"]["right"] is True
ev = ev[1:]
assert ev[0]["html"].startswith("🟢") and ev[0]["buttons"][0][0]["data"].startswith("e|"), ev
st = web.EXAMS["ccarp"].mod.load_state()
assert st["ok"][qid] == 1 and st["pending"] is None and st["log"][-1]["first"] is True

# wrong answer: tutor monkeypatched -> verdict, "drawing" note edited/deleted, diagram or fallback
mod = web.EXAMS["ccarp"].mod


async def fake_tutor(q, picked):
    return {"teach": "Because X.", "kind": "mermaid", "code": "flowchart LR\nA-->B"}


mod.ask_claude = fake_tutor
mod.render = lambda kind, code: b"\x89PNG fake"
wrong = next(l for l in q["letters"] if l not in q["answer"])
ev = events(c.post("/api/ccarp/answer", json={"id": qid, "picked": [wrong]}))
ops = [(e["op"], e.get("kind")) for e in ev][1:]
ev = ev[1:]
assert ops[0] == ("add", "text") and ev[0]["html"].startswith("🔴"), ev[0]
assert ("delete", None) in ops and ("add", "photo") in ops, ops
assert mod.load_state()["last"]["teach"] == "Because X."
# Explain more after a wrong answer is free (no Claude call)
ev = events(c.post("/api/ccarp/act", json={"data": f"e|{qid}|{wrong}"}))
assert "Because X." in ev[0]["html"]

# stats, bad input, reset
assert "PROGRESS" in events(c.post("/api/ccarp/act", json={"data": "stats"}))[0]["html"]
assert events(c.post("/api/ccarp/answer", json={"id": qid, "picked": ["Z"]}))[0]["op"] == "error"
assert events(c.post("/api/ccarp/act", json={"data": "bogus"}))[0]["op"] == "error"
events(c.post("/api/ccarp/act", json={"data": "reset"}))
assert mod.load_state() == {"ok": {}, "bad": {}}

# study JSON + vault line
study = c.get("/api/ccarp/study").json()
assert study["reading"]["domains"][0]["links"][0]["url"].startswith("http") and study["reading"]["start"]
assert c.get("/api/sapc03/study").json()["has_cards"] is True
vault_lines = list((tmp / "vault" / "ccarp" / "Activity").rglob("*.md"))
assert vault_lines and "🌐 web" in vault_lines[0].read_text(encoding="utf-8")

# report meta event carries metrics + verdict
ev = events(c.post("/api/sapc03/act", json={"data": "report"}))
assert ev[0]["op"] == "meta" and "score" in ev[0]["metrics"] and ev[1]["html"].startswith("📊")

# exam mode: start picks unseen first, submit grades everything, writes state and returns a result sheet
ex = c.post("/api/sapc03/exam/start", json={"n": 10}).json()
assert len(ex["questions"]) == 10 and ex["minutes"] == 24 and ex["questions"][0]["options"]
answers = {q["id"]: [q["options"][0]["l"]] for q in ex["questions"]}
res = c.post("/api/sapc03/exam/submit", json={"answers": answers, "minutes_used": 3.5}).json()
assert res["n"] == 10 and 0 <= res["score"] <= 1000 and res["pass"] == 750 and len(res["results"]) == 10
assert all("explanation" in r and r["correct"] for r in res["results"])
st = web.EXAMS["sapc03"].mod.load_state()
assert sum(st["ok"].values()) + sum(st["bad"].values()) >= 10 and st["log"][-1]["exam"] is True
assert c.get("/api/sapc03/home").json()["exam_answers"] == 10

shutil.rmtree(tmp, ignore_errors=True)
print("ok: all web tests passed")
