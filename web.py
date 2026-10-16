#!/usr/bin/env python3
"""Exam Portal: one website for the CCAR-P, SAP-C03 and AIP-C01 quiz bots, every Telegram function included,
plus a Study door (reading list + study cards) and a timed Exam mode.

Each exam's own bot.py is imported from its stack folder and driven with a recorder in place of the
Telegram chat, so the web shows the same message flow (verdict, "drawing", diagram, tutor, report)
and writes the same data/state.json. Events stream to the browser as NDJSON.

Env: EXAMS="key:bot_dir[:vault_dir],..."  (default: sibling project folders whose bot.py logger name is a known exam)
     CLAUDE_MODEL, CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY (as for the bots), PORT (8000).
"""
from __future__ import annotations

import asyncio
import base64
import functools
import html
import importlib.util
import json
import logging
import os
import random
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger("portal")
ROOT = Path(__file__).resolve().parent
SGT = timezone(timedelta(hours=8))
# key (= the bot's logger name) -> short, full name, real exam length, real exam minutes
META = {"ccarp": ("CCAR-P", "Claude Certified Architect Professional", 63, 120),
        "sapc03": ("SAP-C03", "AWS Solutions Architect Professional", 75, 180),
        "aipc01": ("AIP-C01", "AWS Generative AI Developer Professional", 75, 180)}


# ── Exams: import each bot.py as its own module ──────────────────────────────

def vault_writer(root: str | None):
    """Same line format as bot.vault_log, into this exam's vault, tagged as web."""
    def vault_log(what: str, detail: str = "") -> None:
        if not root:
            return
        try:
            now = datetime.now(SGT)
            p = Path(root) / "Activity" / now.strftime("%Y/%m") / f"{now:%Y-%m-%d}.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(f"---\ntags: [active]\nupdated: {now:%Y-%m-%d}\n---\n# {now:%Y-%m-%d}\n\n", encoding="utf-8")
            with p.open("a", encoding="utf-8") as f:
                f.write(f"- {now:%H:%M} {what} · {detail} · 🌐 web\n")
        except Exception as exc:
            log.warning("vault write failed: %s", exc)
    return vault_log


class Exam:
    def __init__(self, key: str, bot_dir: str, vault: str | None):
        self.key, self.dir = key, Path(bot_dir)
        self.short, self.name, self.n_exam, self.minutes = META.get(key, (key.upper(), key, 65, 130))
        os.environ["DATA_DIR"] = str(self.dir / "data")       # bot.py reads it at import
        self.mod = self._load(f"bot_{key}", self.dir / "bot.py")
        if (self.dir / "scrape.py").exists():                  # CCAR-P: refresh_bank does `import scrape` lazily
            sys.modules["scrape"] = self._load("scrape", self.dir / "scrape.py")   # ponytail: one scrape.py across exams
        self.mod.vault_log = vault_writer(vault)
        self.lock = asyncio.Lock()
        sj = ROOT / "study" / f"{key}.json"
        self.study = json.loads(sj.read_text(encoding="utf-8")) if sj.exists() else {}

    @staticmethod
    def _load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    @property
    def has_study(self) -> bool:
        return bool(getattr(self.mod, "NOTES", None))

    def coverage(self, state: dict) -> dict:
        """Per-domain seen / right-first-time / total, like stats_text but as data."""
        Q, ok, bad = self.mod.QUESTIONS, state["ok"], state["bad"]
        seen = set(ok) | set(bad)
        first = {i for i in seen if ok.get(i, 0) >= 1 and bad.get(i, 0) == 0}
        doms: dict[str, dict] = {}
        for i, q in Q.items():
            d = doms.setdefault(q["domain"], {"name": q["domain"], "total": 0, "seen": 0, "right": 0,
                                               "weight": getattr(self.mod, "WEIGHTS", {}).get(q["domain"])})
            d["total"] += 1
            d["seen"] += i in seen
            d["right"] += i in first
        return {"total": len(Q), "seen": len(seen), "right": len(first),
                "domains": sorted(doms.values(), key=lambda d: -(d["weight"] or 0))}


def discover() -> str:
    """Local default: sibling folders whose bot.py logs under a known exam key."""
    found = []
    for d in sorted(ROOT.parent.iterdir()):
        bp = d / "bot.py"
        if d.is_dir() and bp.exists() and (d / "data" / "questions.csv").exists():
            m = re.search(r'logging\.getLogger\("(\w+)"\)', bp.read_text(encoding="utf-8", errors="replace"))
            if m and m.group(1) in META:
                found.append(f"{m.group(1)}:{d}")
    return ",".join(found)


def load_exams() -> dict[str, Exam]:
    exams = {}
    for spec in (os.environ.get("EXAMS") or discover()).split(","):
        key, bot_dir, *vault = spec.strip().split(":", 2) if os.name != "nt" else _split_win(spec.strip())
        exams[key] = Exam(key, bot_dir, vault[0] if vault and vault[0] else None)
        log.info("exam %s: %d questions, study cards=%s (%s)", key, len(exams[key].mod.QUESTIONS), exams[key].has_study, bot_dir)
    return dict(sorted(exams.items(), key=lambda kv: list(META).index(kv[0]) if kv[0] in META else 99))


def _split_win(spec: str) -> list[str]:
    """Windows paths contain ':'; split on ':' only where the next char is not a path separator."""
    parts, cur = [], ""
    for i, ch in enumerate(spec):
        if ch == ":" and not (i + 1 < len(spec) and spec[i + 1] in "\\/"):
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


# ── Recorder: a chat whose messages stream to the browser ────────────────────

def options_of(q: dict) -> list[dict]:
    return [{"l": line[0], "t": line[3:]} for line in q["options"].splitlines() if line[1:3] == ". "]


def qview(q: dict) -> dict:
    return {"id": q["id"], "domain": q["domain"], "question": q["question"], "options": options_of(q), "multi": len(q["answer"]) > 1}


class Msg:
    def __init__(self, rec: "Recorder", mid: int):
        self.rec, self.id = rec, mid

    async def edit_text(self, text: str, reply_markup=None, **_):
        self.rec.emit({"op": "edit", "id": self.id, "html": text, "buttons": self.rec.buttons(reply_markup)})

    async def delete(self):
        self.rec.emit({"op": "delete", "id": self.id})


class Recorder:
    """Duck-types bot.Dest: send_message / send_photo. Each call becomes one streamed event."""

    def __init__(self, exam: Exam, queue: asyncio.Queue):
        self.exam, self.q, self.n = exam, queue, 0

    def emit(self, ev: dict) -> None:
        self.q.put_nowait(ev)

    def buttons(self, kb) -> list[list[dict]]:
        if not kb:
            return []
        return [[{"label": b.text, "data": b.callback_data} for b in row] for row in kb.inline_keyboard]

    def _question_for(self, kb) -> dict | None:
        for row in self.buttons(kb):
            for b in row:
                if b["data"] and b["data"].startswith("a|"):
                    q = self.exam.mod.QUESTIONS.get(b["data"].split("|")[1])
                    if q:
                        return qview(q)
        return None

    async def send_message(self, text: str, reply_markup=None, **_) -> Msg:
        self.n += 1
        ev = {"op": "add", "id": self.n, "kind": "text", "html": text, "buttons": self.buttons(reply_markup)}
        q = self._question_for(reply_markup)
        if q:
            ev["question"] = q
        self.emit(ev)
        return Msg(self, self.n)

    async def send_photo(self, photo: bytes, caption: str = "", reply_markup=None, **_) -> Msg:
        self.n += 1
        self.emit({"op": "add", "id": self.n, "kind": "photo", "png": base64.b64encode(photo).decode(),
                   "html": caption, "buttons": self.buttons(reply_markup)})
        return Msg(self, self.n)


# ── Actions (the bot's button vocabulary) ────────────────────────────────────

async def act(exam: Exam, data: str, rec: Recorder) -> None:
    mod = exam.mod
    state = mod.load_state()
    if data == "next":
        await mod.send_question(rec, state)
    elif data == "stats":
        await rec.send_message(mod.stats_text(state))
    elif data == "study":
        if exam.has_study:
            await mod.send_study(rec, state)
        else:
            await rec.send_message("📚 <i>No study cards for this exam yet. Use the reading list.</i>", reply_markup=mod.NEXT_KB)
    elif data == "report":
        m = mod.metrics(state, datetime.now(SGT))
        rec.emit({"op": "meta", "metrics": {**m, "pass": mod.PASS, "verdict": mod.verdict(m)}})
        await mod.weekly_report(None, rec)
    elif data == "refresh":
        await mod.refresh_bank(None, rec)
    elif data == "reset":
        mod.save_state({"ok": {}, "bad": {}})
        mod.vault_log("🔄 **reset**", "progress cleared")
        await rec.send_message("🔄 Progress cleared.", reply_markup=mod.NEXT_KB)
    elif "|" in data:
        kind, qid, *rest = data.split("|")
        q = mod.QUESTIONS.get(qid)
        if not q:
            raise HTTPException(404, f"unknown question {qid}")
        if kind == "q":                                   # "Quiz me on this" under a study card
            await mod.send_question(rec, state, q)
        elif kind == "e":                                 # Explain more
            last = state.get("last") or {}
            if last.get("id") == qid and last.get("teach"):   # wrong answer: text already generated, free
                await rec.send_message(mod.teach_text(q, last["teach"]), reply_markup=mod.NEXT_KB)
            else:                                              # right answer / exam review: ask Claude now
                await mod.explain(rec, q, set(filter(None, (rest[0] if rest else "").split(","))))
        else:
            raise HTTPException(400, f"unknown action {data}")
    else:
        raise HTTPException(400, f"unknown action {data}")


async def answer(exam: Exam, qid: str, picked: list[str], rec: Recorder) -> None:
    q = exam.mod.QUESTIONS.get(qid)
    if not q:
        raise HTTPException(404, f"unknown question {qid}")
    letters = set(picked) & set(q["letters"])
    if not letters:
        raise HTTPException(400, "pick at least one option")
    rec.emit({"op": "meta", "graded": {"id": q["id"], "picked": sorted(letters), "correct": sorted(q["answer"]),
                                       "right": letters == q["answer"]}})
    await exam.mod.grade(rec, exam.mod.load_state(), q, letters)


def stream(exam: Exam, run) -> StreamingResponse:
    """Run `run(rec)` under the exam lock; stream its messages as NDJSON while it works."""
    queue: asyncio.Queue = asyncio.Queue()
    rec = Recorder(exam, queue)

    async def worker():
        async with exam.lock:
            try:
                await run(rec)
            except HTTPException as exc:
                rec.emit({"op": "error", "html": html.escape(str(exc.detail))})
            except Exception as exc:          # the state write (if any) already happened inside the bot code
                log.exception("action failed")
                rec.emit({"op": "error", "html": f"⚠️ <code>{html.escape(str(exc)[:300])}</code>"})
        queue.put_nowait(None)

    asyncio.create_task(worker())   # keeps running even if the browser disconnects

    async def gen():
        while (ev := await queue.get()) is not None:
            yield json.dumps(ev) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


# ── Exam mode (timed, graded at the end, same state file) ────────────────────

def exam_pick(exam: Exam, n: int) -> list[dict]:
    """Unseen questions first (shuffled), then the rest, so a mock exam is mostly new material."""
    Q, state = exam.mod.QUESTIONS, exam.mod.load_state()
    seen = set(state["ok"]) | set(state["bad"])
    unseen, rest = [i for i in Q if i not in seen], [i for i in Q if i in seen]
    random.shuffle(unseen); random.shuffle(rest)
    return [qview(Q[i]) for i in (unseen + rest)[:n]]


def exam_submit(exam: Exam, answers: dict[str, list[str]], minutes_used: float) -> dict:
    """Grade every answered question, write ok/bad/log like grade() does (no Claude), return the result sheet."""
    mod, Q = exam.mod, exam.mod.QUESTIONS
    state = mod.load_state()
    now = datetime.now(SGT).isoformat(timespec="minutes")
    results, by_dom = [], {}
    weights = getattr(mod, "WEIGHTS", {})
    for qid, picked in answers.items():
        q = Q.get(qid)
        if not q:
            continue
        letters = set(picked) & set(q["letters"])
        right = letters == q["answer"]
        bucket = "ok" if right else "bad"
        state[bucket][qid] = state[bucket].get(qid, 0) + 1
        first = state["ok"].get(qid, 0) + state["bad"].get(qid, 0) == 1
        state.setdefault("log", []).append({"t": now, "id": qid, "ok": right, "first": first, "exam": True})
        d = by_dom.setdefault(q["domain"], [0, 0]); d[1] += 1; d[0] += right
        results.append({"id": qid, "domain": q["domain"], "question": q["question"], "options": options_of(q),
                        "multi": len(q["answer"]) > 1, "picked": sorted(letters), "correct": sorted(q["answer"]),
                        "right": right, "explanation": q["explanation"]})
    state["log"] = state["log"][-5000:]
    mod.save_state(state)
    n_right = sum(r["right"] for r in results)
    # Score like the weekly report: domain accuracy weighted by the blueprint (equal weights where unknown), 1000 scale.
    w = {d: weights.get(d, 1) for d in by_dom}
    score = round(sum(w[d] * (r / n) for d, (r, n) in by_dom.items()) / sum(w.values()) * 1000) if by_dom else 0
    mod.vault_log("🎯 **mock exam**", f"{n_right}/{len(results)} right, score {score}, {round(minutes_used)} min")
    return {"n": len(results), "right": n_right, "score": score, "pass": mod.PASS, "passed": score >= mod.PASS,
            "minutes": round(minutes_used, 1),
            "domains": sorted(({"name": d, "right": r, "n": n, "weight": weights.get(d)} for d, (r, n) in by_dom.items()),
                              key=lambda x: -(x["weight"] or 0)),
            "results": results}


# ── HTTP ─────────────────────────────────────────────────────────────────────

EXAMS: dict[str, Exam] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    EXAMS.update(load_exams())
    yield


app = FastAPI(title="Exam Portal", docs_url=None, redoc_url=None, lifespan=lifespan)


def exam_or_404(key: str) -> Exam:
    if key not in EXAMS:
        raise HTTPException(404, "unknown exam")
    return EXAMS[key]


class Act(BaseModel):
    data: str


class Answer(BaseModel):
    id: str
    picked: list[str]


class ExamStart(BaseModel):
    n: int | None = None


class ExamSubmit(BaseModel):
    answers: dict[str, list[str]]
    minutes_used: float = 0


@app.get("/api/exams")
def api_exams():
    out = []
    for e in EXAMS.values():
        c = e.coverage(e.mod.load_state())
        out.append({"key": e.key, "short": e.short, "name": e.name, "has_study": e.has_study,
                    "total": c["total"], "seen": c["seen"], "right": c["right"], "pass": e.mod.PASS,
                    "exam_n": min(e.n_exam, c["total"]), "exam_minutes": e.minutes})
    return out


@app.get("/api/{key}/home")
def api_home(key: str):
    e = exam_or_404(key)
    state = e.mod.load_state()
    m = e.mod.metrics(state, datetime.now(SGT))
    pending = None
    if state.get("pending") in e.mod.QUESTIONS:            # a question asked (web or Telegram) and not yet answered
        q = e.mod.QUESTIONS[state["pending"]]
        pending = {"html": e.mod.question_text(q), "question": qview(q)}
    last = state.get("last") or {}
    return {"key": e.key, "short": e.short, "name": e.name, "has_study": e.has_study,
            "coverage": e.coverage(state), "score": m["score"], "pass": e.mod.PASS, "verdict": e.mod.verdict(m),
            "week": {"n": m["week_n"], "ok": m["week_ok"]}, "pending": pending,
            "last": {"id": last.get("id"), "has_teach": bool(last.get("teach"))} if last else None,
            "study_cards": len(getattr(e.mod, "NOTES", {}) or {}),
            "studied": len(set(state.get("studied", []))),
            "exam_n": min(e.n_exam, len(e.mod.QUESTIONS)), "exam_minutes": e.minutes,
            "exam_answers": sum(1 for x in state.get("log", []) if x.get("exam"))}


@app.get("/api/{key}/study")
def api_study(key: str):
    e = exam_or_404(key)
    return {"reading": e.study, "coverage": e.coverage(e.mod.load_state()), "has_cards": e.has_study}


@app.post("/api/{key}/act")
async def api_act(key: str, body: Act):
    e = exam_or_404(key)
    return stream(e, functools.partial(act, e, body.data))


@app.post("/api/{key}/answer")
async def api_answer(key: str, body: Answer):
    e = exam_or_404(key)
    return stream(e, functools.partial(answer, e, body.id, body.picked))


@app.post("/api/{key}/exam/start")
def api_exam_start(key: str, body: ExamStart):
    e = exam_or_404(key)
    n = max(1, min(body.n or e.n_exam, len(e.mod.QUESTIONS)))
    minutes = e.minutes if n >= e.n_exam else max(5, round(e.minutes * n / e.n_exam))   # same pace as the real exam
    return {"questions": exam_pick(e, n), "minutes": minutes, "n": n, "pass": e.mod.PASS}


@app.post("/api/{key}/exam/submit")
async def api_exam_submit(key: str, body: ExamSubmit):
    e = exam_or_404(key)
    async with e.lock:
        return exam_submit(e, body.answers, body.minutes_used)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html", headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
