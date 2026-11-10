// FINN: inbox zero for an investor. Plain modules with no build step.
const API = "https://finn-api-wrgo.onrender.com";
const WINDOWS = [[24, "24 hours"], [72, "3 days"], [168, "7 days"], [720, "30 days"]];
const DEMO = ["ASIANPAINT", "DMART", "NAUKRI", "CAMS"];
const CATEGORIES = { results: "Results", management_change: "Management changes", "M&A": "Deals", pledging: "Promoter pledges", credit_rating: "Rating downgrades" };
const app = document.getElementById("app");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
let route = "", cleared = 0, digging = null, pollTimer = null;

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeUrl = (u) => (/^https:\/\//.test(u || "") ? esc(u) : "");
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window */ } },
};

async function api(path, options = {}) {
  const banner = document.getElementById("waking");
  const slow = setTimeout(() => { banner.hidden = false; }, 4500);
  const ctrl = new AbortController();
  const stop = setTimeout(() => ctrl.abort(), 100000);
  try {
    const r = await fetch(API + path, { ...options, signal: ctrl.signal, headers: { "Content-Type": "application/json" } });
    if (!r.ok) { const e = new Error(`HTTP ${r.status}`); e.status = r.status; throw e; }
    return await r.json();
  } finally { clearTimeout(slow); clearTimeout(stop); banner.hidden = true; }
}

function when(stamp) {
  if (!stamp) return "";
  const [d, t] = stamp.split("T");
  const [, m, day] = d.split("-").map(Number);
  let [h, min] = (t || "00:00").split(":").map(Number);
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${day} ${MONTHS[m - 1]}, ${h}:${String(min).padStart(2, "0")} ${ap}`;
}
const tone = (s) => (/^\+(?!0\.0%)/.test(s) ? "up" : /^-/.test(s) ? "down" : "muted");
const signed = (n, suffix = "%") => (n === null || n === undefined ? "n.m." : `${n > 0 ? "+" : ""}${n}${suffix}`);
const crore = (n) => (n === null || n === undefined ? "–" : `₹${Math.round(n).toLocaleString("en-IN")} cr`);
const plain = (n, suffix = "") => (n === null || n === undefined ? "–" : `${n}${suffix}`);

function toast(text, undo) {
  const el = document.getElementById("toast");
  el.innerHTML = `<span style="padding:13px 8px 13px 0">${esc(text)}</span>${undo ? "<button>Undo</button>" : ""}`;
  el.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => { el.hidden = true; }, 5000);
  if (undo) el.querySelector("button").onclick = () => { el.hidden = true; undo(); };
}

function failed(message, retry) {
  app.innerHTML = `<div class="error"><p>${esc(message)}</p><button class="btn quiet" id="retry">Try again</button></div>`;
  document.getElementById("retry").onclick = retry;
}

/* ---------- Today ---------- */
const METRIC = /^(Revenue|EBITDA|PBT|PAT) (₹[\d,]+ cr) · YoY (\S+) · QoQ (\S+)$/;

function cardHTML(c) {
  const [company, ...kind] = c.title.split(" · ");
  const label = kind.join(" · ").replace(" ✓", "");
  const metrics = c.lines.map((l) => l.match(METRIC)).filter(Boolean);
  const rest = c.lines.filter((l) => !METRIC.test(l));
  const reaction = rest.filter((l) => /x volume/.test(l));
  const text = rest.filter((l) => !/x volume/.test(l));
  const table = metrics.length ? `<table class="metrics"><thead><tr><th></th><th></th><th>YoY</th><th>QoQ</th></tr></thead><tbody>${metrics.map((m) =>
    `<tr><td>${m[1]}</td><td class="num">${esc(m[2])}</td><td class="num ${tone(m[3])}">${esc(m[3])}</td><td class="num ${tone(m[4])}">${esc(m[4])}</td></tr>`).join("")}</tbody></table>` : "";
  const pdf = safeUrl(c.pdf);
  return `<article class="card ${c.state === "pinned" ? "pinned" : ""}" data-key="${esc(c.key)}" data-symbol="${esc(c.symbol)}" data-category="${esc(c.category)}" data-title="${esc(c.title)}">
    <div class="card-top"><span><span class="ticker">${esc(c.symbol)}</span> · ${esc(label)}</span><span>${when(c.time)}</span></div>
    <h2>${esc(company)} ${c.verified ? '<span class="check">✓ checked</span>' : ""}</h2>
    ${table}${text.map((l) => `<p class="body">${esc(l)}</p>`).join("")}${reaction.map((l) => `<p class="reaction num">${esc(l)}</p>`).join("")}
    <div class="actions"><button data-act="dismiss">Dismiss</button><button data-act="pin">${c.state === "pinned" ? "Unpin" : "Pin"}</button>${pdf ? `<a href="${pdf}" target="_blank" rel="noopener">Filing</a>` : ""}<button class="primary" data-act="dig">Dig in →</button></div>
  </article>`;
}

async function today() {
  const hours = store.get("hours", 24);
  app.innerHTML = `<header class="masthead"><div class="brand">FINN</div><div class="stamp" id="stamp"></div></header>
    <div id="head"><h1 class="headline">Checking what changed…</h1></div>
    <div class="windows" role="group" aria-label="Time window">${WINDOWS.map(([h, l]) => `<button class="chip" data-hours="${h}" aria-pressed="${h === hours}">${l}</button>`).join("")}</div>
    <div id="list"><div class="skeleton"></div><div class="skeleton"></div></div>`;
  let d;
  try { d = await api(`/digest?hours=${hours}`); } catch { if (route.startsWith("#/today")) failed("Couldn't load your digest.", today); return; }
  if (!route.startsWith("#/today")) return;
  document.getElementById("stamp").textContent = d.data_as_of ? `Filings as of ${when(d.data_as_of)}` : "";
  const urgent = d.cards.filter((c) => c.would_ping || c.state === "pinned");
  const others = d.cards.filter((c) => !urgent.includes(c));
  document.getElementById("list").innerHTML = (d.cards.length ? "" : zeroHTML()) +
    (urgent.length ? `<div class="lane">Needs you</div>${urgent.map(cardHTML).join("")}` : "") +
    (others.length ? `<div class="lane">Worth a look</div>${others.map(cardHTML).join("")}` : "") +
    (d.routine_count ? `<details class="routine"><summary>Routine (${d.routine_count}), folded away</summary>${d.routine.map((r) =>
      `<a class="routine-row" ${safeUrl(r.pdf) ? `href="${safeUrl(r.pdf)}" target="_blank" rel="noopener"` : ""}><span class="ticker">${esc(r.symbol)}</span> ${esc(r.headline)}</a>`).join("")}</details>` : "");
  updateHead();
  armSwipes();
}

const zeroHTML = () => `<div class="zero"><h2>Inbox zero.</h2><p class="muted">${cleared ? `You cleared ${cleared}. ` : ""}Nothing else needs you in this window.</p></div>`;

function updateHead() {
  const left = app.querySelectorAll(".card").length;
  const head = document.getElementById("head");
  if (!head) return;
  head.innerHTML = `<h1 class="headline">${left ? `${left} ${left === 1 ? "thing needs" : "things need"} a decision.` : "All clear."}</h1>`;
  const list = document.getElementById("list");
  if (!left && !list.querySelector(".zero")) list.insertAdjacentHTML("afterbegin", zeroHTML());
  list.querySelectorAll(".lane").forEach((lane) => { if (!lane.nextElementSibling?.classList.contains("card")) lane.remove(); });
}

function setState(key, state) {
  return api("/cards/state", { method: "POST", body: JSON.stringify({ key, state }) });
}

function dismiss(card) {
  card.classList.add("gone");
  cleared += 1;
  setState(card.dataset.key, "dismissed").catch(() => toast("Couldn't save that. Check your connection."));
  const parent = card.parentNode, next = card.nextSibling, key = card.dataset.key;
  setTimeout(() => { card.remove(); updateHead(); }, 220);
  toast("Dismissed", () => {
    cleared -= 1;
    card.classList.remove("gone");
    card.style.transform = "";
    parent.insertBefore(card, next);
    updateHead();
    setState(key, "none").catch(() => {});
  });
}

function armSwipes() {
  app.querySelectorAll(".card").forEach((card) => {
    let x0 = null, y0 = null, dx = 0;
    card.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse" && !e.target.closest("a,button")) { x0 = e.clientX; y0 = e.clientY; dx = 0; } });
    card.addEventListener("pointermove", (e) => {
      if (x0 === null) return;
      dx = e.clientX - x0;
      if (Math.abs(e.clientY - y0) > 24 && Math.abs(dx) < 24) { x0 = null; card.style.transform = ""; return; }
      if (dx < 0) { card.style.transition = "none"; card.style.transform = `translateX(${dx}px)`; card.style.opacity = 1 + dx / 420; }
    });
    const end = () => {
      if (x0 === null) return;
      x0 = null; card.style.transition = ""; card.style.opacity = "";
      if (dx < -110) dismiss(card); else card.style.transform = "";
    };
    card.addEventListener("pointerup", end);
    card.addEventListener("pointercancel", end);
  });
}

/* ---------- Research ---------- */
function researchHome() {
  app.innerHTML = `<header class="masthead"><div class="brand">Research</div></header>
    <h1 class="headline">Did management do what they said?</h1>
    ${searchHTML()}<div class="chips">${DEMO.map((s) => `<a class="chip num" href="#/research/${s}">${s}</a>`).join("")}</div>
    <p class="note">Any NSE ticker. Built only from annual figures and earnings-call transcripts.</p>`;
}
const searchHTML = () => `<form class="search" id="search"><input name="t" aria-label="Ticker" placeholder="Ticker" autocomplete="off" autocapitalize="characters" spellcheck="false"><button class="btn">Open</button></form>`;

const VERDICT = { delivered: "Delivered", partly: "Partly", missed: "Missed", too_early: "Too early", unmeasurable: "Can't test" };

function actualText(a) {
  if (!a) return "";
  const [kind, period, field] = a.key.split(":");
  const when = kind === "FY" ? `FY${period.slice(-2)}` : period;
  const names = { revenue_growth_pct: "revenue growth", revenue_yoy_pct: "revenue growth YoY", pat_growth_pct: "profit growth", pat_yoy_pct: "profit growth YoY", opm_pct: "operating margin", revenue: "revenue", pat: "profit" };
  return `${when} ${names[field] || field}: ${field.endsWith("_pct") ? `${a.value}%` : crore(a.value)}`;
}

function promiseHTML(r) {
  const repeated = r.repeated ? ` · repeated in ${r.repeated + 1} calls` : "";
  const how = ["delivered", "partly", "missed"].includes(r.verdict) ? (r.verdict_source === "computed" ? "Calculated from reported numbers" : "Model's judgement on the evidence") : "";
  return `<div class="promise"><div class="promise-top"><h3>${esc(r.promise)}</h3><span class="badge ${r.verdict}">${VERDICT[r.verdict]}</span></div>
    ${r.actual ? `<p class="outcome num">${esc(actualText(r.actual))}</p>` : ""}
    ${r.reported ? `<p class="outcome">${esc(r.reported.statement)} <span class="source">${esc(r.reported.source)}</span></p>` : ""}
    ${!r.actual && !r.reported ? `<p class="outcome muted">${esc(r.explanation)}</p>` : ""}
    ${how ? `<p class="how">${how}${repeated}</p>` : repeated ? `<p class="how">${repeated.slice(3)}</p>` : ""}
    <details class="quote"><summary>What they said <span class="source">${esc(r.source)}</span></summary><blockquote>${esc(r.quote)}</blockquote></details></div>`;
}

function analysisHTML(symbol, data) {
  const { analysis: a, job } = data;
  const running = job?.status === "running";
  if (running) return `<div class="panel"><strong>Reading the last five earnings calls</strong><p class="note">Usually a few minutes. You can leave this page and come back.</p><div class="pulse"></div></div>`;
  if (!a) {
    const note = job?.status === "failed" ? "The last attempt didn't finish." : "";
    return `<div class="panel"><strong>Not analysed yet</strong><p class="note">${note} FINN will read the last five earnings calls and check each promise against what was reported. It takes a few minutes the first time.</p><button class="btn" data-run="${esc(symbol)}">Read the calls</button></div>`;
  }
  const g = a.guidance, s = g.scorecard, rows = g.rows || [];
  const tested = rows.filter((r) => ["delivered", "partly", "missed"].includes(r.verdict));
  const open = rows.filter((r) => r.verdict === "too_early");
  const untestable = rows.filter((r) => r.verdict === "unmeasurable");
  const cov = g.coverage;
  const fallback = String(g.model || "").startsWith("groq/");
  const needsRun = g.pending || fallback;
  return `<p class="verdict-line">${esc(g.summary.headline)}</p>
    <div class="score">${["delivered", "partly", "missed", "too_early", "unmeasurable"].map((v) => `<div><b>${s[v]}</b>${VERDICT[v]}</div>`).join("")}</div>
    ${tested.map(promiseHTML).join("")}
    ${open.length ? `<div class="lane">Guidance still open</div>${open.map(promiseHTML).join("")}` : ""}
    ${untestable.length ? `<div class="lane">Can't be tested from filings</div>${untestable.map(promiseHTML).join("")}` : ""}
    <p class="note">Read ${cov.calls_read.length} calls: ${esc(cov.calls_read.join(", "))}.${cov.unreadable.length ? ` Unreadable: ${esc(cov.unreadable.join(", "))}.` : ""}${cov.failed.length ? ` Failed: ${esc(cov.failed.join(", "))}.` : ""} Every quote was found word for word in the transcript.${tokens(g)}</p>
    ${needsRun ? `<div class="panel" style="margin-top:12px"><strong>${fallback ? "Done on the backup model" : "Comparison pending"}</strong><p class="note">The main model was busy, so fewer promises may be tested.</p><button class="btn quiet" data-run="${esc(symbol)}">Run again</button></div>` : ""}`;
}

function tokens(g) {
  const t = (g.tokens || []).filter(Boolean);
  if (!t.length) return "";
  const sum = t.reduce((n, x) => n + (x.input_tokens || 0) + (x.output_tokens || 0), 0);
  return ` Used ${sum.toLocaleString("en-IN")} tokens, ₹0 on the free tier.`;
}

function bars(values, labels) {
  const v = values.map((x) => x ?? 0);
  const max = Math.max(...v.map(Math.abs), 1);
  const w = 100 / v.length;
  return `<svg class="bars" viewBox="0 0 100 62" preserveAspectRatio="none" role="img" aria-label="${esc(labels.map((l, i) => `${l}: ${values[i] ?? "n/a"}`).join(", "))}">${v.map((x, i) => {
    const h = Math.max(1, (Math.abs(x) / max) * 60);
    return `<rect class="${x < 0 ? "neg" : ""}" x="${i * w + w * 0.14}" y="${62 - h}" width="${w * 0.72}" height="${h}"/>`;
  }).join("")}</svg><div class="years">${labels.map((l) => `<span>${esc(l.slice(-2))}</span>`).join("")}</div>`;
}

const signalsHTML = (list) => (list.length ? `<ul class="signals">${list.map((s) => `<li class="${esc(s.tone)}">${esc(s.text)}</li>`).join("")}</ul>` : "");
const SRC = `<p class="note">Source: standardised reported financials.</p>`;

function caseHTML(a) {
  const b = a?.bull_bear;
  if (!b) return "";
  if (!b.available) return `<section class="block"><h2>Bull vs bear</h2><p class="muted">${esc(b.reason)}</p></section>`;
  const side = (title, points) => `<div class="case"><h3>${title}</h3>${points.length ? points.map((p) => `<div class="case-point">${esc(p.point)}<br>${p.evidence.map((e) => `<span class="source">${esc(e.source.replace("Screener standardised financials", "Reported financials"))}</span>`).join("")}</div>`).join("") : '<p class="muted">Nothing in the evidence supports this side.</p>'}</div>`;
  return `<section class="block"><h2>Bull vs bear</h2>${side("Bull", b.bull)}${side("Bear", b.bear)}</section>`;
}

function companyHTML(symbol, d) {
  const s = d.sections, snap = s.snapshot, t = s.trajectory, bs = s.balance_sheet, cash = s.cash_quality, m = snap.market, q = snap.latest_quarter;
  const c = t.cagr_pct, lender = d.is_financial;
  const cagrRow = (l, r) => `<tr><td>${l}</td>${["3y", "5y", "10y"].map((y) => `<td class="num">${plain(r[y], "%")}</td>`).join("")}</tr>`;
  return `${searchHTML()}<h1 class="company">${esc(d.name)}</h1>
    ${digging ? `<p class="note">From your card: ${esc(digging.title)}</p>` : ""}
    <div class="facts">${[["Mkt cap", m["Market Cap"] ? `₹${Math.round(m["Market Cap"] / 1000).toLocaleString("en-IN")}k cr` : "–"], ["P/E", plain(m["Stock P/E"])], ["ROCE", plain(m.ROCE, "%")], ["ROE", plain(m.ROE, "%")]].map(([k, v]) => `<div class="fact"><span>${k}</span><b>${v}</b></div>`).join("")}</div>
    <section class="block" id="pvd"><h2>Promised vs delivered</h2><div id="analysis">${analysisHTML(symbol, d)}</div></section>
    <div id="case">${caseHTML(d.analysis)}</div>
    <section class="block" id="snapshot"><h2>Latest numbers</h2><div class="grid2">
      <div class="stat"><span>Revenue, ${esc(q.period)}</span><b>${crore(q.revenue)}</b><div class="num ${q.revenue_yoy_pct > 0 ? "up" : "down"}">${signed(q.revenue_yoy_pct)} YoY</div></div>
      <div class="stat"><span>Profit, ${esc(q.period)}</span><b>${crore(q.pat)}</b><div class="num ${q.pat_yoy_pct > 0 ? "up" : "down"}">${signed(q.pat_yoy_pct)} YoY</div></div>
      <div class="stat"><span>Revenue, last 12 months</span><b>${crore(snap.ttm.revenue)}</b></div>
      <div class="stat"><span>${lender ? "Profit, last 12 months" : "Operating margin"}</span><b>${lender ? crore(snap.ttm.pat) : plain(snap.ttm.margin_pct, "%")}</b></div></div></section>
    <section class="block" id="trajectory"><h2>Trajectory</h2>${signalsHTML(t.signals)}
      <div class="chart-label">Revenue, by financial year</div>${bars(t.series.revenue, t.series.periods)}
      ${lender ? "" : `<div class="chart-label">Operating margin</div>${bars(t.series.margin_pct, t.series.periods)}`}
      <table class="cagr"><thead><tr><th>Growth a year</th><th>3y</th><th>5y</th><th>10y</th></tr></thead><tbody>${cagrRow("Revenue", c.revenue)}${lender ? "" : cagrRow("Operating profit", c.operating_profit)}${cagrRow("Profit", c.pat)}</tbody></table>${SRC}</section>
    <section class="block"><h2>Balance sheet</h2>${signalsHTML(bs.signals)}<div class="chart-label">Debt to equity</div>${bars(bs.series.debt_to_equity, bs.series.periods)}${bs.working_capital ? `<div class="chart-label">Cash conversion cycle, days</div>${bars(bs.working_capital.cash_conversion_days, bs.working_capital.periods)}` : ""}${SRC}</section>
    <section class="block"><h2>Cash quality</h2>${signalsHTML(cash.signals)}${cash.applicable ? `<div class="grid2" style="margin-top:9px"><div class="stat"><span>Operating profit turned to cash, 5y</span><b>${plain(cash.five_year.cfo_to_operating_profit_pct, "%")}</b></div><div class="stat"><span>Free cash flow, 5y</span><b>${crore(cash.five_year.free_cash_flow)}</b></div></div><div class="chart-label">Free cash flow</div>${bars(cash.series.free_cash_flow, cash.series.periods)}${SRC}` : ""}</section>`;
}

async function research(symbol) {
  clearTimeout(pollTimer);
  app.innerHTML = `<div class="skeleton" style="margin-top:14px"></div><div class="skeleton"></div><div class="skeleton"></div>`;
  let d;
  try { d = await api(`/research/${encodeURIComponent(symbol)}`); } catch (e) {
    if (!route.startsWith("#/research")) return;
    if (e.status === 404) { app.innerHTML = `${searchHTML()}<div class="error"><p>No listed company found for ${esc(symbol)}.</p></div>`; return; }
    failed("Couldn't load this company.", () => research(symbol)); return;
  }
  if (route !== `#/research/${symbol}`) return;
  app.innerHTML = companyHTML(symbol, d);
  if (digging) document.getElementById(digging.category === "move" ? "snapshot" : "pvd").scrollIntoView();
  if (d.job?.status === "running") poll(symbol);
}

function poll(symbol) {
  pollTimer = setTimeout(async () => {
    if (route !== `#/research/${symbol}`) return;
    try {
      const d = await api(`/research/${encodeURIComponent(symbol)}`);
      if (d.job?.status === "running") { poll(symbol); return; }
      digging = null;
      app.innerHTML = companyHTML(symbol, d);
    } catch { poll(symbol); }
  }, 7000);
}

async function runResearch(symbol) {
  document.getElementById("analysis").innerHTML = analysisHTML(symbol, { job: { status: "running" } });
  document.getElementById("case").innerHTML = "";
  try {
    const r = await api(`/research/${encodeURIComponent(symbol)}/run`, { method: "POST" });
    if (r.status === "fresh") { research(symbol); return; }
    poll(symbol);
  } catch { toast("Couldn't start. Try again."); research(symbol); }
}

/* ---------- Watch ---------- */
async function watch() {
  app.innerHTML = `<div class="skeleton" style="margin-top:14px"></div>`;
  let s;
  try { s = await api("/settings"); } catch { failed("Couldn't load your settings.", watch); return; }
  if (!route.startsWith("#/watch")) return;
  const slider = (id, label, value, min, max, step, unit, help) => `<div class="setting"><label for="${id}">${label}<span class="num" id="${id}-v">${value}${unit}</span></label><input type="range" id="${id}" min="${min}" max="${max}" step="${step}" value="${value}" data-unit="${unit}"><p>${help}</p></div>`;
  app.innerHTML = `<header class="masthead"><div class="brand">Watch</div></header><h1 class="headline">You decide what counts as unusual.</h1>
    ${slider("price_move_pct", "Price move", s.alerts.price_move_pct, 1, 15, 0.5, "%", "Measured against Nifty, so a market-wide day doesn't set it off.")}
    ${slider("volume_multiple", "Volume spike", s.alerts.volume_multiple, 1.5, 10, 0.5, "×", "Compared with the recent average.")}
    ${slider("volume_lookback_days", "Recent average covers", s.alerts.volume_lookback_days, 5, 60, 5, " days", "")}
    ${slider("max_pings_per_day", "Phone pings a day", s.max_pings_per_day, 0, 15, 1, "", "The most important go first. The rest wait for the morning digest.")}
    <div class="lane">Ping me about</div>
    ${Object.entries(CATEGORIES).map(([k, l]) => `<label class="toggle-row">${l}<input type="checkbox" data-cat="${esc(k)}" ${s.ping_categories.includes(k) ? "checked" : ""}></label>`).join("")}
    <p class="note">Changes apply here straight away, and to Telegram alerts on the next update.</p>`;
  let timer;
  const save = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const num = (id) => Number(document.getElementById(id).value);
      const body = { alerts: { price_move_pct: num("price_move_pct"), volume_multiple: num("volume_multiple"), volume_lookback_days: num("volume_lookback_days") },
        max_pings_per_day: num("max_pings_per_day"), ping_categories: [...app.querySelectorAll("[data-cat]:checked")].map((c) => c.dataset.cat) };
      try { await api("/settings", { method: "PUT", body: JSON.stringify(body) }); toast("Saved"); } catch { toast("Couldn't save. Try again."); }
    }, 500);
  };
  app.querySelectorAll("input[type=range]").forEach((r) => r.addEventListener("input", () => {
    document.getElementById(`${r.id}-v`).textContent = r.value + r.dataset.unit; save();
  }));
  app.querySelectorAll("[data-cat]").forEach((c) => c.addEventListener("change", save));
}

/* ---------- Shell ---------- */
app.addEventListener("click", (e) => {
  const chip = e.target.closest("[data-hours]");
  if (chip) { store.set("hours", Number(chip.dataset.hours)); today(); return; }
  const run = e.target.closest("[data-run]");
  if (run) { runResearch(run.dataset.run); return; }
  const button = e.target.closest("[data-act]");
  if (!button) return;
  const card = button.closest(".card");
  if (button.dataset.act === "dismiss") dismiss(card);
  if (button.dataset.act === "pin") {
    const pinned = card.classList.toggle("pinned");
    button.textContent = pinned ? "Unpin" : "Pin";
    setState(card.dataset.key, pinned ? "pinned" : "none").then(() => toast(pinned ? "Pinned to the top" : "Unpinned")).catch(() => toast("Couldn't save that."));
  }
  if (button.dataset.act === "dig") {
    digging = { title: card.dataset.title, category: card.dataset.category };
    location.hash = `#/research/${card.dataset.symbol}`;
  }
});
app.addEventListener("submit", (e) => {
  if (e.target.id !== "search") return;
  e.preventDefault();
  const t = e.target.t.value.trim().toUpperCase();
  if (t) { digging = null; location.hash = `#/research/${encodeURIComponent(t)}`; }
});

function go() {
  route = decodeURI(location.hash || "#/today");
  clearTimeout(pollTimer);
  const parts = route.split("/");
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === parts[1]));
  window.scrollTo(0, 0);
  if (parts[1] === "research") { parts[2] ? research(parts[2]) : researchHome(); }
  else if (parts[1] === "watch") watch();
  else { route = "#/today"; today(); }
}
window.addEventListener("hashchange", go);
go();
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
