/* Shared helpers and charts for both KODA apps. */
const $ = s => document.querySelector(s);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const msg = d => typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => (x.loc ? x.loc[x.loc.length - 1] + ': ' : '') + (x.msg || '')).join('. ') || 'Check the values and try again.' : 'Something went wrong. Please try again.';
// The signed-in session travels in a header. The traveler app and the insurer desk keep separate sessions.
const sessionKey = () => location.pathname.startsWith('/console') ? 'koda.token' : 'calmy.token';
const api = (url, method = 'GET', body) => {
  const tok = store.get(sessionKey()), staff = store.get('koda.staff');
  return fetch(url, {method, headers: {'Content-Type': 'application/json', ...(tok ? {'X-Session': tok} : {}), ...(staff ? {'X-Staff': staff} : {})}, body: body ? JSON.stringify(body) : undefined})
    .then(r => r.ok ? r.json() : r.json().catch(() => ({})).then(e => {
      const m = msg(e.detail);
      if (r.status === 401 && !url.startsWith('/api/auth/')) window.dispatchEvent(new CustomEvent('koda:unauth', {detail: m}));
      return Promise.reject(m);
    }));
};
let _tt;
function toast(t, bad = false) {
  let el = $('#toast'); if (!el) { el = document.createElement('div'); el.id = 'toast'; el.setAttribute('role', 'status'); document.body.appendChild(el); }
  el.textContent = t; el.className = bad === false ? '' : 'bad'; if (bad === 'ok') el.className = '';
  clearTimeout(_tt); _tt = setTimeout(() => el.className = 'hidden', 5200);
}
const fail = e => toast(e, true);
const num = n => Number(n).toLocaleString('en-US');
const money = (n, c) => num(n) + ' ' + c;
const usd = n => '$' + num(Math.round(n));
const hm = m => `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
const ago = t => { if (!t) return 'not yet'; const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : Math.round(m / 60) + ' h ago'; };
const ico = (n, c = '') => `<span class="ms ${c}" aria-hidden="true">${n}</span>`;
const store = {get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {} }};

/* Where a delay sits against the payout tiers. */
function delayTrack(delay, r, cur, dark) {
  const t1 = +r.delay_tier1_minutes, t2 = +r.delay_tier2_minutes, max = Math.max(t2 * 1.5, delay * 1.08);
  const p = v => (100 * v / max).toFixed(2), pos = Math.min(100, 100 * delay / max);
  const label = delay > 0 ? hm(delay) + ' late' : 'On time';
  return `<div class="dtrack tall ${dark ? 'dark' : ''}" role="img" aria-label="${esc(label)}. Pays ${num(r.delay_tier1_payout)} ${cur} from ${hm(t1)} and ${num(r.delay_tier2_payout)} ${cur} from ${hm(t2)}.">
    <div class="dt-bar"><i class="dt-off" style="width:${p(t1)}%"></i><i class="dt-t1" style="width:${p(t2 - t1)}%"></i><i class="dt-t2" style="flex:1"></i>
      <b class="dt-mark ${pos > 82 ? 'edge' : ''}" style="left:${pos}%"><span>${label}</span></b></div>
    <div class="dt-scale"><span>0</span><span style="left:${p(t1)}%">${hm(t1)}</span><span style="left:${p(t2)}%">${hm(t2)}</span></div>
    <div class="dt-pay"><span style="left:${p(t1)}%">${num(r.delay_tier1_payout)} ${cur}</span><span style="left:${p(t2)}%">${num(r.delay_tier2_payout)} ${cur}</span></div></div>`;
}

/* Risk score as the sum of its parts, with the auto-pay threshold marked. parts: [[reason, points]] */
const SB_COLORS = ['#97A3B4', '#F2B544', '#E08A12', '#BF5A0B', '#8F3A0A'];
function scoreBreakdown(parts, th) {
  const total = parts.reduce((a, p) => a + p[1], 0), cap = Math.max(100, total);
  const w = v => (100 * v / cap).toFixed(2);
  return `<div class="sb"><div class="sb-th" style="left:${w(th)}%"><span>Auto pay below ${th}</span></div>
    <div class="sb-bar" role="img" aria-label="Risk score ${total} of 100. ${parts.map(p => p[0] + ' ' + p[1]).join('. ')}">${parts.map((p, i) => `<i style="width:${w(p[1])}%;background:${SB_COLORS[Math.min(i, 4)]}" title="${esc(p[0])}: ${p[1]}"></i>`).join('')}</div>
    <div class="sb-rows">${parts.map((p, i) => `<div class="sb-row"><u style="background:${SB_COLORS[Math.min(i, 4)]}"></u><span>${esc(p[0])}</span><b class="mono">+${p[1]}</b></div>`).join('')}
      <div class="sb-row" style="border-top:1px solid var(--line);padding-top:8px"><span></span><b>Score</b><b class="mono">${Math.min(total, 100)}</b></div></div></div>`;
}

/* Horizontal bars with direct values. rows: [{label, value, text}] */
function barList(rows) {
  const max = Math.max(...rows.map(r => r.value), 1);
  return rows.map(r => `<div class="bl"><span>${esc(r.label)}</span><div class="bl-t"><i style="width:${(100 * r.value / max).toFixed(1)}%"></i></div><b>${esc(r.text)}</b></div>`).join('');
}

/* Claim outcomes as one stacked bar. Status colors always travel with a label and a count. */
function outcomeBar(o) {
  const items = [['Paid automatically', o.auto, '#0B8A5C'], ['Approved by a person', o.reviewed, '#0A62EA'], ['Held for review', o.held, '#E08A12'], ['Declined', o.declined, '#C0362C']];
  const total = items.reduce((a, i) => a + i[1], 0);
  if (!total) return '<p class="mute">No claims yet. Delays that reach a payout rule appear here.</p>';
  return `<div class="ob" role="img" aria-label="${items.map(i => i[0] + ' ' + i[1]).join(', ')}">${items.filter(i => i[1]).map(i => `<i style="flex:${i[1]};background:${i[2]}" title="${i[0]}: ${i[1]}"></i>`).join('')}</div>
    <div class="leg">${items.map(i => `<span><u style="background:${i[2]}"></u>${i[0]} <b>${i[1]}</b></span>`).join('')}</div>`;
}

/* Payout as a function of delay, drawn from the insurer's rules. */
function payoutCurve(r, cur) {
  const t1 = +r.delay_tier1_minutes, t2 = +r.delay_tier2_minutes, p1 = +r.delay_tier1_payout, p2 = +r.delay_tier2_payout;
  const W = 560, H = 210, L = 64, R = 18, T = 20, B = 36, xm = t2 * 1.5, ym = Math.max(p2, 1) * 1.18;
  const x = v => L + (W - L - R) * v / xm, y = v => T + (H - T - B) * (1 - v / ym);
  const path = `M${x(0)},${y(0)} H${x(t1)} V${y(p1)} H${x(t2)} V${y(p2)} H${x(xm)}`;
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Payout steps from 0 to ${num(p1)} ${cur} at ${hm(t1)} and ${num(p2)} ${cur} at ${hm(t2)}" style="width:100%;height:auto;display:block">
    <line x1="${L}" x2="${W - R}" y1="${y(p1)}" y2="${y(p1)}" stroke="#EBEFF4"/><line x1="${L}" x2="${W - R}" y1="${y(p2)}" y2="${y(p2)}" stroke="#EBEFF4"/>
    <line x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}" stroke="#97A3B4"/>
    <path d="${path} V${y(0)} H${x(0)} Z" fill="#0A62EA" opacity=".08"/><path d="${path}" fill="none" stroke="#0A62EA" stroke-width="2.5" stroke-linejoin="round"/>
    <circle cx="${x(t1)}" cy="${y(p1)}" r="5" fill="#fff" stroke="#0A62EA" stroke-width="2.5"><title>${hm(t1)} delay pays ${num(p1)} ${cur}</title></circle>
    <circle cx="${x(t2)}" cy="${y(p2)}" r="5" fill="#fff" stroke="#0A62EA" stroke-width="2.5"><title>${hm(t2)} delay pays ${num(p2)} ${cur}</title></circle>
    <g font-size="11" fill="#66758B" font-family="Figtree">
      <text x="${L - 8}" y="${y(p1) + 4}" text-anchor="end">${num(p1)}</text><text x="${L - 8}" y="${y(p2) + 4}" text-anchor="end">${num(p2)}</text><text x="${L - 8}" y="${y(0) + 4}" text-anchor="end">0</text>
      <text x="${x(0)}" y="${H - 14}">0</text><text x="${x(t1)}" y="${H - 14}" text-anchor="middle">${hm(t1)}</text><text x="${x(t2)}" y="${H - 14}" text-anchor="middle">${hm(t2)}</text>
      <text x="${W - R}" y="${H - 2}" text-anchor="end">Delay, hours and minutes. Payout in ${cur}.</text></g></svg>`;
}

/* Decorative route map for the first screen: arcs between airports over a graticule. */
function routeArt() {
  const pts = [[40, 118], [118, 70], [196, 96], [262, 52], [330, 104]];
  const arc = (a, b) => `M${a[0]},${a[1]} Q${(a[0] + b[0]) / 2},${Math.min(a[1], b[1]) - 46} ${b[0]},${b[1]}`;
  return `<svg viewBox="0 0 370 150" aria-hidden="true" style="width:100%;height:auto;display:block">
    ${[30, 60, 90, 120].map(v => `<line x1="0" x2="370" y1="${v}" y2="${v}" stroke="#1B4079" stroke-width="1"/>`).join('')}
    ${[60, 120, 180, 240, 300].map(v => `<line y1="0" y2="150" x1="${v}" x2="${v}" stroke="#1B4079" stroke-width="1"/>`).join('')}
    ${pts.slice(0, -1).map((p, i) => `<path d="${arc(p, pts[i + 1])}" fill="none" stroke="#0DC5FB" stroke-width="1.6" stroke-dasharray="3 4"/>`).join('')}
    ${pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="${i === 3 ? 5 : 3.5}" fill="${i === 3 ? '#0DC5FB' : '#071C3F'}" stroke="#0DC5FB" stroke-width="1.6"/>`).join('')}</svg>`;
}

/* ---- AI identity helpers ---- */
const STAR = '<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="aig" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0DC5FB"/><stop offset="1" stop-color="#0A62EA"/></linearGradient></defs><path d="M12 2c.6 5.4 4.6 9.4 10 10-5.4.6-9.4 4.6-10 10-.6-5.4-4.6-9.4-10-10 5.4-.6 9.4-4.6 10-10z" fill="url(#aig)"/></svg>';
const aiChip = (t = 'AI', dark = false) => `<span class="ai ${dark ? 'dark' : ''}">${STAR}${esc(t)}</span>`;

/* Text the AI wrote. It types in once; after that it renders whole so polling never replays it. */
const _typed = new Set();
function aiText(t) {
  const done = _typed.has(t);
  return `<p class="aitext" data-t="${esc(t)}">${done ? esc(t) : ''}</p>`;
}
function typeAll(root = document) {
  root.querySelectorAll('.aitext[data-t]').forEach(el => {
    const t = el.dataset.t; if (_typed.has(t) || el.textContent) return;
    _typed.add(t);
    if (matchMedia('(prefers-reduced-motion:reduce)').matches) { el.textContent = t; return; }
    let i = 0; el.classList.add('typing');
    const tm = setInterval(() => { i = Math.min(t.length, i + 2); el.textContent = t.slice(0, i); if (i >= t.length) { clearInterval(tm); el.classList.remove('typing'); } }, 18);
  });
}

/* Risk gauge: green up to the auto pay line, amber past it. */
function gauge(score, th) {
  const a = v => Math.PI * (1 - v / 100), cx = 120, cy = 112, r = 88;
  const pt = (v, rr = r) => [cx + rr * Math.cos(a(v)), cy - rr * Math.sin(a(v))];
  const arc = (v0, v1) => { const [x0, y0] = pt(v0), [x1, y1] = pt(v1); return `M${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)}`; };
  const [nx, ny] = pt(Math.min(score, 100), r - 14), hold = score >= th;
  return `<svg class="gauge" viewBox="0 0 240 168" role="img" aria-label="Risk score ${score} of 100. Auto pay below ${th}.">
    <path d="${arc(0, th)}" stroke="#0B8A5C" stroke-width="16" fill="none" opacity=".9"/><path d="${arc(th + .8, 100)}" stroke="#E08A12" stroke-width="16" fill="none"/>
    <line x1="${cx}" y1="${cy}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="#071C3F" stroke-width="3" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="7" fill="#071C3F"/>
    <text x="${cx}" y="${cy + 30}" text-anchor="middle" font-size="26" font-weight="800" fill="#071C3F">${score}</text>
    <text x="22" y="${cy + 18}" font-size="11" fill="#66758B" font-family="Figtree">0</text><text x="218" y="${cy + 18}" text-anchor="end" font-size="11" fill="#66758B" font-family="Figtree">100</text>
    <text x="${cx}" y="${cy + 50}" text-anchor="middle" font-size="12" font-weight="700" fill="${hold ? '#8A5300' : '#0B6B49'}" font-family="Figtree">${hold ? 'Sent to a person' : 'Safe to pay automatically'}</text></svg>`;
}

/* KODA's decision time next to a manual claim. The manual bar is illustrative, drawn hatched on purpose. */
function speedCompare(sec) {
  return `<div class="spd"><div class="r"><b>KODA AI</b><div class="b calmy"><i style="width:7%">${sec}s</i></div></div>
    <div class="r"><span class="mute">Manual claim (industry)</span><div class="b manual"><i>About 14 days</i></div></div></div>`;
}
