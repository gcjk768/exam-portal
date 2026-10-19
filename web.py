#!/usr/bin/env python3
"""Exam Portal: one website for the CCAR-P, SAP-C03, AIP-C01 and CKA quiz bots, every Telegram function included,
plus a Study door (reading list, command playbook, study cards) and a timed Exam mode.

Each exam's own bot.py is imported from its stack folder and driven with a recorder in place of the
Telegram chat, so the web shows the same message flow (verdict, "drawing", diagram, tutor, report)
and writes the same data/state.json. Events stream to the browser as NDJSON.

Task exams (CKA): a "question" is a hands-on task with no options. The student types the commands or YAML,
Claude grades them against the reference solution (or the student reveals it and self-marks), and the mock
exam grades all tasks in one Claude call.

Env: EXAMS="key:bot_dir[:vault_dir],..."  (default: sibling project folders whose bot.py logger name is a known exam)
     CLAUDE_MODEL, CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY (as for the bots), PORT (8000).
"""
from __future__ import annotations

import asyncio
import base64
import copy
import functools
import html
import importlib.util
import json
import logging
import os
import random
import re
import sys
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger("portal")
ROOT = Path(__file__).resolve().parent
SGT = timezone(timedelta(hours=8))
PUBLIC_HIDDEN = {"Tutorials Dojo"}   # paid question sources: shown on LAN/tailnet only, never on the public Funnel link
# key (= the bot's logger name) -> short, full name, real exam length, real exam minutes
META = {"ccarp": ("CCAR-P", "Claude Certified Architect Professional", 63, 120),
        "sapc03": ("SAP-C03", "AWS Solutions Architect Professional", 75, 180),
        "aipc01": ("AIP-C01", "AWS Generative AI Developer Professional", 75, 180),
        "cka": ("CKA", "Certified Kubernetes Administrator", 17, 120),   # ~17 hands-on tasks in 120 min
        "gitlabci": ("GitLab CI/CD", "GitLab Certified CI/CD Associate", 50, 75),
        "tfassoc": ("Terraform", "HashiCorp Terraform Associate (003)", 57, 60)}


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
        if (self.dir / "online_parsers.py").exists():          # CCAR-P: scrape.py imports it for the downloaded banks
            sys.modules["online_parsers"] = self._load("online_parsers", self.dir / "online_parsers.py")
        self.mod.vault_log = vault_writer(vault)
        self.mod.PREFER_MERMAID = True                         # tutor draws Mermaid here (icons are added in the browser)
        png = self.mod.render                                  # tutor Mermaid: the browser draws it in the portal's style + icons
        self.mod.render = lambda kind, code: MERMAID_SRC + code.encode() if kind == "mermaid" else png(kind, code)
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

    @property
    def has_tasks(self) -> bool:
        """A hands-on exam (CKA): some bank rows are tasks without options."""
        return any(not q["letters"] for q in self.mod.QUESTIONS.values())

    @property
    def tasks(self) -> bool:
        """Kept for the API: True when the exam has task rows at all."""
        return self.has_tasks

    def mode(self, state: dict) -> str:
        """Study mode for hands-on exams: mcq first, tasks once every MCQ has been seen; state["mode"] overrides."""
        if not self.has_tasks:
            return "mcq"
        forced = state.get("mode")
        if forced in ("mcq", "tasks"):
            return forced
        seen = set(state["ok"]) | set(state["bad"])
        mcq_left = [i for i, q in self.mod.QUESTIONS.items() if q["letters"] and i not in seen]
        return "mcq" if mcq_left else "tasks"

    def questions(self, public: bool = False) -> dict:
        """The bank; on the public link without the paid sources."""
        Q = self.mod.QUESTIONS
        return {i: q for i, q in Q.items() if q.get("source") not in PUBLIC_HIDDEN} if public else Q

    def pool(self, state: dict, public: bool = False) -> dict:
        """Questions of the current mode (hands-on exams); everything otherwise."""
        Q = self.questions(public)
        if not self.has_tasks:
            return Q
        want_tasks = self.mode(state) == "tasks"
        return {i: q for i, q in Q.items() if (not q["letters"]) == want_tasks}

    def pick(self, state: dict, domain: str | None = None, public: bool = False) -> dict:
        """bot.pick_question restricted to the current pool (same tiers: unseen, then missed, then any).
        With `domain` (Study → "Quiz me on this topic"): only that domain, any mode if the current one has none."""
        Q = self.pool(state, public) or self.questions(public)
        if domain is not None:
            Q = ({i: q for i, q in Q.items() if q["domain"] == domain}
                 or {i: q for i, q in self.questions(public).items() if q["domain"] == domain})
            if not Q:
                raise HTTPException(404, f"no questions in domain {domain}")
        seen = set(state["ok"]) | set(state["bad"])
        ids = [i for i in Q if i not in seen] or [i for i in Q if state["bad"].get(i, 0) > state["ok"].get(i, 0)] or list(Q)
        return Q[random.choice(ids)]

    def progress(self, state: dict) -> dict:
        Q = self.mod.QUESTIONS
        seen = set(state["ok"]) | set(state["bad"])
        mcq = [i for i, q in Q.items() if q["letters"]]
        tasks = [i for i, q in Q.items() if not q["letters"]]
        return {"mode": self.mode(state), "forced": state.get("mode") or "auto",
                "mcq": {"total": len(mcq), "seen": sum(i in seen for i in mcq)},
                "tasks": {"total": len(tasks), "seen": sum(i in seen for i in tasks)}}

    def coverage(self, state: dict, public: bool = False) -> dict:
        """Per-domain seen / right-first-time / total, like stats_text but as data."""
        Q, ok, bad = self.questions(public), state["ok"], state["bad"]
        seen = (set(ok) | set(bad)) & set(Q)
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
        if not exams[key].mod.QUESTIONS:            # a scaffolded exam whose bank is not built yet
            log.warning("exam %s has no questions, skipped (%s)", key, bot_dir)
            del exams[key]
            continue
        log.info("exam %s: %d questions, study cards=%s, tasks=%s (%s)", key, len(exams[key].mod.QUESTIONS),
                 exams[key].has_study, exams[key].tasks, bot_dir)
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
    return {"id": q["id"], "domain": q["domain"], "question": q["question"], "options": options_of(q),
            "multi": len(q["answer"]) > 1, "task": not q["letters"]}


class Msg:
    def __init__(self, rec: "Recorder", mid: int):
        self.rec, self.id = rec, mid

    async def edit_text(self, text: str, reply_markup=None, **_):
        self.rec.emit({"op": "edit", "id": self.id, "html": text, "buttons": self.rec.buttons(reply_markup)})

    async def delete(self):
        self.rec.emit({"op": "delete", "id": self.id})


MERMAID_SRC = b"\0mermaid\0"   # marks a "photo" that is really Mermaid source (see Exam.__init__)


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
        if photo.startswith(MERMAID_SRC):
            self.emit({"op": "add", "id": self.n, "kind": "mermaid", "src": photo[len(MERMAID_SRC):].decode("utf-8", "replace"),
                       "html": caption, "buttons": self.buttons(reply_markup)})
            return Msg(self, self.n)
        self.emit({"op": "add", "id": self.n, "kind": "photo", "png": base64.b64encode(photo).decode(),
                   "html": caption, "buttons": self.buttons(reply_markup)})
        return Msg(self, self.n)


# ── State helpers shared by quiz, tasks and exam mode ────────────────────────

def record(exam: Exam, state: dict, qid: str, right: bool, extra: dict | None = None) -> bool:
    """Write ok/bad + log like bot.grade() does. Returns whether this was the first attempt."""
    bucket = "ok" if right else "bad"
    state[bucket][qid] = state[bucket].get(qid, 0) + 1
    first = state["ok"].get(qid, 0) + state["bad"].get(qid, 0) == 1
    state.setdefault("log", []).append({"t": datetime.now(SGT).isoformat(timespec="minutes"), "id": qid,
                                        "ok": right, "first": first, **(extra or {})})
    state["log"] = state["log"][-5000:]
    return first


# ── Actions (the bot's button vocabulary) ────────────────────────────────────

def hidden(exam: Exam, qid: str, public: bool) -> bool:
    return public and qid not in exam.questions(True)


async def act(exam: Exam, data: str, rec: Recorder, public: bool = False) -> None:
    mod = exam.mod
    state = mod.load_state()
    if data == "next":
        q = exam.pick(state, public=public) if (exam.has_tasks or public) else None
        if q is not None and not q["letters"]:
            await send_task(exam, rec, state, q)
        elif q is not None:
            await mod.send_question(rec, state, q)
        else:
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
    elif data.startswith("d|"):                           # Study → "Quiz me on this topic"
        q = exam.pick(state, data[2:], public)
        if not q["letters"]:
            await send_task(exam, rec, state, q)
        else:
            await mod.send_question(rec, state, q)
    elif "|" in data:
        kind, qid, *rest = data.split("|")
        q = mod.QUESTIONS.get(qid)
        if not q or hidden(exam, qid, public):
            raise HTTPException(404, f"unknown question {qid}")
        if kind == "q":                                   # "Quiz me on this" under a study card
            if not q["letters"]:
                await send_task(exam, rec, state, q)
            else:
                await mod.send_question(rec, state, q)
        elif kind == "e":                                 # Explain more: the only place the portal draws a diagram
            await mod.explain(rec, q, set(filter(None, (rest[0] if rest else "").split(","))))
        else:
            raise HTTPException(400, f"unknown action {data}")
    else:
        raise HTTPException(400, f"unknown action {data}")


async def answer(exam: Exam, qid: str, picked: list[str], rec: Recorder, public: bool = False) -> None:
    q = exam.mod.QUESTIONS.get(qid)
    if not q or hidden(exam, qid, public):
        raise HTTPException(404, f"unknown question {qid}")
    letters = set(picked) & set(q["letters"])
    if not letters:
        raise HTTPException(400, "pick at least one option")
    rec.emit({"op": "meta", "graded": {"id": q["id"], "picked": sorted(letters), "correct": sorted(q["answer"]),
                                       "right": letters == q["answer"]}})
    mod, state = exam.mod, exam.mod.load_state()
    right = letters == q["answer"]
    if right:
        await mod.grade(rec, state, q, letters)          # right answers never call Claude in the bots
        return
    # Wrong: the bot's grade() would draw the tutor diagram now; the portal waits for the "Explain more" button.
    state["last"] = {"id": q["id"], "picked": sorted(letters)}
    state["pending"] = None
    record(exam, state, q["id"], False)
    mod.save_state(state)
    mod.vault_log(f"{mod.TITLE['bad']} **wrong**", f"{q['id']} picked {','.join(sorted(letters))} answer {q['correct']}")
    head = (f"{mod.TITLE['bad']} <b>WRONG</b> · you picked <code>{html.escape(','.join(sorted(letters)))}</code>, "
            f"answer <code>{html.escape(q['correct'])}</code>")
    await rec.send_message(f"{head}\n\n{html.escape(q['explanation'])}", reply_markup=mod.after_kb(q, letters))


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


# ── Task exams (CKA): type the commands, Claude grades them ──────────────────

GRADE = """You are the examiner for a hands-on Certified Kubernetes Administrator (CKA) task. The student is still
learning bash and kubectl. You get the task, the reference solution, and the commands or YAML the student typed from
memory (no cluster available). Grade the student's answer: would it accomplish the task on a real cluster? Different
but equivalent commands or YAML are fine (imperative vs declarative, aliases, different but valid flags). Missing steps,
wrong namespaces, wrong resource kinds, wrong flags or YAML keys lose points. Return JSON with: "score" (0-100),
"verdict" (one short sentence), "feedback" (under 160 words, plain text, no markdown: what was right, what was
missing or wrong, and the trap in this task), "commands" (the exact commands or YAML the student should run, as one
plain-text block with one command per line and a short plain-words note after each important flag). Be strict but
encouraging."""
GRADE_SCHEMA = json.dumps({"type": "object", "required": ["score", "verdict", "feedback", "commands"], "additionalProperties": False,
                           "properties": {"score": {"type": "integer"}, "verdict": {"type": "string"},
                                          "feedback": {"type": "string"}, "commands": {"type": "string"}}})
PASS_TASK = 70   # score at or above counts as "ok" for progress


def task_html(q: dict) -> str:
    return f"🛠 <b>{html.escape(q['id'])}</b> · {html.escape(q['domain'])}\n\n{html.escape(q['question'])}"


def solution_html(q: dict) -> str:
    return f"📘 <b>REFERENCE SOLUTION</b> · {html.escape(q['id'])}\n<pre>{html.escape(q['explanation'])}</pre>"


async def send_task(exam: Exam, rec: Recorder, state: dict, q: dict | None = None) -> None:
    q = q or exam.pick(state)
    await rec.send_message(task_html(q))
    rec.emit({"op": "meta", "task": qview(q)})
    state["pending"] = q["id"]
    exam.mod.save_state(state)
    exam.mod.vault_log("🛠 **task asked**", q["id"])


async def grade_one(exam: Exam, q: dict, answer: str) -> dict:
    prompt = (f"TASK:\n{q['question']}\n\nREFERENCE SOLUTION:\n{q['explanation']}\n\nSTUDENT ANSWER:\n{answer.strip() or '(blank)'}\n\n"
              'Reply with ONLY a JSON object: {"score": 0-100, "verdict": "...", "feedback": "...", "commands": "..."}.')
    data = await exam.mod.run_claude(GRADE, prompt, GRADE_SCHEMA)
    out = data.get("structured_output")
    if not isinstance(out, dict):
        text = str(data.get("result") or "")
        out = json.loads(text[text.find("{"):text.rfind("}") + 1])
    out["score"] = max(0, min(100, int(out.get("score", 0))))
    return out


async def check_task(exam: Exam, qid: str, answer: str, rec: Recorder) -> None:
    """Claude grades the typed commands, progress is recorded, the guideline and reference solution are shown."""
    mod = exam.mod
    q = mod.QUESTIONS.get(qid)
    if not q or q["letters"]:
        raise HTTPException(404, f"unknown task {qid}")
    note = await rec.send_message("⏳ <i>Grading your commands…</i>")
    try:
        out = await grade_one(exam, q, answer)
    except Exception as exc:
        log.warning("grading failed: %s", exc)
        await note.edit_text(f"⚠️ Grader unavailable: <code>{html.escape(str(exc)[:200])}</code>. Reveal the solution and self-mark instead.")
        return
    right = out["score"] >= PASS_TASK
    state = mod.load_state()
    record(exam, state, qid, right, {"score": out["score"]})
    state["pending"] = None
    state["last"] = {"id": qid, "picked": [], "teach": out["feedback"]}
    mod.save_state(state)
    mod.vault_log(f"{'🟢' if right else '🔴'} **task {'passed' if right else 'missed'}**", f"{qid} score {out['score']}")
    await note.delete()
    rec.emit({"op": "meta", "graded_task": {"id": qid, "score": out["score"], "right": right}})
    head = f"{'🟢' if right else '🔴'} <b>{'PASS' if right else 'NOT YET'}</b> · score <code>{out['score']}</code>/100 · {html.escape(out['verdict'])}"
    await rec.send_message(f"{head}\n\n{html.escape(out['feedback'])}")
    await rec.send_message(f"🧭 <b>COMMAND GUIDELINE</b> · {html.escape(qid)}\n<pre>{html.escape(out['commands'])}</pre>")
    await rec.send_message(solution_html(q), reply_markup=mod.NEXT_KB)


async def self_mark(exam: Exam, qid: str, result: str, rec: Recorder) -> None:
    """Free path: reveal the reference solution, the student says whether they got it."""
    mod = exam.mod
    q = mod.QUESTIONS.get(qid)
    if not q or q["letters"]:
        raise HTTPException(404, f"unknown task {qid}")
    if result == "reveal":
        await rec.send_message(solution_html(q))
        rec.emit({"op": "meta", "revealed": qid})
        return
    right = result == "ok"
    state = mod.load_state()
    record(exam, state, qid, right, {"self": True})
    state["pending"] = None
    mod.save_state(state)
    mod.vault_log(f"{'🟢' if right else '🔴'} **task self-marked {'ok' if right else 'missed'}**", qid)
    rec.emit({"op": "meta", "graded_task": {"id": qid, "score": 100 if right else 0, "right": right}})
    await rec.send_message(f"{'🟢' if right else '🔴'} <b>{'Marked as done' if right else 'Marked to repeat'}</b> · {html.escape(qid)}",
                           reply_markup=mod.NEXT_KB)


# ── Exam mode (timed, graded at the end, same state file) ────────────────────

def exam_pick(exam: Exam, n: int, public: bool = False) -> list[dict]:
    """Unseen questions first (shuffled), then the rest, so a mock exam is mostly new material."""
    state = exam.mod.load_state()
    Q = exam.pool(state, public) or exam.questions(public)
    seen = set(state["ok"]) | set(state["bad"])
    unseen, rest = [i for i in Q if i not in seen], [i for i in Q if i in seen]
    random.shuffle(unseen); random.shuffle(rest)
    return [qview(Q[i]) for i in (unseen + rest)[:n]]


async def exam_submit(exam: Exam, answers: dict[str, list[str]], minutes_used: float, public: bool = False) -> dict:
    """Grade every answered question, write ok/bad/log like grade() does, return the result sheet.
    MCQ exams need no Claude; task exams grade each typed answer with Claude (blank answers score 0 without a call)."""
    mod, Q = exam.mod, exam.mod.QUESTIONS
    state = mod.load_state()
    results, by_dom = [], {}
    weights = getattr(mod, "WEIGHTS", {})
    for qid, picked in answers.items():
        q = Q.get(qid)
        if not q or hidden(exam, qid, public):
            continue
        if not q["letters"]:
            text = (picked[0] if picked else "").strip()
            if text:
                try:
                    g = await grade_one(exam, q, text)
                except Exception as exc:
                    log.warning("exam grading failed for %s: %s", qid, exc)
                    g = {"score": 0, "verdict": "grader unavailable", "feedback": str(exc)[:200], "commands": ""}
            else:
                g = {"score": 0, "verdict": "blank", "feedback": "No answer given.", "commands": ""}
            right = g["score"] >= PASS_TASK
            record(exam, state, qid, right, {"exam": True, "score": g["score"]})
            d = by_dom.setdefault(q["domain"], [0, 0]); d[1] += 1; d[0] += g["score"] / 100
            results.append({"id": qid, "domain": q["domain"], "question": q["question"], "options": [], "multi": False, "task": True,
                            "answer": text, "score": g["score"], "verdict": g["verdict"], "feedback": g["feedback"],
                            "commands": g["commands"], "right": right, "explanation": q["explanation"], "picked": [], "correct": []})
        else:
            letters = set(picked) & set(q["letters"])
            right = letters == q["answer"]
            record(exam, state, qid, right, {"exam": True})
            d = by_dom.setdefault(q["domain"], [0, 0]); d[1] += 1; d[0] += right
            results.append({"id": qid, "domain": q["domain"], "question": q["question"], "options": options_of(q),
                            "multi": len(q["answer"]) > 1, "task": False, "picked": sorted(letters), "correct": sorted(q["answer"]),
                            "right": right, "explanation": q["explanation"]})
    mod.save_state(state)
    n_right = sum(r["right"] for r in results)
    w = {d: weights.get(d, 1) for d in by_dom}
    score = round(sum(w[d] * (r / n) for d, (r, n) in by_dom.items()) / sum(w.values()) * 1000) if by_dom else 0
    mod.vault_log("🎯 **mock exam**", f"{n_right}/{len(results)} right, score {score}, {round(minutes_used)} min")
    return {"n": len(results), "right": n_right, "score": score, "pass": mod.PASS, "passed": score >= mod.PASS,
            "minutes": round(minutes_used, 1), "tasks": any(r["task"] for r in results),
            "domains": sorted(({"name": d, "right": round(r, 1), "n": n, "weight": weights.get(d)} for d, (r, n) in by_dom.items()),
                              key=lambda x: -(x["weight"] or 0)),
            "results": results}


# ── HTTP ─────────────────────────────────────────────────────────────────────

EXAMS: dict[str, Exam] = {}


# ── Study videos: weekly check, hide the ones YouTube removed or stopped allowing to embed ──
DEAD_VIDEOS: set[str] = set()


def video_ids(study: dict) -> set[str]:
    return {v["id"] for d in study.get("domains", []) for v in [d.get("video")] + [n.get("video") for n in d.get("notes", [])] if v}


def video_dead(vid: str) -> bool:
    """Only YouTube's own answer counts (401/403/404); network trouble is not a verdict."""
    try:
        urllib.request.urlopen(f"https://www.youtube.com/oembed?format=json&url=https://youtu.be/{vid}", timeout=20)
        return False
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403, 404)
    except Exception:
        return False


def without_dead(study: dict) -> dict:
    if not DEAD_VIDEOS:
        return study
    s = copy.deepcopy(study)
    for d in s.get("domains", []):
        for x in [d] + d.get("notes", []):
            if (x.get("video") or {}).get("id") in DEAD_VIDEOS:
                x["video"] = None
    return s


async def check_videos(first_delay: int = 60) -> None:
    await asyncio.sleep(first_delay)
    while True:
        for e in list(EXAMS.values()):
            for vid in sorted(video_ids(e.study)):
                dead = await asyncio.to_thread(video_dead, vid)
                if dead and vid not in DEAD_VIDEOS:
                    log.warning("study video gone: %s (%s)", vid, e.key)
                    e.mod.vault_log("🎬 **video gone**", f"youtu.be/{vid} hidden from Study")
                (DEAD_VIDEOS.add if dead else DEAD_VIDEOS.discard)(vid)
                await asyncio.sleep(1)                     # low volume: one request a second
        log.info("video check done: %d hidden", len(DEAD_VIDEOS))
        await asyncio.sleep(7 * 86400)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    EXAMS.update(load_exams())
    task = asyncio.create_task(check_videos())
    yield
    task.cancel()


app = FastAPI(title="Exam Portal", docs_url=None, redoc_url=None, lifespan=lifespan)


def exam_or_404(key: str) -> Exam:
    if key not in EXAMS:
        raise HTTPException(404, "unknown exam")
    return EXAMS[key]


def is_public(req: Request) -> bool:
    """Public = came in through Tailscale Funnel (it adds a marker header), or asked for the public ts.net name.
    The second rule covers your own tailnet devices that open the public URL: Tailscale connects them directly, so
    there is no Funnel header, but the Host is still the public name. Your private URL is the NAS tailnet IP."""
    h = req.headers
    return "tailscale-funnel-request" in h or any(".ts.net" in h.get(k, "").lower() for k in ("host", "x-forwarded-host"))


async def deny(rec: Recorder) -> None:
    raise HTTPException(403, "Claude and admin actions are off on the public link. Use the home network or Tailscale.")


class Act(BaseModel):
    data: str


class Answer(BaseModel):
    id: str
    picked: list[str]


class TaskAnswer(BaseModel):
    id: str
    answer: str = ""
    result: str = ""        # self-mark: reveal | ok | bad


class Mode(BaseModel):
    mode: str        # auto | mcq | tasks


class ExamStart(BaseModel):
    n: int | None = None


class ExamSubmit(BaseModel):
    answers: dict[str, list[str]]
    minutes_used: float = 0


@app.get("/api/access")
def api_access(req: Request):
    return {"public": is_public(req)}


@app.get("/api/exams")
def api_exams(req: Request):
    out, pub = [], is_public(req)
    for e in EXAMS.values():
        c = e.coverage(e.mod.load_state(), pub)
        st = e.mod.load_state()
        out.append({"key": e.key, "short": e.short, "name": e.name, "has_study": e.has_study, "tasks": e.has_tasks,
                    "mode": e.mode(st), "progress": e.progress(st) if e.has_tasks else None,
                    "total": c["total"], "seen": c["seen"], "right": c["right"], "pass": e.mod.PASS,
                    "exam_n": min(e.n_exam, len(e.pool(st, pub)) if e.has_tasks else c["total"]), "exam_minutes": e.minutes})
    return out


@app.get("/api/{key}/home")
def api_home(key: str, req: Request):
    e = exam_or_404(key)
    pub = is_public(req)
    state = e.mod.load_state()
    m = e.mod.metrics(state, datetime.now(SGT))
    pending = None
    if state.get("pending") in e.questions(pub):           # a question asked (web or Telegram) and not yet answered
        q = e.mod.QUESTIONS[state["pending"]]
        pending = {"html": task_html(q) if e.tasks else e.mod.question_text(q), "question": qview(q)}
    last = state.get("last") or {}
    if pub:                                                # the owner's score, readiness and week are not for the public link
        m = {**m, "score": 0, "week_n": 0, "week_ok": 0}
    return {"key": e.key, "short": e.short, "name": e.name, "has_study": e.has_study, "tasks": e.has_tasks,
            "mode": e.mode(state), "progress": e.progress(state) if e.has_tasks else None,
            "coverage": e.coverage(state, pub), "score": m["score"], "pass": e.mod.PASS, "verdict": "" if pub else e.mod.verdict(m),
            "week": {"n": m["week_n"], "ok": m["week_ok"]}, "pending": pending,
            "last": {"id": last.get("id"), "has_teach": bool(last.get("teach"))} if last else None,
            "study_cards": len(getattr(e.mod, "NOTES", {}) or {}),
            "studied": len(set(state.get("studied", []))),
            "exam_n": min(e.n_exam, len(e.pool(state, pub)) if e.has_tasks else len(e.questions(pub))), "exam_minutes": e.minutes,
            "exam_answers": sum(1 for x in state.get("log", []) if x.get("exam"))}


@app.post("/api/{key}/mode")
def api_mode(key: str, body: Mode):
    """Hands-on exams: force MCQ or task mode, or go back to auto (MCQ until every MCQ is seen)."""
    e = exam_or_404(key)
    if body.mode not in ("auto", "mcq", "tasks"):
        raise HTTPException(400, "mode must be auto, mcq or tasks")
    state = e.mod.load_state()
    if body.mode == "auto":
        state.pop("mode", None)
    else:
        state["mode"] = body.mode
    e.mod.save_state(state)
    e.mod.vault_log("🔀 **mode**", body.mode)
    return e.progress(state)


@app.get("/api/{key}/study")
def api_study(key: str, req: Request):
    e = exam_or_404(key)
    return {"reading": without_dead(e.study), "coverage": e.coverage(e.mod.load_state(), is_public(req)), "has_cards": e.has_study}


@app.post("/api/{key}/act")
async def api_act(key: str, body: Act, req: Request):
    e = exam_or_404(key)
    if is_public(req) and (body.data in ("report", "refresh", "reset") or body.data.startswith("e|")):   # Claude calls + state wipe
        return stream(e, deny)
    return stream(e, functools.partial(act, e, body.data, public=is_public(req)))


@app.post("/api/{key}/answer")
async def api_answer(key: str, body: Answer, req: Request):
    e = exam_or_404(key)
    return stream(e, functools.partial(answer, e, body.id, body.picked, public=is_public(req)))


@app.post("/api/{key}/task")
async def api_task(key: str, body: TaskAnswer):
    e = exam_or_404(key)
    if not e.has_tasks:
        raise HTTPException(400, "not a task exam")
    if body.result:
        return stream(e, functools.partial(self_mark, e, body.id, body.result))
    return stream(e, functools.partial(check_task, e, body.id, body.answer))


@app.post("/api/{key}/exam/start")
def api_exam_start(key: str, body: ExamStart, req: Request):
    e = exam_or_404(key)
    pub = is_public(req)
    st = e.mod.load_state()
    pool = e.pool(st, pub) if e.has_tasks else e.questions(pub)
    mode_tasks = e.has_tasks and e.mode(st) == "tasks"
    n_exam = e.n_exam if (mode_tasks or not e.has_tasks) else 60          # MCQ phase of a hands-on exam: 60 questions in the real 120 min
    n = max(1, min(body.n or n_exam, len(pool)))
    minutes = e.minutes if n >= n_exam else max(5, round(e.minutes * n / n_exam))   # same pace as the real exam
    return {"questions": exam_pick(e, n, pub), "minutes": minutes, "n": n, "pass": e.mod.PASS, "tasks": mode_tasks}


@app.post("/api/{key}/exam/submit")
async def api_exam_submit(key: str, body: ExamSubmit, req: Request):
    e = exam_or_404(key)
    async with e.lock:
        return await exam_submit(e, body.answers, body.minutes_used, is_public(req))


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def revalidate_static(req: Request, call_next):
    """Browsers heuristically cache css/js; make them revalidate so a deploy reaches every visitor."""
    resp = await call_next(req)
    if req.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index(req: Request):
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    if is_public(req):                         # set server-side too, so the public view never depends on cached JS
        page = page.replace("<html", '<html data-public="1"', 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
