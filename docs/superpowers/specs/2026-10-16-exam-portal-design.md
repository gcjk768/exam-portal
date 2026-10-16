# Exam Portal design (2026-10-16)

One website for the CCAR-P and AWS SAP-C03 quiz bots with every Telegram function, plus a Study door.

## Goals
- Same functions as the Telegram bots: quiz (single and multi-select), verdict + bank explanation, Claude tutor diagram on a wrong answer, Explain more, stats, weekly report with coach note, study cards, refresh, reset.
- Same progress: the portal reads and writes each bot's `data/state.json`, so web and Telegram share one history.
- Each exam home offers a choice: **Study** (reading list by domain + study cards) or **Quiz**.
- Home LAN only, no login. One container on the NAS, port 3003.

## Architecture
- `web.py` (FastAPI + uvicorn). For each exam it imports that bot's own `bot.py` from the mounted stack folder (`importlib`, `DATA_DIR` set per exam before import) so prompts, picker, grading, weights, pass mark and Claude calls are the running bot code, not a copy. Each module's `vault_log` is replaced with one that writes to that exam's vault folder.
- The bot flows (`send_question`, `grade`, `explain`, `send_study`, `weekly_report`, `refresh_bank`) take a `chat` object with `send_message` / `send_photo`. The portal passes a **recorder** whose messages go into an `asyncio.Queue`; the HTTP response streams them as NDJSON events (`{op: add|edit|delete, id, html, png, caption, buttons, question?, metrics?}`) so the browser shows the verdict first, then "drawing", then the diagram, like Telegram does.
- Endpoints: `GET /api/exams`, `GET /api/{exam}/home` (pending question, counts), `GET /api/{exam}/study` (reading list JSON + per-domain coverage), `POST /api/{exam}/act {data}` (same callback vocabulary as the bot buttons: `next`, `stats`, `study`, `report`, `refresh`, `reset`, `q|id`, `e|id|picked`), `POST /api/{exam}/answer {id, picked[]}` → `grade`. One `asyncio.Lock` per exam so actions never interleave state writes.
- Static: `static/index.html`, `app.css`, `app.js`. No build step.
- `study/<exam>.json`: reading list `[{domain, links:[{title,url,note}]}]`. CCAR-P from the 2026-10-16 research; SAP-C03 from the AWS exam page and announced topics.

## Frontend
- Views: Home (exam switch + Study / Quiz doors + coverage ring), Quiz feed (cards: question with full-text option buttons, verdict, diagram with lightbox, tutor, stats, report), Study (reading list by domain with coverage, Study card button, cards stream into the same feed).
- Mobile-first, dark, exam-coloured accent, keyboard A–H / Enter, elapsed timer while Claude works, confirm on Reset.
- Audited with web-design-guidelines and Chrome DevTools before completion.

## Deployment
- Dockerfile = bots' Dockerfile + `fastapi`, `uvicorn`, `python-telegram-bot` (the bot modules import it). Compose: `network_mode: bridge`, `ports: 3003:8000`, mounts `/volume1/docker/ccarp-quiz:/exams/ccarp:ro`, `/volume1/docker/sapc03-quiz:/exams/sapc03:ro`, each `data/` rw, both vaults rw. Env: `CLAUDE_CODE_OAUTH_TOKEN` (reused from the NAS), `CLAUDE_MODEL`, `EXAMS=ccarp:/exams/ccarp:/vault/ccarp,sapc03:/exams/sapc03:/vault/sapc03`.
- Public repo `gcjk768/exam-portal` (MIT), nas-sync app `exam-portal`, stack `/volume1/docker/exam-portal`.

## Limits (deliberate)
- `state.json` is load-mutate-save from two processes; single user, no lock (ponytail comment).
- Diagrams are not stored; they live in the page feed until reload (the pending question is restored on reload).
- CCAR-P study cards need `data/study_notes.csv` and `/study` in the CCAR-P bot: a follow-up in that repo (notes built from the Anthropic docs reading list).

## Tests
`test_web.py`: recorder streams add/edit/delete, `answer` grades and writes state, `act next` returns a question event with full options, reading list loads for both exams. Claude calls are monkeypatched.
