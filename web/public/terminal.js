/**
 * The Terminal.
 *
 * One file, no framework, no CDN. The surface arrives as JSON from our own
 * origin and everything else is drawn from it. Two rules run through the whole
 * thing: never show a number without saying how wrong it is, and never show a
 * number as ours when it came from somewhere else.
 */

const $ = (s, r) => (r || document).querySelector(s);
/** Colours go into the SVG as variables, never as resolved literals: a chart
 *  drawn in dark mode must not stay dark when the reader's system turns light.
 *  Not named `v`: the drawing loops use that for an axis value. */
const tok = (n) => `var(${n})`;
const vol = (v) => (v * 100).toFixed(1);
const pct = (v) => (v * 100).toFixed(0) + "%";
const money = (v) => "$" + v.toLocaleString(undefined, { maximumFractionDigits: 2 });

const state = { data: null, ccy: null, expiry: null, account: null };

/** The charts are drawn in viewBox units and scaled to fit, so a fixed label
 *  size becomes unreadable on a narrow screen. Scale the type instead. */
const labelSize = () => (innerWidth < 760 ? 20 : 12);
/** At phone width the rotated axis title collides with its own tick labels, and
 *  the panel heading already carries the units. Drop it rather than crowd. */
const showAxisTitles = () => innerWidth >= 760;

function svg(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) {
    const value = attrs[k];
    // var() is not valid in a presentation attribute, only in a style. Setting
    // fill="var(--band)" silently renders black, which is how this was found.
    if (typeof value === "string" && value.startsWith("var(")) el.style[k] = value;
    else el.setAttribute(k, value);
  }
  return el;
}

/** How old the surface is, in words a reader can act on. */
function age(iso) {
  const secs = (Date.now() - Date.parse(iso)) / 1000;
  if (!Number.isFinite(secs)) return { text: "unknown age", stale: true };
  const m = Math.round(secs / 60);
  if (m < 1) return { text: "just now", stale: false };
  if (m < 60) return { text: `${m} min ago`, stale: m > 45 };
  const h = Math.round(m / 60);
  return { text: `${h} h ago`, stale: true };
}

// ── charts ────────────────────────────────────────────────────────────────
function drawSmile(host, tip, slice) {
  host.textContent = "";
  const W = 900, H = 380, m = { t: 12, r: 16, b: 42, l: 54 };
  const xs = slice.log_moneyness, n = xs.length;
  const ys = [].concat(slice.bid_iv, slice.ask_iv, slice.our_iv, slice.venue_mark_iv)
    .filter((v) => v !== null && Number.isFinite(v));
  if (!n || !ys.length) return;
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.12 || 0.02; y0 -= pad; y1 += pad;
  const X = (v) => m.l + ((v - x0) / ((x1 - x0) || 1)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - ((v - y0) / ((y1 - y0) || 1)) * (H - m.t - m.b);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Implied volatility by strike for ${slice.expiry}` });

  for (let i = 0; i <= 5; i++) {
    const v = y0 + ((y1 - y0) * i) / 5, y = Y(v);
    root.appendChild(svg("line", { x1: m.l, x2: W - m.r, y1: y, y2: y,
      stroke: tok("--rule"), "stroke-width": 1 }));
    const t = svg("text", { x: m.l - 9, y: y + 4, "text-anchor": "end",
      fill: tok("--ink3"), "font-size": labelSize() });
    t.textContent = vol(v); root.appendChild(t);
  }
  for (let i = 0; i <= 4; i++) {
    const v = x0 + ((x1 - x0) * i) / 4;
    const t = svg("text", { x: X(v), y: H - 20, "text-anchor": "middle",
      fill: tok("--ink3"), "font-size": labelSize() });
    t.textContent = v.toFixed(2); root.appendChild(t);
  }
  const xl = svg("text", { x: (m.l + W - m.r) / 2, y: H - 3, "text-anchor": "middle",
    fill: tok("--ink3"), "font-size": labelSize() });
  xl.textContent = "log-moneyness  (0 = the forward)"; root.appendChild(xl);
  const yl = svg("text", { "text-anchor": "middle", fill: tok("--ink3"), "font-size": 12,
    transform: `translate(13,${(m.t + H - m.b) / 2}) rotate(-90)` });
  yl.textContent = "implied volatility, %"; root.appendChild(yl);

  const band = [];
  for (let i = 0; i < n; i++) band.push(`${X(xs[i])},${Y(slice.ask_iv[i])}`);
  for (let i = n - 1; i >= 0; i--) band.push(`${X(xs[i])},${Y(slice.bid_iv[i])}`);
  root.appendChild(svg("polygon", { points: band.join(" "), fill: tok("--band") }));

  root.appendChild(svg("path", {
    d: xs.map((k, i) => `${i ? "L" : "M"}${X(k)},${Y(slice.our_iv[i])}`).join(""),
    fill: "none", stroke: tok("--ours"), "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round" }));

  xs.forEach((k, i) => {
    const v = slice.venue_mark_iv[i];
    if (v === null || !Number.isFinite(v)) return;
    // A 2px ring in the surface colour keeps the dot readable where the two agree.
    root.appendChild(svg("circle", { cx: X(k), cy: Y(v), r: 4.5, fill: tok("--venue"),
      stroke: tok("--s1"), "stroke-width": 2 }));
  });

  const cross = svg("line", { y1: m.t, y2: H - m.b, stroke: tok("--ink3"),
    "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0 });
  root.appendChild(cross);
  root.addEventListener("pointerleave", () => {
    cross.setAttribute("opacity", 0); tip.style.opacity = 0;
  });
  root.addEventListener("pointermove", (ev) => {
    const box = root.getBoundingClientRect();
    const at = x0 + ((ev.clientX - box.left) / box.width) * W;
    let best = 0, bd = Infinity;
    xs.forEach((k, i) => { const d = Math.abs(X(k) - at); if (d < bd) { bd = d; best = i; } });
    const px = X(xs[best]);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("opacity", 1);
    const diff = (slice.our_iv[best] - slice.venue_mark_iv[best]) * 100;
    const outside = slice.venue_mark_iv[best] < slice.bid_iv[best]
                 || slice.venue_mark_iv[best] > slice.ask_iv[best];
    tip.innerHTML =
      `<b>strike ${Math.round(slice.strike[best]).toLocaleString()}</b>` +
      `<div class="r"><span><i style="background:${tok("--ours")}"></i>ours</span>` +
      `<span>${vol(slice.our_iv[best])}</span></div>` +
      `<div class="r"><span><i style="background:${tok("--venue")}"></i>venue mark</span>` +
      `<span>${vol(slice.venue_mark_iv[best])}</span></div>` +
      `<div class="r"><span>bid / ask</span><span>${vol(slice.bid_iv[best])} – ${vol(slice.ask_iv[best])}</span></div>` +
      `<div class="r"><span>difference</span><span>${diff >= 0 ? "+" : ""}${diff.toFixed(2)} vol pts</span></div>` +
      (outside ? `<div class="r" style="color:${tok("--venue")}"><span>the mark is outside the spread</span></div>` : "");
    tip.style.opacity = 1;
    const wrap = root.parentElement.getBoundingClientRect();
    tip.style.left = Math.min(Math.max(box.left - wrap.left + (px / W) * box.width + 14, 8),
      root.parentElement.clientWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = Math.max(ev.clientY - wrap.top - tip.offsetHeight - 12, 4) + "px";
  });
  host.appendChild(root);
}

function atmOf(e) {
  // Interpolate at the forward rather than reading the nearest listed strike: on
  // a two-day expiry the nearest strike can sit percent away on a steep smile.
  const ks = e.log_moneyness, iv = e.our_iv;
  for (let i = 1; i < ks.length; i++) {
    if (ks[i - 1] <= 0 && ks[i] >= 0) {
      const w = (0 - ks[i - 1]) / ((ks[i] - ks[i - 1]) || 1);
      return iv[i - 1] + w * (iv[i] - iv[i - 1]);
    }
  }
  return iv[ks.reduce((b, k, j) => (Math.abs(k) < Math.abs(ks[b]) ? j : b), 0)];
}

function drawTerm(host, tip, expiries) {
  host.textContent = "";
  const W = 900, H = 210, m = { t: 12, r: 16, b: 34, l: 54 };
  const pts = expiries.map((e) => ({ dte: e.dte, atm: atmOf(e), expiry: e.expiry,
    rmse: e.rmse_vol_pts, fallback: e.status !== "ok", status: e.status }))
    .filter((p) => Number.isFinite(p.atm)).sort((a, b) => a.dte - b.dte);
  if (!pts.length) return;
  const x0 = Math.min(...pts.map((p) => p.dte)), x1 = Math.max(...pts.map((p) => p.dte));
  let y0 = Math.min(...pts.map((p) => p.atm)), y1 = Math.max(...pts.map((p) => p.atm));
  const pad = (y1 - y0) * 0.2 || 0.02; y0 -= pad; y1 += pad;
  const X = (v) => m.l + ((v - x0) / ((x1 - x0) || 1)) * (W - m.l - m.r);
  const Y = (v) => H - m.b - ((v - y0) / ((y1 - y0) || 1)) * (H - m.t - m.b);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "At-the-money implied volatility by days to expiry" });

  for (let i = 0; i <= 3; i++) {
    const v = y0 + ((y1 - y0) * i) / 3, y = Y(v);
    root.appendChild(svg("line", { x1: m.l, x2: W - m.r, y1: y, y2: y,
      stroke: tok("--rule"), "stroke-width": 1 }));
    const t = svg("text", { x: m.l - 9, y: y + 4, "text-anchor": "end",
      fill: tok("--ink3"), "font-size": labelSize() });
    t.textContent = vol(v); root.appendChild(t);
  }
  root.appendChild(svg("path", {
    d: pts.map((p, i) => `${i ? "L" : "M"}${X(p.dte)},${Y(p.atm)}`).join(""),
    fill: "none", stroke: tok("--ours"), "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round" }));
  pts.forEach((p) => {
    const c = svg("circle", { cx: X(p.dte), cy: Y(p.atm), r: 4.5,
      fill: p.fallback ? tok("--s1") : tok("--ours"),
      stroke: p.fallback ? tok("--ours") : tok("--s1"),
      "stroke-width": 2, style: "cursor:pointer" });
    c.addEventListener("pointerenter", () => {
      tip.innerHTML = `<b>${p.expiry}</b>` +
        `<div class="r"><span>at the money</span><span>${vol(p.atm)}</span></div>` +
        `<div class="r"><span>days out</span><span>${p.dte.toFixed(1)}</span></div>` +
        `<div class="r"><span>error</span><span>${p.rmse.toFixed(2)} vol pts</span></div>` +
        `<div class="r"><span>fitted by</span><span>${p.fallback
          ? "backbone (" + p.status.replace("_", " ") + ")" : "refined slice"}</span></div>`;
      tip.style.opacity = 1;
      const box = root.getBoundingClientRect();
      const wrap = root.parentElement.getBoundingClientRect();
      tip.style.left = Math.min(box.left - wrap.left + (X(p.dte) / W) * box.width + 14,
        root.parentElement.clientWidth - tip.offsetWidth - 8) + "px";
      tip.style.top = Math.max(box.top - wrap.top + (Y(p.atm) / H) * box.height
        - tip.offsetHeight - 12, 4) + "px";
      c.setAttribute("r", 6);
    });
    c.addEventListener("pointerleave", () => { tip.style.opacity = 0; c.setAttribute("r", 4.5); });
    c.addEventListener("click", () => { state.expiry = p.expiry; render(); });
    root.appendChild(c);
  });
  for (let i = 0; i <= 4; i++) {
    const v = x0 + ((x1 - x0) * i) / 4;
    const t = svg("text", { x: X(v), y: H - 12, "text-anchor": "middle",
      fill: tok("--ink3"), "font-size": labelSize() });
    t.textContent = Math.round(v) + "d"; root.appendChild(t);
  }
  const yl = svg("text", { "text-anchor": "middle", fill: tok("--ink3"), "font-size": 12,
    transform: `translate(13,${(m.t + H - m.b) / 2}) rotate(-90)` });
  yl.textContent = "implied volatility, %"; root.appendChild(yl);
  host.appendChild(root);
}

// ── panels ────────────────────────────────────────────────────────────────
function renderKpis(s) {
  const q = s.quality;
  const clean = q.our_butterfly_violations === 0 && q.our_calendar_violations === 0;
  const arbs = q.executable_venue_arbs;
  $("#kpis").innerHTML = `
    <div class="kpi"><div class="l">Our error</div><div class="v">${q.rmse_vol_pts.toFixed(2)}</div>
      <div class="s">volatility points, root mean square</div></div>
    <div class="kpi"><div class="l">Inside the spread</div><div class="v">${pct(q.inside_bid_ask)}</div>
      <div class="s">of ${q.quotes_fitted} quotes fitted</div></div>
    <div class="kpi"><div class="l">Our surface</div>
      <div class="v ${clean ? "ok" : "bad"}">${clean ? "arbitrage-free" : "violations"}</div>
      <div class="s">butterfly ${q.our_butterfly_violations} · calendar ${q.our_calendar_violations}</div></div>
    <div class="kpi"><div class="l">Arbitrage in the book</div>
      <div class="v ${arbs ? "warn" : "ok"}">${arbs}</div>
      <div class="s">executable against bids and asks</div></div>
    <div class="kpi"><div class="l">Forward</div><div class="v">${money(s.forward_front)}</div>
      <div class="s">front expiry</div></div>`;
}

function renderChain(slice) {
  const rows = slice.strike.map((k, i) => {
    const diff = (slice.our_iv[i] - slice.venue_mark_iv[i]) * 100;
    const outside = slice.venue_mark_iv[i] < slice.bid_iv[i]
                 || slice.venue_mark_iv[i] > slice.ask_iv[i];
    return `<tr><td class="mono">${Math.round(k).toLocaleString()}</td>
      <td>${vol(slice.bid_iv[i])}</td>
      <td><b>${vol(slice.our_iv[i])}</b></td>
      <td>${vol(slice.venue_mark_iv[i])}${outside ? " ·" : ""}</td>
      <td>${vol(slice.ask_iv[i])}</td>
      <td class="${diff >= 0 ? "edge-pos" : "edge-neg"}">${diff >= 0 ? "+" : ""}${diff.toFixed(2)}</td></tr>`;
  }).join("");
  $("#chain").innerHTML =
    `<thead><tr><th>Strike</th><th>Bid</th><th>Ours</th><th>Venue</th><th>Ask</th>
       <th>Diff</th></tr></thead><tbody>${rows}</tbody>`;
  $("#chain-sub").textContent =
    `${slice.expiry} · ${slice.quotes} quotes · a dot marks a venue mark outside its own spread`;
}

function renderArbs(s) {
  const host = $("#arbs");
  if (!s.arbs || !s.arbs.length) {
    host.innerHTML = `<div class="empty">None right now. The book is clean, which is
      the usual answer and the one worth being able to trust.</div>`;
    return;
  }
  const rows = s.arbs.map((a) => `<tr><td>${a.kind}</td><td>${a.expiry}</td>
    <td class="mono">${a.strikes.map((k) => Math.round(k).toLocaleString()).join(" / ")}</td>
    <td class="ok">${a.edge_usd === null ? "—" : money(a.edge_usd)}</td></tr>`).join("");
  host.innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Kind</th><th>Expiry</th><th>Strikes</th><th>Edge</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function renderAccount() {
  const a = state.account;
  const host = $("#acct");
  if (!a || !a.signed_in) {
    host.innerHTML = `<span class="pill">not signed in</span>
      <a class="cta" href="/auth/start?next=/terminal">Sign in</a>`;
    return;
  }
  if (a.entitled) {
    host.innerHTML = `<span class="pill on">${a.tier}</span>
      <span style="color:var(--ink2)">${a.email}</span>`;
    return;
  }
  if (a.needs_link) {
    host.innerHTML = `<span class="pill">no subscription</span>
      <button class="cta" id="link-open">Link a key</button>`;
    $("#link-open").onclick = showLink;
    return;
  }
  host.innerHTML = `<span class="pill">${a.reason}</span>
    <span style="color:var(--ink2)">${a.email}</span>`;
}

function showLink() {
  const card = document.createElement("section");
  card.className = "card";
  card.style.margin = "0 0 18px";
  card.innerHTML = `<h2>Link your subscription</h2><div class="body">
    Signed in as <b>${state.account.email}</b>, which is not the address on any
    subscription. Paste the key we emailed you and this account is bound to it
    for good.
    <div class="linkbox"><input id="link-key" placeholder="tmo_…" autocomplete="off">
      <button class="cta" id="link-go">Link</button></div>
    <div class="msg" id="link-msg"></div></div>`;
  $("main").prepend(card);
  $("#link-go").onclick = async () => {
    const msg = $("#link-msg");
    msg.textContent = "linking…"; msg.className = "msg";
    try {
      const r = await fetch("/auth/link", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: $("#link-key").value.trim() }) });
      const body = await r.json();
      if (!r.ok) { msg.textContent = body.error || "that did not work"; msg.className = "msg bad"; return; }
      msg.textContent = "linked."; msg.className = "msg ok";
      state.account = { signed_in: true, email: state.account.email, ...body };
      renderAccount();
      setTimeout(() => card.remove(), 1200);
    } catch {
      msg.textContent = "the request failed"; msg.className = "msg bad";
    }
  };
}

// ── wiring ────────────────────────────────────────────────────────────────
function render() {
  const s = state.data.surfaces[state.ccy];
  if (!s.expiries.some((e) => e.expiry === state.expiry)) state.expiry = s.expiries[0].expiry;
  const slice = s.expiries.find((e) => e.expiry === state.expiry);

  $("#ccy").innerHTML = Object.keys(state.data.surfaces).map((c) =>
    `<button data-c="${c}" aria-pressed="${c === state.ccy}">${c}</button>`).join("");
  $("#ccy").querySelectorAll("button").forEach((b) => {
    b.onclick = () => { state.ccy = b.dataset.c; state.expiry = null; render(); };
  });

  const a = age(state.data.generated_at);
  $("#stamp").innerHTML = `<b>${a.text}</b> · refit every 30 min` +
    (a.stale ? ` · <span class="warn">stale</span>` : "");

  renderKpis(s);
  $("#pick").innerHTML = s.expiries.map((e) =>
    `<button data-e="${e.expiry}" aria-pressed="${e.expiry === state.expiry}">${e.expiry}</button>`).join("");
  $("#pick").querySelectorAll("button").forEach((b) => {
    b.onclick = () => { state.expiry = b.dataset.e; render(); };
  });

  const flag = $("#fallback");
  flag.hidden = slice.status === "ok";
  flag.textContent = slice.status === "ok" ? "" :
    `This expiry failed the ${slice.status.replace("_fail", "")} check, so it is drawn ` +
    `from the eSSVI backbone: arbitrage-free by construction, but it fits the quotes less closely.`;
  $("#smile-sub").textContent =
    `${slice.expiry} · ${slice.dte.toFixed(1)} days · error ${slice.rmse_vol_pts.toFixed(2)} vol pts · ` +
    `${pct(slice.inside_bid_ask)} inside the spread`;

  drawSmile($("#smile"), $("#smile-tip"), slice);
  drawTerm($("#term"), $("#term-tip"), s.expiries);
  renderChain(slice);
  renderArbs(s);
}

async function boot() {
  // The account and the surface are independent: a signed-out reader still gets
  // the free surface, and a broken surface still shows who is signed in.
  fetch("/auth/entitlement", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : { signed_in: false }))
    .catch(() => ({ signed_in: false }))
    .then((a) => { state.account = a; renderAccount(); });

  try {
    const r = await fetch("/api/surface");
    if (!r.ok) throw new Error((await r.json()).error || `http ${r.status}`);
    state.data = await r.json();
  } catch (e) {
    $("#kpis").innerHTML = `<div class="kpi"><div class="l">Surface</div>
      <div class="v bad">unavailable</div><div class="s">${e.message}</div></div>`;
    return;
  }
  state.ccy = Object.keys(state.data.surfaces)[0];
  render();
  addEventListener("resize", () => state.data && render());
}

boot();
