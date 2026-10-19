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
for key, src in (("ccarp", ROOT.parent / "CCAR-P"), ("sapc03", ROOT.parent / "AWS Certified Solutions Architect - Professional"), ("aipc01", ROOT.parent / "AWS Certified Generative AI Developer - Professional AIP-01"), ("cka", ROOT.parent / "CKA")):
    dst = tmp / key
    (dst / "data").mkdir(parents=True)
    for f in ("bot.py", "scrape.py", "online_parsers.py"):
        if (src / f).exists():
            shutil.copy(src / f, dst / f)
    shutil.copytree(src / "assets", dst / "assets")
    for f in ("questions.csv", "study_notes.csv", "domains.json"):
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


# public (Tailscale Funnel) link: no Claude, no reset; local keeps both
pub = {"Tailscale-Funnel-Request": "?1"}
assert c.get("/api/access").json() == {"public": False} and c.get("/api/access", headers=pub).json() == {"public": True}
for d in ("reset", "report", "refresh", "e|x|A"):
    ev = events(c.post("/api/ccarp/act", json={"data": d}, headers=pub))
    assert ev[0]["op"] == "error" and "public" in ev[0]["html"], (d, ev)

assert 'data-public="1"' in c.get("/", headers=pub).text and 'data-public="1"' not in c.get("/").text
assert c.get("/static/app.css").headers["cache-control"] == "no-cache"
assert c.get("/api/access", headers={"Host": "owner-cck-nas.tailc4f63b.ts.net:10000"}).json() == {"public": True}   # tailnet device opening the public name
qq = c.get(f"/api/ccarp/question/{web.EXAMS['ccarp'].mod.QUESTIONS and next(iter(web.EXAMS['ccarp'].mod.QUESTIONS))}").json()
assert qq["correct"] and qq["options"] and "explanation" in qq, qq
if tdq := [i for i, q in web.EXAMS["ccarp"].mod.QUESTIONS.items() if q.get("source") == "Tutorials Dojo"]:
    assert c.get(f"/api/ccarp/question/{tdq[0]}", headers=pub).status_code == 404 and c.get(f"/api/ccarp/question/{tdq[0]}").status_code == 200
hp = c.get("/api/ccarp/home", headers=pub).json()
assert hp["score"] == 0 and hp["verdict"] == "" and hp["week"] == {"n": 0, "ok": 0}, hp

# paid Tutorials Dojo questions (ids TD-*) are served on LAN/tailnet only, never on the public link
tdq = [i for i, q in web.EXAMS["ccarp"].mod.QUESTIONS.items() if q.get("source") == "Tutorials Dojo"]
if tdq:
    import random
    random.seed(3)
    for _ in range(25):
        ev = events(c.post("/api/ccarp/act", json={"data": "next"}, headers=pub))
        assert not ev[0]["question"]["id"].startswith("TD-"), ev[0]["question"]["id"]
    assert not any(x["id"].startswith("TD-") for x in c.post("/api/ccarp/exam/start", json={"n": 63}, headers=pub).json()["questions"])
    assert events(c.post("/api/ccarp/answer", json={"id": tdq[0], "picked": ["A"]}, headers=pub))[-1]["op"] == "error"
    assert c.get("/api/exams", headers=pub).json()[0]["total"] == c.get("/api/exams").json()[0]["total"] - len(tdq)
    assert any(x["id"].startswith("TD-") for x in c.post("/api/ccarp/exam/start", json={"n": 1900}).json()["questions"])   # private sees them
    events(c.post("/api/ccarp/act", json={"data": "reset"}))      # the checks above touched the pending question

# exams + home
ex = c.get("/api/exams").json()
assert {e["key"] for e in ex} == {"ccarp", "sapc03", "aipc01", "cka"}, ex
assert all(e["total"] > 50 for e in ex) and next(e for e in ex if e["key"] == "cka")["tasks"] is True
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

# tutor monkeypatched (no real Claude calls)
mod = web.EXAMS["ccarp"].mod


async def fake_tutor(q, picked):
    return {"teach": "Because X.", "kind": "mermaid", "code": "flowchart LR\nA-->B"}


mod.ask_claude = fake_tutor
mod.render = lambda kind, code: b"\x89PNG fake"

# answer correctly -> verdict + Explain more button, no diagram and no Claude call
q = mod.QUESTIONS[qid]
calls = []
mod.ask_claude = lambda q, p: calls.append(1) or fake_tutor(q, p)
ev = events(c.post("/api/ccarp/answer", json={"id": qid, "picked": sorted(q["answer"])}))
assert ev[0]["op"] == "meta" and ev[0]["graded"]["right"] is True
ev = ev[1:]
assert len(ev) == 1 and ev[0]["html"].startswith("🟢") and ev[0]["buttons"][0][0]["data"].startswith("e|"), ev
st = mod.load_state()
assert st["ok"][qid] == 1 and st["pending"] is None and st["log"][-1]["first"] is True

# wrong answer: verdict + Explain more only, still no diagram and no Claude call
wrong = next(l for l in q["letters"] if l not in q["answer"])
ev = events(c.post("/api/ccarp/answer", json={"id": qid, "picked": [wrong]}))[1:]
assert len(ev) == 1 and ev[0]["html"].startswith("🔴") and ev[0]["buttons"][0][0]["data"] == f"e|{qid}|{wrong}", ev
assert mod.load_state()["bad"][qid] == 1 and not calls
# the button is the only thing that draws: text, then the diagram
ev = events(c.post("/api/ccarp/act", json={"data": f"e|{qid}|{wrong}"}))
assert calls and any("Because X." in e.get("html", "") for e in ev) and any(e.get("kind") == "photo" for e in ev), ev

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

# "Quiz me on this topic": a question from that domain only; an unknown domain is a clean error
ev = events(c.post("/api/ccarp/act", json={"data": "d|Integration"}))
assert ev[0].get("question", {}).get("domain") == "Integration", ev[:2]
assert events(c.post("/api/ccarp/act", json={"data": "d|No such domain"}))[0]["op"] == "error"
assert web.EXAMS["ccarp"].mod.PREFER_MERMAID is True          # the portal asks the tutor for Mermaid (icons added in the browser)

# weekly video check: a video YouTube reports gone is hidden from the Study JSON
vid = web.EXAMS["ccarp"].study["domains"][0]["video"]["id"]
assert vid in web.video_ids(web.EXAMS["ccarp"].study)
web.DEAD_VIDEOS.add(vid)
assert c.get("/api/ccarp/study").json()["reading"]["domains"][0]["video"] is None
assert web.EXAMS["ccarp"].study["domains"][0]["video"]["id"] == vid       # the source JSON is untouched
web.DEAD_VIDEOS.clear()

# tutor Mermaid goes to the browser as source (drawn in the portal's style + icons); `diagrams` scripts stay PNG
assert web.EXAMS["sapc03"].mod.render("mermaid", "flowchart LR\nA-->B").startswith(web.MERMAID_SRC)
rq: asyncio.Queue = asyncio.Queue()
asyncio.run(web.Recorder(web.EXAMS["sapc03"], rq).send_photo(web.MERMAID_SRC + b"flowchart LR\nA-->B", caption="map"))
mev = rq.get_nowait()
assert mev["kind"] == "mermaid" and mev["src"] == "flowchart LR\nA-->B" and "png" not in mev, mev

# topic media: every study domain of every exam has a YouTube id + a Mermaid diagram; a question finds its
# domain by name, else by keywords (the same rule as topicFor() in app.js)
import re  # noqa: E402
for sj in (ROOT / "study").glob("*.json"):
    for d in json.loads(sj.read_text(encoding="utf-8"))["domains"]:
        assert re.fullmatch(r"[\w-]{11}", d["video"]["id"]) and d["video"]["title"], (sj.name, d["domain"])
        assert d["diagram"].startswith("flowchart"), (sj.name, d["domain"])
        assert d.get("notes"), (sj.name, d["domain"], "no study notes")
        sm = d.get("summary") or {}
        assert sm.get("tests") and 4 <= len(sm.get("must_know", [])) <= 8 and sm.get("traps"), (sj.name, d["domain"], "summary")
        for n in d["notes"]:                     # study notes: short text, one diagram, optional video, a source
            assert n["title"] and n["gist"] and 2 <= len(n["points"]) <= 6 and n["tip"], (sj.name, n.get("title"))
            assert n["diagram"].startswith("flowchart") and n["source"]["url"].startswith("http"), (sj.name, n["title"])
            assert n.get("video") is None or re.fullmatch(r"[\w-]{11}", n["video"]["id"]), (sj.name, n["title"])
for key, e in web.EXAMS.items():
    ds = e.study["domains"]
    found = sum(any(d["domain"] == q["domain"] for d in ds)
                or any(k in (q["question"] + " " + q["options"]).lower() for d in ds for k in d.get("keys", ()))
                for q in e.mod.QUESTIONS.values())
    assert found / len(e.mod.QUESTIONS) > 0.9, (key, found, len(e.mod.QUESTIONS))

# every official icon the diagram renderer can pick exists in static/icons (tools/build_icons.py builds them)
icons_used = set(re.findall(r'"([\w-]+\.(?:svg|png))"', (ROOT / "static" / "app.js").read_text(encoding="utf-8")))
assert len(icons_used) > 60 and not [f for f in icons_used if not (ROOT / "static" / "icons" / f).exists()], icons_used

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

# task exam (CKA): next task streams the task + a task meta event; Claude grading is monkeypatched; self-mark path is free
cka = web.EXAMS["cka"]
home = c.get("/api/cka/home").json()
assert home["tasks"] is True and home["mode"] == "mcq" and home["progress"]["mcq"]["total"] >= 50   # MCQ phase first
ev = events(c.post("/api/cka/act", json={"data": "next"}))
assert ev[0]["op"] == "add" and ev[0]["question"]["options"] and not ev[0]["question"]["task"], ev[0]   # an MCQ while in MCQ mode
assert c.post("/api/cka/exam/start", json={"n": 5}).json()["tasks"] is False
assert c.post("/api/cka/mode", json={"mode": "tasks"}).json()["mode"] == "tasks"                      # manual switch
ev = events(c.post("/api/cka/act", json={"data": "next"}))
assert ev[0]["html"].startswith("🛠") and ev[1]["op"] == "meta" and ev[1]["task"]["task"] is True, ev
tid = ev[1]["task"]["id"]
assert cka.mod.load_state()["pending"] == tid


async def fake_grade(exam, q, answer):
    return {"score": 85, "verdict": "Good", "feedback": "Fine.", "commands": "kubectl get pods"}


web.grade_one = fake_grade
ev = events(c.post("/api/cka/task", json={"id": tid, "answer": "kubectl get pods -A"}))
kinds = [e.get("html", "")[:2] for e in ev if e["op"] == "add"]
assert any(e["op"] == "meta" and e.get("graded_task", {}).get("right") for e in ev), ev
assert any("COMMAND GUIDELINE" in e.get("html", "") for e in ev) and any("REFERENCE SOLUTION" in e.get("html", "") for e in ev)
st = cka.mod.load_state(); assert st["ok"][tid] == 1 and st["pending"] is None and st["log"][-1]["score"] == 85
ev = events(c.post("/api/cka/task", json={"id": tid, "result": "reveal"}))
assert any(e["op"] == "meta" and e.get("revealed") == tid for e in ev)
ev = events(c.post("/api/cka/task", json={"id": tid, "result": "bad"}))
assert cka.mod.load_state()["bad"][tid] == 1
ex = c.post("/api/cka/exam/start", json={"n": 3}).json()
assert ex["tasks"] is True and ex["minutes"] == 21 and all(q["task"] for q in ex["questions"])
res = c.post("/api/cka/exam/submit", json={"answers": {ex["questions"][0]["id"]: ["kubectl apply -f x.yaml"], ex["questions"][1]["id"]: [""]}, "minutes_used": 5}).json()
assert res["tasks"] is True and res["n"] == 2 and res["results"][0]["score"] == 85 and res["results"][1]["verdict"] == "blank"
assert c.post("/api/ccarp/task", json={"id": "x", "answer": "y"}).status_code == 400

shutil.rmtree(tmp, ignore_errors=True)
print("ok: all web tests passed")
