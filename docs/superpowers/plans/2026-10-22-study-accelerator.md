# Study Accelerator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get a student to a pass in the least study time: spaced repetition with a confidence tag, an exam-date plan, a question decoder after every answer, flashcards + a cram sheet, the Udemy CCAR-P mock sets as a source, and three new Claude Foundation exams (CCAO-F, CCDV-F, CCAR-F) on the public link.

**Architecture:** `plan.py` holds the scheduler and plan maths as pure functions over a bot's `state.json` dict (new keys `srs`, `exam_date`, `pace`). `web.py` records every graded answer through one `record()` that updates the schedule, picks the next question by due → weighted-unseen → missed → any, prefetches a cached decoder JSON per question, and serves `/plan`, `/decode/{qid}`, `/card`. The front end (one `app.js`, no build step) adds the guessing toggle, quiz bar, decoder render, Home plan panel, flashcard deck and cram sheet. New exams are bot folders generated from the CCAR-P template with their bank fetched from claudearchitects.org.

**Tech Stack:** Python 3.12, FastAPI, the bots' `claude -p` wrapper (`mod.run_claude`), vanilla JS/CSS, agents (Claude Code `Agent` tool) for content: flashcards, study notes for the new exams, Udemy answer keys.

Spec: `docs/superpowers/specs/2026-10-22-study-accelerator-design.md`.

---

## File map

| File | Responsibility |
|---|---|
| `plan.py` (new) | `schedule`, `due_keys`, `next_question`, `domain_priority`, `plan`, `card_key`. No I/O, no FastAPI. |
| `test_plan.py` (new) | Pure tests for the above, `python test_plan.py`. |
| `web.py` | Calls `plan.*`; right answers graded by the portal; `guess` flag; `/plan`, `/decode/{qid}`, `/card`; decoder prefetch + cache; per-exam `scrape` module; META for the three new exams. |
| `test_web.py` | Endpoint tests for everything above (Claude monkeypatched), card/notes JSON asserts, Udemy rows, new exams. |
| `static/index.html`, `app.js`, `app.css` | Guess toggle, quiz bar, decoder, Home plan panel, flashcards, cram sheet, 3 accents. |
| `study/<exam>.json` | `domains[].cards[]` (all nine exams); full study JSON for the three new exams. |
| `tools/udemy_mock.py` (new) | Parse the Udemy txt into sets; merge agent keys into `../CCAR-P/data/questions.csv`. |
| `tools/new_exam.py` (new) | Generate `../CCAO-F`, `../CCDV-F`, `../CCAR-F` bot folders from the CCAR-P template; fetch their banks. |
| `temp/agents/` (gitignored) | Briefs and agent outputs. |
| `compose.yaml`, `~/nas-sync/deploy_portal.py` | Mounts/EXAMS and uploads for the three new exams. |
| `README.md`, `docs/vault/*` | Docs. |

---

## Task 1: Scheduler module

**Files:** Create `plan.py`, `test_plan.py`.

- [ ] **Step 1: Write the failing tests** — `test_plan.py`:

```python
"""python test_plan.py — the scheduler and plan maths, no server."""
import random
from datetime import datetime, timedelta, timezone

import plan

SGT = timezone(timedelta(hours=8))
now = datetime(2026, 10, 22, 21, 0, tzinfo=SGT)
iso = lambda dt: dt.isoformat(timespec="minutes")

# ladder: a miss comes back in 10 min, each right climbs one rung, a guess restarts, sure-first lands on 21 days
st = {"ok": {}, "bad": {}}
due, mins = plan.schedule(st, "Q1", right=False, now=now)
assert mins == 10 and st["srs"]["Q1"] == [iso(now + timedelta(minutes=10)), 0], st
assert plan.schedule(st, "Q1", True, now)[1] == 1440
assert plan.schedule(st, "Q1", True, now)[1] == 3 * 1440
assert plan.schedule(st, "Q1", True, now, guess=True)[1] == 10              # right but guessed = start over
assert plan.schedule(st, "Q2", True, now)[1] == 21 * 1440                 # first sight, right and sure
for _ in range(5): plan.schedule(st, "Q2", True, now)
assert st["srs"]["Q2"][1] == len(plan.LADDER_MIN) - 1                     # capped at the top rung (60 d)
st["srs"]["BAD"] = "garbage"
assert plan.schedule(st, "BAD", True, now)[1] == 21 * 1440                 # malformed entry = fresh

# due: overdue first; Telegram-only misses (no srs entry) count as due
st = {"ok": {"T1": 1}, "bad": {"T1": 2, "T2": 1}, "srs": {"A": [iso(now - timedelta(days=2)), 1], "B": [iso(now - timedelta(hours=1)), 0],
                                                       "C": [iso(now + timedelta(hours=1)), 0], "c:ab12cd34": [iso(now - timedelta(days=1)), 0]}}
assert plan.due_keys(st, iso(now)) == ["A", "B", "T1"], plan.due_keys(st, iso(now))
assert plan.due_keys(st, iso(now), cards=True) == ["c:ab12cd34"]

# next question: due first, then unseen weighted to heavy weak domains, then missed, then any
Q = {f"H{i}": {"id": f"H{i}", "domain": "Heavy"} for i in range(50)} | {f"L{i}": {"id": f"L{i}", "domain": "Light"} for i in range(50)}
W = {"Heavy": 30, "Light": 5}
by_dom = {"Heavy": [2, 10], "Light": [9, 10]}                              # 20% vs 90% first-try accuracy
st = {"ok": {}, "bad": {}, "srs": {"L3": [iso(now - timedelta(hours=1)), 0]}}
assert plan.next_question(st, Q, W, by_dom, iso(now))["id"] == "L3"
st = {"ok": {}, "bad": {}}
rng = random.Random(1)
picks = [plan.next_question(st, Q, W, by_dom, iso(now), rng)["domain"] for _ in range(200)]
assert picks.count("Heavy") > 160, picks.count("Heavy")
st = {"ok": {i: 1 for i in Q}, "bad": {"L1": 2}}
assert plan.next_question(st, Q, W, by_dom, iso(now))["id"] == "L1"        # all seen: the missed one
st = {"ok": {i: 1 for i in Q}, "bad": {}}
assert plan.next_question(st, Q, W, by_dom, iso(now))["id"] in Q
assert plan.next_question(st, {}, W, by_dom, iso(now)) is None
pri = plan.domain_priority(W, by_dom, {"Heavy": 5, "Light": 5, "Empty": 0})
assert pri["Heavy"] > pri["Light"] and "Empty" not in pri

# plan numbers
st = {"ok": {"H1": 1}, "bad": {"H2": 1}, "exam_date": "2026-11-05", "pace": 15,
      "log": [{"t": iso(now - timedelta(hours=2)), "id": "H1", "ok": True}, {"t": iso(now - timedelta(days=1)), "id": "H2", "ok": False}],
      "srs": {"H2": [iso(now - timedelta(minutes=5)), 0], "c:1": [iso(now - timedelta(minutes=5)), 0], "c:2": [iso(now + timedelta(days=1)), 1]}}
p = plan.plan(st, Q, W, by_dom, now, score=612, pass_mark=720)
assert p["days_left"] == 14 and p["due"] == 1 and p["cards"]["due"] == ["c:1"] and p["new_left"] == 98
assert p["today"] == {"target": 16, "done": 1, "pace": 15} and p["on_track"] is False and p["gap"] == 108
assert [f["domain"] for f in p["focus"]] == ["Heavy", "Light"] and p["focus"][0]["unseen"] == 48
assert plan.plan({"ok": {}, "bad": {}}, Q, W, by_dom, now, 0, 720)["days_left"] is None
assert plan.card_key("What is X?") == plan.card_key("What is X?") and plan.card_key("a").startswith("c:") and len(plan.card_key("a")) == 10
print("ok: plan tests passed")
```

- [ ] **Step 2: Run** `python test_plan.py` → `ModuleNotFoundError: plan`.

- [ ] **Step 3: Implement `plan.py`:**

```python
"""Scheduler and study plan: pure functions over a bot's state dict (ok/bad/log + srs/exam_date/pace). No I/O.

state["srs"][key] = [due_iso, rung]; key = question id, or "c:<8 hex>" for a flashcard (card_key).
"""
from __future__ import annotations

import hashlib
import random
from collections import Counter
from datetime import datetime, timedelta

D = 1440
LADDER_MIN = [10, D, 3 * D, 7 * D, 21 * D, 45 * D, 60 * D]   # minutes to the next review, by rung
FIRST_SURE = 4            # right and sure at first sight: one confirmation review after 21 days
DEFAULT_PACE = 20         # new questions a day
# ponytail: fixed ladder, one rung per right answer; SM-2 ease factors if the queue ever feels wrong


def card_key(question: str) -> str:
    return "c:" + hashlib.sha1(question.encode("utf-8")).hexdigest()[:8]


def _rung(entry) -> int:
    return entry[1] if isinstance(entry, list) and len(entry) == 2 and isinstance(entry[1], int) and 0 <= entry[1] < len(LADDER_MIN) else -1


def schedule(state: dict, key: str, right: bool, now: datetime, guess: bool = False) -> tuple[str, int]:
    """Record an answer; returns (due_iso, minutes until then)."""
    srs = state.setdefault("srs", {})
    rung = _rung(srs.get(key))
    if not right or guess:
        rung = 0
    elif rung < 0:
        rung = FIRST_SURE
    else:
        rung = min(rung + 1, len(LADDER_MIN) - 1)
    mins = LADDER_MIN[rung]
    due = (now + timedelta(minutes=mins)).isoformat(timespec="minutes")
    srs[key] = [due, rung]
    return due, mins


def due_keys(state: dict, now_iso: str, cards: bool = False) -> list[str]:
    """Keys due now, most overdue first. Questions also include Telegram-only misses (no srs entry, missed more than right)."""
    srs = state.get("srs") or {}
    due = sorted((v[0], k) for k, v in srs.items()
                 if isinstance(v, list) and v and isinstance(v[0], str) and v[0] <= now_iso and k.startswith("c:") == cards)
    out = [k for _, k in due]
    if not cards:
        out += [i for i, n in state.get("bad", {}).items() if i not in srs and n > state.get("ok", {}).get(i, 0)]
    return out


def domain_priority(weights: dict, by_dom: dict, unseen_by_dom: dict) -> dict[str, float]:
    """weight x (1 - first-try accuracy) + a floor, for domains that still have unseen questions."""
    pri = {}
    for d, n_unseen in unseen_by_dom.items():
        if not n_unseen:
            continue
        r, n = by_dom.get(d, (0, 0))
        acc = r / n if n >= 3 else 0.5
        pri[d] = (weights.get(d) or 1) * (1 - acc) + 0.05
    return pri


def next_question(state: dict, Q: dict, weights: dict, by_dom: dict, now_iso: str, rng=random) -> dict | None:
    """Due reviews first, then an unseen question from a domain drawn by priority, then missed, then any."""
    if not Q:
        return None
    due = [k for k in due_keys(state, now_iso) if k in Q]
    if due:
        return Q[due[0]]
    seen = set(state["ok"]) | set(state["bad"])
    unseen = [i for i in Q if i not in seen]
    if unseen:
        pri = domain_priority(weights, by_dom, Counter(Q[i]["domain"] for i in unseen))
        d = rng.choices(list(pri), weights=list(pri.values()))[0]
        return Q[rng.choice([i for i in unseen if Q[i]["domain"] == d])]
    missed = [i for i in Q if state["bad"].get(i, 0) > state["ok"].get(i, 0)]
    return Q[rng.choice(missed or list(Q))]


def plan(state: dict, Q: dict, weights: dict, by_dom: dict, now: datetime, score: int, pass_mark: int) -> dict:
    now_iso = now.isoformat(timespec="minutes")
    seen = set(state["ok"]) | set(state["bad"])
    due_q = [k for k in due_keys(state, now_iso) if k in Q]
    unseen_by_dom = Counter(q["domain"] for i, q in Q.items() if i not in seen)
    pri = domain_priority(weights, by_dom, unseen_by_dom)
    today = now.strftime("%Y-%m-%d")
    pace = int(state.get("pace") or DEFAULT_PACE)
    date = state.get("exam_date")
    try:
        days_left = (datetime.fromisoformat(date).date() - now.date()).days if date else None
    except ValueError:
        days_left = None
    focus = []
    for d in sorted(pri, key=pri.get, reverse=True)[:2]:
        r, n = by_dom.get(d, (0, 0))
        focus.append({"domain": d, "weight": weights.get(d), "acc": round(r / n, 2) if n else None, "unseen": unseen_by_dom[d]})
    return {"exam_date": date, "days_left": days_left, "due": len(due_q), "new_left": sum(unseen_by_dom.values()),
            "cards": {"due": due_keys(state, now_iso, cards=True),
                      "seen": [k for k in (state.get("srs") or {}) if k.startswith("c:")]},
            "today": {"target": len(due_q) + pace, "done": sum(1 for e in state.get("log", []) if str(e.get("t", "")).startswith(today)), "pace": pace},
            "focus": focus, "score": score, "pass": pass_mark, "on_track": score >= pass_mark, "gap": max(0, pass_mark - score)}
```

- [ ] **Step 4: Run** `python test_plan.py` → `ok: plan tests passed`.
- [ ] **Step 5: Commit** `git add plan.py test_plan.py && git commit -m "feat: spaced-repetition scheduler and study plan maths"`.

---

## Task 2: Scheduler in the portal

**Files:** Modify `web.py` (`record`, `act`, `answer`, `Exam.pick`, new endpoints, models), `test_web.py`.

- [ ] **Step 1: Failing tests** (append to `test_web.py` after the reset block):

```python
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
events(c.post("/api/ccarp/act", json={"data": "reset"}))
```

- [ ] **Step 2: Run** `python test_web.py` → fails on `due_min`.

- [ ] **Step 3: Implement** in `web.py`:

`import plan` (top). `record()` gains `guess` and the schedule:

```python
def record(exam: Exam, state: dict, qid: str, right: bool, extra: dict | None = None, guess: bool = False) -> int:
    """Write ok/bad + log like bot.grade() does, and schedule the next review. Returns the minutes until it is due."""
    bucket = "ok" if right else "bad"
    state[bucket][qid] = state[bucket].get(qid, 0) + 1
    first = state["ok"].get(qid, 0) + state["bad"].get(qid, 0) == 1
    now = datetime.now(SGT)
    state.setdefault("log", []).append({"t": now.isoformat(timespec="minutes"), "id": qid, "ok": right, "first": first,
                                        **({"guess": True} if guess else {}), **(extra or {})})
    state["log"] = state["log"][-5000:]
    return plan.schedule(state, qid, right, now, guess)[1]
```
(Callers that used the old `first` return value: none depend on it — `check_task`, `self_mark`, `exam_submit` ignore it.)

`Exam.pick` uses the scheduler (keeps the domain filter):

```python
    def pick(self, state: dict, domain: str | None = None, public: bool = False) -> dict:
        """Due reviews, then unseen from the weakest heavy domains, then missed, then any (plan.next_question), inside the current pool.
        With `domain` (Study → "Quiz me on this topic"): only that domain, any mode if the current one has none."""
        Q = self.pool(state, public) or self.questions(public)
        if domain is not None:
            Q = ({i: q for i, q in Q.items() if q["domain"] == domain}
                 or {i: q for i, q in self.questions(public).items() if q["domain"] == domain})
            if not Q:
                raise HTTPException(404, f"no questions in domain {domain}")
        m = self.mod.metrics(state, datetime.now(SGT))
        return plan.next_question(state, Q, getattr(self.mod, "WEIGHTS", {}), m["by_dom"], datetime.now(SGT).isoformat(timespec="minutes"))
```

`act()` "next": always pick in the portal (so the order applies to every exam), then prefetch the decoder (Task 3):

```python
    if data == "next":
        q = exam.pick(state, public=public)
        if not q["letters"]:
            await send_task(exam, rec, state, q)
        else:
            await mod.send_question(rec, state, q)
            prefetch(exam, q, public)
```
(Same `prefetch` call after `mod.send_question(...)` in the `weak`, `d|` and `q|` branches.)

`answer()` grades both outcomes in the portal (one path through `record`):

```python
async def answer(exam: Exam, qid: str, picked: list[str], rec: Recorder, public: bool = False, guess: bool = False) -> None:
    q = exam.mod.QUESTIONS.get(qid)
    if not q or hidden(exam, qid, public):
        raise HTTPException(404, f"unknown question {qid}")
    letters = set(picked) & set(q["letters"])
    if not letters:
        raise HTTPException(400, "pick at least one option")
    mod, state = exam.mod, exam.mod.load_state()
    right = letters == q["answer"]
    state["last"] = {"id": q["id"], "picked": sorted(letters)}
    state["pending"] = None
    mins = record(exam, state, q["id"], right, guess=guess)
    mod.save_state(state)
    rec.emit({"op": "meta", "graded": {"id": q["id"], "picked": sorted(letters), "correct": sorted(q["answer"]), "right": right,
                                       "guess": guess, "due_min": mins}})
    mod.vault_log(f"{mod.TITLE['ok' if right else 'bad']} **{'correct' if right else 'wrong'}**",
                  f"{q['id']} picked {','.join(sorted(letters))} answer {q['correct']}{' (guessed)' if guess else ''}")
    head = (f"{mod.TITLE['ok']} <b>CORRECT</b> · {html.escape(q['correct'])}" if right else
            f"{mod.TITLE['bad']} <b>WRONG</b> · you picked <code>{html.escape(','.join(sorted(letters)))}</code>, answer <code>{html.escape(q['correct'])}</code>")
    head += f" · {'guessed, ' if guess and right else ''}back in {when(mins)}"
    await rec.send_message(f"{head}\n\n{html.escape(q['explanation'])}", reply_markup=mod.after_kb(q, letters))


def when(mins: int) -> str:
    return f"{mins} min" if mins < 60 else f"{mins // 1440} day{'s' if mins >= 2880 else ''}"
```

`Answer` model: `guess: bool = False`; `api_answer` passes `guess=body.guess`. `TaskAnswer` also gets `guess: bool = False` → `check_task(..., guess)` → `record(exam, state, qid, right, {"score": ...}, guess)`.

Endpoints:

```python
class PlanBody(BaseModel):
    exam_date: str | None = None
    pace: int | None = None


def plan_of(e: Exam, state: dict, public: bool) -> dict:
    m = e.mod.metrics(state, datetime.now(SGT))
    return plan.plan(state, e.pool(state, public) or e.questions(public), getattr(e.mod, "WEIGHTS", {}), m["by_dom"],
                     datetime.now(SGT), 0 if public else m["score"], e.mod.PASS)


@app.get("/api/{key}/plan")
def api_plan(key: str, req: Request):
    e = exam_or_404(key)
    return plan_of(e, e.mod.load_state(), is_public(req))


@app.post("/api/{key}/plan")
def api_plan_set(key: str, body: PlanBody, req: Request):
    """Set or clear the exam date and the daily pace (new questions a day)."""
    e = exam_or_404(key)
    state = e.mod.load_state()
    if "exam_date" in body.model_fields_set:
        if body.exam_date:
            try:
                datetime.strptime(body.exam_date, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "exam_date must be YYYY-MM-DD")
            state["exam_date"] = body.exam_date
        else:
            state.pop("exam_date", None)
    if body.pace is not None:
        state["pace"] = max(1, min(200, body.pace))
    e.mod.save_state(state)
    e.mod.vault_log("📅 **plan**", f"exam {state.get('exam_date') or '-'} · pace {state.get('pace', plan.DEFAULT_PACE)}")
    return plan_of(e, state, is_public(req))
```
`api_home` adds `"plan": plan_of(e, state, pub)`.

- [ ] **Step 4: Run** `python test_web.py` → passes. (The earlier test `assert ev[0]["html"].startswith("🟢")` for a right answer still holds; the Explain-more button test too, since `after_kb` is reused.)
- [ ] **Step 5: Commit** `git commit -am "feat: due-first picker, guess tag, exam-date plan endpoints"`.

---

## Task 3: Question decoder (server)

**Files:** Modify `web.py`; `test_web.py`.

- [ ] **Step 1: Failing tests:**

```python
# decoder: prefetched when a question is shown, cached as a file, served to everyone, never started on the public link
dec_calls = []


async def fake_decoder(system, prompt, schema=None):
    dec_calls.append(prompt)
    return {"structured_output": {"clues": ["two seconds"], "options": [{"letter": "A", "why": "fast first"}], "principle": "Route by confidence.", "source": None}}


mod.run_claude = fake_decoder
ev = events(c.post("/api/ccarp/act", json={"data": "next"}))
qid = ev[0]["question"]["id"]
import time as _t
for _ in range(50):
    r = c.get(f"/api/ccarp/decode/{qid}")
    if r.status_code == 200: break
    _t.sleep(0.1)
assert r.status_code == 200 and r.json()["clues"] == ["two seconds"] and (Path(mod.STATE_PATH).parent / "decode" / f"{qid}.json").exists(), r.text
assert len(dec_calls) == 1 and qid in dec_calls[0]
assert c.get(f"/api/ccarp/decode/{qid}", headers=pub).status_code == 200                     # cached: public may read it
other = next(i for i in mod.QUESTIONS if i != qid and mod.QUESTIONS[i].get("source") != "Tutorials Dojo")
assert c.get(f"/api/ccarp/decode/{other}", headers=pub).status_code == 404 and len(dec_calls) == 1   # public never starts one
assert c.get(f"/api/ccarp/decode/{other}?cached=1").status_code == 404 and len(dec_calls) == 1       # review cards: cached only
r = c.get(f"/api/ccarp/decode/{other}"); assert r.status_code in (200, 202)                          # owner: starts it
assert c.get("/api/ccarp/decode/nope").status_code == 404
events(c.post("/api/ccarp/act", json={"data": "reset"}))
```

- [ ] **Step 2: Run** → 404 on the decode route.

- [ ] **Step 3: Implement:**

```python
# ── Question decoder: clue words, why each option is right or wrong, one Claude call per question, cached forever ──
DECODE = """You are a tutor for the {name} exam. The student has just answered a practice question. Using ONLY the question,
its options, the correct answer, the bank's explanation and the study material supplied, explain how to read the question:
"clues": 2 to 4 phrases copied verbatim from the question stem that decide the answer; "options": one entry per option
letter, "why" under 25 words of plain text: for a correct option why it is right, for every other option why it is wrong
(name the trap); "principle": one sentence to remember for the exam; "source": the study note's title and url exactly as
supplied, or null if none was supplied. Never invent a source or a fact that is not in the material."""
DECODE_SCHEMA = json.dumps({"type": "object", "required": ["clues", "options", "principle", "source"], "additionalProperties": False,
                            "properties": {"clues": {"type": "array", "items": {"type": "string"}},
                                           "options": {"type": "array", "items": {"type": "object", "required": ["letter", "why"], "additionalProperties": False,
                                                                                  "properties": {"letter": {"type": "string"}, "why": {"type": "string"}}}},
                                           "principle": {"type": "string"},
                                           "source": {"type": ["object", "null"], "required": ["title", "url"], "additionalProperties": False,
                                                      "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}}})
DECODING: dict[str, asyncio.Task] = {}        # "<exam>/<qid>" -> running call


def decode_path(exam: Exam, qid: str) -> Path:
    return exam.mod.STATE_PATH.parent / "decode" / f"{qid}.json"


def note_for(exam: Exam, q: dict) -> tuple[dict | None, dict | None]:
    """The study domain (by name) and its note with the most stem words in its title + gist + points."""
    dom = next((d for d in exam.study.get("domains", []) if d["domain"] == q["domain"]), None)
    if not dom:
        return None, None
    words = {w for w in re.findall(r"[a-z]{5,}", q["question"].lower())}
    best, hits = None, 0
    for n in dom.get("notes", []):
        text = f"{n.get('title', '')} {n.get('gist', '')} {' '.join(n.get('points', []))}".lower()
        h = sum(w in text for w in words)
        if h > hits:
            best, hits = n, h
    return dom, best


async def decode(exam: Exam, q: dict) -> dict:
    dom, note = note_for(exam, q)
    material = ""
    if dom and dom.get("summary"):
        material += "DOMAIN SUMMARY:\n" + "\n".join(f"- {x}" for x in dom["summary"].get("must_know", [])) + "\n\n"
    if note:
        material += (f"STUDY NOTE: {note['title']}\n{note.get('gist', '')}\n" + "\n".join(f"- {p}" for p in note.get("points", []))
                     + f"\nSOURCE: {note['source']['title']} | {note['source']['url']}\n\n")
    prompt = (f"QUESTION {q['id']}:\n{q['question']}\n\nOPTIONS:\n{q['options']}\n\nCORRECT: {q['correct']}\n\n"
              f"BANK EXPLANATION:\n{q['explanation']}\n\n{material}Reply with ONLY the JSON object.")
    data = await exam.mod.run_claude(DECODE.format(name=exam.name), prompt, DECODE_SCHEMA)
    out = data.get("structured_output")
    if not isinstance(out, dict):
        text = str(data.get("result") or "")
        out = json.loads(text[text.find("{"):text.rfind("}") + 1])
    out = {"clues": [str(x) for x in out.get("clues", [])][:4], "options": [o for o in out.get("options", []) if isinstance(o, dict) and o.get("letter")],
           "principle": str(out.get("principle", "")), "source": out.get("source") if isinstance(out.get("source"), dict) else None}
    if out["source"] and (not note or out["source"].get("url") != note["source"]["url"]):
        out["source"] = {"title": note["source"]["title"], "url": note["source"]["url"]} if note else None   # the note's link or nothing
    p = decode_path(exam, q["id"])
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return out


def prefetch(exam: Exam, q: dict, public: bool) -> asyncio.Task | None:
    """Start the decoder for an MCQ unless cached, running or on the public link. The task outlives the request."""
    key = f"{exam.key}/{q['id']}"
    if public or not q["letters"] or decode_path(exam, q["id"]).exists():
        return DECODING.get(key)
    if key not in DECODING or DECODING[key].done():
        async def run():
            try:
                await decode(exam, q)
            except Exception as exc:
                log.warning("decoder failed for %s: %s", key, exc)
            finally:
                DECODING.pop(key, None)
        DECODING[key] = asyncio.create_task(run())
    return DECODING[key]


@app.get("/api/{key}/decode/{qid}")
def api_decode(key: str, qid: str, req: Request, cached: int = 0):
    """The decoder JSON for a question: 200 with it, 202 while Claude is working, 404 when there is none (public link,
    `cached=1` for review cards, or a failed call). The owner's request starts a missing one."""
    e = exam_or_404(key)
    pub = is_public(req)
    q = e.questions(pub).get(qid)
    if not q:
        raise HTTPException(404, "unknown question")
    p = decode_path(e, qid)
    if p.exists():
        return JSONResponse(json.loads(p.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})
    if pub or cached or not q["letters"]:
        raise HTTPException(404, "not decoded")
    prefetch(e, q, False)
    return JSONResponse({"pending": True}, status_code=202, headers={"Cache-Control": "no-store"})
```
`prefetch(exam, q, public)` is called in `act()` after every `mod.send_question(...)` (next, weak, `d|`, `q|`) and in `api_home` when restoring a pending MCQ (`if pending and not pub and q["letters"]: prefetch(e, q, False)`).

- [ ] **Step 4: Run** `python test_web.py` → passes (the TestClient runs the app's loop; the background task completes within the poll loop).
- [ ] **Step 5: Commit** `git commit -am "feat: question decoder, prefetched per question and cached"`.

---

## Task 4: Front end — guess toggle, quiz bar, decoder, Home plan

**Files:** `static/index.html`, `static/app.js`, `static/app.css`.

- [ ] **Step 1: index.html**

Question template: add the toggle before `.options`:
```html
    <p class="qtext"></p>
    <div class="guess-row"><button type="button" class="guess" aria-pressed="false" title="Mark this answer as a guess (key ?)">🤔 Guessing</button><span class="muted small">A guessed answer comes back in 10 minutes even if it is right</span></div>
    <div class="options"></div>
```
Quiz view: a bar above the feed:
```html
    <p class="quiz-bar muted small" id="quiz-bar" hidden></p>
    <div class="feed" id="feed" aria-live="polite"></div>
```
Home: the plan panel after `#home-verdict`:
```html
        <div class="plan" id="plan" hidden>
          <p class="plan-line" id="plan-line"></p>
          <p class="plan-focus" id="plan-focus"></p>
          <div class="plan-controls row">
            <label class="small">📅 Exam date <input type="date" id="plan-date"></label>
            <label class="small">New a day <input type="number" id="plan-pace" min="1" max="200" step="5"></label>
            <button class="btn btn-accent" type="button" id="plan-go">Start today's session</button>
          </div>
        </div>
```

- [ ] **Step 2: app.js**

```js
  // ── Plan (Home): exam date, today's targets, focus domains ────────────────
  function fmtPlan(p) {
    const days = p.days_left == null ? "No exam date yet" : p.days_left < 0 ? "Exam date passed" : p.days_left === 0 ? "Exam day" : `${p.days_left} day${p.days_left === 1 ? "" : "s"} to the exam`;
    const cards = p.cards.due.length ? ` + ${p.cards.due.length} cards` : "";
    const track = p.score ? (p.on_track ? `${p.score} predicted, on track` : `${p.score} predicted, ${p.gap} short of ${p.pass}`) : "";
    return `${days} · today ${p.due} due + ${p.today.pace} new (${p.today.done} done)${cards}${track ? " · " + track : ""}`;
  }
  async function loadPlan() {
    const p = await api("/plan"); state.plan = p;
    const box = $("#plan"); box.hidden = false;
    $("#plan-line").textContent = fmtPlan(p);
    $("#plan-focus").textContent = p.focus.length ? "Focus: " + p.focus.map(f => `${f.domain}${f.acc != null ? ` (${Math.round(100 * f.acc)}%)` : ""}`).join(", ") : "";
    $("#plan-date").value = p.exam_date || ""; $("#plan-pace").value = p.today.pace;
  }
  async function savePlan(body) { state.plan = await (await post("/plan", body)).json(); $("#plan-line").textContent = fmtPlan(state.plan); }
  $("#plan-date").onchange = (e) => savePlan({ exam_date: e.target.value || null });
  $("#plan-pace").onchange = (e) => savePlan({ pace: Number(e.target.value) || 20 });
  $("#plan-go").onclick = () => { go("quiz"); setTimeout(() => act("next"), 50); };
```
`loadHome()` calls `loadPlan()` at the end. The quiz bar: in `openQuiz()` and after every graded answer, `renderQuizBar(state.home?.plan)` → `${p.due} due · ${p.new_left} new · today ${p.today.done} / ${p.today.target}`; the `graded` meta event refreshes it by refetching `/plan` (cheap).

Guess toggle in `questionCard(ev)`: `const g = $(".guess", el); g.onclick = () => g.setAttribute("aria-pressed", String(g.getAttribute("aria-pressed") !== "true"));` and `submit(letters)` sends `answer(q.id, letters, g.getAttribute("aria-pressed") === "true")`; `answer(id, picked, guess)` posts `{ id, picked, guess }`. Keyboard: `?` toggles the pending card's guess button.

Decoder render + poll (called from `markGraded` for the current card, and `addDecode(el, r.id, true)` for exam-review cards with `cached=1`):
```js
  // ── Decoder: clue words in the stem, one line per option, the principle, a source link ──
  async function addDecode(card, qid, cachedOnly) {
    if (!card || $(".decode", card)) return;
    const box = document.createElement("div"); box.className = "decode"; box.innerHTML = cachedOnly ? "" : `<span class="muted small">🔍 Decoding the question…</span>`;
    card.append(box);
    for (let i = 0; i < 45; i++) {
      const r = await fetch(`/api/${state.exam}/decode/${encodeURIComponent(qid)}${cachedOnly ? "?cached=1" : ""}`);
      if (r.status === 200) return renderDecode(card, box, await r.json());
      if (r.status !== 202 || cachedOnly) { box.remove(); return; }
      await new Promise(ok => setTimeout(ok, 2000));
    }
    box.remove();
  }
  function renderDecode(card, box, d) {
    const qt = $(".qtext", card);
    let html = esc(qt.textContent);
    for (const c of d.clues || []) { const e = esc(c); if (e && html.includes(e)) html = html.split(e).join(`<mark>${e}</mark>`); }
    qt.innerHTML = html;
    for (const o of d.options || []) {
      const b = $(`.opt[data-l="${esc(o.letter)}"]`, card); if (!b || $(".why", b)) continue;
      b.insertAdjacentHTML("beforeend", `<span class="why">${esc(o.why)}</span>`);
    }
    box.innerHTML = `<p class="principle">🔍 <b>Principle</b> ${esc(d.principle || "")}</p>` +
      (d.source?.url ? `<p class="src small">Source: <a href="${esc(d.source.url)}" target="_blank" rel="noopener">${esc(d.source.title || "page")}</a></p>` : "");
  }
```
`.opt` is a 2-column grid (letter | text); `.why` spans the second column on a new row (`grid-column: 2`).

- [ ] **Step 3: app.css**

```css
/* Guess toggle, quiz bar, decoder, plan */
.guess-row { display: flex; align-items: center; gap: .7rem; margin: -.4rem 0 .8rem; }
.guess { font: inherit; font-size: .82rem; color: var(--muted); background: transparent; border: 1px solid var(--line); border-radius: 999px; padding: .25rem .75rem; cursor: pointer; }
.guess[aria-pressed="true"] { color: var(--accent-ink); background: var(--accent); border-color: var(--accent); }
.quiz-bar { margin: 0 0 .8rem; font-family: var(--font-mono); }
.opt .why { grid-column: 2; font-size: .85rem; color: var(--muted); line-height: 1.4; }
.opt.is-correct .why { color: var(--ok); }
.opt.is-wrong .why { color: var(--bad); }
.qtext mark { background: color-mix(in oklab, var(--accent) 35%, transparent); color: inherit; border-radius: 4px; padding: 0 .15em; }
.decode { margin-top: .9rem; border-top: 1px solid var(--line); padding-top: .7rem; }
.decode .principle { margin: 0 0 .3rem; }
.decode .src { margin: 0; color: var(--muted); }
.decode .src a { color: var(--accent); }
.plan { margin: -.8rem 0 1.6rem; padding: .9rem 1.1rem; border-radius: var(--radius); background: var(--surface); border: 1px solid var(--line); display: grid; gap: .5rem; }
.plan-line { margin: 0; font-weight: 500; }
.plan-focus { margin: 0; color: var(--muted); font-size: .92rem; }
.plan-controls label { display: inline-flex; align-items: center; gap: .4rem; color: var(--muted); }
.plan-controls input { font: inherit; color: var(--text); background: var(--surface-2); border: 1px solid var(--line); border-radius: 8px; padding: .3rem .5rem; width: 9.5rem; }
.plan-controls input[type=number] { width: 5rem; }
[data-public="1"] .plan { display: none; }
```

- [ ] **Step 4: Check in Chrome** (the `run` skill / claude-in-chrome): answer a question with Guessing on → verdict says "guessed, back in 10 min"; decoder fills in within ~20 s; Home shows the plan line; set a date → line updates.
- [ ] **Step 5: Commit** `git commit -am "feat: guess toggle, quiz bar, decoder panel and Home study plan"`.

---

## Task 5: Flashcards + cram sheet

**Files:** `study/<exam>.json` (`cards`), `web.py` (`/card`, keys at load), `app.js`, `index.html`, `app.css`, `test_web.py`, `temp/agents/BRIEF-cards.md`.

- [ ] **Step 1: Card schema + brief.** `domains[].cards = [{"q": "...", "a": "..."}]`, 10–15 per domain, q ≤ 25 words, a ≤ 30 words, one fact each, from `summary.must_know`, `summary.traps`, `notes[].points`, `notes[].tip`. Agents write `temp/agents/cards-<exam>.json` = `{"<domain>": [{"q","a"}, ...]}`. Merge with `tools/merge_cards.py` (reads each file, asserts domain names match, writes `cards` into `study/<exam>.json`, re-indents 1 space like the existing files).

- [ ] **Step 2: Failing test** (in the study-JSON loop of `test_web.py`):
```python
        cards = d.get("cards") or []
        assert 8 <= len(cards) <= 20, (sj.name, d["domain"], "cards", len(cards))
        for cd in cards:
            assert cd["q"] and cd["a"] and len(cd["q"].split()) <= 25 and len(cd["a"].split()) <= 30, (sj.name, cd)
```
and the endpoint:
```python
# flashcards: keys are served with the study JSON; Again / Got it schedule them like questions
cd = c.get("/api/ccarp/study").json()["reading"]["domains"][0]["cards"][0]
assert cd["key"].startswith("c:")
assert c.post("/api/ccarp/card", json={"key": cd["key"], "ok": False}).json()["due_min"] == 10
assert c.post("/api/ccarp/card", json={"key": cd["key"], "ok": True}).json()["due_min"] == 1440
assert c.post("/api/ccarp/card", json={"key": "c:nope", "ok": True}).status_code == 404
assert cd["key"] in c.get("/api/ccarp/plan").json()["cards"]["seen"]
```

- [ ] **Step 3: Implement.** `Exam.__init__` after loading `self.study`: `self.cards = {}` then for every domain card `cd["key"] = plan.card_key(cd["q"]); self.cards[cd["key"]] = cd`.
```python
class Card(BaseModel):
    key: str
    ok: bool


@app.post("/api/{key}/card")
def api_card(key: str, body: Card):
    e = exam_or_404(key)
    if body.key not in e.cards:
        raise HTTPException(404, "unknown card")
    state = e.mod.load_state()
    due, mins = plan.schedule(state, body.key, body.ok, datetime.now(SGT))
    e.mod.save_state(state)
    return {"due": due, "due_min": mins}
```
UI (Study): per domain, `🃏 Flashcards` button in `.domain-actions` → `openDeck(d)`: builds the deck (due keys first from `state.plan.cards.due`, then unseen, then the rest) into `#deck` (a section at the top of the Study view: `<section id="deck" class="deck" hidden><div class="deck-head"><span id="deck-pos"></span><button class="btn" id="deck-close">Close</button></div><button type="button" class="flash" id="flash"><span class="side front"></span><span class="side back" hidden></span></button><div class="row deck-actions"><button class="btn btn-danger" id="deck-again">Again</button><button class="btn btn-accent" id="deck-ok">Got it</button></div><p class="muted small">Space flips · ← Again · → Got it</p></section>`). `Again`/`Got it` post `/card` and advance. Cram sheet: `🗒 Cram sheet` button in `.study-head` → route `#cram` → `<section id="view-cram" class="view" hidden>` filled by `loadCram()` from the study JSON (per domain: name + weight, `tests`, must-knows, traps, every note's tip), `<button class="btn" onclick="print()">🖨 Print</button>`; CSS `@media print { .top, .feed-actions, .cram-actions { display: none } body { background: #fff; color: #000 } }`.

- [ ] **Step 4: Run** `python test_web.py` → passes once the cards are merged (Task 9 agents).
- [ ] **Step 5: Commit** `git commit -am "feat: flashcards with spaced review and a printable cram sheet"`.

---

## Task 6: Udemy mock sets → CCAR-P bank

**Files:** Create `tools/udemy_mock.py`; agents write `temp/agents/udemy-key-set{1,2,3}.json`; modify `web.py` (`SOURCE_TAG`, `PUBLIC_HIDDEN` default), `test_web.py`, `../CCAR-P/data/questions.csv`.

- [ ] **Step 1:** `tools/udemy_mock.py parse` → `temp/agents/udemy-set{N}.json`:
```python
"""Udemy mock sets (Microsoft Forms page text) -> CCAR-P bank rows.
python tools/udemy_mock.py parse   -> temp/agents/udemy-set<N>.json (questions, no answers)
python tools/udemy_mock.py merge   -> appends keyed rows (temp/agents/udemy-key-set<N>.json from the agents) to ../CCAR-P/data/questions.csv
"""
import csv, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TXT = ROOT / "Claude Certified Architect Professional Mock Exam.txt"
OUT = ROOT / "temp" / "agents"
CSV = ROOT.parent / "CCAR-P" / "data" / "questions.csv"
LET = "ABCDEFGH"
DOMAINS = ["Solution Design & Architecture", "Claude Models, Prompting & Context Engineering", "Integration", "Evaluation, Testing & Optimization",
           "Governance, Safety & Risk Management", "Stakeholder Communication & Lifecycle Management", "Developer Productivity & Operational Enablement"]


def parse() -> list[dict]:
    lines = TXT.read_text(encoding="utf-8").splitlines()
    sets, cur, q = [], None, None
    for i, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("Udemy ") and i and lines[i - 1].strip():          # "<title>\nUdemy <author>"
            cur = {"set": len(sets) + 1, "title": lines[i - 1].strip(), "author": line[6:].strip(), "questions": []}
            sets.append(cur); q = None; continue
        m = re.match(r"^(\d+)\.(\S.*)$", line)
        if m and i + 1 < len(lines) and lines[i + 1].strip() == "(1 Point)" and cur is not None:
            q = {"n": int(m.group(1)), "question": m.group(2).strip(), "options": [], "multi": bool(re.search(r"select\s+two", m.group(2), re.I))}
            cur["questions"].append(q); continue
        if q is None or not line or line == "(1 Point)":
            continue
        if line in ("Submit", "Microsoft 365") or line.startswith("This content is created"):
            q = None; continue
        q["options"].append(line)
    for s in sets:
        s["questions"] = [x for x in s["questions"] if len(x["options"]) >= 2]
    return sets


def merge() -> None:
    sys.path.insert(0, str(CSV.parent.parent)); import scrape  # noqa: E402  (CCAR-P's _norm for duplicate stems)
    rows = list(csv.DictReader(CSV.open(encoding="utf-8")))
    stems = {scrape._norm(r["question"]) for r in rows}
    added = 0
    for s in parse():
        keys = {k["n"]: k for k in json.loads((OUT / f"udemy-key-set{s['set']}.json").read_text(encoding="utf-8"))}
        for x in s["questions"]:
            k = keys.get(x["n"])
            if not k or scrape._norm(x["question"]) in stems:
                continue
            letters = sorted(set(k["answer"]) & set(LET[:len(x["options"])]))
            assert letters and (len(letters) == 2) == x["multi"], (s["set"], x["n"], k)
            assert k["domain"] in DOMAINS, (s["set"], x["n"], k["domain"])
            rows.append({"id": f"UD{s['set']}-{x['n']:02d}", "domain": k["domain"], "question": x["question"],
                         "options": "\n".join(f"{LET[i]}. {o}" for i, o in enumerate(x["options"])), "correct": ",".join(letters),
                         "explanation": k["explanation"].strip() + ("" if k.get("sure", True) else " (Low-confidence key: check this one.)"),
                         "source": f"Udemy {s['author']} (model-keyed)"})
            stems.add(scrape._norm(x["question"])); added += 1
    tmp = CSV.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "domain", "question", "options", "correct", "explanation", "source"]); w.writeheader(); w.writerows(rows)
    tmp.replace(CSV)
    print(f"added {added} rows, {len(rows)} total")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1:] == ["parse"]:
        for s in parse():
            (OUT / f"udemy-set{s['set']}.json").write_text(json.dumps(s, indent=1, ensure_ascii=False), encoding="utf-8")
            print(s["set"], s["title"], s["author"], len(s["questions"]), "questions,", sum(x["multi"] for x in s["questions"]), "multi")
    elif sys.argv[1:] == ["merge"]:
        merge()
```
Run `python tools/udemy_mock.py parse` → 3 sets, 60 + 61 + 60 questions (set 2 has 61).

- [ ] **Step 2: Key them with three agents** (brief `temp/agents/BRIEF-udemy.md`): read `temp/agents/udemy-set<N>.json`, for each question return `{"n", "answer": ["C"] (two letters when multi), "domain": one of the seven official names, "explanation": ≤ 60 words naming the deciding clue and the trap in the nearest distractor, "sure": true|false}` to `temp/agents/udemy-key-set<N>.json`. Reasoning aids: Anthropic docs via Jina when unsure (`curl https://r.jina.ai/<url>`), the CCAR-P study notes in `study/ccarp.json`.

- [ ] **Step 3:** `python tools/udemy_mock.py merge` → `added 181 rows` (minus duplicates), then in `web.py`: `SOURCE_TAG = {"Tutorials Dojo": "TD", "Udemy Angel Gorgo (model-keyed)": "UD", "Udemy Jacob Bushong (model-keyed)": "UD", ...}` — simpler: `SOURCE_TAG` lookup falls back to `"UD" if source.startswith("Udemy") else ""`; `PUBLIC_HIDDEN` becomes prefix-aware: hidden when the source is in the set **or starts with "Udemy"** (`hidden_source(src)`). Test: `assert any(q.get("source", "").startswith("Udemy") for q in mod.QUESTIONS.values())` and such an id is 404 on the public link.

- [ ] **Step 4:** `python test_web.py` → passes. Commit the portal; the CCAR-P CSV goes to the NAS with `deploy_portal.py` and to GitHub with `sync.py --push ccarp-quiz` (Task 10).

---

## Task 7: Three new exams (CCAO-F, CCDV-F, CCAR-F)

**Files:** Create `tools/new_exam.py`; generated folders `../CCAO-F`, `../CCDV-F`, `../CCAR-F`; modify `web.py` (META), `app.css` (accents), `compose.yaml`, `test_web.py`, `~/nas-sync/deploy_portal.py`, `~/nas-sync/sync.py` (apps, after the owner creates the repos).

- [ ] **Step 1: Generator.** `tools/new_exam.py` holds the exam table and writes each folder from the CCAR-P template:

```python
EXAMS = {
    "ccaof": dict(folder="CCAO-F", short="CCAO-F", name="Claude Certified Associate - Foundations", n=60, minutes=120, pass_=720, accent="#86c17a",
                  weights={"Output Evaluation and Validation": 21, "Workflow Integration and Solution Design": 16, "Governance, Risk, and Responsible Use": 15,
                           "Prompting and Task Execution": 14, "Product and Model Selection": 12, "Configuration and Knowledge Management": 12, "Troubleshooting and Optimization": 10},
                  arch={"Workflow Integration and Solution Design"}),
    "ccdvf": dict(folder="CCDV-F", short="CCDV-F", name="Claude Certified Developer - Foundations", n=53, minutes=120, pass_=720, accent="#e58ab4",
                  weights={"Applications and Integration": 33.1, "Model Selection and Optimization": 16.8, "Agents and Workflows": 14.7, "Prompt and Context Engineering": 11,
                           "Tools and MCPs": 10.6, "Security and Safety": 8.1, "Claude Code": 3.1, "Eval, Testing, and Debugging": 2.6},
                  arch={"Applications and Integration", "Agents and Workflows", "Tools and MCPs"}),
    "ccarf": dict(folder="CCAR-F", short="CCAR-F", name="Claude Certified Architect - Foundations", n=60, minutes=120, pass_=720, accent="#8fcfe6",
                  weights={"Agentic Architecture & Orchestration": 27, "Claude Code Configuration & Workflows": 20, "Prompt Engineering & Structured Output": 20,
                           "Tool Design & MCP Integration": 18, "Context Management & Reliability": 15},
                  arch={"Agentic Architecture & Orchestration", "Tool Design & MCP Integration"}),
}
```
`bot.py` = CCAR-P `bot.py` with these exact-once replacements (assert `src.count(old) == 1`): the docstring title line; `logging.getLogger("ccarp")`; `"ccarp-work"`; `"ccarp-quiz-bot"` UA; `"ccarp-diag-"`; the `cmd_start` header line; the `WEIGHTS = {...}` block and `PASS = 720`; `"🔴 NOT YET: below the 720 pass mark"`; the `COACH` first three lines (exam name, items, minutes, pass, weights); `SYSTEM`'s first line (`Claude Certified Architect - Professional (CCAR-P) exam` → the new name); `ARCH_DOMAINS = {...}`; `ACCENT = "#d48a63"`. `scrape.py` (new, ~45 lines): `EXAM = "CCAO-F"`, `_get/_next_payload/_json_after/_row` copied from CCAR-P, `refresh()` = fetch `https://claudearchitects.org/mockexams/{EXAM}`, keep existing rows by id, append new, write the CSV atomically, return the same report dict shape (`before/added/dupes/failed/total`). `Dockerfile` = CCAR-P's with `COPY bot.py scrape.py ./`. `compose.yaml` = CCAR-P's with `ccarp-quiz` → `<key>-quiz`. Copied verbatim: `requirements.txt`, `bot.env.example`, `LICENSE`, `.gitignore`, `.dockerignore`, `run.ps1`, `assets/claude.png`. New: `README.md` (what it is, source + licence note from the site, pass mark assumption for CCAO-F/CCAR-F, run instructions), `test_bot.py` (imports bot, asserts the bank loads, `pick_question` works, `metrics` on an empty state scores 0, `question_text` renders). Then `python scrape.py` in each folder builds `data/questions.csv` (376 / 731 / 516 rows) and writes `data/state.json` `{"ok": {}, "bad": {}}`.

- [ ] **Step 2: Portal.** `META` gains the three entries (after `ccarp`): `"ccaof": ("CCAO-F", "Claude Certified Associate - Foundations", 60, 120)`, `"ccdvf": ("CCDV-F", "Claude Certified Developer - Foundations", 53, 120)`, `"ccarf": ("CCAR-F", "Claude Certified Architect - Foundations", 60, 120)`. `Exam.__init__`: `self.scrape = self._load(f"scrape_{key}", ...)` when `scrape.py` exists and `sys.modules["scrape"] = self.scrape` only while loading `online_parsers`; `act()` refresh: `sys.modules["scrape"] = exam.scrape` right before `await mod.refresh_bank(None, rec)` (the bot's `import scrape` runs synchronously at the top of that coroutine, so no race). `app.css`: `html[data-exam="ccaof"] { --accent: #86c17a; --accent-ink: #0f1a0d; }`, `ccdvf` `#e58ab4` / `#1f0d16`, `ccarf` `#8fcfe6` / `#0b1a20`. `compose.yaml`: EXAMS + volume pairs + vault lines for the three. `test_web.py`: the copy loop gains `("ccaof", ROOT.parent / "CCAO-F"), ("ccdvf", ROOT.parent / "CCDV-F"), ("ccarf", ROOT.parent / "CCAR-F")` (skipped when the folder is missing) and the key-set assert becomes `<=`.
- [ ] **Step 3: Study JSON** for the three exams by three agents (brief `temp/agents/BRIEF-notes.md`: the schema from `study/ccarp.json`, the test rules — 11-char embeddable video id per domain found with `yt-dlp "ytsearch5:<topic>"` and checked with `https://www.youtube.com/oembed?url=https://youtu.be/<id>`, `flowchart` diagrams with ≤ 6 nodes and no quotes/parentheses inside labels, 5–8 notes per domain from the official objectives on `https://claudearchitects.org/resources` and the Anthropic docs (`curl https://r.jina.ai/https://docs.claude.com/...`), `summary` with `tests`, 5–7 `must_know`, 2–3 `traps`, and 10–15 `cards`). Output straight to `study/<key>.json`; `python test_web.py` validates.
- [ ] **Step 4: Deploy script.** `deploy_portal.py`: VAULTS gains `"ccaof": "/volume1/<USER>/Obsidian/CCAO-F Quiz"`, `"ccdvf": ".../CCDV-F Quiz"`, `"ccarf": ".../CCAR-F Quiz"`; the "folders without a Telegram container" loop gains `("ccaof", "CCAO-F", ["scrape.py"])`, `("ccdvf", "CCDV-F", ["scrape.py"])`, `("ccarf", "CCAR-F", ["scrape.py"])`; the portal file list gains `study/ccaof.json`, `study/ccdvf.json`, `study/ccarf.json`, `plan.py`, `test_plan.py`, `docs/superpowers/specs/2026-10-22-study-accelerator-design.md`.
- [ ] **Step 5:** `python test_web.py` → passes with nine exams. Commit `git commit -am "feat: CCAO-F, CCDV-F and CCAR-F exams"`.

---

## Task 8: Docs

- [ ] `README.md`: new rows (Plan, Decoder, Flashcards, Cram sheet), the nine exams, the Udemy model-keyed note, `plan.py` in the file table.
- [ ] `docs/vault/Changelog.md` (one entry per sub-project), `App Overview.md` (new sections: Scheduler + plan, Decoder, Flashcards/cram, the three exams; fix the stale "NAS serves CCAR-P only"), `Roadmap.md` (phase 2: Context7 MCP grounding, Telegram auto-quiz honouring due dates, SM-2 ease), `Home.md` exam list.

## Task 9: Content agents (run in the background from Task 6/7 onwards)

Twelve agents, all `general-purpose`, each given its brief path and output path: 6 × flashcards (existing exams), 3 × study JSON + cards (new exams), 3 × Udemy keys. Merge: `python tools/merge_cards.py`, `python tools/udemy_mock.py merge`, then `python test_web.py`.

## Task 10: Deploy + push

- [ ] `python -u ~/nas-sync/deploy_portal.py` (uploads the nine folders incl. the Udemy rows in the CCAR-P CSV and restarts the CCAR-P bot, builds the portal). Check `docker logs exam-portal` lists nine exams, open the public link, answer one question, see the decoder.
- [ ] `cd ~/nas-sync && python -u sync.py --push exam-portal && python -u sync.py --push ccarp-quiz` (+ the three new apps once the owner has created their repos and they are registered in `sync.py`). Dry runs must come back clean.

---

## Self-review
- Spec coverage: §1 → Tasks 1, 2, 4; §2 → Tasks 1, 2, 4; §3 → Tasks 3, 4; §4 → Task 5 (+9); §5 → Task 6 (+9); new exams (mid-session request) → Task 7 (+9); error handling in each task; tests in each; deployment → Task 10.
- Names used consistently: `plan.schedule / due_keys / next_question / domain_priority / plan / card_key / LADDER_MIN / DEFAULT_PACE`; `web.record(..., guess)`, `prefetch`, `decode_path`, `note_for`, `plan_of`, `when`, `Exam.cards`, `Exam.scrape`; endpoints `/plan`, `/decode/{qid}?cached=1`, `/card`; `graded.due_min`, `graded.guess`.
- Ladder in the spec says "21 d then × 2.5": implemented as rungs 21 d → 45 d → 60 d (one list, same intent); spec updated.
