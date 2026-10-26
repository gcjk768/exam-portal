"""Decode an exam's free questions ahead of time, so the decoder is instant and costs nothing on the public link.

Each question is one `claude -p` call through the portal's own runner (web.decode: same prompt, same cache in the bot's
data/decode/<qid>.json); questions already decoded are skipped, so it can be stopped and run again. One call at a time,
a few seconds apart. Run it inside the portal container, where the bots, the env and the claude CLI are:

    docker exec exam-portal python tools/predecode.py ccarp --limit 50
"""
import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("exam")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many new decodes (0 = all)")
    ap.add_argument("--gap", type=float, default=3.0, help="seconds between calls")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.EXAMS.update(web.load_exams())
    e = web.EXAMS[a.exam]
    todo = [q for q in e.questions(True).values() if q["letters"] and not web.decode_path(e, q["id"]).exists()]
    todo = todo[:a.limit] if a.limit else todo
    print(f"{a.exam}: {len(todo)} to decode")
    done = failed = 0
    for i, q in enumerate(todo, 1):
        try:
            await web.decode(e, q)
            done += 1
        except Exception as exc:
            failed += 1
            print(f"  {q['id']}: {exc}")
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} · {done} decoded · {failed} failed")
        time.sleep(a.gap)
    print(f"done: {done} decoded, {failed} failed")


if __name__ == "__main__":
    asyncio.run(main())
