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
from fastapi.testclient import TestClient  # noqa: E402

c = TestClient(web.app)
c.__enter__()        # runs the lifespan (loads EXAMS from the env) and keeps one event loop for the whole file, so background tasks (decoder prefetch) finish

# no real Claude anywhere: every question shown prefetches the decoder through the bot's run_claude
dec_calls = []


async def fake_decoder(system, prompt, schema=None):
    dec_calls.append(prompt)
    return {"structured_output": {"clues": ["two seconds"], "options": [{"letter": "A", "why": "fast first"}], "principle": "Route by confidence.", "source": None}}


for _e in web.EXAMS.values():
    _e.mod.run_claude = fake_decoder


def events(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


# public (Tailscale Funnel) link: no Claude, no reset; local keeps both
pub = {"Tailscale-Funnel-Request": "?1"}
assert c.get("/api/access").json()["public"] is False and c.get("/api/access", headers=pub).json()["public"] is True
for d in ("report", "refresh", "e|x|A"):
    ev = events(c.post("/api/ccarp/act", json={"data": d}, headers=pub))
    assert ev[0]["op"] == "error" and "public" in ev[0]["html"], (d, ev)

assert 'data-public="1"' in c.get("/", headers=pub).text and 'data-public="1"' not in c.get("/").text
assert 'data-shared="1"' in c.get("/", headers=pub).text and 'data-shared' not in c.get("/").text   # the shared site hides personal numbers
assert c.get("/api/access", headers=pub).json() == {"public": True, "shared": True} and c.get("/api/access").json() == {"public": False, "shared": False}
assert c.get("/static/app.css").headers["cache-control"] == "no-cache"
assert c.get("/api/access", headers={"Host": "owner-cck-nas.tailc4f63b.ts.net:10000"}).json()["public"] is True   # tailnet device opening the public name
qq = c.get(f"/api/ccarp/question/{web.EXAMS['ccarp'].mod.QUESTIONS and next(iter(web.EXAMS['ccarp'].mod.QUESTIONS))}").json()
assert qq["correct"] and qq["options"] and "explanation" in qq, qq
hid = [i for i, q in web.EXAMS["ccarp"].mod.QUESTIONS.items() if web.hidden_source(q.get("source"))]   # paid sources: TD + the Udemy mock sets
if tdq := [i for i, q in web.EXAMS["ccarp"].mod.QUESTIONS.items() if q.get("source") == "Tutorials Dojo"]:
    assert c.get(f"/api/ccarp/question/{tdq[0]}", headers=pub).status_code == 404 and c.get(f"/api/ccarp/question/{tdq[0]}").status_code == 200
hp = c.get("/api/ccarp/home", headers=pub).json()
assert hp["score"] == 0 and hp["verdict"] == "" and hp["week"] == {"n": 0, "ok": 0}, hp

# public visitors are graded but nothing is recorded: the owner's state.json, schedule and vault are untouched
mod = web.EXAMS["ccarp"].mod
owner_before = json.dumps(mod.load_state(), sort_keys=True)
pc = {"Cookie": "pid=" + "ab" * 16}
for _ in range(3):
    ev = events(c.post("/api/ccarp/act", json={"data": "next"}, headers={**pub, **pc}))
    qv = ev[0]["question"]
    ev = events(c.post("/api/ccarp/answer", json={"id": qv["id"], "picked": ["A"]}, headers={**pub, **pc}))
    assert ev[0]["graded"]["correct"] and "back in" not in ev[1]["html"], ev[1]["html"]       # graded, not scheduled
assert json.dumps(mod.load_state(), sort_keys=True) == owner_before                      # owner untouched
pst = c.get("/api/ccarp/home", headers={**pub, **pc}).json()
assert pst["coverage"]["seen"] == 0 and pst["plan"]["due"] == 0, pst["coverage"]           # nothing kept for the visitor
assert not (Path(mod.STATE_PATH).parent / "public").exists()
assert c.get("/api/ccarp/home").json()["coverage"]["seen"] == json.loads(owner_before).get("ok", {}).__len__() + len(json.loads(owner_before).get("bad", {}))
assert events(c.post("/api/ccarp/act", json={"data": "reset"}, headers={**pub, **pc}))[0]["html"].startswith("🔄")   # public reset = own progress only
assert json.dumps(mod.load_state(), sort_keys=True) == owner_before
ev = events(c.post("/api/ccarp/act", json={"data": "weak"}))                                # weak-topic quiz: header message then a question
assert "Weak topic" in ev[0]["html"] and ev[1]["question"], ev
events(c.post("/api/ccarp/act", json={"data": "reset"}))
# leaderboard: opt-in; nothing kept before joining; only a question's first answer counts; leaving deletes everything
web.LB_DIR = tmp / "lb"; web.BOARD_CACHE["t"] = 0.0
lbc = {**pub, "Cookie": "pid=" + "ef" * 16}
for bad_name in ("a", "x" * 25, "see http://x.io", "me@mail", "<b>hi</b>"):
    assert c.post("/api/board/join", json={"name": bad_name}, headers=lbc).status_code == 400, bad_name
def lb_answer(ok: bool):
    qv = events(c.post("/api/ccarp/act", json={"data": "next"}, headers=lbc))[0]["question"]
    q = web.EXAMS["ccarp"].mod.QUESTIONS[qv["id"]]
    return qv["id"], events(c.post("/api/ccarp/answer", json={"id": qv["id"], "picked": sorted(q["answer"]) if ok else ["Z"]}, headers=lbc))
lb_answer(True)
assert not web.LB_DIR.exists() or not list(web.LB_DIR.glob("*.json"))                      # not joined: nothing kept
assert c.get("/api/board", headers=lbc).json()["me"]["joined"] is False
assert c.post("/api/board/join", json={"name": "  Ann   Lee "}, headers=lbc).json() == {"joined": True, "name": "Ann Lee"}
qid, _ = lb_answer(True)
events(c.post("/api/ccarp/answer", json={"id": qid, "picked": ["A"]}, headers=lbc))        # same question again: not counted twice
b = c.get("/api/board?exam=ccarp&period=all", headers=lbc).json()
assert b["rows"][0]["name"] == "Ann Lee" and b["rows"][0]["done"] == 1 and b["rows"][0]["me"] and b["me"]["rank"] == 1, b
assert c.get("/api/board?exam=nope", headers=lbc).status_code == 404
assert c.get("/api/board?period=week", headers=lbc).json()["rows"][0]["done"] == 1
assert c.post("/api/board/leave", headers=lbc).json() == {"joined": False}
assert c.get("/api/board", headers=lbc).json()["rows"] == [] and not list(web.LB_DIR.glob("*.json"))
# the owner (any non-public link) can join too, for CCAR-P only and never for paid questions
mq = web.EXAMS["ccarp"].mod.QUESTIONS
free = next(i for i, q in mq.items() if not web.hidden_source(q.get("source")) and q["letters"])
assert c.get("/api/board").json()["me"] == {"visitor": True, "owner": True, "joined": False, "name": None, "rank": None}
events(c.post("/api/ccarp/answer", json={"id": free, "picked": sorted(mq[free]["answer"])}))
assert c.get("/api/board?period=all").json()["rows"] == []                                  # before joining nothing is kept
events(c.post("/api/ccarp/answer", json={"id": free, "picked": ["Z"]}))                    # earlier history: a wrong first answer recorded in the owner's state
hist = c.get("/api/board?period=all").json()["rows"]
assert c.post("/api/board/join", json={"name": "the owner"}).json()["joined"]
ob0 = c.get("/api/board?period=all").json()["rows"][0]
assert ob0["done"] >= 1 and ob0["name"] == "the owner", ob0                                    # joining back-fills the owner's history
other = next(i for i, q in mq.items() if i != free and not web.hidden_source(q.get("source")) and q["letters"])
events(c.post("/api/ccarp/answer", json={"id": other, "picked": sorted(mq[other]["answer"])}))
sq = web.EXAMS["sapc03"].mod.QUESTIONS; sid = next(iter(sq))
events(c.post("/api/sapc03/answer", json={"id": sid, "picked": sorted(sq[sid]["answer"])}))   # not a CCAR-P question: not counted
if tdq:
    events(c.post("/api/ccarp/answer", json={"id": tdq[0], "picked": ["A"]}))                  # paid question: not counted
ob = c.get("/api/board?period=all").json()
assert ob["rows"][0]["name"] == "the owner" and ob["rows"][0]["done"] == ob0["done"] + 1 and ob["me"]["owner"], (ob, ob0)   # +1: only the new free CCAR-P answer counted
assert c.post("/api/board/leave").json() == {"joined": False} and c.get("/api/board?period=all").json()["rows"] == []
# rate limit: one visitor over its budget gets 429
web.HITS.clear()
codes = [c.get("/api/exams", headers={**pub, "Cookie": "pid=" + "cd" * 16}).status_code for _ in range(95)]
assert codes[0] == 200 and codes[-1] == 429, codes[-3:]
web.HITS.clear()

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
    assert c.get("/api/exams", headers=pub).json()[0]["total"] == c.get("/api/exams").json()[0]["total"] - len(hid)
    assert any(x["id"].startswith("TD-") for x in c.post("/api/ccarp/exam/start", json={"n": 1900}).json()["questions"])   # private sees them
    events(c.post("/api/ccarp/act", json={"data": "reset"}))      # the checks above touched the pending question
    # owner unlock: right key (query or cookie) shows the paid bank on the public link, a wrong key does not
    web.OWNER_KEY = "k3y-for-test"
    assert c.get("/api/exams", headers=pub).json()[0]["total"] == c.get("/api/exams").json()[0]["total"] - len(hid)
    assert c.get("/api/exams?td=wrong", headers=pub).json()[0]["total"] == c.get("/api/exams").json()[0]["total"] - len(hid)
    assert c.get("/api/exams?td=k3y-for-test", headers=pub).json()[0]["total"] == c.get("/api/exams").json()[0]["total"]
    assert c.get("/api/access?td=k3y-for-test", headers=pub).json()["public"] is False   # key holder = owner: Claude/reset allowed
    assert c.get("/api/access?td=wrong", headers=pub).json()["public"] is True
    assert c.get(f"/api/ccarp/question/{tdq[0]}").json()["tag"] == "TD" and c.get("/api/ccarp/question/" + next(i for i, q in web.EXAMS["ccarp"].mod.QUESTIONS.items() if q.get("source") != "Tutorials Dojo" and not q.get("examlike"))).json()["tag"] == ""
    web.OWNER_KEY = ""

# per-audience exam lists: EXAM_LISTS (env PUBLIC_EXAMS / PRIVATE_EXAMS / PERSONAL_EXAMS) filter /api/exams and every /api/{exam} route
web.EXAM_LISTS.update(public={"ccarp"}, private={"ccarp", "sapc03", "aipc01", "cka"})
assert [e["key"] for e in c.get("/api/exams", headers=pub).json()] == ["ccarp"]
assert c.get("/api/sapc03/home", headers=pub).status_code == 404 and c.get("/api/sapc03/home").status_code == 200
assert c.get("/api/ccarp/home", headers=pub).status_code == 200
web.EXAM_LISTS["private"] = {"ccarp"}
assert c.get("/api/sapc03/home").status_code == 404
web.OWNER_KEY = "k3y-for-test"                                     # the owner key on the public name gets the personal list
web.EXAM_LISTS.update(public={"ccarp", "sapc03"}, personal={"ccarp"})
assert {e["key"] for e in c.get("/api/exams", headers=pub).json()} == {"ccarp", "sapc03"}
assert [e["key"] for e in c.get("/api/exams?td=k3y-for-test", headers=pub).json()] == ["ccarp"]
assert c.get("/api/sapc03/home?td=k3y-for-test", headers=pub).status_code == 404
assert c.get("/api/access?td=k3y-for-test", headers=pub).json()["public"] is False
web.OWNER_KEY = ""
web.EXAM_LISTS.update(public=set(), private=set(), personal=set())

# exams + home
ex = c.get("/api/exams").json()
assert {"ccarp", "sapc03", "aipc01", "cka"} <= {e["key"] for e in ex}, ex
assert all(e["total"] > 50 for e in ex) and next(e for e in ex if e["key"] == "cka")["tasks"] is True
home = c.get("/api/ccarp/home").json()
assert home["pending"]["question"]["id"] and home["coverage"]["seen"] == 0 and home["pass"] == 720   # the reset above asked question 1
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
ev = events(c.post("/api/ccarp/act", json={"data": "reset"}))
assert ev[0]["html"].startswith("🔄") and ev[1].get("question"), ev          # reset goes straight into question 1
st = mod.load_state()
assert st["ok"] == {} and st["bad"] == {} and st["pending"] == ev[1]["question"]["id"], st

# scheduler: every graded path writes srs; a guessed right answer comes back in 10 min; the next question is the due one
ev = events(c.post("/api/ccarp/act", json={"data": "next"}))
qid = ev[0]["question"]["id"]; q = mod.QUESTIONS[qid]
ev = events(c.post("/api/ccarp/answer", json={"id": qid, "picked": sorted(q["answer"]), "guess": True}))
assert ev[0]["graded"]["right"] is True and ev[0]["graded"]["due_min"] == 10 and ev[0]["graded"]["guess"] is True, ev[0]
st = mod.load_state(); assert st["srs"][qid][1] == 0 and st["log"][-1]["guess"] is True and st["ok"][qid] == 1
st["srs"][qid][0] = "2000-01-01T00:00"; mod.save_state(st)                                  # make it overdue
assert events(c.post("/api/ccarp/act", json={"data": "next"}))[0]["question"]["id"] == qid    # due beats unseen
ev = events(c.post("/api/ccarp/answer", json={"id": qid, "picked": sorted(q["answer"])}))
assert ev[0]["graded"]["due_min"] == 1440 and ev[1]["html"].startswith("🟢") and "1 day" in ev[1]["html"], ev[1]["html"]
# plan endpoint
p = c.get("/api/ccarp/plan").json(); assert p["due"] == 0 and p["today"]["done"] >= 2 and p["days_left"] is None and p["focus"], p
assert c.post("/api/ccarp/plan", json={"exam_date": "2099-01-01", "pace": 30}).json()["today"]["pace"] == 30
assert c.get("/api/ccarp/plan").json()["days_left"] > 1000 and mod.load_state()["exam_date"] == "2099-01-01"
assert c.post("/api/ccarp/plan", json={"exam_date": "not a date"}).status_code == 400
assert c.post("/api/ccarp/plan", json={"exam_date": None}).json()["days_left"] is None
assert c.get("/api/ccarp/home").json()["plan"]["new_left"] > 0

# decoder: prefetched when a question is shown, cached as a file, served to everyone, never started on the public link
dec_dir = Path(mod.STATE_PATH).parent / "decode"
for f in dec_dir.glob("*.json"):
    f.unlink()
dec_calls.clear()
ev = events(c.post("/api/ccarp/act", json={"data": "next"}))
qid = ev[0]["question"]["id"]
import time as _t  # noqa: E402
for _ in range(50):
    r = c.get(f"/api/ccarp/decode/{qid}")
    if r.status_code == 200:
        break
    _t.sleep(0.1)
assert r.status_code == 200 and r.json()["clues"] == ["two seconds"] and (dec_dir / f"{qid}.json").exists(), r.text
assert len(dec_calls) == 1 and qid in dec_calls[0], len(dec_calls)
assert c.get(f"/api/ccarp/decode/{qid}", headers=pub).status_code == 200                     # cached: public may read it
other = next(i for i in mod.QUESTIONS if i != qid and not web.hidden_source(mod.QUESTIONS[i].get("source")) and not (dec_dir / f"{i}.json").exists())
assert c.get(f"/api/ccarp/decode/{other}", headers=pub).status_code == 404 and len(dec_calls) == 1   # public never starts one
assert c.get(f"/api/ccarp/decode/{other}?cached=1").status_code == 404 and len(dec_calls) == 1       # review cards: cached only
r = c.get(f"/api/ccarp/decode/{other}"); assert r.status_code in (200, 202)                          # owner: starts it
assert c.get("/api/ccarp/decode/nope").status_code == 404

# free questions that read like the exam get the "≈ exam" badge on every link
eid = next(i for i, q in web.EXAMS["ccarp"].mod.QUESTIONS.items() if q.get("examlike")); assert c.get(f"/api/ccarp/question/{eid}", headers=pub).json()["tag"] == "≈ exam"
# paid mock sets (Udemy via Microsoft Forms, rows MF*) are hidden on the public link and tagged on the card
if mfq := [i for i, q in mod.QUESTIONS.items() if q.get("source", "").startswith("Microsoft Forms mock")]:
    assert web.hidden_source(mod.QUESTIONS[mfq[0]]["source"]) and c.get(f"/api/ccarp/question/{mfq[0]}", headers=pub).status_code == 404
    assert c.get(f"/api/ccarp/question/{mfq[0]}").json()["tag"] == "UD"

# flashcards: keys are served with the study JSON; Again / Got it schedule them like questions
cd = c.get("/api/ccarp/study").json()["reading"]["domains"][0]["cards"][0]
assert cd["key"].startswith("c:") and len(cd["key"]) == 10, cd
assert c.post("/api/ccarp/card", json={"key": cd["key"], "ok": False}).json()["due_min"] == 10
assert c.post("/api/ccarp/card", json={"key": cd["key"], "ok": True}).json()["due_min"] == 1440
assert c.post("/api/ccarp/card", json={"key": "c:nope", "ok": True}).status_code == 404
assert cd["key"] in c.get("/api/ccarp/plan").json()["cards"]["seen"]
events(c.post("/api/ccarp/act", json={"data": "reset"}))

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
        cards = d.get("cards") or []                 # flashcards: 8-20 short recall prompts per domain
        assert 8 <= len(cards) <= 20, (sj.name, d["domain"], "cards", len(cards))
        for cd in cards:
            assert cd["q"] and cd["a"] and len(cd["q"].split()) <= 25 and len(cd["a"].split()) <= 30, (sj.name, cd)
for key, e in web.EXAMS.items():
    if not e.study:                              # an exam whose study JSON is not written yet
        continue
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

c.__exit__(None, None, None)
shutil.rmtree(tmp, ignore_errors=True)
print("ok: all web tests passed")
