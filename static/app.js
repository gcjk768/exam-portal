/* Exam Portal front end: hash routing, NDJSON event feed, the bot's button vocabulary, timed exam mode, task exams. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const state = { exams: [], exam: localStorage.getItem("exam") || "", view: "home", busy: false,
                  cards: new Map(), metrics: null, pendingCard: null, home: null, feedLoaded: "" };

  // ── Routing ───────────────────────────────────────────────────────────────
  function go(view) { location.hash = view; }
  function route() {
    const v = (location.hash || "#home").slice(1);
    state.view = ["home", "study", "quiz", "exam", "stats", "cram", "board"].includes(v) ? v : "home";
    $$(".view").forEach(s => s.hidden = s.id !== `view-${state.view}`);
    $$(".views a").forEach(a => a.setAttribute("aria-current", a.dataset.view === state.view || (state.view === "cram" && a.dataset.view === "study")));
    ({ home: loadHome, study: loadStudy, quiz: openQuiz, exam: loadExam, stats: loadStats, cram: loadCram, board: loadBoard })[state.view]();
    window.scrollTo({ top: 0 });
  }
  window.addEventListener("hashchange", route);

  // ── Exams ─────────────────────────────────────────────────────────────────
  async function init() {
    state.exams = await (await fetch("/api/exams")).json();
    const access = await (await fetch("/api/access")).json();
    state.public = access.public;                                        // public visitor: no Claude, no reset, nothing recorded
    state.shared = access.shared;                                        // the shared site (public name): no personal numbers, for anyone
    document.documentElement.dataset.public = state.public ? "1" : "";
    document.documentElement.dataset.shared = state.shared ? "1" : "";
    const sc = $("#scope"); sc.hidden = false; sc.innerHTML = state.public ? `${ic("globe")}Public view` : `${ic("lock-simple")}Private`; sc.dataset.public = state.public ? "1" : "";
    if (!state.exams.some(e => e.key === state.exam)) state.exam = state.exams[0].key;
    renderSwitch();
    setExam(state.exam, false);
    route();
    if (state.public && !localStorage.getItem("lb-asked")) $("#lb-join").showModal();   // ask once; "No thanks" is remembered and nothing is recorded
  }

  // ── Leaderboard: opt-in; public visitors, and the owner for the public exams (web.py /api/board) ───────────────
  const LB = { exam: "all", period: "week" };
  const lbPost = (p, body) => fetch(`/api/board/${p}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  async function lbJoin(name) {
    const r = await lbPost("join", { name });
    if (!r.ok) { const e = $("#lb-err"); e.textContent = (await r.json()).detail || "Could not join."; e.hidden = false; return false; }
    localStorage.setItem("lb-asked", "1");
    return true;
  }
  $("#lb-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (await lbJoin($("#lb-name").value)) { $("#lb-join").close(); if (state.view === "board") loadBoard(); }
  });
  $("#lb-no").addEventListener("click", () => { localStorage.setItem("lb-asked", "1"); $("#lb-join").close(); });
  async function loadBoard() {
    const nav = $("#board-exam");
    if (!nav.children.length) {
      for (const [k, t] of [["all", "All"], ...state.exams.map(e => [e.key, e.short])]) {
        const b = document.createElement("button"); b.type = "button"; b.textContent = t; b.dataset.k = k;
        b.onclick = () => { LB.exam = k; loadBoard(); };
        nav.append(b);
      }
      $$("#board-period button").forEach(b => b.onclick = () => { LB.period = b.dataset.p; loadBoard(); });
    }
    $$("#board-exam button").forEach(b => b.setAttribute("aria-current", b.dataset.k === LB.exam));
    $$("#board-period button").forEach(b => b.setAttribute("aria-current", b.dataset.p === LB.period));
    const d = await (await fetch(`/api/board?exam=${LB.exam}&period=${LB.period}`)).json();
    const tb = $("#board-table tbody"); tb.innerHTML = "";
    for (const r of d.rows) {
      const tr = document.createElement("tr"); if (r.me) tr.className = "me";
      for (const v of [r.rank, r.name + (r.me ? " (you)" : ""), r.done, r.right, r.wrong, r.acc + "%"]) { const td = document.createElement("td"); td.textContent = v; tr.append(td); }
      tb.append(tr);
    }
    $("#board-empty").hidden = d.rows.length > 0;
    const me = $("#board-me"); me.innerHTML = "";
    const own = d.me.owner ? "On your own links only CCAR-P counts, and not the paid questions. " : "";
    if (d.me.joined) {
      me.append(`${own}You are on the board as ${d.me.name}${d.me.rank ? `, rank ${d.me.rank} of ${d.total}` : ", answer a question to get ranked"}. `);
      const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.textContent = "Leave and delete my data";
      b.onclick = async () => { await lbPost("leave"); localStorage.setItem("lb-asked", "1"); loadBoard(); };
      me.append(b);
    } else {
      const b = document.createElement("button"); b.type = "button"; b.className = "btn btn-accent"; b.textContent = "Join the leaderboard";
      b.onclick = () => $("#lb-join").showModal();
      me.append(`${own}You are not on the board. Nothing is recorded until you join. `, b);
    }
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
  const isTask = () => !!(cur() && cur().tasks);
  const api = (p) => fetch(`/api/${state.exam}${p}`).then(r => r.json());
  const post = (p, body) => fetch(`/api/${state.exam}${p}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ── Home ──────────────────────────────────────────────────────────────────
  async function loadHome() {
    const h = await api("/home");
    state.home = h;
    const unit = h.tasks ? "tasks" : "questions";
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
    $("#door-quiz-d").textContent = state.shared ? `${h.coverage.total} ${unit} with explanations, unseen first`
      : h.pending ? `Resume ${h.pending.question.id}, still unanswered`
      : h.progress ? (h.mode === "tasks" ? `Scenario tasks: ${h.progress.tasks.total - h.progress.tasks.seen} left to do`
                                         : `Multiple choice first: ${h.progress.mcq.total - h.progress.mcq.seen} left, then ${h.progress.tasks.total} scenario tasks`)
      : `${h.coverage.total - h.coverage.seen} ${unit} you have not seen yet`;
    $("#door-exam-d").textContent = `${h.exam_n} ${unit} in ${h.exam_minutes} min, scored at the end`;
    if (!state.public && !state.shared) renderPlan(h.plan);
    if (state.public) { renderPick(); showLeader(); }
  }

  // ── Public home: pick your certification, and this exam's leader (opt-in board) ──
  function renderPick() {
    const g = $("#pick-grid"); g.innerHTML = ""; $("#home-pick").hidden = false;
    for (const e of state.exams) {
      const b = document.createElement("button"); b.type = "button"; b.className = "pick-card"; b.dataset.exam = e.key;
      b.setAttribute("aria-current", e.key === state.exam);
      const k = document.createElement("b"); k.textContent = e.short;
      const n = document.createElement("span"); n.textContent = e.name.replace(/^Claude Certified /, "");
      const c = document.createElement("small"); c.textContent = `${e.total} questions`;
      b.append(k, n, c);
      b.onclick = () => setExam(e.key, true);
      g.append(b);
    }
  }
  async function showLeader() {
    const box = $("#home-leader");
    let d = null, period = "this week";
    for (const p of ["week", "all"]) {
      d = await (await fetch(`/api/board?exam=${state.exam}&period=${p}`)).json();
      if (d.rows.length) { if (p === "all") period = "all time"; break; }
    }
    box.innerHTML = ""; box.hidden = false;
    const t = d.rows[0];
    const i = document.createElement("i"); i.className = "ph ph-trophy"; i.setAttribute("aria-hidden", "true");
    const msg = document.createElement("span");
    msg.textContent = t ? `${cur().short} leader, ${period}: ${t.name}, ${t.right} right first time (${t.acc}% of ${t.done})`
                        : `Nobody is on the ${cur().short} leaderboard yet. Be the first.`;
    const a = document.createElement("a"); a.href = "#board"; a.textContent = "Leaderboard";
    box.append(i, msg, a);
  }

  // ── Plan (Home): exam date, today's targets, focus domains; the quiz bar shows the same numbers while you work ──
  const days = (n) => n == null ? "No exam date yet" : n < 0 ? "Exam date passed" : n === 0 ? "Exam day" : `${n} day${n === 1 ? "" : "s"} to the exam`;
  function fmtPlan(p) {
    const cards = p.cards.due.length ? ` and ${p.cards.due.length} flashcards` : "";
    const track = p.score ? (p.on_track ? `${p.score} predicted: on track for the ${p.pass} pass mark.` : `${p.score} predicted, ${p.gap} short of the ${p.pass} pass mark.`) : "Answer questions to get a predicted score.";
    return `${days(p.days_left)}. Today: ${p.due} due reviews${cards}, then ${p.today.pace} new questions. ${track}`;
  }
  function renderPlan(p) {
    state.plan = p;
    const box = $("#plan"); box.hidden = !p; if (!p) return;
    $("#ps-due").textContent = p.due + (p.cards.due.length ? ` +${p.cards.due.length}` : "");
    $("#ps-new").textContent = p.today.pace;
    $("#ps-done").textContent = p.today.done;
    $("#ps-days").textContent = p.days_left == null ? "none" : Math.max(0, p.days_left);
    $("#plan-line").textContent = fmtPlan(p);
    $("#plan-focus").textContent = p.focus.length ? "Focus next: " + p.focus.map(f => `${f.domain}${f.acc != null ? ` (${Math.round(100 * f.acc)}% right)` : ""}`).join(", ") : "";
    $("#plan-date").value = p.exam_date || ""; $("#plan-pace").value = p.today.pace;
  }
  async function savePlan(body) { renderPlan(await (await post("/plan", body)).json()); }
  $("#plan-date").onchange = (e) => savePlan({ exam_date: e.target.value || null });
  $("#plan-pace").onchange = (e) => savePlan({ pace: Number(e.target.value) || 20 });
  $("#plan-go").onclick = () => { go("quiz"); setTimeout(() => act("next"), 50); };
  function renderQuizBar(p) {
    const bar = $("#quiz-bar"); bar.hidden = !p; if (!p) return;
    $("#quiz-bar-text").textContent = `${p.due} due · ${p.new_left} unseen · today ${p.today.done} of ${p.today.target}${p.days_left != null ? ` · ${days(p.days_left)}` : ""}`;
  }
  async function refreshPlan() { try { const p = await api("/plan"); state.plan = p; renderQuizBar(p); } catch (e) { /* bar is cosmetic */ } }

  // ── Study ─────────────────────────────────────────────────────────────────
  async function loadStudy() {
    const s = await api("/study");
    const r = s.reading || {};
    $("#deck").hidden = true;
    try { state.plan = await api("/plan"); } catch (e) { state.plan = null; }     // due flashcards per domain
    const dueKeys = new Set(state.plan?.cards.due || []);
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
    const pb = $("#study-playbook"); pb.innerHTML = ""; $("#study-playbook-h").hidden = !(r.playbook || []).length;
    (r.playbook || []).forEach((p, i) => {
      const det = document.createElement("details"); det.className = "domain"; det.open = i === 0;
      det.innerHTML = `<summary><span class="dn">${esc(p.problem)}</span><span class="cov">${p.commands.length} commands</span></summary>`;
      const ul = document.createElement("ul"); ul.className = "cmds";
      p.commands.forEach(c => { const li = document.createElement("li"); li.innerHTML = `<code>${esc(c.cmd)}</code><span>${esc(c.what)}</span>`; ul.append(li); });
      det.append(ul); pb.append(det);
    });
    const cov = Object.fromEntries(s.coverage.domains.map(d => [d.name, d]));
    const box = $("#study-domains"); box.innerHTML = "";
    (r.domains || []).forEach((d, i) => {
      const c = cov[d.domain];
      const det = document.createElement("details"); det.className = "domain"; det.open = i === 0;
      const pct = c && c.total ? Math.round(100 * c.seen / c.total) : 0;
      det.innerHTML = `<summary><span><span class="dn">${esc(d.domain)}</span>${d.weight ? `<span class="dw">${d.weight}%</span>` : ""}</span>
        <span class="cov">${c ? `<b>${c.right}</b> right first time, ${c.seen} of ${c.total} seen` : "coverage by source bank"}</span>${ic("caret-down", "chev")}
        <span class="bar"><i style="width:${pct}%"></i></span></summary>`;
      if (d.summary) det.append(summaryBox(d.summary));
      const bar = document.createElement("div"); bar.className = "domain-actions";
      const nDue = (d.cards || []).filter(x => dueKeys.has(x.key)).length;
      bar.innerHTML = `<button type="button" class="btn btn-accent">${ic("target")}Quiz me on this topic</button>
        ${d.cards?.length ? `<button type="button" class="btn card-btn">${ic("cards")}Flashcards${nDue ? ` · ${nDue} due` : ` (${d.cards.length})`}</button>` : ""}
        ${d.notes?.length ? `<span class="read-count muted small"></span>` : ""}`;
      $("button", bar).onclick = () => { go("quiz"); setTimeout(() => act(`d|${d.domain}`), 50); };
      $(".card-btn", bar)?.addEventListener("click", () => openDeck(d));
      det.append(bar);
      det.append(topicMedia(d));
      if (d.notes?.length) { det.append(notesBlock(d)); readCount(det, d); }   // own notes instead of links out to the vendor docs
      else {
        const ul = document.createElement("ul"); ul.className = "links";
        d.links.forEach(l => ul.append(link(l)));
        det.append(ul);
      }
      det.addEventListener("toggle", () => det.open && drawPending(det));   // diagrams are drawn when a domain is opened
      if (det.open) drawPending(det);
      box.append(det);
    });
    const focus = state.studyFocus; state.studyFocus = null;     // "📖 Notes" from a question's Topic panel
    const target = focus && $$("#study-domains .domain").find(x => $(".dn", x).textContent === focus);
    if (target) { target.open = true; drawPending(target); setTimeout(() => target.scrollIntoView({ block: "start" }), 50); }
  }

  // a known question source (SOURCE_TAG in web.py, e.g. TD = Tutorials Dojo) gets a small label; CSS draws it, so the id text stays intact
  const setQid = (n, id, tag) => { n.textContent = id; if (tag) n.dataset.tag = tag; else delete n.dataset.tag; };
  // ── Study notes: gist, a few points, exam tip, diagram, optional video, source (study/<exam>.json domains[].notes) ──
  const rich = (s) => esc(s || "").replace(/`([^`]+)`/g, "<code>$1</code>");
  function summaryBox(s) {
    const el = document.createElement("section"); el.className = "summary";
    el.innerHTML = `<h4>${ic("push-pin", "ic-accent")}What you need to know</h4>
      ${s.tests ? `<p class="tests">${rich(s.tests)}</p>` : ""}
      <ul class="must">${(s.must_know || []).map(x => `<li>${rich(x)}</li>`).join("")}</ul>
      ${(s.traps || []).length ? `<p class="traps-h">${ic("warning", "ic-bad")}Common traps</p><ul class="traps">${s.traps.map(x => `<li>${rich(x)}</li>`).join("")}</ul>` : ""}`;
    return el;
  }
  // Read marks live in this browser (localStorage), one set of "domain#title" keys per exam.
  const readKey = () => `read:${state.exam}`;
  const readSet = () => new Set(JSON.parse(localStorage.getItem(readKey()) || "[]"));
  const noteKey = (d, n) => `${d.domain}#${n.title}`;
  function readCount(det, d) {
    const done = readSet(), n = d.notes.filter(x => done.has(noteKey(d, x))).length;
    const el = $(".read-count", det); if (el) el.innerHTML = `${ic("book-open")} ${n} of ${d.notes.length} notes read`;
  }
  function notesBlock(d) {
    const wrap = document.createElement("div"); wrap.className = "notes";
    const toc = document.createElement("nav"); toc.className = "note-toc"; toc.setAttribute("aria-label", `${d.domain} notes`);
    const done = readSet();
    d.notes.forEach((n, i) => {
      const a = document.createElement("a"); a.href = "#"; a.textContent = `${i + 1}. ${n.title}`;
      a.classList.toggle("read", done.has(noteKey(d, n)));
      a.onclick = (ev) => { ev.preventDefault(); wrap.children[i + 1].scrollIntoView({ behavior: "smooth", block: "start" }); };
      toc.append(a);
    });
    wrap.append(toc, ...d.notes.map((n, i) => noteCard(n, i, d, toc.children[i])));
    return wrap;
  }
  function noteCard(n, i, d, chip) {
    const el = document.createElement("article"); el.className = "note";
    const text = document.createElement("div"); text.className = "note-text";
    text.innerHTML = `<h4><span class="nn">${i + 1}</span>${esc(n.title)}</h4>
      ${n.objective ? `<p class="obj">${esc(n.objective)}</p>` : ""}
      <p class="gist">${rich(n.gist)}</p>
      <ul>${(n.points || []).map(p => `<li>${rich(p)}</li>`).join("")}</ul>
      ${n.tip ? `<p class="tip"><b>${ic("target", "ic-accent")} Exam tip</b> ${rich(n.tip)}</p>` : ""}
      <div class="note-foot">${n.source?.url ? `<p class="src">Source: <a href="${esc(n.source.url)}" target="_blank" rel="noopener">${esc(n.source.title || "page")}</a></p>` : ""}</div>`;
    if (d) {                                                   // "Mark as read" toggle, kept per browser
      const b = document.createElement("button"); b.type = "button"; b.className = "read-btn";
      const paint = (on) => { b.textContent = on ? "✓ Read" : "Mark as read"; b.setAttribute("aria-pressed", String(on)); el.classList.toggle("is-read", on); chip?.classList.toggle("read", on); };
      paint(readSet().has(noteKey(d, n)));
      b.onclick = () => {
        const s = readSet(), k = noteKey(d, n);
        s.has(k) ? s.delete(k) : s.add(k);
        localStorage.setItem(readKey(), JSON.stringify([...s]));
        paint(s.has(k)); readCount(el.closest(".domain"), d);
      };
      $(".note-foot", text).append(b);
    }
    const media = document.createElement("div"); media.className = "note-media";
    if (n.diagram) media.append(lazyDiagram(n.diagram));
    if (n.video) media.append(videoFig(n.video));
    el.append(text, media);
    return el;
  }

  // ── Topic media: a short video + a concept diagram per domain (study/<exam>.json) ──
  function topicMedia(d) {
    const box = document.createElement("div"); box.className = "topic";
    if (d.video) box.append(videoFig(d.video));
    if (d.diagram) box.append(lazyDiagram(d.diagram));
    return box;
  }
  function videoFig(v) {
    const fig = document.createElement("figure"); fig.className = "topic-video";
    fig.innerHTML = `<button type="button" class="yt" aria-label="Play ${esc(v.title)}">
        <img src="https://i.ytimg.com/vi/${esc(v.id)}/hqdefault.jpg" alt="" loading="lazy"><span class="play">▶</span></button>
      <figcaption><a href="https://youtu.be/${esc(v.id)}" target="_blank" rel="noopener">${esc(v.title)}</a>
        <span class="muted small">${esc(v.channel || "")}${v.min ? ` · ${v.min} min` : ""}</span></figcaption>`;
    $(".yt", fig).onclick = (ev) => ev.currentTarget.outerHTML =     // click-to-load: no YouTube iframe until asked
      `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(v.id)}?autoplay=1&rel=0" title="${esc(v.title)}"
        allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>`;
    return fig;
  }
  function lazyDiagram(src) {
    const dia = document.createElement("div"); dia.className = "topic-diagram"; dia.textContent = "Drawing the diagram…";
    dia.dataset.src = src;
    return dia;
  }
  function drawPending(root) {
    $$(".topic-diagram[data-src]", root).forEach(dia => { const src = dia.dataset.src; delete dia.dataset.src; drawDiagram(dia, src); });
  }

  // Diagram palette: shapes carry meaning. Stadium = start/end (exam accent), diamond = decision (amber),
  // cylinder = data store (teal), hexagon = model/service (violet), everything else = an accent-tinted step.
  const LIGHT = matchMedia("(prefers-color-scheme: light)").matches;
  const PAL = LIGHT
    ? { bg: "#ffffff", text: "#1d1d19", line: "#8a897f", amber: "#e2a93b", teal: "#3a9d91", violet: "#8b6fe0" }
    : { bg: "#272823", text: "#ebe9e0", line: "#8f8e84", amber: "#e3b04b", teal: "#4fb5a8", violet: "#a68cf0" };
  const mix = (a, b, t) => "#" + [1, 3, 5].map(i => Math.round(parseInt(a.slice(i, i + 2), 16) * t + parseInt(b.slice(i, i + 2), 16) * (1 - t))
    .toString(16).padStart(2, "0")).join("");
  const OPEN = { "([": "term", "[(": "db", "{{": "hex", "{": "dec" };
  const CLOSE = { "([": "])", "[(": ")]", "[[": "]]", "((": "))", "{{": "}}", "[": "]", "(": ")", "{": "}" };
  // Official icons (static/icons, built by tools/build_icons.py) for labels that name a product. First match wins.
  // Scope: null = every exam; Kubernetes kinds only for CKA; words that are ambiguous outside AWS only for the AWS exams.
  const K8S = ["cka"], AWS = ["sapc03", "aipc01"];
  const ICONS = [
    [/CronJob/, "k8s-cronjob.svg", K8S], [/\bJobs?\b/, "k8s-job.svg", K8S], [/PersistentVolumeClaim|\bPVCs?\b/, "k8s-pvc.svg", K8S],
    [/PersistentVolume\b|\bPVs?\b/, "k8s-pv.svg", K8S], [/StorageClass/, "k8s-sc.svg", K8S], [/ConfigMap/, "k8s-cm.svg", K8S],
    [/\bSecrets?\b/, "k8s-secret.svg", K8S], [/Ingress|Gateway API/, "k8s-ing.svg", K8S], [/\bHPA\b/, "k8s-hpa.svg", K8S],
    [/NetworkPolic/, "k8s-netpol.svg", K8S], [/Namespace/, "k8s-ns.svg", K8S], [/Endpoint/, "k8s-ep.svg", K8S],
    [/DaemonSet/, "k8s-ds.svg", K8S], [/StatefulSet/, "k8s-sts.svg", K8S], [/ServiceAccount/, "k8s-sa.svg", K8S],
    [/ClusterRoleBinding/, "k8s-crb.svg", K8S], [/ClusterRole/, "k8s-c-role.svg", K8S], [/RoleBinding/, "k8s-rb.svg", K8S],
    [/\bRoles?\b|RBAC/, "k8s-role.svg", K8S], [/ReplicaSet/, "k8s-rs.svg", K8S], [/Deployment/, "k8s-deploy.svg", K8S],
    [/\bPods?\b/, "k8s-pod.svg", K8S], [/\bService\b|ClusterIP|NodePort|LoadBalancer/, "k8s-svc.svg", K8S],
    [/etcd/, "k8s-etcd.svg", K8S], [/kube-apiserver|API server/i, "k8s-api.svg", K8S], [/kubelet/, "k8s-kubelet.svg", K8S],
    [/kube-proxy/, "k8s-k-proxy.svg", K8S], [/[Ss]cheduler/, "k8s-sched.svg", K8S], [/controller-manager/, "k8s-c-m.svg", K8S],
    [/[Cc]ontrol plane/, "k8s-control-plane.svg", K8S], [/\bNodes?\b|[Ww]orker node/, "k8s-node.svg", K8S],
    [/[Vv]olume/, "k8s-vol.svg", K8S],
    [/Bedrock|AgentCore/, "aws-bedrock.png"], [/Knowledge Base|Guardrail|Foundation model|\bFM\b/, "aws-bedrock.png", AWS],
    [/SageMaker/, "aws-sagemaker.png"], [/Organizations|\bSCPs?\b|\bOUs?\b/, "aws-organizations.png", AWS],
    [/Control Tower/, "aws-control-tower.png"], [/Macie/, "aws-macie.png"], [/GuardDuty/, "aws-guardduty.png"],
    [/Security Hub/, "aws-security-hub.png"], [/Inspector/, "aws-inspector.png", AWS], [/Transit Gateway/, "aws-transit-gateway.png"],
    [/Direct Connect/, "aws-direct-connect.png"], [/\bECR\b/, "aws-ecr.png"], [/API Gateway/, "aws-api-gateway.svg"],
    [/Athena/, "aws-athena.svg"], [/Aurora|pgvector/, "aws-aurora.svg"], [/\bACM\b|Certificate Manager/, "aws-certificate-manager.svg"],
    [/CloudFormation|StackSets/, "aws-cloudformation.svg"], [/CloudFront/, "aws-cloudfront.svg"], [/CloudTrail/, "aws-cloudtrail.svg"],
    [/CloudWatch/, "aws-cloudwatch.svg"], [/CodePipeline/, "aws-codepipeline.svg"], [/Cognito/, "aws-cognito.svg"],
    [/AWS Config|Config rules?/, "aws-config.svg", AWS], [/DynamoDB/, "aws-dynamodb.svg"], [/\bEC2\b/, "aws-ec2.svg"],
    [/\bECS\b/, "aws-ecs.svg"], [/\bEKS\b/, "aws-eks.svg"], [/\bELB\b|\bALB\b|\bNLB\b|Load Balancer/, "aws-elb.svg", AWS],
    [/EventBridge/, "aws-eventbridge.svg"], [/Fargate/, "aws-fargate.svg"], [/Glacier/, "aws-glacier.svg"], [/\bGlue\b/, "aws-glue.svg"],
    [/\bIAM\b|Identity Center/, "aws-iam.svg"], [/Kinesis/, "aws-kinesis.svg"], [/\bKMS\b/, "aws-kms.svg"],
    [/Lake Formation/, "aws-lake-formation.svg"], [/Lambda/, "aws-lambda.svg"], [/\bRDS\b/, "aws-rds.svg"], [/Route 53/, "aws-route53.svg"],
    [/\bS3\b/, "aws-s3.svg"], [/Secrets Manager/, "aws-secrets-manager.svg"], [/\bSNS\b/, "aws-sns.svg"], [/\bSQS\b/, "aws-sqs.svg"],
    [/Step Functions/, "aws-step-functions.svg"], [/Systems Manager|\bSSM\b/, "aws-systems-manager.svg"], [/\bVPC\b/, "aws-vpc.svg"],
    [/\bWAF\b/, "aws-waf.svg"], [/X-Ray/, "aws-xray.svg"], [/OpenSearch/, "opensearch.svg"], [/PostgreSQL/, "postgresql.svg"],
    [/Claude|Haiku|Sonnet|Opus|Fable/, "claude-icon.svg"], [/Anthropic/, "anthropic-icon.svg"], [/OpenAI|GPT/, "openai-icon.svg"],
    [/\bHCP\b|Terraform\b/, "terraform-icon.svg"], [/HashiCorp/, "hashicorp-icon.svg"], [/\bVault\b/, "vault-icon.svg"],
    [/GitLab|\bRunners?\b/, "gitlab.svg", ["gitlabci"]], [/GitLab/, "gitlab.svg"], [/GitHub/, "github-icon.svg"], [/\bGit\b/, "git-icon.svg"],
    [/Docker|\bdind\b/, "docker-icon.svg"], [/Kubernetes|\bK8s\b|kubectl|kubeadm/, "kubernetes.svg"], [/Helm/i, "helm.svg"],
    [/Prometheus/, "prometheus.svg"], [/Grafana/, "grafana.svg"], [/NGINX|nginx/, "nginx.svg"], [/Python/, "python.svg"], [/Slack/, "slack-icon.svg"],
  ];
  const iconFor = (label) => ICONS.find(([re, , scope]) => (!scope || scope.includes(state.exam)) && re.test(label))?.[1];
  function styled(src) {
    const css = getComputedStyle(document.documentElement);
    const accent = css.getPropertyValue("--accent").trim(), ink = css.getPropertyValue("--accent-ink").trim();
    const sets = { term: new Set(), db: new Set(), hex: new Set(), dec: new Set() };
    const iconised = new Set();
    for (const line of src.split("\n").slice(1))              // node ids sit at a segment start, after an edge or its |label|
      for (const seg of line.split(/-\.->|-->|==>|---/)) {
        const m = seg.replace(/^\s*\|[^|]*\|/, "").match(/^\s*([A-Za-z]\w*)\s*(\(\[|\[\(|\[\[|\(\(|\{\{|\[|\(|\{)/);
        if (!m) continue;
        const [, id, open] = m;
        if (OPEN[open]) sets[OPEN[open]].add(id);
        const rest = seg.slice(seg.indexOf(id + open) + id.length + open.length), end = rest.indexOf(CLOSE[open]);
        const label = end > 0 ? rest.slice(0, end) : "", text = label.replace(/^"(.*)"$/, "$1");   // tutor labels may be "quoted"
        const icon = text && open !== "{" && !/["<>`]/.test(text) && !iconised.has(id) && iconFor(text);
        if (!icon) continue;
        iconised.add(id);                                      // the icon sits above the text inside the same node shape
        src = src.replace(id + open + label + CLOSE[open], `${id}${open}"<img src='/static/icons/${icon}' class='ni' width='36' height='36' style='display:block;margin:2px auto 6px;object-fit:contain;box-sizing:border-box;padding:4px'/>${esc(text)}"${CLOSE[open]}`);
      }
    const tint = (c, t) => `fill:${mix(c, PAL.bg, t)},stroke:${c},stroke-width:1.6px,color:${PAL.text}`;
    const defs = [
      `classDef default ${tint(accent, LIGHT ? .1 : .16)}`,
      `classDef term fill:${accent},stroke:${accent},stroke-width:1.6px,color:${ink},font-weight:600`,
      `classDef dec ${tint(PAL.amber, LIGHT ? .16 : .2)}`,
      `classDef db ${tint(PAL.teal, LIGHT ? .14 : .2)}`,
      `classDef hex ${tint(PAL.violet, LIGHT ? .14 : .22)}`,
      `linkStyle default stroke:${PAL.line},stroke-width:1.6px`,
    ];
    for (const [k, ids] of Object.entries(sets)) if (ids.size) defs.push(`class ${[...ids].join(",")} ${k}`);
    return `${src}\n${defs.join("\n")}`;
  }

  let mermaidP = null, mmdN = 0;
  function mermaidLib() {
    // Served by the portal (the Dockerfile bakes static/vendor/mermaid.min.js into the image); the pinned CDN copy is the fallback.
    return mermaidP ||= loadScript("/static/vendor/mermaid.min.js")
      .catch(() => loadScript(`https://cdn.jsdelivr.net/npm/mermaid@${MERMAID_VERSION}/dist/mermaid.min.js`))
      .then(() => {
        const m = window.mermaid;
        m.initialize({ startOnLoad: false, theme: "base", securityLevel: "loose",   // loose: HTML labels carry the icons (own content, labels escaped)
          themeVariables: { fontFamily: "Geist, system-ui, sans-serif", fontSize: "15px", primaryTextColor: PAL.text,
                            lineColor: PAL.line, edgeLabelBackground: PAL.bg, background: "transparent" },
          flowchart: { htmlLabels: true, useMaxWidth: true, curve: "basis", nodeSpacing: 36, rankSpacing: 46, padding: 14 } });
        return m;
      });
  }
  const MERMAID_VERSION = "11.17.2";                       // keep in step with the Dockerfile ARG
  function loadScript(src) {
    if (window.mermaid) return Promise.resolve();
    return new Promise((ok, bad) => {
      const s = document.createElement("script"); s.src = src;
      s.onload = () => (window.mermaid ? ok() : bad(new Error("no mermaid"))); s.onerror = () => { s.remove(); bad(new Error(src)); };
      document.head.append(s);
    });
  }
  // Tutor Mermaid is model-written from scraped question text, and the renderer runs "loose" so our icon <img> tags work:
  // drop click/call lines, init directives, raw HTML tags and entity codes before drawing. Our own icons are added after this.
  const safeMermaid = (src) => String(src).split("\n").filter(l => !/^\s*(click|call|href|%%\s*\{)/i.test(l)).join("\n")
    .replace(/<\/?[a-z!][^>]*>/gi, " ").replace(/javascript\s*:/gi, "").replace(/#\w{1,10};/g, " ");
  async function drawDiagram(box, src) {
    let lib;
    try { lib = await mermaidLib(); } catch (e) {
      mermaidP = null;                                   // CDN hiccup: let the next diagram retry the import
      box.textContent = "Diagram could not be drawn (offline?).";
      return;
    }
    // Styled first; tutor (model-written) Mermaid may not follow the notes' label rules, so fall back to plain, then to the code.
    let svg = "";
    for (const text of [styled(src), src]) {
      try { ({ svg } = await lib.render(`mmd${++mmdN}`, text)); break; } catch (e) { document.getElementById(`dmmd${mmdN}`)?.remove(); }
    }
    if (!svg) { box.innerHTML = `<pre class="mmd-src">${esc(src)}</pre>`; return; }
    box.innerHTML = svg;
    box.title = "Click to enlarge";
    box.onclick = () => {
      $("#lightbox-img").hidden = true;                     // inline copy: icons and HTML labels survive (an <img> of the SVG drops them)
      $("#lightbox-svg").innerHTML = box.innerHTML; $("#lightbox-svg").hidden = false;
      $("#lightbox").showModal();
    };
  }

  // The study domain a question belongs to: same domain name, else the most keyword hits (banks tagged by source).
  // ponytail: substring keyword count, ties go to the first domain; tag the banks with real domains to drop it.
  const studyCache = {};
  async function topicFor(domain, text) {
    const key = state.exam;
    const r = await (studyCache[key] ||= api("/study").then(s => s.reading || {}).catch(() => { delete studyCache[key]; return {}; }));
    const ds = r.domains || [];
    const same = ds.find(d => d.domain === domain);
    if (same) return same;
    const t = (text || "").toLowerCase();
    let best = null, hits = 0;
    for (const d of ds) {
      const n = (d.keys || []).filter(k => t.includes(k)).length;
      if (n > hits) { best = d; hits = n; }
    }
    return best;
  }
  // Collapsed "Topic" panel under an answered question: video + diagram for its domain.
  async function addTopic(card, text) {
    if (!card || $(".topic-panel", card)) return;
    const d = await topicFor($(".dom", card)?.textContent, text ?? `${$(".qtext", card)?.textContent} ${$(".options", card)?.textContent || ""}`);
    if (!d || !(d.video || d.diagram) || $(".topic-panel", card)) return;
    const det = document.createElement("details"); det.className = "topic-panel";
    det.innerHTML = `<summary>${ic("film-slate")} Topic: ${esc(d.domain)} · ${d.summary ? "key points + " : ""}video + diagram${d.notes?.length ? " + notes" : ""}</summary>`;
    det.addEventListener("toggle", () => {
      if (!det.open || $(".topic", det)) return;
      if (d.summary) det.append(summaryBox(d.summary));
      det.append(topicMedia(d));
      if (d.notes?.length) {
        const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.innerHTML = `${ic("book-open")}Read the ${d.notes.length} study notes`;
        b.onclick = () => { state.studyFocus = d.domain; go("study"); };
        det.append(b);
      }
      drawPending(det);
    });
    card.append(det);
  }
  function studyCard() { go("quiz"); setTimeout(() => act("study"), 50); }
  $("#btn-card").onclick = studyCard;

  // ── Flashcards: due first, then unseen, then the rest; Again / Got it schedule them on the question ladder ──
  const DECK = { cards: [], i: 0, side: "front" };
  function openDeck(d) {
    const due = new Set(state.plan?.cards.due || []), seen = new Set(state.plan?.cards.seen || []);
    const rank = (c) => due.has(c.key) ? 0 : seen.has(c.key) ? 2 : 1;
    DECK.cards = [...d.cards].sort((a, b) => rank(a) - rank(b)); DECK.i = 0;
    $("#deck-title").textContent = `🃏 ${d.domain}`;
    const box = $("#deck"); box.hidden = false; box.scrollIntoView({ block: "start", behavior: "smooth" });
    showCard();
  }
  function showCard(note) {
    const c = DECK.cards[DECK.i], flash = $("#flash");
    DECK.side = "front"; flash.dataset.side = "front";
    $("#deck-again").disabled = $("#deck-ok").disabled = !c;
    if (!c) { $("#flash-front").textContent = "Deck done. Cards you missed come back in 10 minutes, the rest on their schedule."; $("#flash-back").textContent = ""; $("#deck-pos").textContent = `${DECK.cards.length} reviewed`; return; }
    $("#flash-front").textContent = c.q; $("#flash-back").textContent = c.a;
    const due = (state.plan?.cards.due || []).includes(c.key);
    $("#deck-pos").textContent = `${DECK.i + 1} of ${DECK.cards.length}${due ? " · due" : ""}${note ? ` · ${note}` : ""}`;
  }
  function flipCard() {
    if (!DECK.cards[DECK.i]) return;
    DECK.side = DECK.side === "front" ? "back" : "front"; $("#flash").dataset.side = DECK.side;   // CSS rotates the inner card
  }
  async function rateCard(ok) {
    const c = DECK.cards[DECK.i]; if (!c) return;
    $("#deck-again").disabled = $("#deck-ok").disabled = true;
    let note = "";
    try {
      const r = await (await post("/card", { key: c.key, ok })).json();
      note = `last one back in ${r.due_min < 60 ? `${r.due_min} min` : `${Math.round(r.due_min / 1440)} d`}`;
      if (state.plan) { state.plan.cards.due = state.plan.cards.due.filter(k => k !== c.key); if (!state.plan.cards.seen.includes(c.key)) state.plan.cards.seen.push(c.key); }
    } catch (e) { note = "not saved (offline?)"; }
    DECK.i++; showCard(note);
  }
  $("#flash").onclick = flipCard;
  $("#deck-again").onclick = () => rateCard(false);
  $("#deck-ok").onclick = () => rateCard(true);
  $("#deck-close").onclick = () => { $("#deck").hidden = true; };

  // ── Cram sheet: every domain's must-knows, traps and exam tips on one printable page ──
  async function loadCram() {
    const s = await api("/study"); const r = s.reading || {};
    $("#cram-title").textContent = `${cur()?.short || ""} cram sheet`;
    const box = $("#cram-body"); box.innerHTML = "";
    for (const d of r.domains || []) {
      const sm = d.summary || {}, tips = (d.notes || []).map(n => n.tip).filter(Boolean);
      const el = document.createElement("section"); el.className = "dom";
      el.innerHTML = `<h3>${esc(d.domain)}${d.weight ? `<span class="dw">${d.weight}%</span>` : ""}</h3>
        ${sm.tests ? `<p class="tests">${rich(sm.tests)}</p>` : ""}
        ${(sm.must_know || []).length ? `<h4>Must know</h4><ul>${sm.must_know.map(x => `<li>${rich(x)}</li>`).join("")}</ul>` : ""}
        ${(sm.traps || []).length ? `<h4>Traps</h4><ul class="traps">${sm.traps.map(x => `<li>${rich(x)}</li>`).join("")}</ul>` : ""}
        ${tips.length ? `<h4>Exam tips</h4><ul>${tips.map(x => `<li>${rich(x)}</li>`).join("")}</ul>` : ""}`;
      box.append(el);
    }
    if (!box.children.length) box.innerHTML = `<p class="muted">No study notes for this exam yet.</p>`;
  }
  $("#cram-print").onclick = () => print();

  // ── Stats ─────────────────────────────────────────────────────────────────
  async function loadStats() {
    const h = await api("/home");
    $("#stats-summary").textContent = `${h.coverage.seen} of ${h.coverage.total} seen, ${h.coverage.right} right first time. Predicted ${h.score} of 1000, pass ${h.pass}. ${h.verdict.replace(/^[^\w]+/, "")}`;
    const box = $("#stats-bars"); box.innerHTML = "";
    for (const d of h.coverage.domains) {
      const row = document.createElement("div"); row.className = "bar-row";
      row.innerHTML = `<span class="bn">${esc(d.name || "Unclassified")}${d.weight ? ` <span class="muted small">${d.weight}%</span>` : ""}</span>
        <span class="bv">${d.right} right, ${d.seen}/${d.total} seen</span>
        <span class="bar"><i class="right" style="width:${100 * d.right / d.total}%"></i><i class="seen" style="width:${100 * (d.seen - d.right) / d.total}%"></i></span>`;
      box.append(row);
    }
  }
  // Two buttons (quiz + progress view), same two-click confirm. Reset restarts the bank at question 1.
  let resetArmed = 0;
  const resetBtns = $$(".js-reset");
  const setResetLabel = t => resetBtns.forEach(b => ($(".lbl", b) || b).textContent = t);
  resetBtns.forEach(b => b.onclick = async () => {
    if (!resetArmed) { setResetLabel("Confirm reset"); resetArmed = setTimeout(() => { resetArmed = 0; setResetLabel("Reset progress"); }, 5000); return; }
    clearTimeout(resetArmed); resetArmed = 0; setResetLabel("Reset progress");
    go("quiz");
    clearFeed(); state.feedLoaded = state.exam; state.home = null;   // drop stale cards and cached progress
    await act("reset");
    state.home = await api("/home"); renderMode(state.home.progress);
  });

  // ── Feed ──────────────────────────────────────────────────────────────────
  const feed = $("#feed");
  function clearFeed() { feed.innerHTML = ""; state.cards.clear(); state.pendingCard = null; state.feedLoaded = ""; }
  // One question per screen: when the next question (or task) starts, everything already on the feed folds into a History list.
  function archiveFeed() {
    const cards = [...feed.children].filter(c => c.id !== "history" && !c.classList.contains("empty"));
    if (!cards.length) return;
    let h = $("#history", feed);
    if (!h) {
      h = document.createElement("details"); h.id = "history"; h.className = "history"; h.innerHTML = "<summary></summary>";
      feed.prepend(h);
    }
    cards.forEach(c => h.append(c));
    $("summary", h).textContent = `History · ${h.children.length - 1} earlier`;
  }
  function renderMode(p) {
    const bar = $("#mode-bar"); bar.hidden = !p;
    if (!p) return;
    const inTasks = p.mode === "tasks";
    $("#mode-text").textContent = inTasks
      ? `Scenario tasks: ${p.tasks.seen} of ${p.tasks.total} done. Multiple choice: ${p.mcq.seen} of ${p.mcq.total}.`
      : `Multiple choice first: ${p.mcq.seen} of ${p.mcq.total} seen. Scenario tasks (${p.tasks.total}) unlock automatically when the MCQ bank is done.`;
    $$("#mode-bar [data-mode]").forEach(b => b.setAttribute("aria-current", String(b.dataset.mode === p.forced)));
    $("[data-act=next] .lbl").textContent = inTasks ? "Next task" : "Next question";
  }
  $$("#mode-bar [data-mode]").forEach(b => b.onclick = async () => {
    const p = await (await post("/mode", { mode: b.dataset.mode })).json();
    if (state.home) { state.home.progress = p; state.home.mode = p.mode; }
    renderMode(p);
  });
  async function openQuiz() {
    $("#btn-study-feed").hidden = !(cur() && cur().has_study);
    $("[data-act=next] .lbl").textContent = "Next question";
    const h0 = state.home && state.home.key === state.exam ? state.home : await api("/home");
    state.home = h0;
    renderMode(h0.progress);
    renderQuizBar(h0.plan);
    if (state.feedLoaded === state.exam) return;
    state.feedLoaded = state.exam;
    const h = h0;
    if (h.pending && h.pending.question.task) { addCard({ op: "add", id: "pending", kind: "text", html: h.pending.html, buttons: [] }); addTaskCard(h.pending.question); }
    else if (h.pending) addCard({ op: "add", id: "pending", kind: "text", html: h.pending.html, question: h.pending.question, buttons: [] });
    else if (!feed.children.length) feed.innerHTML = `<div class="empty">${ic(h.mode === "tasks" ? "terminal-window" : "lightning")}<p>${h.mode === "tasks" ? "No task yet. Press Next task, read it, then type the commands you would run." : "Nothing on screen yet. Press Next question: due reviews come first, then new questions from your weakest heavy domains."}</p></div>`;
  }
  $$("[data-act]").forEach(b => b.onclick = () => act(b.dataset.act));

  async function act(data) { await run("/act", { data }); }
  async function answer(id, picked, guess) { await run("/answer", { id, picked, guess: !!guess }); }
  async function taskCheck(id, text) { await run("/task", { id, answer: text }, "Grading your commands"); }
  async function taskSelf(id, result) { await run("/task", { id, result }); }

  async function run(path, body, label) {
    if (state.busy) return;
    closePeek();
    setBusy(true, label || (body.data === "study" ? "Writing the study card" : body.data === "report" ? "Preparing the report" : "Working"));
    $(".empty", feed)?.remove();
    state.fresh = body.data === "next" || body.data === "weak" || /^[dq]\|/.test(body.data || "");   // next question: start a clean screen on its first card
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
    } finally { state.fresh = false; setBusy(false); }
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
    if (ev.op === "add" && state.fresh) { state.fresh = false; archiveFeed(); }
    if (ev.op === "add") addCard(ev);
    else if (ev.op === "edit") editCard(ev);
    else if (ev.op === "delete") { state.cards.get(ev.id)?.remove(); state.cards.delete(ev.id); }
    else if (ev.op === "meta") {
      if (ev.metrics) state.metrics = ev.metrics;
      if (ev.graded) markGraded(ev.graded);
      if (ev.task) addTaskCard(ev.task);
      if (ev.revealed) revealSelfMark(ev.revealed);
      if (ev.graded_task) finishTask(ev.graded_task);
    }
    else if (ev.op === "error") addCard({ op: "add", id: `err${Date.now()}`, kind: "text", html: ev.html, buttons: [], error: true });
    if (ev.op === "add" && ev.question) state.cards.get(ev.id)?.scrollIntoView({ block: "start", behavior: "smooth" });   // a new question starts at the top
    else feed.lastElementChild?.scrollIntoView({ block: "end", behavior: "smooth" });
  }

  const KIND = { "📝": "q", "🟢": "ok", "🔴": "bad", "🎓": "tutor", "📊": "stats", "📚": "study", "⏳": "wait", "⚠️": "err", "🆕": "bank", "🗺": "map", "🔄": "bank", "🛠": "taskq", "🧭": "guide", "📘": "solution" };
  function kindOf(html) { const k = [...html.trim()][0]; return KIND[k] || (html.startsWith("⚠") ? "err" : "text"); }

  function addCard(ev) {
    if (ev.question) state.curQ = ev.question.id;      // the question on screen (not "previous")
    let el;
    if (ev.question && !ev.question.task) el = questionCard(ev);
    else if (ev.kind === "mermaid") {                        // tutor diagram drawn here, same style + icons as Study
      el = document.createElement("article"); el.className = "card card-photo card-mmd";
      el.append(lazyDiagram(safeMermaid(ev.src)));
      el.insertAdjacentHTML("beforeend", `<div class="cap html">${tg(ev.html)}</div>`);
      drawPending(el);
    } else if (ev.kind === "photo") {
      el = document.createElement("article"); el.className = "card card-photo";
      el.innerHTML = `<img src="data:image/png;base64,${ev.png}" alt="Diagram"><div class="cap html">${tg(ev.html)}</div>`;
      $("img", el).onclick = () => { $("#lightbox-img").src = $("img", el).src; $("#lightbox-img").hidden = false; $("#lightbox-svg").hidden = true; $("#lightbox").showModal(); };
    } else {
      el = document.createElement("article");
      const k = ev.error ? "err" : kindOf(ev.html);
      el.className = `card card-${k}`;
      if (k === "stats" && state.metrics && /WEEKLY REPORT/.test(ev.html)) {
        const m = state.metrics; state.metrics = null;
        el.innerHTML = `<div class="score-strip"><strong>${m.score}</strong><span>predicted of 1000, pass ${m.pass}<br>${esc(m.verdict)}</span></div>`;
        // Study next: the two weakest domains (first-try accuracy, at least 3 answered), else ones not touched yet
        const doms = Object.entries(m.by_dom || {}).map(([d, [r, n]]) => ({ d, n, acc: n ? r / n : 0 }));
        const tried = doms.filter(x => x.n >= 3).sort((a, b) => a.acc - b.acc);   // weak (< 80%), then untouched, then the rest
        const weak = [...tried.filter(x => x.acc < .8), ...doms.filter(x => !x.n), ...tried.filter(x => x.acc >= .8)].slice(0, 2);
        if (weak.length) {
          const nav = document.createElement("div"); nav.className = "study-next";
          nav.innerHTML = `<span class="muted small">Study next</span>`;
          for (const w of weak) {
            const b = document.createElement("button"); b.type = "button"; b.className = "btn";
            b.innerHTML = `${ic("book-open")}${esc(w.d)}${w.n ? ` · ${Math.round(100 * w.acc)}%` : " · not started"}`;
            b.onclick = () => { state.studyFocus = w.d; go("study"); };
            nav.append(b);
          }
          el.append(nav);
        }
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
      if (!b.data || b.data.startsWith("a|") || b.data.startsWith("s|") || (state.public && b.data.startsWith("e|"))) continue;   // option buttons are the question card
      const btn = document.createElement("button"); btn.type = "button"; btn.className = "btn"; btn.innerHTML = iconize(esc(b.label));
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
    setQid($(".qid", el), q.id, q.tag);
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
    const guess = $(".guess", el);                        // 🤔 Guessing: a right answer still comes back in 10 minutes
    guess.onclick = () => guess.setAttribute("aria-pressed", String(guess.getAttribute("aria-pressed") !== "true"));
    function submit(letters) {
      if (state.busy) return;
      $$(".opt", el).forEach(b => { b.disabled = true; b.setAttribute("aria-pressed", String(letters.includes(b.dataset.l))); });
      sub.hidden = true;
      $(".guess-row", el).hidden = true;
      state.pendingCard = null;
      answer(q.id, letters, guess.getAttribute("aria-pressed") === "true");
    }
    state.pendingCard = { el, q, picked, submit };
    return el;
  }

  // Previous-question viewer: the ids you answered (per exam, kept in this browser) and a read-only card for each.
  const histKey = () => `hist:${state.exam}`;
  const hist = () => JSON.parse(localStorage.getItem(histKey()) || "[]");
  function remember(g) {
    const h = hist().filter(x => x.id !== g.id);
    h.push({ id: g.id, picked: g.picked, right: g.right, dom: g.dom || "" });
    localStorage.setItem(histKey(), JSON.stringify(h.slice(-300)));
  }
  const peek = { list: [], i: 0 };
  async function showPeek() {
    const x = peek.list[peek.i];
    $("#peek-pos").textContent = `${peek.list.length - peek.i} back · ${peek.list.length} answered`;
    $("#peek-older").disabled = peek.i <= 0; $("#peek-newer").disabled = peek.i >= peek.list.length - 1;
    const box = $("#peek-card"); box.innerHTML = "";
    const r = await (await api(`/question/${encodeURIComponent(x.id)}`));
    if (!r.options) { box.innerHTML = `<p class="muted">That question is not available here.</p>`; return; }
    const el = $("#t-question").content.firstElementChild.cloneNode(true);
    const right = r.correct.length === x.picked.length && r.correct.every(l => x.picked.includes(l));
    el.classList.add("card-review", right ? "card-ok" : "card-bad");
    setQid($(".qid", el), r.id, r.tag); $(".dom", el).textContent = r.domain; $(".qtext", el).textContent = r.question;
    $(".submit-row", el).remove(); $(".guess-row", el).remove();
    const v = document.createElement("p"); v.className = `verdict-line ${right ? "ok" : "bad"}`;
    v.textContent = right ? `Correct, ${r.correct.join(",")}` : `Wrong, you picked ${x.picked.join(",")}, answer ${r.correct.join(",")}`;
    $(".qtext", el).after(v);
    for (const o of r.options) {
      const b = optionButton(o); b.disabled = true;
      if (r.correct.includes(o.l)) b.classList.add("is-correct"); else if (x.picked.includes(o.l)) b.classList.add("is-wrong");
      $(".options", el).append(b);
    }
    const ex = document.createElement("div"); ex.className = "expl"; ex.textContent = r.explanation; el.append(ex);
    box.append(el);
    addDecode(el, r.id, true);
  }
  function closePeek() { $("#peek").hidden = true; feed.hidden = false; }
  function peekList() {
    const wrong = $("#peek-wrong").checked, dom = $("#peek-dom").value;
    return hist().filter(x => x.id !== state.curQ && (!wrong || x.right === false) && (!dom || x.dom === dom));   // the question on screen is not "previous"
  }
  function refilter() {
    peek.list = peekList(); peek.i = peek.list.length - 1;
    if (!peek.list.length) { $("#peek-card").innerHTML = `<p class="muted">Nothing matches that filter.</p>`; $("#peek-pos").textContent = ""; $("#peek-older").disabled = $("#peek-newer").disabled = true; return; }
    showPeek();
  }
  $("#peek-wrong").onchange = $("#peek-dom").onchange = refilter;
  $("#btn-prev-q").onclick = () => {
    const doms = [...new Set(hist().map(x => x.dom).filter(Boolean))].sort();
    $("#peek-dom").innerHTML = `<option value="">All domains</option>` + doms.map(d => `<option>${esc(d)}</option>`).join("");
    $("#peek-wrong").checked = false;
    peek.list = peekList();
    if (!peek.list.length) { apply({ op: "error", html: "No earlier answered question yet." }); return; }
    peek.i = peek.list.length - 1;
    feed.hidden = true; $("#peek").hidden = false; showPeek(); window.scrollTo({ top: 0 });
  };
  $("#peek-older").onclick = () => { if (peek.i > 0) { peek.i--; showPeek(); } };
  $("#peek-newer").onclick = () => { if (peek.i < peek.list.length - 1) { peek.i++; showPeek(); } };
  $("#peek-close").onclick = closePeek;

  function markGraded(g) {
    const el = [...state.cards.values()].reverse().find(c => c.querySelector(".qid")?.textContent === g.id);
    remember({ ...g, dom: el?.querySelector(".dom")?.textContent || "" });
    if (!el) return;
    $$(".opt", el).forEach(b => {
      if (g.correct.includes(b.dataset.l)) b.classList.add("is-correct");
      else if (g.picked.includes(b.dataset.l)) b.classList.add("is-wrong");
    });
    addTopic(el);
    addDecode(el, g.id, state.public);
    refreshPlan();
  }

  // ── Decoder: clue words in the stem, one line per option, the principle, a source link (web.py decode) ──
  async function addDecode(card, qid, cachedOnly) {
    if (!card || $(".decode", card)) return;
    const box = document.createElement("div"); box.className = "decode";
    box.innerHTML = cachedOnly ? "" : `<span class="wait">${ic("magnifying-glass", "ic-accent")}Decoding the question…</span>`;
    card.append(box);
    for (let i = 0; i < 45; i++) {                         // prefetched when the question was shown; usually ready at once
      let r;
      try { r = await fetch(`/api/${state.exam}/decode/${encodeURIComponent(qid)}${cachedOnly ? "?cached=1" : ""}`); } catch (e) { break; }
      if (r.status === 200) return renderDecode(card, box, await r.json());
      if (r.status !== 202 || cachedOnly) break;
      await new Promise(ok => setTimeout(ok, 2000));
    }
    box.remove();
  }
  function renderDecode(card, box, d) {
    const qt = $(".qtext", card);
    let html = esc(qt.textContent);
    for (const c of d.clues || []) { const e = esc(String(c).trim()); if (e && html.includes(e)) html = html.split(e).join(`<mark>${e}</mark>`); }
    qt.innerHTML = html;
    for (const o of d.options || []) {
      const b = $(`.opt[data-l="${esc(String(o.letter))}"]`, card);
      if (b && !$(".why", b)) b.insertAdjacentHTML("beforeend", `<span class="why">${esc(o.why)}</span>`);
    }
    box.innerHTML = (d.principle ? `<p class="principle"><b>${ic("magnifying-glass", "ic-accent")} Principle</b> ${esc(d.principle)}</p>` : "") +
      (d.source?.url ? `<p class="src small">Source: <a href="${esc(d.source.url)}" target="_blank" rel="noopener">${esc(d.source.title || "page")}</a></p>` : "");
  }

  // Task exams: the task text was streamed as a normal card; this adds the answer box under it.
  function addTaskCard(q) {
    const el = $("#t-task").content.firstElementChild.cloneNode(true);
    setQid($(".qid", el), q.id, q.tag); $(".dom", el).textContent = q.domain;
    $(".task-text", el).remove();                                   // the task text is already on the card above
    const ta = $(".answer", el);
    const btns = Object.fromEntries($$("[data-task]", el).map(b => [b.dataset.task, b]));
    btns.check.onclick = () => { if (!ta.value.trim()) { ta.focus(); return; } lock(); taskCheck(q.id, ta.value); };
    btns.reveal.onclick = () => { btns.reveal.disabled = true; taskSelf(q.id, "reveal"); };
    btns.ok.onclick = () => { lock(); taskSelf(q.id, "ok"); };
    btns.bad.onclick = () => { lock(); taskSelf(q.id, "bad"); };
    function lock() { ta.readOnly = true; $$("[data-task]", el).forEach(b => b.disabled = true); }
    el.dataset.task = q.id;
    feed.append(el); state.cards.set(`task:${q.id}`, el);
    state.pendingCard = { el, q, task: true };
    ta.focus();
  }
  function revealSelfMark(qid) {
    const el = state.cards.get(`task:${qid}`); if (!el) return;
    $("[data-task=check]", el).disabled = true;
    $("[data-task=ok]", el).hidden = false; $("[data-task=bad]", el).hidden = false;
  }
  function finishTask(g) {
    const el = state.cards.get(`task:${g.id}`); if (!el) return;
    el.classList.add(g.right ? "card-ok" : "card-bad");
    $$("[data-task]", el).forEach(b => b.disabled = true);
    $(".answer", el).readOnly = true;
    state.pendingCard = null;
    addTopic(el, "");
  }

  // Keyboard: A-H pick, Enter submits a multi-select or asks for the next question (not while typing a task answer).
  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, dialog")) return;
    const k = e.key.toUpperCase();
    if (state.view === "exam" && EX.run && !EX.run.done) {
      if (!EX.run.tasks && /^[A-H]$/.test(k)) { $(`#exam-q .opt[data-l="${k}"]`)?.click(); e.preventDefault(); }
      else if (e.key === "ArrowRight" || (!EX.run.tasks && e.key === "Enter")) $("#exam-next").click();
      else if (e.key === "ArrowLeft") $("#exam-prev").click();
      return;
    }
    if (state.view === "study" && !$("#deck").hidden) {    // flashcards: Space flips, arrows rate, Esc closes
      if (e.key === " " || e.key === "Enter") { flipCard(); e.preventDefault(); }
      else if (e.key === "ArrowLeft") rateCard(false);
      else if (e.key === "ArrowRight") rateCard(true);
      else if (e.key === "Escape") $("#deck").hidden = true;
      return;
    }
    if (state.view !== "quiz") return;
    const p = state.pendingCard;
    if (p && p.task) return;
    if (p && p.el.isConnected && e.key === "?") { $(".guess", p.el)?.click(); e.preventDefault(); }
    else if (p && p.el.isConnected && /^[A-H]$/.test(k)) { $(`.opt[data-l="${k}"]`, p.el)?.click(); e.preventDefault(); }
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
    const modeTasks = !!(e.tasks && e.mode === "tasks");
    const unit = modeTasks ? "tasks" : "questions";
    $("#exam-blurb").textContent = modeTasks
      ? "Like the real sitting: hands-on tasks, a countdown at the real pace, no feedback until you submit. Type the commands or YAML you would run; Claude grades each task against the reference solution."
      : "Like the real sitting: no feedback until you submit, a countdown at the real pace, scored on the 1000 scale.";
    const box = $("#exam-sizes"); box.innerHTML = "";
    const pace = (n) => Math.max(5, Math.round(e.exam_minutes * n / e.exam_n));
    const sizes = modeTasks ? [[e.exam_n, e.exam_minutes, "Full exam", true], [8, pace(8), "Half exam", false], [3, pace(3), "Warm-up", false]]
                          : [[e.exam_n, e.exam_minutes, "Full exam", true], [25, pace(25), "Sprint", false], [10, pace(10), "Quick check", false]];
    for (const [n, min, label, primary] of sizes) {
      if (n > e.total) continue;
      const b = document.createElement("button"); b.type = "button"; b.className = `size${primary ? " primary" : ""}`;
      b.innerHTML = `<b>${n} ${unit}</b><span>${label}, ${min} min</span>`;
      b.onclick = () => startExam(n);
      box.append(b);
    }
  }
  async function startExam(n) {
    const r = await (await post("/exam/start", { n })).json();
    EX.run = { questions: r.questions, minutes: r.minutes, started: Date.now(), answers: {}, done: false, tasks: !!r.tasks };
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
  const answered = (q) => (EX.run.answers[q.id] || []).some(a => a && a.trim());
  function renderExamNav() {
    const nav = $("#exam-nav"); nav.innerHTML = "";
    EX.run.questions.forEach((q, i) => {
      const b = document.createElement("button"); b.type = "button"; b.textContent = i + 1;
      b.className = answered(q) ? "done" : "";
      b.setAttribute("aria-current", String(i === EX.cur));
      b.onclick = () => { EX.cur = i; renderExamNav(); renderExamQ(); };
      nav.append(b);
    });
    const n = EX.run.questions.filter(answered).length;
    $("#exam-progress").textContent = `${n} of ${EX.run.questions.length} answered`;
    $("#exam-prev").disabled = EX.cur === 0;
    $("#exam-next").disabled = EX.cur === EX.run.questions.length - 1;
  }
  function renderExamQ() {
    const q = EX.run.questions[EX.cur];
    const box = $("#exam-q"); box.innerHTML = "";
    if (q.task) {
      const el = $("#t-task").content.firstElementChild.cloneNode(true);
      setQid($(".qid", el), `${EX.cur + 1} of ${EX.run.questions.length}`, q.tag); $(".dom", el).textContent = q.domain;
      $(".task-text", el).textContent = q.question;
      $(".actions", el).remove();
      const ta = $(".answer", el); ta.value = (EX.run.answers[q.id] || [""])[0];
      ta.oninput = () => { EX.run.answers[q.id] = [ta.value]; saveExam(); renderExamNav(); };
      box.append(el); return;
    }
    const el = $("#t-question").content.firstElementChild.cloneNode(true);
    setQid($(".qid", el), `${EX.cur + 1} of ${EX.run.questions.length}`, q.tag);
    $(".dom", el).textContent = q.multi ? "Select all that apply" : "";
    $(".qtext", el).textContent = q.question;
    $(".submit-row", el).remove(); $(".guess-row", el).remove();
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
    box.append(el);
  }
  $("#exam-prev").onclick = () => { if (EX.cur > 0) { EX.cur--; renderExamNav(); renderExamQ(); } };
  $("#exam-next").onclick = () => { if (EX.cur < EX.run.questions.length - 1) { EX.cur++; renderExamNav(); renderExamQ(); } };
  let submitArmed = 0;
  $("#exam-submit").onclick = () => {
    const blank = EX.run.questions.length - EX.run.questions.filter(answered).length;
    if (!submitArmed && blank) { $("#exam-submit").textContent = `Submit with ${blank} blank`; submitArmed = setTimeout(() => { submitArmed = 0; $("#exam-submit").textContent = "Submit exam"; }, 5000); return; }
    clearTimeout(submitArmed); submitArmed = 0; $("#exam-submit").textContent = "Submit exam";
    submitExam(false);
  };
  async function submitExam(timedOut) {
    if (!EX.run || EX.run.done) return;
    EX.run.done = true; clearInterval(EX.timer);
    const answers = {};
    for (const q of EX.run.questions) answers[q.id] = EX.run.answers[q.id] || [];
    if (EX.run.tasks) { $("#exam-submit").disabled = true; $("#exam-submit").textContent = "Grading with Claude, this takes a minute per task"; }
    const res = await (await post("/exam/submit", { answers, minutes_used: (Date.now() - EX.run.started) / 60000 })).json();
    localStorage.removeItem(EX.key());
    $("#exam-submit").disabled = false; $("#exam-submit").textContent = "Submit exam";
    showExamResult(res, timedOut);
  }
  function showExamResult(res, timedOut) {
    $("#exam-setup").hidden = true; $("#exam-run").hidden = true; $("#exam-result").hidden = false;
    $("#res-score").textContent = res.score;
    $("#res-verdict").textContent = `${res.passed ? "Pass" : "Below the pass mark"} at ${res.pass}. ${res.right} of ${res.n} ${res.tasks ? "tasks passed" : "right"} in ${res.minutes} min${timedOut ? ", time ran out" : ""}.`;
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
      setQid($(".qid", el), r.id, r.tag); $(".dom", el).textContent = r.domain; $(".qtext", el).textContent = r.question;
      $(".submit-row", el).remove(); $(".guess-row", el).remove();
      const v = document.createElement("p"); v.className = `verdict-line ${r.right ? "ok" : "bad"}`;
      if (r.task) {
        v.textContent = `Score ${r.score}/100, ${r.verdict}`;
        $(".qtext", el).after(v);
        $(".options", el).remove();
        el.insertAdjacentHTML("beforeend", `<div class="review-answer">${esc(r.answer || "(blank)")}</div><div class="expl">${esc(r.feedback)}</div>` +
          (r.commands ? `<div class="html card-guide" style="margin-top:.8rem;padding:.6rem .8rem;border-radius:10px"><b>Command guideline</b><pre>${esc(r.commands)}</pre></div>` : "") +
          `<details style="margin-top:.6rem"><summary class="muted">Reference solution</summary><pre class="review-answer">${esc(r.explanation)}</pre></details>`);
        list.append(el); addTopic(el); continue;
      }
      v.textContent = r.right ? `Correct, ${r.correct.join(",")}` : r.picked.length ? `Wrong, you picked ${r.picked.join(",")}, answer ${r.correct.join(",")}` : `Blank, answer ${r.correct.join(",")}`;
      $(".qtext", el).after(v);
      const opts = $(".options", el);
      for (const o of r.options) {
        const b = optionButton(o); b.disabled = true;
        if (r.correct.includes(o.l)) b.classList.add("is-correct"); else if (r.picked.includes(o.l)) b.classList.add("is-wrong");
        opts.append(b);
      }
      const ex = document.createElement("div"); ex.className = "expl"; ex.textContent = r.explanation; el.append(ex);
      const a = document.createElement("div"); a.className = "actions";
      const b = document.createElement("button"); b.type = "button"; b.className = "btn"; b.innerHTML = `${ic("graduation-cap")}Explain more with a diagram`;
      b.onclick = () => { go("quiz"); setTimeout(() => act(`e|${r.id}|${r.picked.join(",")}`), 50); };
      if (!state.public) a.append(b);
      el.append(a);
      list.append(el); addTopic(el); addDecode(el, r.id, true);
    }
    window.scrollTo({ top: 0 });
  }
  $("#res-again").onclick = () => { EX.run = null; loadExam(); };

  // ── Telegram HTML -> page HTML ────────────────────────────────────────────
  // The bots speak Telegram's emoji vocabulary; the page draws the same meaning with one icon family (Phosphor).
  const ic = (name, cls) => `<i class="ph ph-${name}${cls ? " " + cls : ""}" aria-hidden="true"></i>`;
  const EMOJI = { "🟢": ["check-circle", "ic-ok"], "🔴": ["x-circle", "ic-bad"], "📝": ["note-pencil", "ic-accent"], "🎓": ["graduation-cap", "ic-accent"],
    "📊": ["chart-bar", "ic-accent"], "📚": ["books", "ic-accent"], "⏳": ["hourglass-medium"], "⚠️": ["warning", "ic-warn"], "⚠": ["warning", "ic-warn"],
    "🆕": ["sparkle", "ic-accent"], "🎯": ["target", "ic-accent"], "📅": ["calendar-blank"], "🔄": ["arrow-counter-clockwise"], "◀️": ["caret-left"],
    "➡️": ["arrow-right"], "▶️": ["caret-right"], "✅": ["check", "ic-ok"], "☑": ["check-square"], "🛠": ["wrench", "ic-accent"], "🧭": ["compass", "ic-accent"],
    "📘": ["book-open-text", "ic-accent"], "🗺": ["map-trifold"], "🎬": ["film-slate"], "📖": ["book-open"], "🔀": ["shuffle"], "🪪": ["identification-card"],
    "💡": ["lightbulb", "ic-warn"], "🔍": ["magnifying-glass"], "📌": ["push-pin", "ic-accent"], "🔁": ["arrows-clockwise"], "⚪": ["circle"], "🟡": ["warning-circle", "ic-warn"],
    "❌": ["x", "ic-bad"], "🤔": ["dice-three"], "🖨": ["printer"], "🃏": ["cards"], "🗒": ["notepad"] };
  const EMOJI_RX = new RegExp(Object.keys(EMOJI).sort((a, b) => b.length - a.length).map(e => e.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "g");
  const iconize = (html) => html.replace(EMOJI_RX, m => ic(EMOJI[m][0], EMOJI[m][1]));
  function tg(html) {
    return iconize((html || "").replace(/<tg-spoiler>/g, '<span class="spoiler">').replace(/<\/tg-spoiler>/g, "</span>")
      .replace(/<blockquote expandable>/g, "<blockquote>").replace(/<a /g, '<a target="_blank" rel="noopener" '));
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }

  $$("[data-view]").forEach(a => a.addEventListener("click", (e) => { e.preventDefault(); go(a.dataset.view); }));
  init();
})();
