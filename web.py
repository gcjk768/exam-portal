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
import hashlib
import copy
import functools
import html
import importlib.util
import json
import logging
import os
import random
import secrets
import shutil
import tempfile
import time
from collections import defaultdict, deque
from contextvars import ContextVar
import re
import sys
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import plan   # scheduler + study plan maths (pure functions over state.json)

log = logging.getLogger("portal")
ROOT = Path(__file__).resolve().parent
SGT = timezone(timedelta(hours=8))
VISITOR: ContextVar[str | None] = ContextVar("visitor", default=None)   # public visitor id (cookie, rate limit only); None = the owner
AUDIENCE: ContextVar[str] = ContextVar("audience", default="private")   # which exam list this request gets: public (the public name), personal (public name + owner key), private (tailnet IP / LAN)
OWNER_KEY = os.environ.get("TD_UNLOCK_KEY", "")   # open /?td=<key> once on a browser: the public link then treats it as the owner (Claude, reset, paid bank, own progress); empty = off
PUBLIC_HIDDEN = {x.strip() for x in os.environ.get("PUBLIC_HIDDEN_SOURCES", "Tutorials Dojo").split(",") if x.strip()}   # paid question sources: shown on LAN/tailnet only, never on the public Funnel link
# key (= the bot's logger name) -> short, full name, real exam length, real exam minutes
META = {"ccarp": ("CCAR-P", "Claude Certified Architect Professional", 63, 120),
        "ccaof": ("CCAO-F", "Claude Certified Associate - Foundations", 60, 120),
        "ccdvf": ("CCDV-F", "Claude Certified Developer - Foundations", 53, 120),
        "ccarf": ("CCAR-F", "Claude Certified Architect - Foundations", 60, 120),
        "sapc03": ("SAP-C03", "AWS Solutions Architect Professional", 75, 180),
        "aipc01": ("AIP-C01", "AWS Generative AI Developer Professional", 75, 180),
        "cka": ("CKA", "Certified Kubernetes Administrator", 17, 120),   # ~17 hands-on tasks in 120 min
        "gitlabci": ("GitLab CI/CD", "GitLab Certified CI/CD Associate", 50, 75),
        "tfassoc": ("Terraform", "HashiCorp Terraform Associate (003)", 57, 60)}


# Which loaded exams each link shows (the owner, 2026-10-23): the public name shows the Claude certifications, the owner's
# link (tailnet IP / LAN) shows the exams the owner is taking, and the public name opened with the owner key (his personal
# URL) shows only CCAR-P. Empty = every loaded exam.
EXAM_LISTS = {a: {x.strip() for x in os.environ.get(f"{a.upper()}_EXAMS", "").split(",") if x.strip()}
              for a in ("public", "private", "personal")}


def visible(key: str, audience: str) -> bool:
    allowed = EXAM_LISTS[audience]
    return not allowed or key in allowed


def audience(req: Request) -> str:
    return ("personal" if owner_key(req) else "public") if funnel(req) else "private"


def hidden_source(src: str | None) -> bool:
    """Paid banks stay off the public link: the env list, plus the Udemy mock sets (CCAR-P rows MF1/MF2/MF3, "Microsoft Forms mock (...)")."""
    return bool(src) and (src in PUBLIC_HIDDEN or "Udemy" in src or src.startswith("Microsoft Forms mock"))


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


def quiet_for_public(fn):
    """Public visitors' activity stays out of the owner's vault log."""
    return lambda *a, **k: None if VISITOR.get() else fn(*a, **k)


TRACK_DIR = Path(os.environ.get("TRACKER_DIR", ROOT / "data" / "tracker"))   # opt-in per-visitor progress: <pid>/on marks consent, <pid>/<exam>.json is the state


def track_path(pid: str, key: str) -> Path:
    return TRACK_DIR / pid / f"{key}.json"


def tracked() -> bool:
    """This request is a public visitor who chose to save their progress."""
    pid = VISITOR.get()
    return bool(pid) and (TRACK_DIR / pid / "on").exists()


# Claude credentials (the owner, 2026-10-26): the portal runs `claude -p` itself for every exam (same flags as the bots' run_claude)
# so it can pick the credential per call: the API key first; once it reports its spend limit, the subscription token
# CLAUDE_FALLBACK_OAUTH_TOKEN until the key's reset time, for every caller. The CLI reports API errors in its JSON on stdout.
LIMIT_RX = re.compile(r"usage limits?|credit balance", re.I)
RESET_RX = re.compile(r"(\d{4}-\d{2}-\d{2}) at (\d{2}:\d{2}) UTC")
KEY_BACK: dict[str, float] = {}               # "at": epoch when the API key is tried again; empty = the key is in use


def claude_env(token: bool) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "TELEGRAM_BOT_TOKEN"}
    env["DISABLE_AUTOUPDATER"] = "1"
    if token:
        env.pop("ANTHROPIC_API_KEY", None)        # the CLI prefers the API key over a token: leave it out
        env["CLAUDE_CODE_OAUTH_TOKEN"] = os.environ["CLAUDE_FALLBACK_OAUTH_TOKEN"]
    return env


async def claude_once(mod, key: str, system: str, prompt: str, schema: str | None, env: dict) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not on PATH")
    work = Path(tempfile.gettempdir()) / f"{key}-work"
    work.mkdir(exist_ok=True)
    cmd = [exe, "-p", "--output-format", "json", "--no-session-persistence", "--permission-mode",
           "dontAsk", "--model", mod.MODEL, "--tools", "", "--max-turns", "6", "--append-system-prompt", system]
    if schema:
        cmd += ["--json-schema", schema]
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, cwd=str(work), env=env)
    try:
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), mod.CLAUDE_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"claude timed out after {mod.CLAUDE_TIMEOUT}s")
    text = out.decode(errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = {}
    if proc.returncode or not data or data.get("is_error"):
        why = data.get("result") or err.decode(errors="replace") or text
        raise RuntimeError(f"claude exit {proc.returncode}: {str(why)[:300]}")
    log.info("claude %s cost=$%s turns=%s", data.get("subtype"), data.get("total_cost_usd"), data.get("num_turns"))
    return data


def claude_runner(mod, key: str):
    """The exam module's run_claude(system, prompt, schema=None), with the key-to-token fallback."""
    async def run_claude(system: str, prompt: str, schema: str | None = None) -> dict:
        if KEY_BACK and time.time() >= KEY_BACK["at"]:
            KEY_BACK.clear()                          # the key's limit has reset: try it again
        if KEY_BACK:
            return await claude_once(mod, key, system, prompt, schema, claude_env(True))
        try:
            return await claude_once(mod, key, system, prompt, schema, claude_env(False))
        except RuntimeError as exc:
            if not os.environ.get("ANTHROPIC_API_KEY") or not os.environ.get("CLAUDE_FALLBACK_OAUTH_TOKEN") or not LIMIT_RX.search(str(exc)):
                raise
            m = RESET_RX.search(str(exc))
            KEY_BACK["at"] = datetime.strptime(" ".join(m.groups()), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() if m else time.time() + 3600
            log.warning("Claude API key at its spend limit: subscription token until %s", datetime.fromtimestamp(KEY_BACK["at"], SGT).isoformat(timespec="minutes"))
            return await claude_once(mod, key, system, prompt, schema, claude_env(True))
    return run_claude


class Exam:
    def __init__(self, key: str, bot_dir: str, vault: str | None):
        self.key, self.dir = key, Path(bot_dir)
        self.short, self.name, self.n_exam, self.minutes = META.get(key, (key.upper(), key, 65, 130))
        os.environ["DATA_DIR"] = str(self.dir / "data")       # bot.py reads it at import
        self.mod = self._load(f"bot_{key}", self.dir / "bot.py")
        self.scrape = self.parsers = None
        if (self.dir / "scrape.py").exists():                  # refresh_bank does `import scrape` lazily: act() points sys.modules at this exam's copy
            self.scrape = sys.modules["scrape"] = self._load(f"scrape_{key}", self.dir / "scrape.py")
        if (self.dir / "online_parsers.py").exists():          # CCAR-P: scrape.py imports it for the downloaded banks; several exams have one, so act() swaps it in too
            self.parsers = sys.modules["online_parsers"] = self._load("online_parsers", self.dir / "online_parsers.py")
        self.vault, self.vault_raw = vault, vault_writer(vault)     # vault_raw: flag reviews (no visitor data in them)
        self.mod.vault_log = quiet_for_public(self.vault_raw)
        self._own_state(self.mod, key)
        self.mod.PREFER_MERMAID = True                         # tutor draws Mermaid here (icons are added in the browser)
        self.mod.run_claude = claude_runner(self.mod, key)     # the portal's own runner: API key, then the subscription token at its limit
        self.mod.ask_claude = cached_tutor(self)               # Explain more: once per question and picked answer
        png = self.mod.render                                  # tutor Mermaid: the browser draws it in the portal's style + icons
        self.mod.render = lambda kind, code: MERMAID_SRC + code.encode() if kind == "mermaid" else png(kind, code)
        self.lock = asyncio.Lock()
        self.mark_examlike()
        sj = ROOT / "study" / f"{key}.json"
        self.study = json.loads(sj.read_text(encoding="utf-8")) if sj.exists() else {}
        self.cards: dict[str, dict] = {}                       # flashcards by key (plan.card_key), see /card
        for d in self.study.get("domains", []):
            for cd in d.get("cards") or []:
                cd["key"] = plan.card_key(cd["q"])
                self.cards[cd["key"]] = cd

    @staticmethod
    def _own_state(mod, key: str) -> None:
        """Public visitors are graded but nothing is recorded unless they opted in to the tracker (the owner, 2026-10-23): then they
        read and write their own file (TRACK_DIR/<pid>/<exam>.json); otherwise an empty state and dropped writes. The owner's
        state.json, schedule and vault never see them. The bot code is unchanged."""
        load, save = mod.load_state, mod.save_state

        def load_own():
            pid = VISITOR.get()
            if not pid:
                return load()
            try:
                return json.loads(track_path(pid, key).read_text(encoding="utf-8")) if tracked() else {"ok": {}, "bad": {}}
            except (OSError, ValueError):
                return {"ok": {}, "bad": {}}

        def save_own(state):
            pid = VISITOR.get()
            if not pid:
                return save(state)
            if tracked():
                tmp = track_path(pid, key).with_suffix(".tmp")
                tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
                tmp.replace(track_path(pid, key))
        mod.load_state, mod.save_state = load_own, save_own

    def mark_examlike(self) -> None:
        """Flag the free questions that read like the paid mocks (tools/examlike.py): a badge on every link. Runs at start and after a bank refresh."""
        el = ROOT / "study" / "examlike" / f"{self.key}.json"
        for i in json.loads(el.read_text(encoding="utf-8")) if el.exists() else []:
            if i in self.mod.QUESTIONS:
                self.mod.QUESTIONS[i]["examlike"] = True

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
        return {i: q for i, q in Q.items() if not hidden_source(q.get("source"))} if public else Q

    def pool(self, state: dict, public: bool = False) -> dict:
        """Questions of the current mode (hands-on exams); everything otherwise."""
        Q = self.questions(public)
        if not self.has_tasks:
            return Q
        want_tasks = self.mode(state) == "tasks"
        return {i: q for i, q in Q.items() if (not q["letters"]) == want_tasks}

    def pick(self, state: dict, domain: str | None = None, public: bool = False) -> dict:
        """Due reviews, then unseen from the weakest heavy domains, then missed, then any (plan.next_question), inside the
        current pool. With `domain` (Study → "Quiz me on this topic"): only that domain, any mode if the current one has none."""
        Q = self.pool(state, public) or self.questions(public)
        if domain is not None:
            Q = ({i: q for i, q in Q.items() if q["domain"] == domain}
                 or {i: q for i, q in self.questions(public).items() if q["domain"] == domain})
            if not Q:
                raise HTTPException(404, f"no questions in domain {domain}")
        now = datetime.now(SGT)
        m = self.mod.metrics(state, now)
        return plan.next_question(state, Q, getattr(self.mod, "WEIGHTS", {}), m["by_dom"], now.isoformat(timespec="minutes"))

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


SOURCE_TAG = {"Tutorials Dojo": "TD"}   # known question sources get a small label on the card; add "source": "tag" to label more


def tag_of(src: str | None) -> str:
    return SOURCE_TAG.get(src or "") or ("UD" if hidden_source(src) else "")   # paid mock sets: one small label on the card


def qview(q: dict) -> dict:
    return {"id": q["id"], "tag": tag_of(q.get("source")) or ("≈ exam" if q.get("examlike") else ""), "domain": q["domain"], "question": q["question"], "options": options_of(q),
            "multi": len(q["answer"]) > 1, "pick": len(q["answer"]), "task": not q["letters"]}   # pick: how many to select, as the real exam says


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

def record(exam: Exam, state: dict, qid: str, right: bool, extra: dict | None = None, guess: bool = False, paced: bool = False,
           board: bool = True) -> int:
    """Write ok/bad + log like bot.grade() does, and schedule the next review. Returns the minutes until it is due."""
    bucket = "ok" if right else "bad"
    state[bucket][qid] = state[bucket].get(qid, 0) + 1
    first = state["ok"].get(qid, 0) + state["bad"].get(qid, 0) == 1
    now = datetime.now(SGT)
    state.setdefault("log", []).append({"t": now.isoformat(timespec="minutes"), "id": qid, "ok": right, "first": first,
                                        **({"guess": True} if guess else {}), **(extra or {})})
    state["log"] = state["log"][-5000:]
    if board:                                             # a blank mock answer is graded wrong but is not an attempt on the board
        lb_count(exam.key, qid, right, paced)             # paced: a mock exam that took real time (checked once per mock)
    return plan.schedule(state, qid, right, now, guess)[1]


# ── Leaderboard: opt-in, public visitors only (the owner, 2026-10-23) ─────────────
# One small file per visitor who joined: {"name", "joined", "seen": {exam: {qid: [first_try_right 0/1, "YYYY-Www"]}},
# "mocks": {exam: [[score, questions, minutes, "YYYY-MM-DDTHH:MM"], ...]}} (the mock-exam board, same name). Nothing is kept
# for anyone who has not joined; leaving deletes the file. Only a question's first answer counts, so repeating one earns nothing.
LB_DIR = Path(os.environ.get("LEADERBOARD_DIR", ROOT / "data" / "leaderboard"))
NAME_BAD = re.compile(r"https?|www|\.\w{2,}|@", re.I)
BOARD_CACHE: dict = {"t": 0.0, "rows": []}
LB_MIN_GAP = 3.0   # seconds between two counted answers by the same person


OWNER_PID = hashlib.sha256(b"exam-portal-owner").hexdigest()[:32]            # the owner's id on the board (no cookie: any non-public link is the owner)
OWNER_BOARD = {x.strip() for x in os.environ.get("OWNER_BOARD_EXAMS", "ccarp").split(",") if x.strip()}   # exams the owner's answers count for


def lb_id(req: Request | None = None) -> str:
    return VISITOR.get() or OWNER_PID


def lb_file(pid: str) -> Path:
    return LB_DIR / f"{pid}.json"


def lb_load(pid: str) -> dict | None:
    try:
        return json.loads(lb_file(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def lb_save(pid: str, prof: dict) -> None:
    LB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = lb_file(pid).with_suffix(".tmp")
    tmp.write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
    tmp.replace(lb_file(pid))
    BOARD_CACHE["t"] = 0.0


def lb_count(exam_key: str, qid: str, right: bool, paced: bool = False) -> None:
    pid = lb_id()
    if pid == OWNER_PID and (exam_key not in OWNER_BOARD or hidden_source(EXAMS[exam_key].mod.QUESTIONS.get(qid, {}).get("source"))):
        return                                   # the owner counts for the public exams only, and never for paid questions visitors cannot see
    prof = lb_load(pid)
    if prof is None or qid in prof["seen"].setdefault(exam_key, {}):
        return
    if not paced and time.time() - prof.get("last", 0) < LB_MIN_GAP:
        return                                   # answers faster than anyone can read a scenario are graded but not counted (a script, not a person)
    prof["last"] = time.time()
    now = datetime.now(SGT)
    prof["seen"][exam_key][qid] = [int(right), f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"]
    lb_save(pid, prof)   # ponytail: rewrites the visitor's file per new question; fine at hundreds of questions, batch if it grows


MOCK_MIN = 20     # a mock exam needs this many counted questions (or the exam's full size, if smaller) to go on the board


def mock_entry(exam: Exam, credits: list[tuple[str, float]], minutes: float | None, when: str) -> list | None:
    """[score, questions, minutes, when] for the mock board from (qid, credit 0..1) pairs: scored like the result sheet but on
    free questions only (the owner's paid questions never count, the same rule as the practice board). None for a short mock."""
    Q = exam.mod.QUESTIONS
    free = [(qid, c) for qid, c in credits if qid in Q and not hidden_source(Q[qid].get("source"))]
    if len(free) < min(MOCK_MIN, exam.n_exam):
        return None
    by_dom: dict[str, list[float]] = {}
    for qid, c in free:
        d = by_dom.setdefault(Q[qid]["domain"], [0, 0]); d[1] += 1; d[0] += c
    return [weighted_score(by_dom, getattr(exam.mod, "WEIGHTS", {})), len(free), None if minutes is None else round(minutes), when]


def lb_mock(exam: Exam, results: list[dict], minutes: float) -> None:
    """A finished mock exam on the board, under the joined name."""
    pid = lb_id()
    if pid == OWNER_PID and exam.key not in OWNER_BOARD:
        return
    prof = lb_load(pid)
    entry = prof and mock_entry(exam, [(r["id"], r["score"] / 100 if r["task"] else r["right"]) for r in results], minutes,
                                datetime.now(SGT).isoformat(timespec="minutes"))
    if not entry:
        return
    mocks = prof.setdefault("mocks", {}).setdefault(exam.key, [])
    mocks.append(entry)
    del mocks[:-50]
    lb_save(pid, prof)


def lb_mock_start() -> None:
    """Server-side start time of a joined person's mock exam: its answers all arrive at once on submit, so the pacing check
    (LB_MIN_GAP) is made per mock instead of per answer."""
    prof = lb_load(lb_id())
    if prof is not None:
        lb_save(lb_id(), {**prof, "mock_start": time.time()})


def lb_paced(n: int) -> bool:
    """The mock took at least LB_MIN_GAP per answer since /exam/start. One use per start."""
    prof = lb_load(lb_id())
    if prof is None or "mock_start" not in prof:
        return False
    ok = time.time() - prof.pop("mock_start") >= LB_MIN_GAP * n
    lb_save(lb_id(), prof)
    return ok


def lb_history(pid: str, prof: dict) -> None:
    """Mocks from before the mock board (2026-10-26), from the person's own saved log since joining: the tracker file for a
    visitor, state.json for the owner. Runs once per profile (until it has a "mocks" key). The practice board is left alone:
    the log does not say which mock answers were blank, and a blank is not an attempt."""
    mocks, joined = {}, prof.get("joined", "")
    for k, e in EXAMS.items():
        if pid == OWNER_PID:
            if k not in OWNER_BOARD:
                continue
            log_ = e.mod.load_state().get("log", [])           # startup: no visitor set, so this is the owner's state.json
        else:
            try:
                log_ = json.loads(track_path(pid, k).read_text(encoding="utf-8")).get("log", [])
            except (OSError, ValueError):
                continue
        runs: dict[str, list] = {}
        for x in log_:
            if x.get("exam") and x.get("t", "") >= joined:
                runs.setdefault(x["t"], []).append(x)            # one submit = one minute stamp
        for t, xs in runs.items():
            entry = mock_entry(e, [(x["id"], x["score"] / 100 if "score" in x else int(x["ok"])) for x in xs], None, t)
            if entry:
                mocks.setdefault(k, []).append(entry)
    prof["mocks"] = mocks
    lb_save(pid, prof)


def lb_backfill_mocks() -> None:
    for f in LB_DIR.glob("*.json") if LB_DIR.exists() else []:
        prof = lb_load(f.stem)
        if prof is not None and "mocks" not in prof:
            try:
                lb_history(f.stem, prof)
                log.info("leaderboard: %s back-filled, %d mocks", prof.get("name"), sum(len(v) for v in prof["mocks"].values()))
            except Exception as exc:                            # best effort: never block startup
                log.warning("leaderboard back-fill failed for %s: %s", f.stem[:8], exc)


MOCK_READY = 800    # the bots' READY bar (verdict(): predicted score 800+), here on real mock scores


def mock_ready(exam: Exam, scores: list[int]) -> str:
    """Ready to book the exam, as of the last of these mocks (one exam, oldest first): two in a row at 800+ is ready."""
    p, last = exam.mod.PASS, scores[-1]
    if len(scores) >= 2 and min(scores[-2:]) >= MOCK_READY:
        return f"🟢 Ready to book: this and the mock before both {MOCK_READY}+"
    if last >= MOCK_READY:
        return f"🟡 Nearly: {MOCK_READY}+ once; one more to confirm"
    if last >= p:
        return f"🟡 Nearly: a pass, but aim for {MOCK_READY}+ twice in a row"
    return f"🔴 Not yet: {p - last} below the {p} pass mark"


def lb_mock_rows(exams: set[str]) -> list[dict]:
    """One row per mock attempt (not combined per person), best score first; readiness as of that attempt."""
    rows = []
    for r in lb_rows():
        for k in exams:
            ms = sorted(r["mocks"].get(k, []), key=lambda m: m[3])     # stable: same-minute mocks keep the order they were taken
            for i, m in enumerate(ms):
                rows.append({"pid": r["pid"], "name": r["name"], "exam": EXAMS[k].short, "score": m[0], "passed": m[0] >= EXAMS[k].mod.PASS,
                             "n": m[1], "when": m[3], "ready": mock_ready(EXAMS[k], [x[0] for x in ms[:i + 1]])})
    rows.sort(key=lambda r: (-r["score"], r["when"]))
    return rows


def lb_rows() -> list[dict]:
    """Every joined visitor, cached for 20 s (the board is read far more often than it changes)."""
    if time.monotonic() - BOARD_CACHE["t"] > 20:
        rows = []
        for f in LB_DIR.glob("*.json") if LB_DIR.exists() else []:
            try:
                prof = json.loads(f.read_text(encoding="utf-8"))
                rows.append({"pid": f.stem, "name": prof["name"], "seen": prof["seen"], "mocks": prof.get("mocks", {})})
            except (OSError, ValueError, KeyError):
                continue
        BOARD_CACHE.update(t=time.monotonic(), rows=rows)
    return BOARD_CACHE["rows"]


def lb_totals(seen: dict, exams: set[str], week: str | None) -> tuple[int, int]:
    got = [v[0] for k, d in seen.items() if k in exams for v in d.values() if week is None or v[1] == week]
    return len(got), sum(got)


def lb_topics(seen: dict, exams: set[str]) -> tuple[str | None, str | None]:
    """(strongest, weakest) topic by first-try accuracy, topics with at least 3 counted answers; None until there is a real gap."""
    by: dict[str, list[int]] = {}
    for k, d in seen.items():
        qs = EXAMS[k].mod.QUESTIONS if k in exams and k in EXAMS else {}
        for qid, v in d.items():
            if qid in qs:
                t = by.setdefault(qs[qid]["domain"], [0, 0]); t[0] += v[0]; t[1] += 1
    tried = sorted((r / n, n, d) for d, (r, n) in by.items() if n >= 3)
    if len(tried) < 2 or tried[0][0] == tried[-1][0]:
        return None, None
    return tried[-1][2], tried[0][2]


def when(mins: int) -> str:
    return f"{mins} min" if mins < 60 else f"{mins // 1440} day{'s' if mins >= 2880 else ''}"


# ── Actions (the bot's button vocabulary) ────────────────────────────────────

def hidden(exam: Exam, qid: str, public: bool) -> bool:
    return public and qid not in exam.questions(True)


def today() -> str:
    return datetime.now(SGT).strftime("%Y-%m-%d")


def report_cache(exam: Exam) -> Path | None:
    """A visitor who saves their progress gets the weekly coach on the public link, cached for the day (None for the owner)."""
    pid = VISITOR.get()
    return TRACK_DIR / pid / f"report-{exam.key}.json" if pid else None


def report_cached(exam: Exam) -> bool:
    p = report_cache(exam)
    try:
        return bool(p) and json.loads(p.read_text(encoding="utf-8")).get("day") == today()
    except (OSError, ValueError):
        return False


async def act(exam: Exam, data: str, rec: Recorder, public: bool = False) -> None:
    mod = exam.mod
    state = mod.load_state()
    if data == "next":
        q = exam.pick(state, public=public)
        if not q["letters"]:
            await send_task(exam, rec, state, q)
        else:
            await mod.send_question(rec, state, q)
            prefetch(exam, q, public)
    elif data == "stats":
        await rec.send_message(mod.stats_text(state))
    elif data == "study":
        if exam.has_study:
            await mod.send_study(rec, state)
        else:
            await rec.send_message("📚 <i>No study cards for this exam yet. Use the reading list.</i>", reply_markup=mod.NEXT_KB)
    elif data == "weak":                                  # quiz me on the weakest topic
        m = mod.metrics(state, datetime.now(SGT))
        seen = {q["domain"] for q in exam.questions(public).values()}
        tried = sorted((r / n, d) for d, (r, n) in m["by_dom"].items() if n >= 3 and d in seen)
        fresh = [d for d, (r, n) in m["by_dom"].items() if n < 3 and d in seen]
        if tried and tried[0][0] < 0.8:
            dom, why = tried[0][1], f"{round(100 * tried[0][0])}% right first time"
        elif fresh:
            dom, why = random.choice(fresh), "not enough answers yet"
        else:
            dom, why = (tried[0][1], f"{round(100 * tried[0][0])}% right first time") if tried else (None, "")
        if dom is None:
            raise HTTPException(404, "no questions available")
        await rec.send_message(f"🎯 <b>Weak topic</b> · {html.escape(dom)} ({why})")
        q = exam.pick(state, dom, public)
        if not q["letters"]:
            await send_task(exam, rec, state, q)
        else:
            await mod.send_question(rec, state, q)
            prefetch(exam, q, public)
    elif data == "report":
        m = mod.metrics(state, datetime.now(SGT))
        rec.emit({"op": "meta", "metrics": {**m, "pass": mod.PASS, "verdict": mod.verdict(m)}})
        cache = report_cache(exam)
        if report_cached(exam):
            for ev in json.loads(cache.read_text(encoding="utf-8"))["events"]:
                rec.emit(ev)                               # a visitor's coach: once a day, the same reply after that
            return
        events, emit = [], rec.emit
        rec.emit = lambda ev: (events.append(ev), emit(ev))
        try:
            await mod.weekly_report(None, rec)
        finally:
            rec.emit = emit
        if cache and any("COACH" in str(ev.get("html", "")) for ev in events):
            write_json(cache, {"day": today(), "events": events})
    elif data == "refresh":
        if exam.scrape is not None:
            sys.modules["scrape"] = exam.scrape               # the bot's refresh_bank does `import scrape` first thing, before any await
        if exam.parsers is not None:
            sys.modules["online_parsers"] = exam.parsers      # else the last-loaded exam's parsers (CKA) answer CCAR-P's import
        await mod.refresh_bank(None, rec)
        exam.mark_examlike()                                  # a refresh reloads the question dicts: put the badges back
    elif data == "reset":
        mod.save_state({"ok": {}, "bad": {}})
        mod.vault_log("🔄 **reset**", "progress cleared")
        await rec.send_message("🔄 Progress cleared · starting again from question 1.")
        await act(exam, "next", rec, public)                 # straight into the first question, no extra click
    elif data.startswith("d|"):                           # Study → "Quiz me on this topic"
        q = exam.pick(state, data[2:], public)
        if not q["letters"]:
            await send_task(exam, rec, state, q)
        else:
            await mod.send_question(rec, state, q)
            prefetch(exam, q, public)
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
                prefetch(exam, q, public)
        elif kind == "e":                                 # Explain more: the only place the portal draws a diagram
            await mod.explain(rec, q, explain_args(exam, data)[1])
        else:
            raise HTTPException(400, f"unknown action {data}")
    else:
        raise HTTPException(400, f"unknown action {data}")


async def answer(exam: Exam, qid: str, picked: list[str], rec: Recorder, public: bool = False, guess: bool = False) -> None:
    """Grade like bot.grade() (same state writes, same verdict text) without its wrong-answer tutor call: the portal waits
    for the "Explain more" button. Both outcomes go through record(), so the review schedule sees every answer."""
    q = exam.mod.QUESTIONS.get(qid)
    if not q or hidden(exam, qid, public):
        raise HTTPException(404, f"unknown question {qid}")
    letters = set(picked) & set(q["letters"])
    if not letters:
        raise HTTPException(400, "pick at least one option")
    mod, state = exam.mod, exam.mod.load_state()
    right = letters == q["answer"]
    state["last"] = {"id": q["id"], "picked": sorted(letters)}
    if state.get("pending") == q["id"]:                   # an older question answered from History: the one on screen stays pending
        state["pending"] = None
    mins = record(exam, state, q["id"], right, guess=guess)
    mod.save_state(state)
    rec.emit({"op": "meta", "graded": {"id": q["id"], "picked": sorted(letters), "correct": sorted(q["answer"]), "right": right,
                                       "guess": guess, "due_min": mins}})
    mod.vault_log(f"{mod.TITLE['ok' if right else 'bad']} **{'correct' if right else 'wrong'}**",
                  f"{q['id']} picked {','.join(sorted(letters))} answer {q['correct']}{' (guessed)' if guess else ''}")
    head = (f"{mod.TITLE['ok']} <b>CORRECT</b> · {html.escape(q['correct'])}" if right else
            f"{mod.TITLE['bad']} <b>WRONG</b> · you picked <code>{html.escape(','.join(sorted(letters)))}</code>, "
            f"answer <code>{html.escape(q['correct'])}</code>")
    if not public or tracked():                           # visitors are not scheduled, unless they save their progress
        head += f" · {'guessed, ' if guess and right else ''}back in {when(mins)}"
    await rec.send_message(f"{head}\n\n{html.escape(q['explanation'])}", reply_markup=mod.after_kb(q, letters))


# ── Question decoder: clue words, why each option is right or wrong; one Claude call per question, cached forever ──

DECODE = """You are a tutor for the {name} exam. The student has just answered a practice question. Using ONLY the question,
its options, the correct answer, the bank's explanation and the study material supplied, explain how to read the question:
"clues": 2 to 4 phrases copied verbatim from the question stem that decide the answer; "options": one entry per option
letter, "why" under 25 words of plain text: for a correct option why it is right, for every other option why it is wrong
(name the trap); "principle": one sentence to remember for the exam; "source": the study note's title and url exactly as
supplied, or empty strings if none was supplied. Never invent a source or a fact that is not in the material."""
# plain types only: `claude -p --json-schema` drops the schema silently on a ["object", "null"] union and answers free-form
DECODE_SCHEMA = json.dumps({"type": "object", "required": ["clues", "options", "principle", "source"], "additionalProperties": False,
                            "properties": {"clues": {"type": "array", "items": {"type": "string"}},
                                           "options": {"type": "array", "items": {"type": "object", "required": ["letter", "why"], "additionalProperties": False,
                                                                                  "properties": {"letter": {"type": "string"}, "why": {"type": "string"}}}},
                                           "principle": {"type": "string"},
                                           "source": {"type": "object", "required": ["title", "url"], "additionalProperties": False,
                                                      "properties": {"title": {"type": "string"}, "url": {"type": "string"}}}}})
DECODING: dict[str, asyncio.Task] = {}        # "<exam>/<qid>" -> running call
DECODE_FAILED: dict[str, float] = {}         # "<exam>/<qid>" -> time of a failed call: no retry for an hour, so the page's 2 s polls never re-run Claude
DECODE_RETRY_S = 3600
PUBLIC_DECODE_SLOTS = 4                       # decoder calls running at once before public visitors get none (each is a claude process on the NAS)


def decode_path(exam: Exam, qid: str) -> Path:
    return exam.mod.STATE_PATH.parent / "decode" / f"{qid}.json"


def note_for(exam: Exam, q: dict) -> tuple[dict | None, dict | None]:
    """The study domain (by name) and its note with the most stem words in its title + gist + points."""
    dom = next((d for d in exam.study.get("domains", []) if d["domain"] == q["domain"]), None)
    if not dom:
        return None, None
    words = set(re.findall(r"[a-z]{5,}", q["question"].lower()))
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
    shape = ('{"clues": ["<2-4 phrases copied verbatim from the question>"], "options": [{"letter": "A", "why": "<under 25 words: why right, or why wrong and the trap>"}, '
             '{"letter": "B", "why": "..."}, "...one entry per option letter"], "principle": "<one sentence to remember>", '
             '"source": {"title": "<the study note title, or empty>", "url": "<its url, or empty>"}}')
    prompt = (f"QUESTION {q['id']}:\n{q['question']}\n\nOPTIONS:\n{q['options']}\n\nCORRECT: {q['correct']}\n\n"
              f"BANK EXPLANATION:\n{q['explanation']}\n\n{material}Reply with ONLY one JSON object of exactly this shape, no code fence:\n{shape}")
    out = await claude_json(exam, DECODE.format(name=exam.name), prompt, DECODE_SCHEMA)
    src = out.get("source") if isinstance(out.get("source"), dict) else {}
    out = {"clues": [str(x) for x in out.get("clues", [])][:4],
           "options": [{"letter": str(o["letter"]).strip().upper()[:1], "why": str(o.get("why", ""))} for o in out.get("options", []) if isinstance(o, dict) and o.get("letter")],
           "principle": str(out.get("principle", "")).strip(), "source": src if src.get("url") else None}
    if not out["options"] and not out["principle"]:
        raise RuntimeError("decoder returned no content")        # schema ignored or a refusal: do not cache it
    if out["source"] and (not note or out["source"].get("url") != note["source"]["url"]):
        out["source"] = {"title": note["source"]["title"], "url": note["source"]["url"]} if note else None   # the note's link or nothing
    p = decode_path(exam, q["id"])
    p.parent.mkdir(exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return out


async def claude_json(exam: Exam, system: str, prompt: str, schema: str) -> dict:
    """One `claude -p` call that must answer one JSON object: the schema'd reply, else the object found in the text."""
    data = await exam.mod.run_claude(system, prompt, schema)
    out = data.get("structured_output")
    if not isinstance(out, dict):
        text = str(data.get("result") or "")
        out = json.loads(text[text.find("{"):text.rfind("}") + 1])
    return out


def write_json(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def safe_id(qid: str) -> str:
    return re.sub(r"[^\w.-]", "_", qid)


# ── Explain more: one tutor call per question and picked answer, cached like the decoder (the owner, 2026-10-26) ──

def explain_path(exam: Exam, qid: str, picked: set[str]) -> Path:
    return exam.mod.STATE_PATH.parent / "explain" / f"{safe_id(qid)}-{''.join(sorted(picked)) or 'none'}.json"


def explain_args(exam: Exam, data: str) -> tuple[dict | None, set[str]]:
    """The question and picked letters of an `e|<qid>|<letters>` action; letters outside the question are dropped."""
    _, qid, *rest = data.split("|")
    q = exam.mod.QUESTIONS.get(qid)
    return q, set(filter(None, (rest[0] if rest else "").split(","))) & set(q["letters"] if q else [])


def cached_tutor(exam: Exam):
    """Wrap the bot's ask_claude(q, picked) -> {teach, kind, code}: the first answer is kept, later ones are free."""
    ask = exam.mod.ask_claude

    async def ask_claude(q: dict, picked: set[str]) -> dict:
        p = explain_path(exam, q["id"], picked)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        out = await ask(q, picked)
        if isinstance(out, dict) and out.get("teach"):
            write_json(p, out)
        return out
    return ask_claude


# ── Mock-exam debrief: what went wrong, what to read, the next step; one call per finished mock ──

DEBRIEF = """You are a coach for the {name} exam (scored out of 1000, pass mark {pass_}). A student has just finished a mock
exam. Using ONLY the data supplied (their domain results, the stems of the questions they missed, the study note titles
per domain), write a short, frank debrief. "headline": one sentence under 25 words on where they stand. "focus": at most
3 entries, the domains that would gain them the most points first (weight x how far below 80% they are); "domain" copied
exactly from the data, "why" under 30 words naming the pattern in the missed questions, "notes" 1 to 3 study note titles
copied exactly from that domain's list (empty if the list is empty). "next": one concrete next step under 25 words.
Plain text only, no markdown. Never invent a domain, a note title or a fact."""
DEBRIEF_SCHEMA = json.dumps({"type": "object", "required": ["headline", "focus", "next"], "additionalProperties": False,
                             "properties": {"headline": {"type": "string"}, "next": {"type": "string"},
                                            "focus": {"type": "array", "items": {"type": "object", "required": ["domain", "why", "notes"], "additionalProperties": False,
                                                                                 "properties": {"domain": {"type": "string"}, "why": {"type": "string"},
                                                                                                "notes": {"type": "array", "items": {"type": "string"}}}}}}})


async def debrief(exam: Exam, domains: list[dict], missed: list[str], public: bool) -> dict:
    weights = getattr(exam.mod, "WEIGHTS", {}) or {}
    known = set(weights) | {q["domain"] for q in exam.mod.QUESTIONS.values()}
    doms = [d for d in domains if d.get("name") in known and isinstance(d.get("n"), (int, float)) and d["n"] > 0][:20]
    if not doms:
        raise HTTPException(400, "no domain results")
    Q = exam.questions(public)
    miss = [Q[i] for i in dict.fromkeys(missed) if i in Q][:40]
    titles = {d["domain"]: [n["title"] for n in d.get("notes", [])][:15] for d in exam.study.get("domains", [])}
    score = weighted_score({d["name"]: [float(d.get("right", 0)), d["n"]] for d in doms}, weights)
    lines = [f"SCORE {score} / 1000, pass {exam.mod.PASS}", "", "DOMAINS (name | weight % | right of n):"]
    lines += [f"- {d['name']} | {weights.get(d['name'], '-')} | {d.get('right', 0)} of {d['n']}" for d in doms]
    lines += ["", "MISSED QUESTIONS (domain: stem):"] + [f"- {q['domain']}: {q['question'][:220]}" for q in miss]
    lines += ["", "STUDY NOTE TITLES PER DOMAIN:"] + [f"- {d['name']}: " + " ; ".join(titles.get(d["name"], [])) for d in doms]
    shape = '{"headline": "...", "focus": [{"domain": "...", "why": "...", "notes": ["..."]}], "next": "..."}'
    out = await claude_json(exam, DEBRIEF.format(name=exam.name, pass_=exam.mod.PASS), "\n".join(lines) +
                            f"\n\nReply with ONLY one JSON object of exactly this shape, no code fence:\n{shape}", DEBRIEF_SCHEMA)
    names = {d["name"] for d in doms}
    focus = [{"domain": f["domain"], "why": str(f.get("why", "")),
              "notes": [t for t in f.get("notes", []) if t in titles.get(f["domain"], [])][:3]}
             for f in out.get("focus", []) if isinstance(f, dict) and f.get("domain") in names][:3]
    if not out.get("headline") and not focus:
        raise RuntimeError("empty debrief")
    return {"score": score, "headline": str(out.get("headline", "")), "focus": focus, "next": str(out.get("next", ""))}


# ── Flag a question: visitors report a doubtful key; Claude checks it once, the owner reviews it in the vault ──

CHECK = """You review exam practice questions for the {name} exam. A student flagged this question as possibly wrong.
Using the question, options, answer key and explanation (and your own knowledge of the subject), judge the KEY:
"verdict" is one of key_ok (the key is right), key_wrong (another option is right), ambiguous (two options defensible or
the stem is unclear), outdated (the facts changed). "answer": the option letter(s) you think are right, comma-separated.
"reason": under 60 words, plain text."""
CHECK_SCHEMA = json.dumps({"type": "object", "required": ["verdict", "answer", "reason"], "additionalProperties": False,
                           "properties": {"verdict": {"type": "string"}, "answer": {"type": "string"}, "reason": {"type": "string"}}})
CHECKING: dict[str, asyncio.Task] = {}


def flag_path(exam: Exam, qid: str) -> Path:
    return exam.mod.STATE_PATH.parent / "flags" / f"{safe_id(qid)}.json"


def review_note(exam: Exam, q: dict, rec: dict) -> None:
    """Reviews/<qid>.md in the exam's vault: the question, the key, Claude's check and every flag (best effort)."""
    if not exam.vault:
        return
    try:
        c = rec.get("check") or {}
        now = datetime.now(SGT)
        body = [f"---\ntags: [active]\nupdated: {now:%Y-%m-%d}\n---", f"# Flag: {q['id']}", "",
                f"**Domain:** {q['domain']} · **Key:** {q['correct']} · **Flags:** {len(rec['flags'])}", "",
                q["question"], "", q["options"], "",
                f"**Claude check:** {c.get('verdict', 'pending')} · thinks {c.get('answer', '-')}", "", c.get("reason", ""), "",
                "Accept: move the row to `data/removed_questions.csv` or fix the key. Reject: set `tags: [archived]`.", "", "## History"]
        body += [f"- {f['t']} 🚩 flagged{': ' + f['note'] if f.get('note') else ''}" for f in rec["flags"]]
        p = Path(exam.vault) / "Reviews" / f"{safe_id(q['id'])}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(body) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    except Exception as exc:
        log.warning("review note failed for %s: %s", q["id"], exc)


async def check_flag(exam: Exam, q: dict) -> None:
    p = flag_path(exam, q["id"])
    try:
        notes = "\n".join(f"- {f['note']}" for f in json.loads(p.read_text(encoding="utf-8"))["flags"] if f.get("note"))
        prompt = (f"QUESTION {q['id']}:\n{q['question']}\n\nOPTIONS:\n{q['options']}\n\nKEY: {q['correct']}\n\n"
                  f"EXPLANATION:\n{q['explanation']}\n\nSTUDENT NOTES:\n{notes or '(none)'}\n\nReply with ONLY one JSON object of exactly "
                  'this shape, no code fence:\n{"verdict": "key_ok|key_wrong|ambiguous|outdated", "answer": "B", "reason": "..."}')
        out = await claude_json(exam, CHECK.format(name=exam.name), prompt, CHECK_SCHEMA)
        rec = json.loads(p.read_text(encoding="utf-8"))           # re-read: more flags may have come in meanwhile
        rec["check"] = {"verdict": str(out.get("verdict", "")), "answer": str(out.get("answer", "")), "reason": str(out.get("reason", ""))[:600],
                        "t": datetime.now(SGT).isoformat(timespec="minutes")}
        write_json(p, rec)
        review_note(exam, q, rec)
        exam.vault_raw("🚩 **question checked**", f"{q['id']} · {rec['check']['verdict']}")
    except Exception as exc:
        log.warning("flag check failed for %s: %s", q["id"], exc)
    finally:
        CHECKING.pop(f"{exam.key}/{q['id']}", None)


def prefetch(exam: Exam, q: dict, public: bool) -> asyncio.Task | None:
    """Start the decoder for an MCQ unless cached or running; on the public link only within the Claude budget. The task outlives the request."""
    key = f"{exam.key}/{q['id']}"
    if not q["letters"] or decode_path(exam, q["id"]).exists() or time.time() - DECODE_FAILED.get(key, 0) < DECODE_RETRY_S:
        return DECODING.get(key)
    if key not in DECODING or DECODING[key].done():
        if public and (len(DECODING) >= PUBLIC_DECODE_SLOTS or not claude_ok()):
            return None                                       # busy or over the day's budget: no decoder this time
        async def run():
            try:
                await decode(exam, q)
            except Exception as exc:
                DECODE_FAILED[key] = time.time()
                log.warning("decoder failed for %s: %s", key, exc)
            finally:
                DECODING.pop(key, None)
        DECODING[key] = asyncio.create_task(run())
    return DECODING[key]


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


async def check_task(exam: Exam, qid: str, answer: str, rec: Recorder, guess: bool = False) -> None:
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
    record(exam, state, qid, right, {"score": out["score"]}, guess)
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


def weighted_score(by_dom: dict, weights: dict) -> int:
    """The 1000-scale score of a result sheet: domain accuracy weighted by the exam's domain weights."""
    w = {d: weights.get(d, 1) for d in by_dom}
    return round(sum(w[d] * (r / n) for d, (r, n) in by_dom.items()) / sum(w.values()) * 1000) if by_dom else 0


async def exam_submit(exam: Exam, answers: dict[str, list[str]], minutes_used: float, public: bool = False) -> dict:
    """Grade every answered question, write ok/bad/log like grade() does, return the result sheet.
    MCQ exams need no Claude; task exams grade each typed answer with Claude (blank answers score 0 without a call)."""
    mod, Q = exam.mod, exam.mod.QUESTIONS
    state = mod.load_state()
    results, by_dom = [], {}
    paced = lb_paced(len(answers))
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
                    g = {"score": 0, "verdict": "grader unavailable", "feedback": str(exc)[:200], "commands": "", "failed": True}
            else:
                g = {"score": 0, "verdict": "blank", "feedback": "No answer given.", "commands": ""}
            right = g["score"] >= PASS_TASK
            if not g.get("failed"):                           # a Claude outage is not a failed attempt: progress and schedule stay as they were
                record(exam, state, qid, right, {"exam": True, "score": g["score"]}, paced=paced, board=bool(text))
            d = by_dom.setdefault(q["domain"], [0, 0]); d[1] += 1; d[0] += g["score"] / 100
            results.append({"id": qid, "domain": q["domain"], "question": q["question"], "options": [], "multi": False, "task": True,
                            "answer": text, "score": g["score"], "verdict": g["verdict"], "feedback": g["feedback"],
                            "commands": g["commands"], "right": right, "explanation": q["explanation"], "picked": [], "correct": []})
        else:
            letters = set(picked) & set(q["letters"])
            right = letters == q["answer"]
            record(exam, state, qid, right, {"exam": True}, paced=paced, board=bool(letters))
            d = by_dom.setdefault(q["domain"], [0, 0]); d[1] += 1; d[0] += right
            results.append({"id": qid, "domain": q["domain"], "question": q["question"], "options": options_of(q),
                            "multi": len(q["answer"]) > 1, "task": False, "picked": sorted(letters), "correct": sorted(q["answer"]),
                            "right": right, "explanation": q["explanation"]})
    mod.save_state(state)
    n_right = sum(r["right"] for r in results)
    score = weighted_score(by_dom, weights)
    mod.vault_log("🎯 **mock exam**", f"{n_right}/{len(results)} right, score {score}, {round(minutes_used)} min")
    lb_mock(exam, results, minutes_used)
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
    lb_backfill_mocks()
    task = asyncio.create_task(check_videos())
    yield
    task.cancel()


app = FastAPI(title="Exam Portal", docs_url=None, redoc_url=None, lifespan=lifespan)


def exam_or_404(key: str) -> Exam:
    """The exam, if it is loaded and this link shows it (the middleware sets AUDIENCE per request)."""
    if key not in EXAMS or not visible(key, AUDIENCE.get()):
        raise HTTPException(404, "unknown exam")
    return EXAMS[key]


def owner_key(req: Request) -> str:
    """The key the request carries (query on first visit, cookie after), if it is the right one."""
    k = req.query_params.get("td") or req.cookies.get("td", "")
    return k if OWNER_KEY and secrets.compare_digest(k.encode(), OWNER_KEY.encode()) else ""


def is_public(req: Request) -> bool:
    return funnel(req) and not owner_key(req)


def funnel(req: Request) -> bool:
    """Public = came in through Tailscale Funnel (it adds a marker header), or asked for the public ts.net name.
    The second rule covers your own tailnet devices that open the public URL: Tailscale connects them directly, so
    there is no Funnel header, but the Host is still the public name. Your private URL is the NAS tailnet IP."""
    h = req.headers
    return "tailscale-funnel-request" in h or any(".ts.net" in h.get(k, "").lower() for k in ("host", "x-forwarded-host"))


async def deny(rec: Recorder) -> None:
    raise HTTPException(403, "Admin actions are off on the public link. Use the home network or Tailscale.")


async def over_budget(rec: Recorder) -> None:
    raise HTTPException(429, "Claude explanations are used up for today on this site. They come back tomorrow.")


def claude_ok() -> bool:
    """A public visitor may start one more Claude call: daily caps per visitor and overall (every call is billed to the owner's key).
    Counts the call. Cached decoder results are free and never come here."""
    pid = VISITOR.get() or "anon"
    return not too_many(pid, "claude") and not too_many("x", "claude-all")


class Act(BaseModel):
    data: str


class Answer(BaseModel):
    id: str
    picked: list[str]
    guess: bool = False     # "🤔 Guessing" was on: a right answer is still scheduled like a miss


class TaskAnswer(BaseModel):
    id: str
    answer: str = ""
    result: str = ""        # self-mark: reveal | ok | bad
    guess: bool = False


class PlanBody(BaseModel):
    exam_date: str | None = None
    pace: int | None = None


class Card(BaseModel):
    key: str
    ok: bool


class Mode(BaseModel):
    mode: str        # auto | mcq | tasks


class ExamStart(BaseModel):
    n: int | None = None


class ExamSubmit(BaseModel):
    answers: dict[str, list[str]]
    minutes_used: float = 0


@app.get("/api/access")
def api_access(req: Request):
    """public: no admin actions, Claude within a daily budget, nothing recorded (owner key lifts it); shared: came in through the public name, so the
    page shows no personal numbers to anyone (the site is shared by many people)."""
    return {"public": is_public(req), "shared": is_public(req) and not tracked(), "track": tracked()}


@app.get("/api/exams")
def api_exams(req: Request):
    out, pub = [], is_public(req)
    for e in EXAMS.values():
        if not visible(e.key, audience(req)):
            continue
        c = e.coverage(e.mod.load_state(), pub)
        st = e.mod.load_state()
        out.append({"key": e.key, "short": e.short, "name": e.name, "has_study": e.has_study, "tasks": e.has_tasks,
                    "mode": e.mode(st), "progress": e.progress(st) if e.has_tasks else None,
                    "total": c["total"], "seen": c["seen"], "right": c["right"], "pass": e.mod.PASS,
                    "exam_n": min(exam_size(e, st), len(e.pool(st, pub)) if e.has_tasks else c["total"]), "exam_minutes": e.minutes,
                    "board": visible(e.key, "public")})
    return out


def plan_of(e: Exam, state: dict, public: bool) -> dict:
    m = e.mod.metrics(state, datetime.now(SGT))
    return plan.plan(state, e.pool(state, public) or e.questions(public), getattr(e.mod, "WEIGHTS", {}), m["by_dom"],
                     datetime.now(SGT), 0 if public and not tracked() else m["score"], e.mod.PASS)


@app.get("/api/{key}/home")
async def api_home(key: str, req: Request):
    e = exam_or_404(key)
    pub = is_public(req)
    state = e.mod.load_state()
    m = e.mod.metrics(state, datetime.now(SGT))
    pending = None
    if state.get("pending") in e.questions(pub):           # a question asked (web or Telegram) and not yet answered
        q = e.mod.QUESTIONS[state["pending"]]
        pending = {"html": task_html(q) if e.tasks else e.mod.question_text(q), "question": qview(q)}
        if q["letters"]:
            prefetch(e, q, pub)                            # asked in Telegram or before a restart: decode it now
    last = state.get("last") or {}
    nonum = pub and not tracked()                          # the owner's numbers are not for the public link; a visitor who saves their progress sees their own
    if nonum:
        m = {**m, "score": 0, "week_n": 0, "week_ok": 0}
    return {"key": e.key, "short": e.short, "name": e.name, "has_study": e.has_study, "tasks": e.has_tasks,
            "mode": e.mode(state), "progress": e.progress(state) if e.has_tasks else None,
            "coverage": e.coverage(state, pub), "score": m["score"], "pass": e.mod.PASS, "verdict": "" if nonum else e.mod.verdict(m),
            "week": {"n": m["week_n"], "ok": m["week_ok"]}, "pending": pending, "plan": plan_of(e, state, pub),
            "last": {"id": last.get("id"), "has_teach": bool(last.get("teach"))} if last else None,
            "study_cards": len(getattr(e.mod, "NOTES", {}) or {}),
            "studied": len(set(state.get("studied", []))),
            "exam_n": min(exam_size(e, state), len(e.pool(state, pub)) if e.has_tasks else len(e.questions(pub))), "exam_minutes": e.minutes,
            "exam_answers": sum(1 for x in state.get("log", []) if x.get("exam"))}


@app.get("/api/{key}/question/{qid}")
def api_question(key: str, qid: str, req: Request):
    """One question with its answer and explanation (the Previous-question viewer); hidden sources stay hidden on the public link."""
    e = exam_or_404(key)
    q = e.questions(is_public(req)).get(qid)
    if not q:
        raise HTTPException(404, "unknown question")
    return {**qview(q), "correct": sorted(q["answer"]), "explanation": q["explanation"]}


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


SUMMARY_TARGET = 0.8      # "lift it to" accuracy: the bot's READY bar (verdict: score 800 and 80% first-try)


def summary(e: Exam, state: dict) -> dict:
    """Strengths, weaknesses and the shortest way to the pass mark, from the same first-try numbers as the predicted score
    (bot metrics: score = sum(weight x domain accuracy) / sum(weights) x 1000, untried domains count 0). A step is worth
    weight / total x (target - accuracy) x 1000 points; the biggest steps are listed until they close the gap."""
    m = e.mod.metrics(state, datetime.now(SGT))
    weights = getattr(e.mod, "WEIGHTS", None) or {d: 1 for d in m["by_dom"]}
    total = sum(weights.values())
    acc = {d: (r / n if n else None, n) for d, (r, n) in m["by_dom"].items()}
    tried = sorted(((a, d) for d, (a, n) in acc.items() if n >= 3), reverse=True)
    strong = [{"domain": d, "acc": round(a, 2)} for a, d in tried if a >= 0.75][:2]
    weak = [{"domain": d, "acc": round(a, 2)} for a, d in reversed(tried) if a < 0.72][:2]
    gap, steps, need = max(0, e.mod.PASS - m["score"]), [], e.mod.PASS - m["score"]
    for pts, d in sorted(((weights.get(d, 1) / total * (SUMMARY_TARGET - (a or 0)) * 1000, d) for d, (a, n) in acc.items()), reverse=True):
        if need <= 0 or pts <= 0:
            break
        a, n = acc[d]
        steps.append({"domain": d, "weight": round(100 * weights.get(d, 1) / total), "acc": None if a is None else round(a, 2), "seen": n, "target": SUMMARY_TARGET, "points": round(pts)})
        need -= pts
    return {"exam": e.short, "score": m["score"], "pass": e.mod.PASS, "gap": gap, "answered": m["first_n"], "strong": strong, "weak": weak, "steps": steps}


@app.get("/api/{key}/summary")
def api_summary(key: str, req: Request):
    e = exam_or_404(key)
    if is_public(req) and not tracked():
        raise HTTPException(404, "no personal numbers on the shared link")   # nothing is kept for them, so there is nothing to sum up
    return summary(e, e.mod.load_state())


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


@app.post("/api/{key}/card")
def api_card(key: str, body: Card):
    """Flashcard review: Again (ok=false) or Got it (ok=true), scheduled on the same ladder as questions."""
    e = exam_or_404(key)
    if body.key not in e.cards:
        raise HTTPException(404, "unknown card")
    state = e.mod.load_state()
    due, mins = plan.schedule(state, body.key, body.ok, datetime.now(SGT))
    e.mod.save_state(state)
    return {"due": due, "due_min": mins}


@app.get("/api/{key}/decode/{qid}")
async def api_decode(key: str, qid: str, req: Request, cached: int = 0):
    """The decoder JSON for a question: 200 with it, 202 while Claude is working, 404 when there is none (public link,
    `cached=1` for review cards, a task, or a failed call). The owner's request starts a missing one."""
    e = exam_or_404(key)
    pub = is_public(req)
    q = e.questions(pub).get(qid)
    if not q:
        raise HTTPException(404, "unknown question")
    p = decode_path(e, qid)
    if p.exists():
        return JSONResponse(json.loads(p.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})
    if cached or not q["letters"] or time.time() - DECODE_FAILED.get(f"{e.key}/{qid}", 0) < DECODE_RETRY_S:
        raise HTTPException(404, "not decoded")                  # a failed call is not retried by every poll
    if prefetch(e, q, pub) is None:
        raise HTTPException(404, "not decoded")                  # public: busy or over the day's Claude budget
    return JSONResponse({"pending": True}, status_code=202, headers={"Cache-Control": "no-store"})


@app.post("/api/{key}/act")
async def api_act(key: str, body: Act, req: Request):
    e = exam_or_404(key)
    pub = is_public(req)
    if pub and (body.data == "refresh" or (body.data == "report" and not tracked())):   # the shared bank refresh; the report needs saved progress
        return stream(e, deny)
    if pub and body.data == "report" and not report_cached(e) and not claude_ok():   # the coach: once a day per visitor, within the budget
        return stream(e, over_budget)
    if pub and body.data.startswith("e|"):                     # Explain more: free when cached, else within the budget
        q, picked = explain_args(e, body.data)
        if not (q and explain_path(e, q["id"], picked).exists()) and not claude_ok():
            return stream(e, over_budget)
    return stream(e, functools.partial(act, e, body.data, public=is_public(req)))


@app.post("/api/{key}/answer")
async def api_answer(key: str, body: Answer, req: Request):
    e = exam_or_404(key)
    return stream(e, functools.partial(answer, e, body.id, body.picked, public=is_public(req), guess=body.guess))


@app.post("/api/{key}/task")
async def api_task(key: str, body: TaskAnswer):
    e = exam_or_404(key)
    if not e.has_tasks:
        raise HTTPException(400, "not a task exam")
    if body.result:
        return stream(e, functools.partial(self_mark, e, body.id, body.result))
    return stream(e, functools.partial(check_task, e, body.id, body.answer, guess=body.guess))


class Debrief(BaseModel):
    domains: list[dict]
    missed: list[str] = []


@app.post("/api/{key}/exam/debrief")
async def api_exam_debrief(key: str, body: Debrief, req: Request):
    """The coach's read of a finished mock (the page sends its own result sheet: domain results and missed ids)."""
    e = exam_or_404(key)
    pub = is_public(req)
    if pub and not claude_ok():
        raise HTTPException(429, "Claude is used up for today on this site. It comes back tomorrow.")
    try:
        return await debrief(e, body.domains, body.missed, pub)
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("debrief failed for %s: %s", key, exc)
        raise HTTPException(503, "The coach is not available right now.")


class Flag(BaseModel):
    id: str
    note: str = ""


@app.post("/api/{key}/flag")
async def api_flag(key: str, body: Flag, req: Request):
    """Flag a doubtful question: one flag per question per person; the first flag starts Claude's check of the key."""
    e = exam_or_404(key)
    pub = is_public(req)
    q = e.questions(pub).get(body.id)
    if not q:
        raise HTTPException(404, "unknown question")
    who = hashlib.sha256(lb_id().encode()).hexdigest()[:12]      # not the cookie itself
    p = flag_path(e, q["id"])
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        rec = {"id": q["id"], "flags": [], "check": None}
    if any(f["who"] == who for f in rec["flags"]):
        return {"flagged": True, "again": True}
    note = re.sub(r"\s+", " ", body.note).strip()[:300]
    rec["flags"].append({"who": who, "note": note, "t": datetime.now(SGT).isoformat(timespec="minutes")})
    write_json(p, rec)
    e.vault_raw("🚩 **question flagged**", f"{q['id']} · {len(rec['flags'])} flag{'s' if len(rec['flags']) > 1 else ''}")
    review_note(e, q, rec)
    k = f"{e.key}/{q['id']}"
    if not rec["check"] and k not in CHECKING and (not pub or claude_ok()):
        CHECKING[k] = asyncio.create_task(check_flag(e, q))
    return {"flagged": True, "again": False}


def exam_size(e: Exam, state: dict) -> int:
    """Questions in a full mock exam in the current mode: the MCQ phase of a hands-on exam is 60 in the real 120 min."""
    return e.n_exam if not e.has_tasks or e.mode(state) == "tasks" else 60


@app.post("/api/{key}/exam/start")
def api_exam_start(key: str, body: ExamStart, req: Request):
    e = exam_or_404(key)
    pub = is_public(req)
    st = e.mod.load_state()
    lb_mock_start()
    pool = e.pool(st, pub) if e.has_tasks else e.questions(pub)
    mode_tasks = e.has_tasks and e.mode(st) == "tasks"
    n_exam = exam_size(e, st)
    n = max(1, min(body.n or n_exam, len(pool)))
    minutes = e.minutes if n >= n_exam else max(5, round(e.minutes * n / n_exam))   # same pace as the real exam
    return {"questions": exam_pick(e, n, pub), "minutes": minutes, "n": n, "pass": e.mod.PASS, "tasks": mode_tasks}


@app.post("/api/{key}/exam/submit")
async def api_exam_submit(key: str, body: ExamSubmit, req: Request):
    e = exam_or_404(key)
    async with e.lock:
        return await exam_submit(e, body.answers, body.minutes_used, is_public(req))


@app.post("/api/track/on")
def api_track_on(req: Request):
    pid = VISITOR.get()
    if not pid:
        raise HTTPException(403, "Your own links already keep your progress.")
    (TRACK_DIR / pid).mkdir(parents=True, exist_ok=True)
    (TRACK_DIR / pid / "on").write_text(datetime.now(SGT).isoformat(timespec="minutes"), encoding="utf-8")
    return {"track": True}


@app.post("/api/track/off")
def api_track_off(req: Request):
    pid = VISITOR.get()
    if not pid:
        raise HTTPException(403, "Your own links already keep your progress.")
    shutil.rmtree(TRACK_DIR / pid, ignore_errors=True)       # every saved answer is deleted
    return {"track": False}


class Join(BaseModel):
    name: str


def lb_backfill(exam_key: str) -> dict:
    """The owner's existing first-try results for one exam, from the real state.json (free questions only). Entries without a timestamp
    go under week "0000-W00": they count for all time, never for a week."""
    mod = EXAMS[exam_key].mod
    st = mod.load_state()
    seen = {}
    for e in st.get("log", []):
        if e.get("first") and e["id"] in mod.QUESTIONS:
            seen[e["id"]] = [int(e["ok"]), (lambda d: f"{d.isocalendar().year}-W{d.isocalendar().week:02d}")(datetime.fromisoformat(e["t"]))]
    for qid in set(st["ok"]) | set(st["bad"]):
        if qid in mod.QUESTIONS and qid not in seen:
            seen[qid] = [int(st["ok"].get(qid, 0) >= 1 and st["bad"].get(qid, 0) == 0), "0000-W00"]
    return {k: v for k, v in seen.items() if not hidden_source(mod.QUESTIONS[k].get("source"))}


@app.post("/api/board/join")
def api_board_join(body: Join, req: Request):
    pid = lb_id(req)
    name = re.sub(r"\s+", " ", body.name).strip()
    if not 2 <= len(name) <= 24 or not re.fullmatch(r"[\w .'\-]+", name) or NAME_BAD.search(name):
        raise HTTPException(400, "Use 2 to 24 letters, numbers, spaces, - . ' _ (no links or @).")
    BOARD_CACHE["t"] = 0.0
    if any(r["pid"] != pid and r["name"].lower() == name.lower() for r in lb_rows()):
        raise HTTPException(409, "That name is already on the board. Pick another.")
    prof = lb_load(pid)
    if prof is None:
        prof = {"joined": datetime.now(SGT).isoformat(timespec="minutes"), "seen": {}}
        if pid == OWNER_PID:                          # the owner's existing history counts (the owner, 2026-10-23); visitors start from zero
            prof["seen"] = {k: lb_backfill(k) for k in OWNER_BOARD if k in EXAMS}
    lb_save(pid, {**prof, "name": name})
    return {"joined": True, "name": name}


@app.post("/api/board/leave")
def api_board_leave(req: Request):
    pid = lb_id(req)
    lb_file(pid).unlink(missing_ok=True)       # their name and every count are deleted
    BOARD_CACHE["t"] = 0.0
    return {"joined": False}


@app.get("/api/board/mock")
def api_board_mock(req: Request, exam: str = "all"):
    """The mock-exam board: a separate ranking (best score on the 1000 scale) under the same joined names."""
    return api_board(req, exam, "mock")


@app.get("/api/board")
def api_board(req: Request, exam: str = "all", period: str = "week"):
    shown = {e.key for e in EXAMS.values() if visible(e.key, "public")}     # one board, the public exams, for every link
    if exam != "all" and exam not in shown:
        raise HTTPException(404, "unknown exam")
    exams = shown if exam == "all" else {exam}
    now = datetime.now(SGT)
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}" if period == "week" else None
    if period == "mock":                          # mock exams: best score on the 1000 scale
        rows = lb_mock_rows(exams)
    else:
        rows = []
        for r in lb_rows():
            done, right = lb_totals(r["seen"], exams, week)
            if done:
                strong, weak = lb_topics(r["seen"], exams)
                rows.append({"pid": r["pid"], "name": r["name"], "done": done, "right": right, "wrong": done - right, "acc": round(100 * right / done), "strong": strong, "weak": weak})
        rows.sort(key=lambda r: (-r["right"], -r["acc"], r["done"], r["name"].lower()))
    pid, mine = lb_id(req), None
    for i, r in enumerate(rows, 1):
        r["rank"], r["me"] = i, r["pid"] == pid
        mine = mine or (r if r["me"] else None)
        del r["pid"]
    prof = lb_load(pid)
    return {"rows": rows[:50], "me": {"visitor": True, "owner": pid == OWNER_PID, "joined": prof is not None, "name": prof and prof["name"], "rank": mine and mine["rank"]},
            "total": len(rows), "week": week, "exams": [{"key": e.key, "short": e.short} for e in EXAMS.values() if e.key in shown]}


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


HITS: dict[str, deque] = defaultdict(deque)
PID_RX = re.compile(r"[0-9a-f]{32}")
RATE = {"visitor": (90, 60), "all": (900, 60),     # public requests allowed per (count, seconds), per visitor and overall
        "claude": (int(os.environ.get("PUBLIC_CLAUDE_VISITOR_DAY", 30)), 86400),   # public Claude calls (decoder, Explain more) per visitor a day
        "claude-all": (int(os.environ.get("PUBLIC_CLAUDE_DAY", 300)), 86400)}       # and for everyone together


def too_many(key: str, name: str) -> bool:
    n, secs = RATE[name]
    q, now = HITS[f"{name}:{key}"], time.monotonic()
    while q and now - q[0] > secs:
        q.popleft()
    q.append(now)
    if len(HITS) > 5000:                      # forget idle visitors
        for k in [k for k, v in HITS.items() if not v or now - v[-1] > RATE[k.split(":", 1)[0]][1]]:   # idle longer than its own window
            HITS.pop(k, None)
    return len(q) > n


@app.middleware("http")
async def visitors(req: Request, call_next):
    """Public requests: a cookie for the per-visitor rate limit (nothing else is kept). Static files always revalidate after a deploy."""
    pub = is_public(req) and not req.url.path.startswith("/static/")
    AUDIENCE.set(audience(req))
    pid, new = req.cookies.get("pid", ""), False
    if pub:
        if not PID_RX.fullmatch(pid):
            pid, new = secrets.token_hex(16), True
        if too_many(pid, "visitor") or too_many("x", "all"):
            return JSONResponse({"detail": "Too many requests, slow down."}, status_code=429, headers={"Retry-After": "30"})
        VISITOR.set(pid)
    resp = await call_next(req)
    if req.query_params.get("td") and owner_key(req):
        resp.set_cookie("td", owner_key(req), max_age=90 * 86400, httponly=True, samesite="lax", secure=True)
    if new:
        resp.set_cookie("pid", pid, max_age=90 * 86400, httponly=True, samesite="lax", secure=True)
    if req.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/favicon.ico")
def favicon():
    return FileResponse(ROOT / "static" / "favicon.svg", media_type="image/svg+xml")


@app.get("/")
def index(req: Request):
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    if is_public(req):                         # set server-side too, so the public view never depends on cached JS
        page = page.replace("<html", '<html data-public="1"', 1)
    if is_public(req) and not tracked():
        page = page.replace("<html", '<html data-shared="1"', 1)      # no personal numbers unless the visitor saves their progress
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), log_level="info")
