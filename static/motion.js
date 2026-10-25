/* Exam Portal revamp 2026-10-25: motion + "show me how this number is made"
   Additive only: watches the elements app.js already fills, never touches app.js or its data. */
(() => {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s) => document.querySelector(s);
  let chip = null;   // "Updated … ago" label, created below; declared first because the counters call tick()

  // 1. Headline: split into words that rise in one after another.
  const title = $("#home-title");
  if (title) {
    let busy = false;
    const split = () => {
      const t = title.textContent;
      if (busy || !t.trim() || title.dataset.split === t) return;
      busy = true; title.dataset.split = t; title.textContent = "";
      t.split(/\s+/).forEach((w, i) => {
        const s = document.createElement("span");
        s.className = "w"; s.textContent = w; s.style.setProperty("--i", i);
        title.append(s, " ");
      });
      busy = false;
    };
    new MutationObserver(split).observe(title, { childList: true, characterData: true, subtree: true });
    split();
  }

  // 2. Numbers count up from what was on screen to the new value (first integer only, so "12 / 340" keeps its tail).
  const counters = ["#ring-score", "#f-seen", "#f-right", "#f-week", "#ps-due", "#ps-new", "#ps-done", "#ps-days"];
  const stamp = { t: Date.now() };
  counters.forEach((sel) => {
    const el = $(sel); if (!el) return;
    let shown = null, mine = null, raf = 0;   // mine = the last text this counter wrote; observers fire async, so a flag can't tell our writes from app.js's
    const run = () => {
      if (el.textContent === mine) return;
      cancelAnimationFrame(raf);
      const text = el.textContent, m = text.match(/\d+/);
      if (!m) { shown = null; return; }
      const to = +m[0], from = shown ?? 0, head = text.slice(0, m.index), tail = text.slice(m.index + m[0].length);
      shown = to; stamp.t = Date.now(); tick();
      if (still || from === to) return;
      const t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / 900), v = Math.round(from + (to - from) * (1 - Math.pow(1 - k, 3)));
        mine = head + v + tail; el.textContent = mine;
        if (k < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    };
    new MutationObserver(run).observe(el, { childList: true, characterData: true, subtree: true });
    run();
  });

  // 3. Dashboard habits: click a number to see how it is made, and each panel says when it was last refreshed.
  const HOW = {
    "f-seen": "Questions you have answered at least once, out of every question in this exam's bank.",
    "f-right": "Questions you answered correctly the first time you saw them.",
    "f-week": "Correct answers out of all answers you gave in the last 7 days.",
    "ring-score": "Predicted exam score from your answers so far, weighted by domain. The pass mark sits beside it.",
  };
  const ring = $(".hero-ring");
  if (ring) {
    chip = document.createElement("p"); chip.className = "fresh"; chip.setAttribute("role", "status");
    chip.innerHTML = '<i aria-hidden="true"></i><span></span>'; ring.append(chip);
  }
  function tick() {
    if (!chip) return;
    const s = Math.round((Date.now() - stamp.t) / 1000);
    chip.lastChild.textContent = s < 5 ? "Updated just now" : s < 90 ? `Updated ${s}s ago` : `Updated ${Math.round(s / 60)} min ago`;
  }
  setInterval(tick, 5000);

  const note = document.createElement("div");
  note.className = "how"; note.hidden = true; note.setAttribute("role", "note");
  (ring || document.body).append(note);
  const open = (id, host) => {
    if (!HOW[id]) return;
    const same = !note.hidden && note.dataset.id === id;
    note.hidden = same; note.dataset.id = id;
    document.querySelectorAll("[data-how]").forEach((h) => h.setAttribute("aria-expanded", "false"));
    if (same) return;
    note.textContent = HOW[id]; host.setAttribute("aria-expanded", "true");
  };
  [["f-seen"], ["f-right"], ["f-week"], ["ring-score"]].forEach(([id]) => {
    const dd = document.getElementById(id); if (!dd) return;
    const host = id === "ring-score" ? dd.closest(".ring-label") || dd : dd.parentElement;
    host.dataset.how = id; host.tabIndex = 0; host.setAttribute("role", "button"); host.setAttribute("aria-expanded", "false");
    host.setAttribute("aria-label", "How is this calculated?");
    host.addEventListener("click", () => open(id, host));
    host.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(id, host); } });
  });

  // 4. Light / dark switch: the head script already set html[data-theme]; this flips it, remembers it, and follows the OS until you choose.
  const tbtn = document.getElementById("theme-toggle");
  if (tbtn) {
    const root = document.documentElement;
    const paint = () => tbtn.setAttribute("aria-label", root.dataset.theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
    const set = (t, keep) => { root.dataset.theme = t; if (keep) { try { localStorage.setItem("ep-theme", t); } catch (e) {} } paint(); };
    tbtn.addEventListener("click", () => set(root.dataset.theme === "dark" ? "light" : "dark", true));
    matchMedia("(prefers-color-scheme: light)").addEventListener("change", (e) => {
      let saved = null; try { saved = localStorage.getItem("ep-theme"); } catch (x) {}
      if (!saved) set(e.matches ? "light" : "dark", false);
    });
    paint();
  }
})();

/* Revamp part 2 (2026-10-25): every other view. Same rules as above: additive, observes what app.js writes, never edits app.js's text.
   Each element I write is either my own, or compared against the last value I wrote (observers fire asynchronously). */
(() => {
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, el = document) => el.querySelector(s);
  const watch = (el, fn, opts, now = true) => { if (!el) return; new MutationObserver(fn).observe(el, opts || { childList: true, characterData: true, subtree: true }); if (now) fn(); };
  const set = (el, k, v) => el && el.style.setProperty(k, v);
  const afterPaint = (fn) => (still ? fn() : requestAnimationFrame(() => requestAnimationFrame(fn)));   // lets a 0 -> value transition actually run

  // 1. A thin accent line sweeps under the header on every view change.
  const route = document.createElement("div");
  route.id = "route-bar"; route.setAttribute("aria-hidden", "true"); document.body.append(route);
  addEventListener("hashchange", () => {
    document.documentElement.classList.add("navd");   // after the first move, content waits for the curtain
    route.classList.remove("go"); void route.offsetWidth; route.classList.add("go");
  });
  const fonts = document.createElement("link");       // the new type system: Instrument Serif (display), Hanken Grotesk (text), DM Mono (labels)
  fonts.rel = "stylesheet";
  fonts.href = "https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Hanken+Grotesk:wght@400;500;600;700&family=Instrument+Serif:ital@0;1&display=swap";
  document.head.append(fonts);

  // 2. Staggered entrance: number the children of every list app.js (re)builds; CSS turns --n into a delay.
  ["#study-start", "#study-domains", "#study-playbook", "#stats-bars", "#res-domains", "#exam-sizes", "#pick-grid", "#cram-body", "#res-list", "#board-table tbody"].forEach((s) => {
    const box = $(s);
    watch(box, () => [...box.children].forEach((c, i) => set(c, "--n", Math.min(i, 12))), { childList: true });
  });

  // 3. Count-up for my own elements (they count from 0 each time the numbers load).
  function countTo(el, to) {
    cancelAnimationFrame(el._raf);
    if (still) { el.textContent = to; return; }
    const t0 = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - t0) / 1000);
      el.textContent = Math.round(to * (1 - Math.pow(1 - k, 3)));
      if (k < 1) el._raf = requestAnimationFrame(step);
    };
    el._raf = requestAnimationFrame(step);
  }

  // 4. "Updated … ago" on every panel, and "click the number to see how it is made".
  const chips = [];
  const say = (c) => {
    const s = Math.round((Date.now() - c.t) / 1000);
    c.el.lastChild.textContent = s < 5 ? "Updated just now" : s < 90 ? `Updated ${s}s ago` : `Updated ${Math.round(s / 60)} min ago`;
  };
  setInterval(() => chips.forEach(say), 5000);
  function fresh(place, target) {
    if (!target) return;
    const el = document.createElement("p");
    el.className = "fresh"; el.hidden = true; el.setAttribute("role", "status"); el.innerHTML = '<i aria-hidden="true"></i><span></span>';
    place(el);
    const c = { el, t: Date.now() }; chips.push(c);
    watch(target, () => { c.t = Date.now(); el.hidden = false; say(c); }, null, false);   // only real data refreshes the stamp
  }
  function howTo(pairs, place) {
    const note = document.createElement("div");
    note.className = "how"; note.hidden = true; note.setAttribute("role", "note"); place(note);
    pairs.forEach(([host, text]) => {
      host.dataset.explain = "1"; host.setAttribute("aria-expanded", "false");
      if (host.tagName !== "BUTTON") { host.tabIndex = 0; host.setAttribute("role", "button"); }
      const open = () => {
        const same = !note.hidden && note.dataset.for === text;
        pairs.forEach(([h]) => h.setAttribute("aria-expanded", "false"));
        note.hidden = same; note.dataset.for = text;
        if (!same) { note.textContent = text; host.setAttribute("aria-expanded", "true"); }
      };
      host.addEventListener("click", open);
      host.addEventListener("keydown", (e) => { if (host.tagName !== "BUTTON" && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); open(); } });
    });
  }

  // 5. Stats: the summary sentence app.js writes becomes four live tiles (numbers count up, bars fill).
  const sum = $("#stats-summary");
  if (sum) {
    const view = $("#view-stats");
    const tiles = document.createElement("div");
    tiles.className = "tiles"; tiles.hidden = true;
    const defs = [["seen", "Seen", "Questions you have answered at least once, out of every question in this exam's bank."],
                  ["right", "Right first time", "Questions you answered correctly the first time you saw them."],
                  ["pred", "Predicted score", "Predicted exam score from your answers so far, weighted by domain. The pass mark sits beside it."],
                  ["pass", "Pass mark", "The score you need to pass this exam, on the same 1000 scale as the predicted score."]];
    tiles.innerHTML = defs.map(([id, k]) => `<button type="button" class="tile" data-t="${id}"><span class="tile-k">${k}</span><strong class="tile-v">0</strong><small class="tile-s"></small><span class="tile-bar"><b></b>${id === "pred" ? '<s class="tick"></s>' : ""}</span></button>`).join("");
    const pill = document.createElement("p"); pill.className = "readiness"; pill.hidden = true;
    sum.before(tiles); sum.after(pill);
    const legend = document.createElement("p"); legend.className = "legend";
    legend.innerHTML = '<span><i class="sw sw-right"></i>Right first time</span><span><i class="sw sw-seen"></i>Seen, not right first time</span>';
    $("#stats-bars").before(legend);
    howTo(defs.map(([id, , text]) => [$(`.tile[data-t="${id}"]`, tiles), text]), (n) => tiles.after(n));
    fresh((el) => legend.before(el), $("#stats-bars"));
    const RX = /^(\d+) of (\d+) seen, (\d+) right first time\. Predicted (\d+) of 1000, pass (\d+)\.\s*(.*)$/s;
    watch(sum, () => {
      const m = RX.exec(sum.textContent.trim());
      view.classList.toggle("has-tiles", !!m); tiles.hidden = pill.hidden = !m;
      if (!m) return;
      const [seen, total, right, pred, pass] = m.slice(1, 6).map(Number);
      const pct = (a, b) => (b ? Math.min(1, a / b) : 0);
      const rows = { seen: [seen, `of ${total} in the bank`, pct(seen, total)], right: [right, `${seen ? Math.round(100 * right / seen) : 0}% of those you have seen`, pct(right, total)],
                     pred: [pred, "of 1000", pct(pred, 1000)], pass: [pass, "on the 1000 scale", pct(pass, 1000)] };
      for (const [id, [v, sub, p]] of Object.entries(rows)) {
        const t = $(`.tile[data-t="${id}"]`, tiles);
        $(".tile-s", t).textContent = sub; countTo($(".tile-v", t), v);
        set(t, "--p", 0); afterPaint(() => set(t, "--p", p));
      }
      set($(".tick", tiles), "--tick", pct(pass, 1000));
      pill.textContent = m[6] ? `Exam readiness: ${m[6]}` : "";
      pill.hidden = !m[6];
    });
  }

  // 6. Exam result: big score counts up, a meter shows it against the pass mark.
  const rs = $("#res-score"), rv = $("#res-verdict"), result = $("#exam-result");
  if (rs && rv && result) {
    const strip = $(".score-strip", result);
    const meter = document.createElement("div");
    meter.className = "meter"; meter.innerHTML = '<span class="meter-track"><i class="meter-fill"></i><b class="meter-pass"></b></span><span class="meter-l">0</span><span class="meter-r">1000</span>';
    strip.after(meter);
    howTo([[strip, "Your mock exam score on the 1000 scale. Whether it is a pass is judged against the pass mark shown in the line beside it and on the meter."]], (n) => meter.after(n));
    fresh((el) => strip.after(el), rv);
    let mine = null, raf = 0;
    watch(rs, () => {
      if (rs.textContent === mine) return;             // my own count-up writes
      const to = parseInt(rs.textContent, 10);
      if (Number.isNaN(to)) return;
      cancelAnimationFrame(raf);
      set(meter, "--p", 0); afterPaint(() => set(meter, "--p", Math.min(to, 1000) / 1000));
      if (still || to === 0) return;
      const t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / 1100);
        mine = String(Math.round(to * (1 - Math.pow(1 - k, 3)))); rs.textContent = mine;
        if (k < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    });
    watch(rv, () => {
      const m = /at (\d+)\./.exec(rv.textContent);
      if (m) set(meter, "--pass", +m[1] / 1000);
      result.dataset.pass = /^Pass\b/.test(rv.textContent) ? "1" : rv.textContent ? "0" : "";
    });
  }

  // 7. Exam clock urgency (last 5 minutes warns, last minute pulses faster) and answered-progress fill.
  const clock = $("#exam-clock"), ebar = $(".exam-bar");
  if (clock && ebar) {
    watch(clock, () => {
      const m = /^(\d+):(\d+)$/.exec(clock.textContent.trim());
      if (!m) return;
      const s = +m[1] * 60 + +m[2];
      if (s < 60) ebar.dataset.urgent = "crit"; else if (s < 300) ebar.dataset.urgent = "warn"; else delete ebar.dataset.urgent;
    });
    const prog = $("#exam-progress");
    watch(prog, () => { const m = /(\d+) of (\d+)/.exec(prog.textContent); if (m && +m[2]) set(ebar, "--p", +m[1] / +m[2]); });
  }

  // 8. Quiz bar: "today X of Y" fills a line along its bottom edge; its panel says when it was refreshed.
  const qb = $("#quiz-bar-text");
  if (qb) {
    watch(qb, () => { const m = /today (\d+) of (\d+)/.exec(qb.textContent); if (m && +m[2]) set(qb.parentElement, "--p", Math.min(1, +m[1] / +m[2])); });
    fresh((el) => qb.after(el), qb);
  }

  // 9. Flashcards: progress line along the top of the deck, and each new card slides in.
  const pos = $("#deck-pos"), deck = $("#deck"), flash = $("#flash");
  if (pos && deck) watch(pos, () => {
    const m = /^(\d+) of (\d+)/.exec(pos.textContent);
    if (m && +m[2]) set(deck, "--p", +m[1] / +m[2]); else if (/reviewed/.test(pos.textContent)) set(deck, "--p", 1);
  });
  const front = $("#flash-front");
  if (front && flash) watch(front, () => { flash.classList.remove("swap"); void flash.offsetWidth; flash.classList.add("swap"); }, null, false);

  // 10. Study and Leaderboard panels: last refreshed, and how the ranking is worked out.
  fresh((el) => $("#study-intro") && $("#study-intro").after(el), $("#study-domains"));
  const me = $("#board-me");
  if (me) {
    const info = document.createElement("button");
    info.type = "button"; info.className = "info-btn"; info.textContent = "How is the ranking worked out?";
    me.after(info);
    howTo([[info, "Ranked by questions answered right on the first try. Only a question's first answer counts. Accuracy is that right count as a percentage of the questions done."]], (n) => info.after(n));
    fresh((el) => $("#board-filters").append(el), $("#board-table tbody"));
  }
})();
