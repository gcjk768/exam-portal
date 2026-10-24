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
    recent = [e.get("id") for e in state.get("log", [])[-3:]]             # mix it up: no straight repeat, fewer same-topic runs
    due = [k for k in due_keys(state, now_iso) if k in Q]
    if due:
        return Q[next((k for k in due if k not in recent[-1:]), due[0])]
    seen = set(state["ok"]) | set(state["bad"])
    unseen = [i for i in Q if i not in seen]
    if unseen:
        pri = domain_priority(weights, by_dom, Counter(Q[i]["domain"] for i in unseen))
        run = Counter(Q[i]["domain"] for i in recent[-2:] if i in Q)
        pri = {d: p for d, p in pri.items() if run[d] < 2} or pri            # two in a row from one domain: switch topic
        d = rng.choices(list(pri), weights=list(pri.values()))[0]
        return Q[rng.choice([i for i in unseen if Q[i]["domain"] == d])]
    missed = [i for i in Q if state["bad"].get(i, 0) > state["ok"].get(i, 0)]
    pool = [i for i in (missed or list(Q)) if i not in recent[-1:]] or missed or list(Q)
    return Q[rng.choice(pool)]


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
