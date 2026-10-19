# Exam Portal

One website for the quiz bots that live in their own repos: [CCAR-P](https://github.com/gcjk768/claude-certified-architect-professional-portable), [AWS SAP-C03](https://github.com/gcjk768/aws-certified-solutions-architect-professional-portable) and [AWS AIP-C01](https://github.com/gcjk768/aws-certified-generative-ai-developer-professional-portable). Every Telegram function, plus a Study door and a timed Exam mode, on your home LAN, plus a locked-down public link.

The portal does not copy the bots' logic. It imports each bot's own `bot.py` from its folder and drives it with a recorder in place of the Telegram chat, so the question picker, grading, Claude tutor prompts, diagrams, scoring weights and pass marks are exactly the running bot code. It reads and writes the same `data/state.json`, so a question answered on the web counts in Telegram and the other way round.

## What you get

| Door | What |
|---|---|
| **Home** | Exam switch, predicted score ring, readiness verdict, this week's numbers, three doors. |
| **Study** | Reading list by exam domain with your coverage next to each domain, official guide and free courses first. Study cards where the bot has them (SAP-C03, AIP-C01): Claude writes a note from your slide deck and redraws the slide's diagram with official icons, with a *Quiz me on this* button. |
| **Quiz** | Random question (unseen first, then ones you got wrong), one button per option, *Select TWO* with Submit, verdict and the bank's explanation, an architecture diagram drawn by Claude when you are wrong, *Explain more*, Stats, the weekly Report with coach note. Keyboard: A to H, Enter. |
| **Exam** | Timed mock exam at the real pace (full length, 25-question sprint or 10-question check), no feedback until you submit, scored on the 1000 scale against the real pass mark, per-domain breakdown, full review with *Explain more with a diagram* on every miss. Survives a reload. |
| **Stats** | Per-domain bars, Refresh bank, Reset progress (two-step). |

Claude is called exactly as the bots call it (`claude -p`, one call per wrong answer, study card or coach note), so the cost is the same as Telegram.

## Run

Local, next to the bot folders (they are discovered by the logger name in each `bot.py`):

```sh
pip install -r requirements.txt
python web.py            # http://localhost:8000
python test_web.py       # no Claude calls
```

Docker (the NAS): fill `web.env` from `web.env.example`, set the host paths in `.env` (`CCARP_DIR`, `SAPC03_DIR`, `AIPC01_DIR`, `*_VAULT`), then

```sh
docker compose up -d --build
```

and open `http://<host>:3003`. The image is the bots' image (Python, Claude Code, Graphviz) plus FastAPI. Each bot stack is mounted read-only for code and read-write for `data/`; the `EXAMS` variable in `compose.yaml` lists `key:bot_dir:vault_dir` triples.

## Files

| File | Purpose |
|---|---|
| `web.py` | FastAPI app: exam loading, recorder, actions, exam mode, NDJSON streaming. |
| `static/index.html`, `app.css`, `app.js` | The site. No build step. |
| `study/<exam>.json` | Per exam: `start` links; `domains` with a summary (what you need to know), study notes (gist, points, exam tip, Mermaid diagram, video, source) and the reading list. |
| `static/icons/` | Official product icons used in the diagrams; rebuilt by `python tools/build_icons.py`. |
| `test_web.py` | `python test_web.py`: streaming, grading, explain, reset, study, report, exam mode, all three exams. |
| `Dockerfile`, `compose.yaml`, `web.env.example` | The container. |
| `docs/superpowers/specs/` | The design spec. |

## Limits

Two ways in. Your own link (NAS tailnet IP or LAN) has everything: Claude calls, reset, refresh, report, all question sources. The public link (Tailscale Funnel, or any request for the `.ts.net` name) has no Claude or admin actions, hides paid question sources (`PUBLIC_HIDDEN_SOURCES`, default `Tutorials Dojo`), keeps each visitor's progress in its own file (cookie, `data/public/`), does not write to the owner's vault, and is rate-limited (90 requests a minute per visitor). Diagrams live in the page until you move on (the pending question is restored on reload). The bot and the portal both write `state.json` with load-mutate-save; one owner, no file lock.

## License

MIT for the code and notes.

Icons in `static/icons/` belong to their owners and keep their own terms: AWS Architecture Icons (Amazon Web Services; `aws-*`), Kubernetes icons (The Kubernetes Authors, github.com/kubernetes/community, Apache-2.0 or CC-BY-4.0; `k8s-*`), and product logos from the Iconify `logos` set (CC0; trademarks of their owners). They are used only to label diagrams.
