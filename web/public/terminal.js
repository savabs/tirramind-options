/**
 * The Terminal.
 *
 * One file, no framework, no CDN. The surface arrives as JSON from our own
 * origin and everything else is drawn from it. Two rules run through the whole
 * thing: never show a number without saying how wrong it is, and never show a
 * number as ours when it came from somewhere else.
 */

import { analyse, authenticate, credentials, positions } from "/positions.js";

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
  if (showAxisTitles()) {
    const xl = svg("text", { x: (m.l + W - m.r) / 2, y: H - 3, "text-anchor": "middle",
      fill: tok("--ink3"), "font-size": labelSize() });
    xl.textContent = "log-moneyness  (0 = the forward)"; root.appendChild(xl);
    const yl = svg("text", { "text-anchor": "middle", fill: tok("--ink3"), "font-size": 12,
      transform: `translate(13,${(m.t + H - m.b) / 2}) rotate(-90)` });
    yl.textContent = "implied volatility, %"; root.appendChild(yl);
  }

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
  if (showAxisTitles()) {
    const yl = svg("text", { "text-anchor": "middle", fill: tok("--ink3"), "font-size": 12,
      transform: `translate(13,${(m.t + H - m.b) / 2}) rotate(-90)` });
    yl.textContent = "implied volatility, %"; root.appendChild(yl);
  }
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
      <div class="s">of ${q.quotes_fitted} quotes · compare venues on the error,
        not on this</div></div>
    <div class="kpi"><div class="l">Our surface</div>
      <div class="v ${clean ? "ok" : "bad"}">${clean ? "arbitrage-free" : "violations"}</div>
      <div class="s">butterfly ${q.our_butterfly_violations} · calendar ${q.our_calendar_violations}</div></div>
    <div class="kpi"><div class="l">Arbitrage in the book</div>
      <div class="v ${arbs ? "warn" : "ok"}">${arbs}</div>
      <div class="s">executable against bids and asks</div></div>
    <div class="kpi"><div class="l">Forward</div><div class="v">${money(s.forward_front)}</div>
      <div class="s">front expiry</div></div>
    <div class="kpi"><div class="l">Quoted in</div><div class="v">${s.settled_in || "—"}</div>
      <div class="s">${s.convention === "linear" ? "already in dollars" : "in the coin, on the forward"}
        · agrees with the venue to
        ${(s.quality.convention_check_vol_pts ?? 0).toFixed(2)} vol pts</div></div>`;
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

// ── alerts ────────────────────────────────────────────────────────────────
const ago = (s) => {
  if (!s) return "never";
  const m = Math.round((Date.now() / 1000 - s) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
};

async function renderAlerts() {
  const host = $("#alerts-panel");
  const a = state.account;
  if (!a || !a.signed_in) {
    host.innerHTML = `<div class="setup">Sign in to be told when an executable
      arbitrage appears, instead of watching a page.</div>`;
    return;
  }
  if (!a.entitled) {
    host.innerHTML = `<div class="setup">The monitor above runs every thirty
      minutes whether anyone is looking. Alerts send it to a webhook of yours
      when there is something to see, which is part of the paid tier.</div>`;
    return;
  }
  let data;
  try {
    const r = await fetch("/api/alerts", { credentials: "same-origin" });
    data = await r.json();
    if (!r.ok) throw new Error(data.error || `http ${r.status}`);
  } catch (e) {
    host.innerHTML = `<div class="setup bad">${e.message}</div>`;
    return;
  }
  const rows = (data.alerts || []).map((x) => `<tr>
      <td>${x.kind.replace("_", " ")}</td>
      <td class="mono" style="word-break:break-all">${x.url}</td>
      <td>${x.currency || "any"}</td>
      <td>${x.min_edge_usd ? "$" + x.min_edge_usd : "any"}</td>
      <td>${ago(x.last_sent_at)}${x.last_error
        ? `<div class="row-note">${x.last_error}</div>` : ""}</td>
      <td><button class="cta" data-test="${x.id}" style="padding:3px 9px">Test</button>
          <button class="cta" data-del="${x.id}" style="padding:3px 9px">Remove</button></td>
    </tr>`).join("");

  host.innerHTML = (data.alerts || []).length ? `<div class="scroll"><table>
      <thead><tr><th>Kind</th><th>Webhook</th><th>Currency</th><th>Min edge</th>
        <th>Last sent</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="setup" style="padding-top:12px" id="alerts-add"></div>`
    : `<div class="setup" id="alerts-add"></div>`;

  $("#alerts-add").innerHTML = `
    Send an alert to a webhook when an executable arbitrage appears. The same
    violation is reported once, not every thirty minutes, and every attempt is
    logged whether it worked or not.
    <div class="linkbox"><input id="al-url" placeholder="https://your-endpoint/hook"
      autocomplete="off"><input id="al-edge" placeholder="min $" style="max-width:90px"
      autocomplete="off"><button class="cta" id="al-add">Add</button></div>
    <div class="msg" id="al-msg"></div>` +
    ((data.recent_deliveries || []).length ? `<div style="margin-top:12px">
      <div class="l" style="font-size:11px;letter-spacing:.04em;text-transform:uppercase;
        color:var(--ink3)">Recent deliveries</div>` +
      data.recent_deliveries.slice(0, 5).map((d) =>
        `<div style="font-size:12.5px;margin-top:4px" class="${d.ok ? "ok" : "bad"}">
          ${ago(d.sent_at)} · ${d.ok ? "delivered" : "failed"}
          ${d.status ? "(" + d.status + ")" : ""} · ${d.items} item(s)
          ${d.ok ? "" : "· " + d.detail}</div>`).join("") + `</div>` : "");

  host.querySelectorAll("[data-del]").forEach((b) => {
    b.onclick = async () => {
      await fetch(`/api/alerts?id=${encodeURIComponent(b.dataset.del)}`,
        { method: "DELETE", credentials: "same-origin" });
      renderAlerts();
    };
  });
  host.querySelectorAll("[data-test]").forEach((b) => {
    b.onclick = async () => {
      b.textContent = "sending…";
      const r = await fetch(`/api/alerts/test?id=${encodeURIComponent(b.dataset.test)}`,
        { method: "POST", credentials: "same-origin" });
      b.textContent = r.ok ? "sent" : "failed";
      setTimeout(renderAlerts, 900);
    };
  });
  $("#al-add").onclick = async () => {
    const msg = $("#al-msg");
    msg.textContent = "adding…"; msg.className = "msg";
    try {
      const r = await fetch("/api/alerts", { method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "executable_arb", url: $("#al-url").value.trim(),
                               min_edge_usd: Number($("#al-edge").value) || 0 }) });
      const body = await r.json();
      if (!r.ok) { msg.textContent = body.error; msg.className = "msg bad"; return; }
      // Shown once, here. A secret that can be read back is a secret in a log.
      msg.innerHTML = `Added. Your signing secret, shown once:
        <div class="mono" style="margin-top:6px;word-break:break-all">${body.secret}</div>`;
      msg.className = "msg ok";
      setTimeout(renderAlerts, 6000);
    } catch (e) {
      msg.textContent = e.message; msg.className = "msg bad";
    }
  };
}

// ── positions ─────────────────────────────────────────────────────────────
const num = (v, d = 2) => (v >= 0 ? "+" : "") + v.toFixed(d);

function setupForm(message) {
  return `<div class="setup">
    Connect a Deribit account to see your own book priced on our surface.
    <ol>
      <li>In Deribit, open <b>Account, then API</b>, and add a new key.</li>
      <li>Give it <b>read-only</b> scope. Nothing here needs trade or withdraw,
          and a key that can trade should not be pasted into any website.</li>
      <li>Paste the client id and secret below.</li>
    </ol>
    <div class="privacy">Your key stays in this browser. Deribit accepts requests
      from this page directly, so the credential is never sent to us, never
      stored on our side, and there is no database of other people's keys to
      lose.</div>
    <div class="linkbox"><input id="dbt-id" placeholder="client id" autocomplete="off"></div>
    <div class="linkbox"><input id="dbt-secret" type="password" placeholder="client secret"
      autocomplete="off"><button class="cta" id="dbt-go">Connect</button></div>
    <label style="display:block;margin-top:8px;font-size:12.5px">
      <input type="checkbox" id="dbt-remember" checked> remember it in this browser</label>
    <div class="msg ${message ? "bad" : ""}" id="dbt-msg">${message || ""}</div>
  </div>`;
}

function renderPositions() {
  const host = $("#positions");
  const a = state.account;
  if (!a || !a.signed_in) {
    host.innerHTML = `<div class="setup">Sign in to connect a Deribit account and
      see your own positions with delta, gamma, vega and theta from our
      arbitrage-checked surface rather than the venue's marks.</div>`;
    return;
  }
  if (!a.entitled) {
    host.innerHTML = `<div class="setup">Your own positions, priced on our surface,
      are part of the paid tier.
      <ul style="margin:10px 0 0;padding-left:18px;color:var(--ink3)">
        <li>Delta, gamma, vega and theta from an arbitrage-checked fit</li>
        <li>Where the venue's mark sits outside its own spread, on your strikes</li>
        <li>Your own edge, measured with a confidence interval</li>
      </ul>
      <div class="msg">${a.needs_link ? "Already subscribed? Link your key from the header."
        : "The crypto surface above stays free."}</div></div>`;
    return;
  }
  if (!state.deribit) {
    host.innerHTML = setupForm(state.deribitError);
    $("#dbt-go").onclick = connectDeribit;
    return;
  }
  const { rows, totals } = state.deribit;
  if (!rows.length) {
    host.innerHTML = `<div class="empty">Connected, and the book is empty.</div>
      <div class="setup"><button class="cta" id="dbt-forget">Disconnect</button></div>`;
    $("#dbt-forget").onclick = forgetDeribit;
    return;
  }
  const body = rows.map((r) => {
    if (!r.ours) {
      return `<tr><td class="mono">${r.name}</td><td>${r.size}</td>
        <td colspan="5" class="row-note">${r.note}</td></tr>`;
    }
    const edge = r.venueVol != null && r.ourVol != null
      ? ((r.ourVol - r.venueVol) * 100).toFixed(2) : "—";
    return `<tr><td class="mono">${r.name}</td>
      <td>${r.size}</td>
      <td>${r.ourVol != null ? (r.ourVol * 100).toFixed(1) : "—"}</td>
      <td class="${edge !== "—" && +edge >= 0 ? "edge-pos" : "edge-neg"}">${edge}</td>
      <td>${num(r.size * r.ours.delta, 3)}</td>
      <td>${num(r.size * r.ours.vega, 1)}</td>
      <td>${num(r.size * r.ours.theta, 1)}</td></tr>`;
  }).join("");
  host.innerHTML = `
    <div class="totals">
      <div><div class="l">Delta</div><div class="v">${num(totals.delta, 3)}</div></div>
      <div><div class="l">Gamma</div><div class="v">${totals.gamma.toExponential(1)}</div></div>
      <div><div class="l">Vega</div><div class="v">${num(totals.vega, 1)}</div></div>
      <div><div class="l">Theta / day</div><div class="v">${num(totals.theta, 1)}</div></div>
      <div><div class="l">Open P&L</div>
        <div class="v ${totals.pnl >= 0 ? "ok" : "bad"}">${num(totals.pnl, 4)}</div></div>
    </div>
    <div class="scroll"><table>
      <thead><tr><th>Instrument</th><th>Size</th><th>Our vol</th><th>Diff</th>
        <th>Delta</th><th>Vega</th><th>Theta</th></tr></thead>
      <tbody>${body}</tbody></table></div>
    <div class="setup" style="padding-top:12px">
      Greeks are ours, from the fit above: delta against the forward, vega per
      volatility point, theta per day. Profit and loss is Deribit's own, in coin.
      <div style="margin-top:10px"><button class="cta" id="dbt-refresh">Refresh</button>
        <button class="cta" id="dbt-forget" style="margin-left:6px">Disconnect</button></div>
    </div>`;
  $("#dbt-refresh").onclick = () => loadDeribit(state.deribitToken);
  $("#dbt-forget").onclick = forgetDeribit;
}

async function connectDeribit() {
  const msg = $("#dbt-msg");
  const id = $("#dbt-id").value.trim(), secret = $("#dbt-secret").value.trim();
  if (!id || !secret) { msg.textContent = "both fields are needed"; msg.className = "msg bad"; return; }
  msg.textContent = "connecting…"; msg.className = "msg";
  try {
    const { token, scope } = await authenticate(id, secret);
    // Say so rather than silently accepting a key that can move money.
    if (/trade|wallet/.test(scope) && !/read/.test(scope.split(" ")[0] || "")) {
      msg.innerHTML = `connected, but this key has scope <b>${scope}</b>. ` +
        `A read-only key is enough here.`;
      msg.className = "msg warn";
    }
    if ($("#dbt-remember").checked) credentials.save({ id, secret });
    state.deribitToken = token;
    state.deribitError = null;
    await loadDeribit(token);
  } catch (e) {
    state.deribitError = e.message;
    msg.textContent = e.message; msg.className = "msg bad";
  }
}

async function loadDeribit(token) {
  try {
    const all = [];
    for (const c of Object.keys(state.data.surfaces)) all.push(...await positions(token, c));
    state.deribit = analyse(all, state.data.surfaces);
    state.deribitError = null;
  } catch (e) {
    state.deribit = null;
    state.deribitError = e.message;
  }
  renderPositions();
}

function forgetDeribit() {
  credentials.clear();
  state.deribit = null; state.deribitToken = null; state.deribitError = null;
  renderPositions();
}

async function resumeDeribit() {
  const saved = credentials.load();
  if (!saved || !state.account?.entitled || !state.data) return;
  try {
    const { token } = await authenticate(saved.id, saved.secret);
    state.deribitToken = token;
    await loadDeribit(token);
  } catch (e) {
    state.deribitError = e.message + " — the saved key may have been revoked";
    renderPositions();
  }
}

// ── wiring ────────────────────────────────────────────────────────────────
function render() {
  const s = state.data.surfaces[state.ccy];
  if (!s.expiries.some((e) => e.expiry === state.expiry)) state.expiry = s.expiries[0].expiry;
  const slice = s.expiries.find((e) => e.expiry === state.expiry);

  // The underlying is what a reader looks for; the venue beside it matters
  // because the same underlying on two venues is not the same surface.
  $("#ccy").innerHTML = Object.entries(state.data.surfaces).map(([c, v]) =>
    `<button data-c="${c}" aria-pressed="${c === state.ccy}">${v.base || c}` +
    `<span class="settled">${v.venue || ""}</span></button>`).join("");
  $("#ccy").querySelectorAll("button").forEach((b) => {
    b.onclick = () => { state.ccy = b.dataset.c; state.expiry = null; render(); };
  });

  const a = age(state.data.generated_at);
  const dead = Object.keys(state.data.failed || {});
  $("#stamp").innerHTML = `<b>${a.text}</b> · refit every 30 min` +
    (a.stale ? ` · <span class="warn">stale</span>` : "") +
    // A market that failed to fit is said out loud. Quietly showing six of
    // seven would let a dead book look like one that does not exist.
    (dead.length ? ` · <span class="warn">${dead.join(", ")} did not fit</span>` : "");

  renderKpis(s);
  // Settlement and convention are said out loud: a price quoted in the coin and
  // one quoted in dollars are read differently, and the check that they were not
  // confused is a number worth showing.
  $("#smile-sub").dataset.venue = s.venue || "";
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
  renderPositions();
}

async function boot() {
  // The account and the surface are independent: a signed-out reader still gets
  // the free surface, and a broken surface still shows who is signed in.
  fetch("/auth/entitlement", { credentials: "same-origin" })
    .then((r) => (r.ok ? r.json() : { signed_in: false }))
    .catch(() => ({ signed_in: false }))
    .then((a) => { state.account = a; renderAccount(); renderPositions();
                   renderAlerts(); resumeDeribit(); });

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
