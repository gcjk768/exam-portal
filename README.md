# Exam Portal

One website for the quiz bots that live in their own repos: [CCAR-P](https://github.com/gcjk768/claude-certified-architect-professional-portable), [AWS SAP-C03](https://github.com/gcjk768/aws-certified-solutions-architect-professional-portable), [AWS AIP-C01](https://github.com/gcjk768/aws-certified-generative-ai-developer-professional-portable), [CKA](https://github.com/gcjk768/certified-kubernetes-administrator-portable), GitLab CI/CD Associate and [Terraform Associate](https://github.com/gcjk768/terraform-associate-portable). Every Telegram function, plus a Study door, a timed Exam mode and a study planner that puts your time where the score gain is largest, on your home LAN, plus a locked-down public link.

The portal does not copy the bots' logic. It imports each bot's own `bot.py` from its folder and drives it with a recorder in place of the Telegram chat, so grading, Claude tutor prompts, diagrams, scoring weights and pass marks are exactly the running bot code. It reads and writes the same `data/state.json`, so a question answered on the web counts in Telegram and the other way round.

## What you get

| Door | What |
|---|---|
| **Home** | Exam switch, predicted score ring, readiness verdict, and **Today**: due reviews, new questions a day, done today, days to the exam date you set, the two domains to focus on, on-track or the gap to the pass mark. *Start today's session* opens the quiz. |
| **Study** | Study notes per exam domain (summary of what the domain tests, must-knows, traps, notes with a diagram and a video), reading list, **flashcards** per domain on the same review schedule as questions, a printable **cram sheet**, study cards where the bot has them. |
| **Quiz** | **Due reviews first** (spaced repetition: 10 min, 1, 3, 7, 21, 45, 60 days; a miss or a *Guessing* answer restarts), then unseen questions drawn from the heaviest weak domains, then misses. One button per option, *Select TWO* with Submit, verdict with the next review time, the bank's explanation, and the **decoder**: the clue phrases in the stem, one line per option on why it is right or wrong, the principle and a source note. *Explain more* draws Claude's diagram. Keyboard: A to H, Enter, ? for guessing. |
| **Exam** | Timed mock exam at the real pace (full length, 25-question sprint or 10-question check), no feedback until you submit, scored on the 1000 scale against the real pass mark, per-domain breakdown, full review with the decoder and *Explain more with a diagram*. Survives a reload. |
| **Stats** | Per-domain bars, Refresh bank, Reset progress (two-step; reset goes straight into question 1). |

Claude is called for the decoder (once per question shown, cached forever in the bot's `data/decode/`), for *Explain more*, study cards and the coach note, exactly as the bots call it (`claude -p`). Answering, scheduling, flashcards and the plan never call Claude.

## Run

Local, next to the bot folders (they are discovered by the logger name in each `bot.py`):

```sh
pip install -r requirements.txt
python web.py            # http://localhost:8000
python test_web.py       # no Claude calls
python test_plan.py      # scheduler and plan maths
```

Docker (the NAS): fill `web.env` from `web.env.example`, set the host paths in `.env` (`CCARP_DIR`, `SAPC03_DIR`, `AIPC01_DIR`, `*_VAULT`), then

```sh
docker compose up -d --build
```

and open `http://<host>:3003`. The image is the bots' image (Python, Claude Code, Graphviz) plus FastAPI. Each bot stack is mounted read-only for code and read-write for `data/`; the `EXAMS` variable in `compose.yaml` lists `key:bot_dir:vault_dir` triples.

## Files

| File | Purpose |
|---|---|
| `web.py` | FastAPI app: exam loading, recorder, actions, decoder, plan and flashcard endpoints, exam mode, NDJSON streaming. |
| `plan.py` | The review ladder, due queue, next-question order and plan numbers: pure functions over a bot's `state.json` (`srs`, `exam_date`, `pace`). |
| `static/index.html`, `app.css`, `app.js`, `favicon.svg` | The site. No build step. Phosphor icons and the fonts come from a CDN. |
| `study/<exam>.json` | Per exam: `start` links; `domains` with a summary (what you need to know), study notes (gist, points, exam tip, Mermaid diagram, video, source), flashcards (`cards`) and the reading list. |
| `static/icons/` | Official product icons used in the diagrams; rebuilt by `python tools/build_icons.py`. |
| `tools/` | `check_study.py` (validate a study JSON), `merge_cards.py` (merge agent-written flashcards), `new_exam.py` (generate a Claude-certification bot folder from the CCAR-P template with its bank from claudearchitects.org). |
| `test_web.py`, `test_plan.py` | `python test_web.py`: streaming, grading, scheduler, plan, decoder, flashcards, study JSON, report, exam mode. |
| `Dockerfile`, `compose.yaml`, `web.env.example` | The container. |
| `docs/superpowers/` | Design specs and plans. |

## Limits

Two ways in. Your own link (NAS tailnet IP or LAN) has everything: Claude calls, reset, refresh, report, all question sources, the plan. The public link (Tailscale Funnel, or any request for the `.ts.net` name) has no Claude or admin actions, hides paid question sources (`PUBLIC_HIDDEN_SOURCES`, default `Tutorials Dojo`, plus the Udemy mock sets), serves cached decoders only, keeps each visitor's progress in its own file (cookie, `data/public/`), does not write to the owner's vault, and is rate-limited (90 requests a minute per visitor). Diagrams live in the page until you move on (the pending question is restored on reload). The bot and the portal both write `state.json` with load-mutate-save; one owner, no file lock. The review ladder is fixed (no SM-2 ease factors); Telegram's auto-quiz does not honour due dates.

## License

MIT for the code and notes.

Icons in `static/icons/` belong to their owners and keep their own terms: AWS Architecture Icons (Amazon Web Services; `aws-*`), Kubernetes icons (The Kubernetes Authors, github.com/kubernetes/community, Apache-2.0 or CC-BY-4.0; `k8s-*`), and product logos from the Iconify `logos` set (CC0; trademarks of their owners). They are used only to label diagrams. UI icons are [Phosphor Icons](https://phosphoricons.com) (MIT).
