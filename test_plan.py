"""python test_plan.py — the scheduler and plan maths, no server."""
import random
from datetime import datetime, timedelta, timezone

import plan

SGT = timezone(timedelta(hours=8))
now = datetime(2026, 10, 22, 21, 0, tzinfo=SGT)
iso = lambda dt: dt.isoformat(timespec="minutes")  # noqa: E731

# ladder: a miss comes back in 10 min, each right climbs one rung, a guess restarts, sure-first lands on 21 days
st = {"ok": {}, "bad": {}}
due, mins = plan.schedule(st, "Q1", right=False, now=now)
assert mins == 10 and st["srs"]["Q1"] == [iso(now + timedelta(minutes=10)), 0], st
assert plan.schedule(st, "Q1", True, now)[1] == 1440
assert plan.schedule(st, "Q1", True, now)[1] == 3 * 1440
assert plan.schedule(st, "Q1", True, now, guess=True)[1] == 10              # right but guessed = start over
assert plan.schedule(st, "Q2", True, now)[1] == 21 * 1440                 # first sight, right and sure
for _ in range(5):
    plan.schedule(st, "Q2", True, now)
assert st["srs"]["Q2"][1] == len(plan.LADDER_MIN) - 1                     # capped at the top rung (60 d)
st["srs"]["BAD"] = "garbage"
assert plan.schedule(st, "BAD", True, now)[1] == 21 * 1440                 # malformed entry = fresh

# due: overdue first; Telegram-only misses (no srs entry) count as due
st = {"ok": {"T1": 1}, "bad": {"T1": 2, "T2": 1},
      "srs": {"A": [iso(now - timedelta(days=2)), 1], "B": [iso(now - timedelta(hours=1)), 0],
              "C": [iso(now + timedelta(hours=1)), 0], "c:ab12cd34": [iso(now - timedelta(days=1)), 0]}}
assert plan.due_keys(st, iso(now)) == ["A", "B", "T1", "T2"], plan.due_keys(st, iso(now))   # T1, T2: missed more than right
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
assert p["days_left"] == 14 and p["due"] == 1 and p["cards"]["due"] == ["c:1"] and p["new_left"] == 98, p
assert p["today"] == {"target": 16, "done": 1, "pace": 15} and p["on_track"] is False and p["gap"] == 108
assert [f["domain"] for f in p["focus"]] == ["Heavy", "Light"] and p["focus"][0]["unseen"] == 48
assert plan.plan({"ok": {}, "bad": {}}, Q, W, by_dom, now, 0, 720)["days_left"] is None
assert plan.card_key("What is X?") == plan.card_key("What is X?") and plan.card_key("a").startswith("c:") and len(plan.card_key("a")) == 10
print("ok: plan tests passed")
