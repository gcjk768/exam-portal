# Study accelerator design (2026-10-22)

Make the portal get a student to a pass in the least study time, for all six exams (CCAR-P, SAP-C03, AIP-C01, CKA, GitLab CI/CD, Terraform): spaced repetition with a confidence tag, an exam-date plan, a question decoder after every answer, flashcards + a cram sheet from the study notes, and the three Udemy CCAR-P mock sets as a new question source. Also: Reset goes straight into question 1 (done).

## Goals
- Study time goes where the score gain is largest: due reviews first, then new questions from the domains with the highest weight × (1 − accuracy).
- Every answer teaches more than "right/wrong": which words in the stem decide it, why each distractor is wrong, the principle, a source.
- Progress stays in each bot's `data/state.json` (shared by phone, PC, Telegram, public visitors' own files). The bots' code is not changed.
- No new dependencies, no build step, no runtime Claude call except the decoder (one per question, cached forever).

## Non-goals (deliberate)
- Telegram's auto-quiz does not honour due dates (would need changes in six bot repos; phase 2).
- Live documentation lookup via MCP (Context7) in the decoder: phase 2 behind an env flag; the first version grounds answers in our study notes.
- Per-user accounts; the public link keeps its per-cookie progress files.

## Architecture
- `plan.py` (new, pure functions, no FastAPI): the scheduler and the plan numbers. `web.py` calls it.
- `web.py`: `record()` updates the schedule for every graded path (quiz, exam submit, task, self-mark); `Exam.pick` uses the scheduler's order; new endpoints below; decoder prefetch + cache; right answers graded by the portal (same shape as the wrong path today) so one code path records everything.
- `static/app.js` + `app.css` + `index.html`: guessing toggle, quiz bar, decoder block, Home plan panel, flashcards and cram sheet in Study.
- `study/<exam>.json`: `domains[].cards[]` (new), written once by agents from the notes.
- CCAR-P bank (`../CCAR-P/data/`): Udemy rows added as a file source that `scrape.refresh()` keeps.

## 1. Spaced repetition + confidence
State: `state["srs"][key] = [due_iso, streak]`; key is the question id or `c:<8-hex sha1 of the card question>` for a flashcard. `state["exam_date"] = "YYYY-MM-DD"`.

Ladder (`plan.LADDER_MIN`, one rung per right answer): `10 min → 1 d → 3 d → 7 d → 21 d → 45 d → 60 d`. A miss or a guessed answer goes back to the first rung; a question right and sure at first sight starts on the 21 d rung (one confirmation review). `ponytail:` fixed ladder, SM-2 ease factors if the queue ever feels wrong.

Also new: three Claude Foundation exams (CCAO-F, CCDV-F, CCAR-F; the owner, mid-session) as bot folders generated from the CCAR-P template with their banks from claudearchitects.org, served on the public link like CCAR-P. See the plan, Task 7.

Log entries get `"guess": true` when the toggle was on; `metrics()` (first-try accuracy) is unchanged by it.

Due: `srs` entries with `due <= now`, plus questions answered only in Telegram (no `srs` entry) with `bad > ok`. Order: most overdue first.

Next question (portal only; Telegram picker unchanged): due → unseen (domain drawn with probability ∝ `weight × (1 − accuracy) + 0.05`, accuracy from `metrics()["by_dom"]`, unseen-only domains count as accuracy 0.5) → missed more than right → any. Study → "Quiz me on this topic" and 🎯 Weak topic keep their domain filter and apply the same order inside it.

UI: question card gets a **🤔 Guessing** toggle (keyboard `?`), off by default, reset per question. Verdict line adds "· back in 10 min / 3 d". Quiz bar above the feed: `🔁 12 due · 1,980 new · today 7 / 20`.

Endpoints: `POST /answer {id, picked, guess?}`; `POST /task` same `guess` field; `GET /plan` returns the numbers below.

## 2. Exam-date plan (Home)
`GET /api/{exam}/plan` → `{exam_date, days_left, due, new_left, today: {target, done, new_target}, focus: [{domain, weight, acc, unseen}], on_track, score, pass}`. `POST /api/{exam}/plan {exam_date|null, pace?}`; pace (new questions per day, default 20) stored as `state["pace"]`.

Rules: `today.done` = log entries dated today (SGT). `today.target = due + pace`. `focus` = top two domains by `weight × (1 − acc)` with unseen questions left. `on_track` = predicted score ≥ pass, else the gap in points. Panel text: "📅 14 days · today 12 due + 20 new (7 done) · focus Integration, Governance · 612 predicted, 108 short of 720". Native `<input type="date">`; "Start today's session" opens Quiz and presses Next.

## 3. Question decoder
Prompt (`DECODE` in web.py, JSON schema): given the question, options, the correct letters, the bank explanation, the domain summary and the best-matching study note (same domain; most keyword overlap between stem and note title+gist+points), return `{clues: [2-4 exact phrases from the stem], options: {A: "one line", ...}, principle: "one sentence", source: {title, url}}`. One line per option: for the correct option(s) why right, for the others why wrong. Plain text, under 25 words each. The source is the note's source URL (never invented).

Runtime: when a question is sent (`send_question` / `send_task` path), `asyncio.create_task(decode(exam, q))` unless `data/decode/<qid>.json` exists; one task per qid at a time (`DECODING` dict). `GET /api/{exam}/decode/{qid}` → the JSON, or `202 {pending: true}` while running, or `404` when neither (public link, or a failed call). After grading, the browser polls every 2 s for up to 90 s and renders: stem with `<mark>` on the clues, a line under each option, the principle, the source link. Failures are logged and leave no file, so the next showing retries once. Model: the bot's `CLAUDE_MODEL`. Exam-mode review cards call the same endpoint (cached only, no prefetch).

Public link: no prefetch, cached files are served.

## 4. Flashcards + cram sheet
`study/<exam>.json` `domains[].cards = [{q, a}]`, 10–15 per domain, q ≤ 25 words, a ≤ 30 words, from `summary.must_know`, `summary.traps`, `notes[].points` and `notes[].tip`; one fact per card, the answer side states the fact, no "see note". Written by six parallel agents, then `test_web.py` asserts every domain has ≥ 8 cards and the length limits.

Study view: per domain a `🃏 Flashcards (n due)` button → a flip-card deck in the Study view (card front, click/Space to flip, **Again** / **Got it**, J/K or ←/→). `POST /api/{exam}/card {key, ok}` → `srs` via the same ladder (Again = wrong, Got it = right + sure). Deck order: due cards first, then unseen cards, then the rest; the Home plan counts card dues separately ("+ 9 cards").

`🗒 Cram sheet` button in the Study head → `#cram` route: one page, per domain `tests` line, must-knows, traps, every note's tip; `@media print` hides the chrome; a Print button.

## 5. Udemy sets → CCAR-P bank
`tools/udemy_mock.py` (portal): parse `Claude Certified Architect Professional Mock Exam.txt` into three sets (headers restart at "1."; "(Select TWO.)" ⇒ two answers) and write `../CCAR-P/data/online/udemy-gorgo.json` rows `{id: "UD-<set>-<nn>", domain, question, options, correct, explanation, source: "Udemy Angel Gorgo (model-keyed)"}`. Answers + explanations come from three parallel agents (one per set) reading the questions, each answer with a short explanation (≤ 60 words) naming the deciding clue; the domain is assigned by the set's own blueprint table + `scrape._classify`. `scrape.refresh()` merges file sources through `online_files()`, so the rows survive refreshes with stable ids. `SOURCE_TAG["Udemy Angel Gorgo (model-keyed)"] = "UD"`, `PUBLIC_HIDDEN_SOURCES` default gains it, README says the key is model-generated.

## Error handling
- Scheduler and plan: never raise; a malformed `srs` entry is dropped on read.
- Decoder: any exception → warning log, no file; the UI shows nothing (no placeholder lingering past 90 s).
- Flashcards/cram: missing `cards` → buttons hidden.
- Vault I/O stays best-effort as today.

## Testing
- `test_plan.py`: ladder (wrong → 10 min, streak growth, guess = wrong, sure-first = 21 d, cap), due ordering, Telegram-only misses are due, domain weighting prefers weak heavy domains, plan numbers (days left, today, focus, on track).
- `test_web.py` additions: answer with `guess` schedules 10 min; right answer records `srs` and `log`; exam submit records `srs`; `/plan` get/post; `/decode/{qid}` with a monkeypatched `run_claude` (pending → cached → served on the public link, no prefetch there); `/card`; every domain has cards within limits; Udemy rows load with tag `UD` and are hidden on the public link.
- Front end: Chrome run-through of quiz (guess toggle, decoder render), Home plan, flashcards, cram print view.

## Deployment
NAS: `deploy_portal.py` uploads the portal and the exam folders; `web.env` gains nothing new (decoder uses the existing Claude token); `compose.yaml` mounts are unchanged (`data/decode` lives in the CCAR-P data mount). Push `main` with the scrubbing sync in the same session. Vault: Changelog + App Overview + Roadmap (phase 2 items).
