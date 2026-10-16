/* Exam Portal front end: hash routing, NDJSON event feed, the bot's button vocabulary, timed exam mode. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const state = { exams: [], exam: localStorage.getItem("exam") || "", view: "home", busy: false,
                  cards: new Map(), metrics: null, pendingCard: null, home: null, feedLoaded: "" };

  // ── Routing ───────────────────────────────────────────────────────────────
  function go(view) { location.hash = view; }
  function route() {
    const v = (location.hash || "#home").slice(1);
    state.view = ["home", "study", "quiz", "exam", "stats"].includes(v) ? v : "home";
    $$(".view").forEach(s => s.hidden = s.id !== `view-${state.view}`);
    $$(".views a").forEach(a => a.setAttribute("aria-current", a.dataset.view === state.view));
    ({ home: loadHome, study: loadStudy, quiz: openQuiz, exam: loadExam, stats: loadStats })[state.view]();
    window.scrollTo({ top: 0 });
  }
  window.addEventListener("hashchange", route);

  // ── Exams ─────────────────────────────────────────────────────────────────
  async function init() {
    state.exams = await (await fetch("/api/exams")).json();
    if (!state.exams.some(e => e.key === state.exam)) state.exam = state.exams[0].key;
    renderSwitch();
    setExam(state.exam, false);
    route();
  }
  function renderSwitch() {
    const nav = $("#exam-switch");
    nav.innerHTML = "";
    for (const e of state.exams) {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = e.short; b.dataset.key = e.key;
      b.onclick = () => setExam(e.key, true);
      nav.append(b);
    }
  }
  function setExam(key, reload) {
    state.exam = key;
    localStorage.setItem("exam", key);
    document.documentElement.dataset.exam = key;
    $$("#exam-switch button").forEach(b => b.setAttribute("aria-current", b.dataset.key === key));
    if (reload) { clearFeed(); EX.run = null; clearInterval(EX.timer); route(); }
  }
  const cur = () => state.exams.find(e => e.key === state.exam);
  const api = (p) => fetch(`/api/${state.exam}${p}`).then(r => r.json());
  const post = (p, body) => fetch(`/api/${state.exam}${p}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ── Home ──────────────────────────────────────────────────────────────────
  async function loadHome() {
    const h = await api("/home");
    state.home = h;
    $("#home-name").textContent = h.name;
    $("#home-title").textContent = h.short;
    $("#home-verdict").textContent = h.verdict.replace(/^[^\w]+/, "");
    $("#ring-score").textContent = h.score;
    $("#ring-pass").textContent = h.pass;
    $("#ring-fill").style.strokeDashoffset = 326.7 * (1 - Math.min(h.score, 1000) / 1000);
    $("#f-seen").textContent = `${h.coverage.seen} / ${h.coverage.total}`;
    $("#f-right").textContent = h.coverage.right;
    $("#f-week").textContent = `${h.week.ok} / ${h.week.n}`;
    $("#door-study").textContent = h.has_study
      ? `Reading list by domain and ${h.study_cards} study cards (${h.studied} done)`
      : "Reading list by domain, official guide and free courses";
    $("#door-quiz-d").textContent = h.pending ? `Resume ${h.pending.question.id}, still unanswered`
      : `${h.coverage.total - h.coverage.seen} questions you have not seen yet`;
    $("#door-exam-d").textContent = `${h.exam_n} questions in ${h.exam_minutes} min, scored at the end`;
  }

  // ── Study ─────────────────────────────────────────────────────────────────
  async function loadStudy() {
    const s = await api("/study");
    const r = s.reading || {};
    $("#study-intro").textContent = r.intro || "";
    $("#btn-card").hidden = !s.has_cards;
    const link = (l) => {
      const a = document.createElement("a");
      a.href = l.url; a.rel = "noopener"; a.target = l.url.startsWith("#") ? "" : "_blank";
      a.innerHTML = `<div class="t"><span>${esc(l.title)}</span>${l.kind ? `<span class="tag ${l.kind}">${l.kind}</span>` : ""}</div><div class="n">${esc(l.note || "")}</div>`;
      if (l.url === "#study-cards") a.onclick = (ev) => { ev.preventDefault(); studyCard(); };
      const li = document.createElement("li"); li.append(a); return li;
    };
    const start = $("#study-start"); start.innerHTML = "";
    (r.start || []).forEach(l => start.append(link(l)));
    const cov = Object.fromEntries(s.coverage.domains.map(d => [d.name, d]));
    const box = $("#study-domains"); box.innerHTML = "";
    (r.domains || []).forEach((d, i) => {
      const c = cov[d.domain];
      const det = document.createElement("details"); det.className = "domain"; det.open = i === 0;
      const pct = c && c.total ? Math.round(100 * c.seen / c.total) : 0;
      det.innerHTML = `<summary><span><span class="dn">${esc(d.domain)}</span>${d.weight ? `<span class="dw">${d.weight}%</span>` : ""}</span>
        <span class="cov">${c ? `<b>${c.right}</b> right first time, ${c.seen} of ${c.total} seen` : "coverage by source bank"}</span>
        <span class="bar"><i style="width:${pct}%"></i></span></summary>`;
      const ul = document.createElement("ul"); ul.className = "links";
      d.links.forEach(l => ul.append(link(l)));
      det.append(ul); box.append(det);
    });
  }
  function studyCard() { go("quiz"); setTimeout(() => act("study"), 50); }
  $("#btn-card").onclick = studyCard;

  // ── Stats ─────────────────────────────────────────────────────────────────
  async function loadStats() {
    const h = await api("/home");
    $("#stats-summary").textContent = `${h.coverage.seen} of ${h.coverage.total} seen, ${h.coverage.right} right first time. Predicted ${h.score} of 1000, pass ${h.pass}. ${h.verdict.replace(/^[^\w]+/, "")}`;
    const box = $("#stats-bars"); box.innerHTML = "";
    for (const d of h.coverage.domains) {
      const row = document.createElement("div"); row.className = "bar-row";
      row.innerHTML = `<span class="bn">${esc(d.name)}${d.weight ? ` <span class="muted small">${d.weight}%</span>` : ""}</span>
        <span class="bv">${d.right} right, ${d.seen}/${d.total} seen</span>
        <span class="bar"><i class="right" style="width:${100 * d.right / d.total}%"></i><i class="seen" style="width:${100 * (d.seen - d.right) / d.total}%"></i></span>`;
      box.append(row);
    }
  }
  const resetBtn = $("#btn-reset");
  let resetArmed = 0;
  resetBtn.onclick = () => {
    if (!resetArmed) { resetBtn.textContent = "Confirm reset"; resetArmed = setTimeout(() => { resetArmed = 0; resetBtn.textContent = "Reset progress"; }, 5000); return; }
    clearTimeout(resetArmed); resetArmed = 0; resetBtn.textContent = "Reset progress";
    go("quiz"); setTimeout(() => act("reset"), 50);
  };

  // ── Feed ──────────────────────────────────────────────────────────────────
  const feed = $("#feed");
  function clearFeed() { feed.innerHTML = ""; state.cards.clear(); state.pendingCard = null; state.feedLoaded = ""; }
  async function openQuiz() {
    $("#btn-study-feed").hidden = !(cur() && cur().has_study);
    if (state.feedLoaded === state.exam) return;
    state.feedLoaded = state.exam;
    const h = state.home && state.home.key === state.exam ? state.home : await api("/home");
    if (h.pending) addCard({ op: "add", id: "pending", kind: "text", html: h.pending.html, question: h.pending.question, buttons: [] });
    else if (!feed.children.length) feed.innerHTML = `<p class="empty">No question yet. Press Next question, or A to H once one is on screen.</p>`;
  }
  $$("[data-act]").forEach(b => b.onclick = () => act(b.dataset.act));

  async function act(data) { await run("/act", { data }); }
  async function answer(id, picked) { await run("/answer", { id, picked }); }

  async function run(path, body) {
    if (state.busy) return;
    setBusy(true, body.data === "study" ? "Writing the study card" : body.data === "report" ? "Preparing the report" : "Working");
    $(".empty", feed)?.remove();
    try {
      const res = await post(path, body);
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n")) >= 0) { const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1); if (line) apply(JSON.parse(line)); }
      }
    } catch (e) {
      apply({ op: "error", html: `Connection lost: ${esc(String(e))}` });
    } finally { setBusy(false); }
  }

  let tick = null;
  function setBusy(on, label) {
    state.busy = on;
    $$(".feed-actions .btn, #btn-card").forEach(b => b.disabled = on);
    const w = $("#working"); w.hidden = !on;
    clearInterval(tick);
    if (on) {
      $("#working-text").textContent = label;
      const t0 = Date.now();
      $("#working-time").textContent = "0s";
      tick = setInterval(() => $("#working-time").textContent = `${Math.round((Date.now() - t0) / 1000)}s`, 1000);
      w.scrollIntoView({ block: "nearest" });
    }
  }

  function apply(ev) {
    if (ev.op === "add") addCard(ev);
    else if (ev.op === "edit") editCard(ev);
    else if (ev.op === "delete") { state.cards.get(ev.id)?.remove(); state.cards.delete(ev.id); }
    else if (ev.op === "meta") { if (ev.metrics) state.metrics = ev.metrics; if (ev.graded) markGraded(ev.graded); }
    else if (ev.op === "error") addCard({ op: "add", id: `err${Date.now()}`, kind: "text", html: ev.html, buttons: [], error: true });
    feed.lastElementChild?.scrollIntoView({ block: "end", behavior: "smooth" });
  }

  const KIND = { "📝": "q", "🟢": "ok", "🔴": "bad", "🎓": "tutor", "📊": "stats", "📚": "study", "⏳": "wait", "⚠️": "err", "🆕": "bank", "🗺": "map", "🔄": "bank" };
  function kindOf(html) { const k = [...html.trim()][0]; return KIND[k] || (html.startsWith("⚠") ? "err" : "text"); }

  function addCard(ev) {
    let el;
    if (ev.question) el = questionCard(ev);
    else if (ev.kind === "photo") {
      el = document.createElement("article"); el.className = "card card-photo";
      el.innerHTML = `<img src="data:image/png;base64,${ev.png}" alt="Diagram"><div class="cap html">${tg(ev.html)}</div>`;
      $("img", el).onclick = () => { $("#lightbox-img").src = $("img", el).src; $("#lightbox").showModal(); };
    } else {
      el = document.createElement("article");
      const k = ev.error ? "err" : kindOf(ev.html);
      el.className = `card card-${k}`;
      if (k === "stats" && state.metrics && /WEEKLY REPORT/.test(ev.html)) {
        const m = state.metrics; state.metrics = null;
        el.innerHTML = `<div class="score-strip"><strong>${m.score}</strong><span>predicted of 1000, pass ${m.pass}<br>${esc(m.verdict)}</span></div>`;
      }
      el.insertAdjacentHTML("beforeend", `<div class="html">${tg(ev.html)}</div>`);
    }
    el.append(actionsFor(ev.buttons, el));
    el.dataset.id = ev.id;
    feed.append(el);
    state.cards.set(ev.id, el);
    $$(".spoiler", el).forEach(s => s.onclick = () => s.classList.toggle("open"));
  }

  function editCard(ev) {
    const el = state.cards.get(ev.id);
    if (!el) return addCard({ ...ev, op: "add", kind: "text" });
    el.className = `card card-${kindOf(ev.html)}`;
    $(".html", el).innerHTML = tg(ev.html);
    $(".actions", el)?.replaceWith(actionsFor(ev.buttons, el));
  }

  function actionsFor(rows, card) {
    const box = document.createElement("div"); box.className = "actions";
    for (const row of rows || []) for (const b of row) {
      if (!b.data || b.data.startsWith("a|") || b.data.startsWith("s|")) continue;   // option buttons are the question card
      const btn = document.createElement("button"); btn.type = "button"; btn.className = "btn"; btn.textContent = b.label;
      btn.onclick = () => { $$(".btn", box).forEach(x => x.disabled = true); act(b.data); };
      box.append(btn);
    }
    if (!box.children.length) box.hidden = true;
    return box;
  }

  function optionButton(o) {
    const b = document.createElement("button"); b.type = "button"; b.className = "opt"; b.dataset.l = o.l;
    b.setAttribute("aria-pressed", "false");
    b.innerHTML = `<span class="l">${o.l}</span><span>${esc(o.t)}</span>`;
    return b;
  }

  function questionCard(ev) {
    const q = ev.question;
    const el = $("#t-question").content.firstElementChild.cloneNode(true);
    $(".qid", el).textContent = q.id;
    $(".dom", el).textContent = q.domain;
    $(".qtext", el).textContent = q.question;
    const opts = $(".options", el), picked = new Set();
    for (const o of q.options) {
      const b = optionButton(o);
      b.onclick = () => {
        if (!q.multi) return submit([o.l]);
        picked.has(o.l) ? picked.delete(o.l) : picked.add(o.l);
        b.setAttribute("aria-pressed", String(picked.has(o.l)));
        $(".submit-row .btn", el).disabled = !picked.size;
      };
      opts.append(b);
    }
    const sub = $(".submit-row", el);
    sub.hidden = !q.multi;
    $(".btn", sub).disabled = true;
    $(".btn", sub).onclick = () => submit([...picked].sort());
    function submit(letters) {
      if (state.busy) return;
      $$(".opt", el).forEach(b => { b.disabled = true; b.setAttribute("aria-pressed", String(letters.includes(b.dataset.l))); });
      sub.hidden = true;
      state.pendingCard = null;
      answer(q.id, letters);
    }
    state.pendingCard = { el, q, picked, submit };
    return el;
  }

  function markGraded(g) {
    const el = [...state.cards.values()].reverse().find(c => c.querySelector(".qid")?.textContent === g.id);
    if (!el) return;
    $$(".opt", el).forEach(b => {
      if (g.correct.includes(b.dataset.l)) b.classList.add("is-correct");
      else if (g.picked.includes(b.dataset.l)) b.classList.add("is-wrong");
    });
  }

  // Keyboard: A-H pick, Enter submits a multi-select or asks for the next question.
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, dialog")) return;
    const k = e.key.toUpperCase();
    if (state.view === "exam" && EX.run && !EX.run.done) {
      if (/^[A-H]$/.test(k)) { $(`#exam-q .opt[data-l="${k}"]`)?.click(); e.preventDefault(); }
      else if (e.key === "ArrowRight" || e.key === "Enter") $("#exam-next").click();
      else if (e.key === "ArrowLeft") $("#exam-prev").click();
      return;
    }
    if (state.view !== "quiz") return;
    const p = state.pendingCard;
    if (p && p.el.isConnected && /^[A-H]$/.test(k)) { $(`.opt[data-l="${k}"]`, p.el)?.click(); e.preventDefault(); }
    else if (e.key === "Enter") {
      if (p && p.el.isConnected && p.q.multi) { if (p.picked.size) p.submit([...p.picked].sort()); }
      else if (!state.busy) act("next");
    }
  });

  // ── Exam mode: timed, no feedback until submit, graded in one call ────────
  const EX = { key: () => `exam:${state.exam}`, run: null, timer: null, cur: 0 };
  function loadExam() {
    const saved = JSON.parse(localStorage.getItem(EX.key()) || "null");
    if (saved && !saved.done) { EX.run = saved; EX.cur = Math.min(EX.cur, saved.questions.length - 1); return showExamRun(); }
    EX.run = null;
    $("#exam-setup").hidden = false; $("#exam-run").hidden = true; $("#exam-result").hidden = true;
    const e = cur();
    const box = $("#exam-sizes"); box.innerHTML = "";
    const pace = (n) => Math.max(5, Math.round(e.exam_minutes * n / e.exam_n));
    const sizes = [[e.exam_n, e.exam_minutes, "Full exam", true], [25, pace(25), "Sprint", false], [10, pace(10), "Quick check", false]];
    for (const [n, min, label, primary] of sizes) {
      if (n > e.total) continue;
      const b = document.createElement("button"); b.type = "button"; b.className = `size${primary ? " primary" : ""}`;
      b.innerHTML = `<b>${n} questions</b><span>${label}, ${min} min</span>`;
      b.onclick = () => startExam(n);
      box.append(b);
    }
  }
  async function startExam(n) {
    const r = await (await post("/exam/start", { n })).json();
    EX.run = { questions: r.questions, minutes: r.minutes, started: Date.now(), answers: {}, done: false };
    EX.cur = 0; saveExam(); showExamRun();
  }
  function saveExam() { localStorage.setItem(EX.key(), JSON.stringify(EX.run)); }
  function showExamRun() {
    $("#exam-setup").hidden = true; $("#exam-run").hidden = false; $("#exam-result").hidden = true;
    renderExamNav(); renderExamQ(); tickExam();
    clearInterval(EX.timer); EX.timer = setInterval(tickExam, 1000);
  }
  function examLeft() { return EX.run.minutes * 60 - Math.floor((Date.now() - EX.run.started) / 1000); }
  function tickExam() {
    if (!EX.run || EX.run.done) return clearInterval(EX.timer);
    const left = examLeft();
    const c = $("#exam-clock");
    c.textContent = `${String(Math.max(0, Math.floor(left / 60))).padStart(2, "0")}:${String(Math.max(0, left % 60)).padStart(2, "0")}`;
    c.classList.toggle("low", left < 300);
    if (left <= 0) submitExam(true);
  }
  function renderExamNav() {
    const nav = $("#exam-nav"); nav.innerHTML = "";
    EX.run.questions.forEach((q, i) => {
      const b = document.createElement("button"); b.type = "button"; b.textContent = i + 1;
      b.className = EX.run.answers[q.id]?.length ? "done" : "";
      b.setAttribute("aria-current", String(i === EX.cur));
      b.onclick = () => { EX.cur = i; renderExamNav(); renderExamQ(); };
      nav.append(b);
    });
    const n = Object.values(EX.run.answers).filter(a => a.length).length;
    $("#exam-progress").textContent = `${n} of ${EX.run.questions.length} answered`;
    $("#exam-prev").disabled = EX.cur === 0;
    $("#exam-next").disabled = EX.cur === EX.run.questions.length - 1;
  }
  function renderExamQ() {
    const q = EX.run.questions[EX.cur];
    const el = $("#t-question").content.firstElementChild.cloneNode(true);
    $(".qid", el).textContent = `${EX.cur + 1} of ${EX.run.questions.length}`;
    $(".dom", el).textContent = q.multi ? "Select all that apply" : "";
    $(".qtext", el).textContent = q.question;
    $(".submit-row", el).remove();
    const picked = new Set(EX.run.answers[q.id] || []);
    const opts = $(".options", el);
    for (const o of q.options) {
      const b = optionButton(o);
      b.setAttribute("aria-pressed", String(picked.has(o.l)));
      b.onclick = () => {
        if (q.multi) picked.has(o.l) ? picked.delete(o.l) : picked.add(o.l);
        else { picked.clear(); picked.add(o.l); }
        EX.run.answers[q.id] = [...picked].sort(); saveExam();
        $$(".opt", el).forEach(x => x.setAttribute("aria-pressed", String(picked.has(x.dataset.l))));
        renderExamNav();
        if (!q.multi && EX.cur < EX.run.questions.length - 1) setTimeout(() => { EX.cur++; renderExamNav(); renderExamQ(); }, 220);
      };
      opts.append(b);
    }
    const box = $("#exam-q"); box.innerHTML = ""; box.append(el);
  }
  $("#exam-prev").onclick = () => { if (EX.cur > 0) { EX.cur--; renderExamNav(); renderExamQ(); } };
  $("#exam-next").onclick = () => { if (EX.cur < EX.run.questions.length - 1) { EX.cur++; renderExamNav(); renderExamQ(); } };
  let submitArmed = 0;
  $("#exam-submit").onclick = () => {
    const blank = EX.run.questions.length - Object.values(EX.run.answers).filter(a => a.length).length;
    if (!submitArmed && blank) { $("#exam-submit").textContent = `Submit with ${blank} blank`; submitArmed = setTimeout(() => { submitArmed = 0; $("#exam-submit").textContent = "Submit exam"; }, 5000); return; }
    clearTimeout(submitArmed); submitArmed = 0; $("#exam-submit").textContent = "Submit exam";
    submitExam(false);
  };
  async function submitExam(timedOut) {
    if (!EX.run || EX.run.done) return;
    EX.run.done = true; clearInterval(EX.timer);
    const answers = {};
    for (const q of EX.run.questions) answers[q.id] = EX.run.answers[q.id] || [];
    const res = await (await post("/exam/submit", { answers, minutes_used: (Date.now() - EX.run.started) / 60000 })).json();
    localStorage.removeItem(EX.key());
    showExamResult(res, timedOut);
  }
  function showExamResult(res, timedOut) {
    $("#exam-setup").hidden = true; $("#exam-run").hidden = true; $("#exam-result").hidden = false;
    $("#res-score").textContent = res.score;
    $("#res-verdict").textContent = `${res.passed ? "Pass" : "Below the pass mark"} at ${res.pass}. ${res.right} of ${res.n} right in ${res.minutes} min${timedOut ? ", time ran out" : ""}.`;
    const box = $("#res-domains"); box.innerHTML = "";
    for (const d of res.domains) {
      const row = document.createElement("div"); row.className = "bar-row";
      row.innerHTML = `<span class="bn">${esc(d.name)}${d.weight ? ` <span class="muted small">${d.weight}%</span>` : ""}</span><span class="bv">${d.right}/${d.n}</span>
        <span class="bar"><i class="right" style="width:${100 * d.right / d.n}%"></i></span>`;
      box.append(row);
    }
    const list = $("#res-list"); list.innerHTML = "";
    for (const r of res.results) {
      const el = $("#t-question").content.firstElementChild.cloneNode(true);
      el.classList.add("card-review", r.right ? "card-ok" : "card-bad");
      $(".qid", el).textContent = r.id; $(".dom", el).textContent = r.domain; $(".qtext", el).textContent = r.question;
      $(".submit-row", el).remove();
      const v = document.createElement("p"); v.className = `verdict-line ${r.right ? "ok" : "bad"}`;
      v.textContent = r.right ? `Correct, ${r.correct.join(",")}` : r.picked.length ? `Wrong, you picked ${r.picked.join(",")}, answer ${r.correct.join(",")}` : `Blank, answer ${r.correct.join(",")}`;
      $(".qtext", el).after(v);
      const opts = $(".options", el);
      for (const o of r.options) {
        const b = optionButton(o); b.disabled = true;
        if (r.correct.includes(o.l)) b.classList.add("is-correct"); else if (r.picked.includes(o.l)) b.classList.add("is-wrong");
        opts.append(b);
      }
      const ex = document.createElement("div"); ex.className = "expl"; ex.textContent = r.explanation; el.append(ex);
      if (!r.right) {
        const a = document.createElement("div"); a.className = "actions";
        const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = "Explain more with a diagram";
        b.onclick = () => { go("quiz"); setTimeout(() => act(`e|${r.id}|${r.picked.join(",")}`), 50); };
        a.append(b); el.append(a);
      }
      list.append(el);
    }
    window.scrollTo({ top: 0 });
  }
  $("#res-again").onclick = () => { EX.run = null; loadExam(); };

  // ── Telegram HTML -> page HTML ────────────────────────────────────────────
  function tg(html) {
    return (html || "").replace(/<tg-spoiler>/g, '<span class="spoiler">').replace(/<\/tg-spoiler>/g, "</span>")
      .replace(/<blockquote expandable>/g, "<blockquote>").replace(/<a /g, '<a target="_blank" rel="noopener" ');
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  $$("[data-view]").forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); go(a.dataset.view); }));
  init();
})();
