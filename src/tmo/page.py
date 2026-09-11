"""Render the Deribit tab: one self-contained HTML file, no network at view time.

Every number on the page carries its own error, because that is the only thing
this product has that the category does not. The competitors' pages say their
numbers are estimates in a footer; this one says how wrong it is, per expiry,
at the top.

The page is static and inert: the data is embedded as JSON, the charts are drawn
by a few dozen lines of vanilla JavaScript, and nothing is fetched when a reader
opens it. That keeps it cheap to host, fast to open, and honest about its own
staleness, since the generation time is baked in rather than implied.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import voltorch

# Categorical slots 1 and 2 of the reference palette, validated for both modes:
# worst adjacent CVD delta-E 24.7 light and 26.8 dark against an 8 target, and
# both clear 3:1 on their surface. Our surface is the subject, the venue's marks
# are the thing it is being read against.
_STYLE = """
:root{
  color-scheme: light;
  --surface-0:#ffffff; --surface-1:#fcfcfb; --surface-2:#f4f3f0;
  --ink-1:#0b0b0b; --ink-2:#52514e; --ink-3:#84837c;
  --rule:#e3e2dd;
  --ours:#2a78d6; --venue:#eb6834; --band:#e8e7e2;
  --good:#1a7f37; --bad:#b42318;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --surface-0:#141413; --surface-1:#1a1a19; --surface-2:#232322;
    --ink-1:#ffffff; --ink-2:#c3c2b7; --ink-3:#8e8d84;
    --rule:#33332f;
    --ours:#3987e5; --venue:#d95926; --band:#2b2b29;
    --good:#4ac26b; --bad:#f97066;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface-0:#141413; --surface-1:#1a1a19; --surface-2:#232322;
  --ink-1:#ffffff; --ink-2:#c3c2b7; --ink-3:#8e8d84;
  --rule:#33332f;
  --ours:#3987e5; --venue:#d95926; --band:#2b2b29;
  --good:#4ac26b; --bad:#f97066;
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--ink-1);
  font:15px/1.55 ui-sans-serif,-apple-system,system-ui,"Segoe UI",sans-serif;
  font-variant-numeric:tabular-nums;-webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:40px 20px 72px}
header h1{font-size:26px;line-height:1.2;margin:0 0 6px;letter-spacing:-.015em}
header p{margin:0;color:var(--ink-2);max-width:64ch}
header p a{color:inherit}
.stamp{margin-top:14px;font-size:13px;color:var(--ink-3)}
.tabs{display:flex;gap:4px;margin:28px 0 18px;border-bottom:1px solid var(--rule)}
.tab{appearance:none;border:0;background:none;font:inherit;font-weight:600;
  color:var(--ink-3);padding:8px 14px;cursor:pointer;border-bottom:2px solid transparent;
  margin-bottom:-1px}
.tab[aria-selected="true"]{color:var(--ink-1);border-bottom-color:var(--ours)}
.tab .settled{font-weight:400;font-size:11px;color:var(--ink-3);margin-left:5px}
.tabs{flex-wrap:wrap}
.tab:focus-visible{outline:2px solid var(--ours);outline-offset:2px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1px;
  background:var(--rule);border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.kpi{background:var(--surface-1);padding:14px 16px}
.kpi .label{font-size:12px;color:var(--ink-3);letter-spacing:.02em;text-transform:uppercase}
.kpi .value{font-size:26px;line-height:1.15;margin-top:4px;letter-spacing:-.02em}
.kpi .sub{font-size:12px;color:var(--ink-2);margin-top:2px}
.ok{color:var(--good)} .bad{color:var(--bad)}
h2{font-size:15px;margin:34px 0 2px;letter-spacing:-.01em}
h2 + .note{margin:0 0 12px;font-size:13px;color:var(--ink-3);max-width:70ch}
.chart{position:relative;border:1px solid var(--rule);border-radius:10px;
  background:var(--surface-1);padding:12px 12px 6px}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--ink-2);
  padding:2px 4px 10px}
.legend i{display:inline-block;width:22px;height:0;border-top:2px solid currentColor;
  vertical-align:middle;margin-right:6px;box-sizing:border-box}
.legend .sw-band i{border-top:10px solid var(--band);border-radius:2px}
.legend .sw-dot i{width:10px;height:10px;border:2px solid currentColor;border-radius:50%;
  background:var(--ours)}
.legend .sw-ring i{width:10px;height:10px;border:2px solid currentColor;border-radius:50%;
  background:var(--surface-1)}
.flag{margin:0 0 10px;font-size:13px;color:var(--ink-2);background:var(--surface-2);
  border-left:2px solid var(--venue);padding:8px 12px;border-radius:0 6px 6px 0;max-width:70ch}
.pick{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 10px}
.pick button{appearance:none;font:inherit;font-size:13px;cursor:pointer;
  border:1px solid var(--rule);background:var(--surface-1);color:var(--ink-2);
  padding:4px 10px;border-radius:999px}
.pick button[aria-pressed="true"]{background:var(--ours);border-color:var(--ours);color:#fff}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--rule)}
th{font-weight:600;color:var(--ink-3);font-size:12px;text-transform:uppercase;
  letter-spacing:.02em;text-align:right}
th:first-child,td:first-child{text-align:left}
tbody tr:hover{background:var(--surface-2)}
.meter{display:inline-block;width:64px;height:6px;border-radius:3px;
  background:var(--surface-2);vertical-align:middle;margin-left:8px;overflow:hidden}
.meter span{display:block;height:100%;background:var(--ours);border-radius:3px}
.tip{position:absolute;pointer-events:none;opacity:0;transition:opacity .08s;
  background:var(--surface-0);border:1px solid var(--rule);border-radius:8px;
  padding:8px 10px;font-size:12px;line-height:1.5;box-shadow:0 6px 20px rgba(0,0,0,.14);
  white-space:nowrap;z-index:5}
.tip b{font-weight:600}
.tip .row{display:flex;justify-content:space-between;gap:14px}
.tip .row i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px}
footer{margin-top:44px;padding-top:18px;border-top:1px solid var(--rule);
  font-size:13px;color:var(--ink-3);max-width:76ch}
footer code{background:var(--surface-2);padding:1px 5px;border-radius:4px}
footer a{color:var(--ink-2)}
.arbs{margin-top:8px;font-size:13px;color:var(--ink-2)}
.arbs li{margin:2px 0}
@media (max-width:640px){.wrap{padding:24px 14px 56px}header h1{font-size:22px}
  .kpi .value{font-size:22px}}
"""

_SCRIPT = r"""
const DATA = window.__SURFACES__;
const $ = (s, r) => (r || document).querySelector(s);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const state = {cur: Object.keys(DATA)[0], expiry: null};

const fmtPct = v => (v * 100).toFixed(1) + "%";
const fmtVol = v => (v * 100).toFixed(1);

function svg(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

// A smile: our arbitrage-checked curve, the venue's marks, and the spread the
// two are being judged against. The band is context and stays neutral.
function drawSmile(host, tip, slice) {
  host.textContent = "";
  const W = 900, H = 360, m = {t: 14, r: 18, b: 42, l: 52};
  const root = svg("svg", {viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `Implied volatility smile for ${slice.expiry}`});
  const xs = slice.log_moneyness, n = xs.length;
  const ys = [].concat(slice.bid_iv, slice.ask_iv, slice.our_iv, slice.venue_mark_iv)
               .filter(v => v !== null && isFinite(v));
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1 - y0) * 0.12 || 0.02; y0 -= pad; y1 += pad;
  const X = v => m.l + (v - x0) / ((x1 - x0) || 1) * (W - m.l - m.r);
  const Y = v => H - m.b - (v - y0) / ((y1 - y0) || 1) * (H - m.t - m.b);

  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const v = y0 + (y1 - y0) * i / ticks, y = Y(v);
    root.appendChild(svg("line", {x1: m.l, x2: W - m.r, y1: y, y2: y,
      stroke: css("--rule"), "stroke-width": 1}));
    const t = svg("text", {x: m.l - 8, y: y + 4, "text-anchor": "end",
      fill: css("--ink-3"), "font-size": 12});
    t.textContent = fmtVol(v); root.appendChild(t);
  }
  for (let i = 0; i <= 4; i++) {
    const v = x0 + (x1 - x0) * i / 4;
    const t = svg("text", {x: X(v), y: H - 12, "text-anchor": "middle",
      fill: css("--ink-3"), "font-size": 12});
    t.textContent = v.toFixed(2); root.appendChild(t);
  }
  const xl = svg("text", {x: (m.l + W - m.r) / 2, y: H + 6, "text-anchor": "middle",
    fill: css("--ink-3"), "font-size": 12});
  xl.textContent = "log-moneyness  (0 = the forward)"; root.appendChild(xl);
  const yl = svg("text", {x: 0, y: 0, "text-anchor": "middle", fill: css("--ink-3"),
    "font-size": 12, transform: `translate(12,${(m.t + H - m.b) / 2}) rotate(-90)`});
  yl.textContent = "implied volatility, %"; root.appendChild(yl);

  const band = [];
  for (let i = 0; i < n; i++) band.push(`${X(xs[i])},${Y(slice.ask_iv[i])}`);
  for (let i = n - 1; i >= 0; i--) band.push(`${X(xs[i])},${Y(slice.bid_iv[i])}`);
  root.appendChild(svg("polygon", {points: band.join(" "), fill: css("--band")}));

  const path = xs.map((k, i) => `${i ? "L" : "M"}${X(k)},${Y(slice.our_iv[i])}`).join("");
  root.appendChild(svg("path", {d: path, fill: "none", stroke: css("--ours"),
    "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}));

  // A 2px surface ring keeps overlapping marks readable where the two agree.
  xs.forEach((k, i) => {
    const v = slice.venue_mark_iv[i];
    if (v === null || !isFinite(v)) return;
    root.appendChild(svg("circle", {cx: X(k), cy: Y(v), r: 4.5,
      fill: css("--venue"), stroke: css("--surface-1"), "stroke-width": 2}));
  });

  const cross = svg("line", {y1: m.t, y2: H - m.b, stroke: css("--ink-3"),
    "stroke-width": 1, "stroke-dasharray": "3 3", opacity: 0});
  root.appendChild(cross);
  root.addEventListener("pointerleave", () => {cross.setAttribute("opacity", 0); tip.style.opacity = 0;});
  root.addEventListener("pointermove", ev => {
    const box = root.getBoundingClientRect();
    const kx = x0 + (ev.clientX - box.left) / box.width * W;
    let best = 0, bd = Infinity;
    xs.forEach((k, i) => {const d = Math.abs(X(k) - kx); if (d < bd) {bd = d; best = i;}});
    const px = X(xs[best]);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("opacity", 1);
    const dev = (slice.our_iv[best] - slice.venue_mark_iv[best]) * 100;
    tip.innerHTML =
      `<b>strike ${Math.round(slice.strike[best]).toLocaleString()}</b>` +
      `<div class="row"><span><i style="background:${css("--ours")}"></i>ours</span>` +
      `<span>${fmtVol(slice.our_iv[best])}</span></div>` +
      `<div class="row"><span><i style="background:${css("--venue")}"></i>venue mark</span>` +
      `<span>${fmtVol(slice.venue_mark_iv[best])}</span></div>` +
      `<div class="row"><span>bid / ask</span><span>${fmtVol(slice.bid_iv[best])} – ${fmtVol(slice.ask_iv[best])}</span></div>` +
      `<div class="row"><span>difference</span><span>${dev >= 0 ? "+" : ""}${dev.toFixed(2)} vol pts</span></div>`;
    tip.style.opacity = 1;
    const left = box.left + px / W * box.width - root.parentElement.getBoundingClientRect().left;
    tip.style.left = Math.min(Math.max(left + 14, 8), root.parentElement.clientWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = (ev.clientY - root.parentElement.getBoundingClientRect().top - tip.offsetHeight - 12) + "px";
  });
  host.appendChild(root);
}

// One series, so no legend: the heading names it.
function drawTerm(host, tip, expiries) {
  host.textContent = "";
  const W = 900, H = 210, m = {t: 14, r: 18, b: 34, l: 52};
  const atmOf = e => {
    // Interpolate our curve at the forward rather than reading the nearest
    // listed strike: on a two-day expiry the nearest strike can be percent
    // away on a steep smile, which reads as a spike that is not there.
    const ks = e.log_moneyness, iv = e.our_iv;
    for (let i = 1; i < ks.length; i++) {
      if (ks[i - 1] <= 0 && ks[i] >= 0) {
        const w = (0 - ks[i - 1]) / ((ks[i] - ks[i - 1]) || 1);
        return iv[i - 1] + w * (iv[i] - iv[i - 1]);
      }
    }
    return iv[ks.reduce((b, k, j) => Math.abs(k) < Math.abs(ks[b]) ? j : b, 0)];
  };
  const pts = expiries.map(e => ({dte: e.dte, atm: atmOf(e), expiry: e.expiry,
    rmse: e.rmse_vol_pts, fallback: e.status !== "ok", status: e.status}))
    .filter(p => isFinite(p.atm)).sort((a, b) => a.dte - b.dte);
  if (!pts.length) return;
  const root = svg("svg", {viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": "At-the-money implied volatility by days to expiry"});
  const x0 = Math.min(...pts.map(p => p.dte)), x1 = Math.max(...pts.map(p => p.dte));
  let y0 = Math.min(...pts.map(p => p.atm)), y1 = Math.max(...pts.map(p => p.atm));
  const pad = (y1 - y0) * 0.2 || 0.02; y0 -= pad; y1 += pad;
  const X = v => m.l + (v - x0) / ((x1 - x0) || 1) * (W - m.l - m.r);
  const Y = v => H - m.b - (v - y0) / ((y1 - y0) || 1) * (H - m.t - m.b);
  for (let i = 0; i <= 3; i++) {
    const v = y0 + (y1 - y0) * i / 3, y = Y(v);
    root.appendChild(svg("line", {x1: m.l, x2: W - m.r, y1: y, y2: y,
      stroke: css("--rule"), "stroke-width": 1}));
    const t = svg("text", {x: m.l - 8, y: y + 4, "text-anchor": "end",
      fill: css("--ink-3"), "font-size": 12});
    t.textContent = fmtVol(v); root.appendChild(t);
  }
  root.appendChild(svg("path", {
    d: pts.map((p, i) => `${i ? "L" : "M"}${X(p.dte)},${Y(p.atm)}`).join(""),
    fill: "none", stroke: css("--ours"), "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round"}));
  pts.forEach(p => {
    const c = svg("circle", {cx: X(p.dte), cy: Y(p.atm), r: 4.5,
      fill: p.fallback ? css("--surface-1") : css("--ours"),
      stroke: p.fallback ? css("--ours") : css("--surface-1"),
      "stroke-width": 2, style: "cursor:pointer"});
    c.addEventListener("pointerenter", () => {
      tip.innerHTML = `<b>${p.expiry}</b><div class="row"><span>at the money</span>` +
        `<span>${fmtVol(p.atm)}</span></div>` +
        `<div class="row"><span>days out</span><span>${p.dte.toFixed(1)}</span></div>` +
        `<div class="row"><span>error</span><span>${p.rmse.toFixed(2)} vol pts</span></div>` +
        `<div class="row"><span>fitted by</span><span>` +
        (p.fallback ? `backbone (${p.status.replace("_", " ")})` : "refined slice") + `</span></div>`;
      tip.style.opacity = 1;
      const box = host.getBoundingClientRect(), par = host.parentElement.getBoundingClientRect();
      tip.style.left = Math.min(box.left - par.left + X(p.dte) / W * box.width + 14,
        host.parentElement.clientWidth - tip.offsetWidth - 8) + "px";
      tip.style.top = (box.top - par.top + Y(p.atm) / H * box.height - tip.offsetHeight - 12) + "px";
      c.setAttribute("r", 6);
    });
    c.addEventListener("pointerleave", () => {tip.style.opacity = 0; c.setAttribute("r", 4.5);});
    root.appendChild(c);
  });
  for (let i = 0; i <= 4; i++) {
    const v = x0 + (x1 - x0) * i / 4;
    const t = svg("text", {x: X(v), y: H - 11, "text-anchor": "middle",
      fill: css("--ink-3"), "font-size": 12});
    t.textContent = Math.round(v) + "d"; root.appendChild(t);
  }
  const yl = svg("text", {x: 0, y: 0, "text-anchor": "middle", fill: css("--ink-3"),
    "font-size": 12, transform: `translate(12,${(m.t + H - m.b) / 2}) rotate(-90)`});
  yl.textContent = "implied volatility, %"; root.appendChild(yl);
  host.appendChild(root);
}

const panel = () => document.querySelector(`[data-panel="${state.cur}"]`);

function renderPicker() {
  const box = $(".pick", panel()); box.textContent = "";
  DATA[state.cur].expiries.forEach(e => {
    const b = document.createElement("button");
    b.textContent = e.expiry;
    b.setAttribute("aria-pressed", String(e.expiry === state.expiry));
    b.onclick = () => {state.expiry = e.expiry; render();};
    box.appendChild(b);
  });
}

function render() {
  const d = DATA[state.cur];
  if (!d.expiries.some(e => e.expiry === state.expiry)) state.expiry = d.expiries[0].expiry;
  document.querySelectorAll(".tab").forEach(t =>
    t.setAttribute("aria-selected", String(t.dataset.cur === state.cur)));
  document.querySelectorAll("[data-panel]").forEach(p =>
    p.hidden = p.dataset.panel !== state.cur);
  renderPicker();
  const box = panel();
  const slice = d.expiries.find(e => e.expiry === state.expiry);
  const flag = $(".fallback", box);
  flag.hidden = slice.status === "ok";
  flag.textContent = slice.status === "ok" ? "" :
    `This expiry failed the ${slice.status.replace("_fail", "")} check, so it is drawn from ` +
    `the eSSVI backbone, which is arbitrage-free by construction but fits the quotes less closely.`;
  drawSmile($(".smile", box), $(".smile-tip", box), slice);
  drawTerm($(".term", box), $(".term-tip", box), d.expiries);
}

document.querySelectorAll(".tab").forEach(t =>
  t.onclick = () => {state.cur = t.dataset.cur; state.expiry = null; render();});
render();
addEventListener("resize", () => render());
"""


def _kpis(p: dict[str, Any]) -> str:
    q = p["quality"]
    arbs = q["executable_venue_arbs"]
    clean = q["our_butterfly_violations"] == 0 and q["our_calendar_violations"] == 0
    return f"""<div class="kpis">
  <div class="kpi"><div class="label">Our error</div>
    <div class="value">{q['rmse_vol_pts']:.2f}</div>
    <div class="sub">volatility points, root mean square</div></div>
  <div class="kpi"><div class="label">Inside the spread</div>
    <div class="value">{q['inside_bid_ask'] * 100:.0f}%</div>
    <div class="sub">of {q['quotes_fitted']} quotes fitted</div></div>
  <div class="kpi"><div class="label">Our surface</div>
    <div class="value {'ok' if clean else 'bad'}">{'arbitrage-free' if clean else 'violations'}</div>
    <div class="sub">butterfly {q['our_butterfly_violations']} · calendar {q['our_calendar_violations']}</div></div>
  <div class="kpi"><div class="label">Arbitrage in the book</div>
    <div class="value {'ok' if arbs == 0 else 'bad'}">{arbs}</div>
    <div class="sub">executable against bids and asks</div></div>
</div>"""


def _table(p: dict[str, Any]) -> str:
    worst = max((e["rmse_vol_pts"] for e in p["expiries"]), default=1.0) or 1.0
    rows = []
    for e in p["expiries"]:
        ok = e["status"] == "ok"
        rows.append(
            f"<tr><td>{e['expiry']}</td><td>{e['dte']:.1f}</td><td>{e['quotes']}</td>"
            f"<td>{e['rmse_vol_pts']:.2f}"
            f"<span class='meter'><span style='width:{e['rmse_vol_pts'] / worst * 100:.0f}%'></span></span></td>"
            f"<td>{e['inside_bid_ask'] * 100:.0f}%</td>"
            f"<td class='{'ok' if ok else 'bad'}'>{'checked' if ok else e['status'].replace('_', ' ')}</td></tr>")
    return ("<table><thead><tr><th>Expiry</th><th>Days</th><th>Quotes</th>"
            "<th>Error, vol pts</th><th>Inside spread</th><th>No-arbitrage</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def render(payloads: dict[str, dict[str, Any]], *, generated_at: datetime | None = None) -> str:
    """One HTML document showing every surface in ``payloads``."""
    if not payloads:
        raise ValueError("nothing to render")
    stamp = (generated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    tabs = "".join(
        f'<button class="tab" role="tab" data-cur="{c}" aria-selected="false">{c}'
        f'<span class="settled">{p.get("settled_in", "")}</span></button>'
        for c, p in payloads.items())
    panels = []
    for c, p in payloads.items():
        panels.append(f"""<section data-panel="{c}" hidden>
  {_kpis(p)}
  <h2>The smile, expiry by expiry</h2>
  <p class="note">Our arbitrage-checked curve against the venue's own marks, inside the
    quoted spread. Where the orange sits outside the grey band, the venue is marking
    a price you could not trade at.</p>
  <div class="pick"></div>
  <p class="flag fallback" hidden></p>
  <div class="chart"><div class="legend">
      <span style="color:var(--ours)"><i></i>our surface</span>
      <span style="color:var(--venue)"><i></i>venue mark</span>
      <span class="sw-band" style="color:var(--ink-2)"><i></i>bid to ask</span>
    </div><div class="smile"></div><div class="tip smile-tip"></div></div>
  <h2>At the money, by maturity</h2>
  <p class="note">Our fitted volatility interpolated at the forward, one point per listed
    expiry. A hollow point failed a no-arbitrage check and comes from the backbone
    instead of the refined slice, which is why it can sit away from its neighbours.</p>
  <div class="chart"><div class="legend">
      <span class="sw-dot" style="color:var(--ours)"><i></i>refined slice</span>
      <span class="sw-ring" style="color:var(--ours)"><i></i>backbone fallback</span>
    </div><div class="term"></div><div class="tip term-tip"></div></div>
  <p class="note">Quoted in {p.get("settled_in", "?")}, so prices are
    {"already in dollars" if p.get("convention") == "linear" else "in the coin and converted on the forward"}.
    Our implied volatilities agree with the venue's own marks to
    {p["quality"].get("convention_check_vol_pts", float("nan")):.2f} volatility points at the median,
    near the money, which is how a confused convention would show up.</p>
  <h2>How wrong we are, per expiry</h2>
  <p class="note">Published because nobody else in this category publishes it. Error is the
    root-mean-square gap between our fitted volatility and the mid of the quoted spread.
    A slice marked otherwise failed a no-arbitrage check and fell back to the backbone,
    which is arbitrage-free by construction.</p>
  {_table(p)}
</section>""")

    data_json = json.dumps(payloads, separators=(",", ":"))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deribit volatility surface — TirraMind Options</title>
<meta name="description" content="An arbitrage-checked BTC and ETH volatility surface, published with its own error, per expiry.">
<style>{_STYLE}</style></head><body><div class="wrap">
<header>
  <h1>Deribit volatility surface, with its error</h1>
  <p>An arbitrage-free surface fitted to the public book, published alongside how wrong it
  is. No key, no vendor feed, nothing you cannot recompute. The rest of this category
  ships a picture and a footnote saying the numbers are estimates.</p>
  <div class="stamp">Generated {stamp} · refit every 30 minutes ·
    engine <a href="https://pypi.org/project/voltorch/">voltorch</a> {voltorch.__version__} ·
    <a href="https://github.com/savabs/tirramind-options">source</a></div>
</header>
<div class="tabs" role="tablist">{tabs}</div>
{''.join(panels)}
<footer>
  <p>Backbone is eSSVI, arbitrage-free by construction. Each expiry is then refined with a
  per-slice SVI fit that is checked for butterfly and calendar arbitrage on a dense grid;
  where a check fails the slice falls back to the backbone, and the table says so.
  Implied volatilities are ours, by bisection on Black-76 against the forward, using the
  inverse-contract convention that a coin price times the forward is the USD price.</p>
  <p>Software only. Nothing here is a recommendation, a signal, or advice.</p>
</footer>
</div>
<script>window.__SURFACES__={data_json};</script>
<script>{_SCRIPT}</script>
</body></html>"""


__all__ = ["render"]


def build_site(out_path: str = "site/index.html", currencies=None) -> str:
    """Fetch, fit, write the page, and write the same data as JSON.

    The JSON is what the Terminal reads. One fit serves both, so the page a
    stranger sees and the surface a subscriber trades against are the same
    numbers from the same instant, rather than two fits that happen to agree.
    """
    import json as _json
    import os

    from . import venues
    from .service import surface

    # Every market we fit. A market that fails is reported and skipped rather
    # than taking the page down with it: one dead book should not hide six live
    # ones.
    keys = [m.key for m in venues.MARKETS] if currencies is None else list(currencies)
    payloads, failed = {}, {}
    for k in keys:
        try:
            payloads[k] = surface.build(k)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            failed[k] = f"{type(exc).__name__}: {exc}"
            print(f"  {k}: FAILED {failed[k]}")
    if not payloads:
        raise RuntimeError(f"every market failed: {failed}")
    out_dir = os.path.dirname(out_path) or "."
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render(payloads))
    with open(os.path.join(out_dir, "surface.json"), "w", encoding="utf-8") as fh:
        _json.dump({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "engine": f"voltorch {voltorch.__version__}",
                    "failed": failed,
                    "surfaces": payloads}, fh, separators=(",", ":"))
    return out_path


if __name__ == "__main__":
    import sys

    print(build_site(sys.argv[1] if len(sys.argv) > 1 else "site/index.html"))
