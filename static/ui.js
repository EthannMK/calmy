/* Shared helpers and charts for both Calmy apps. */
const $ = s => document.querySelector(s);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const msg = d => typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => (x.loc ? x.loc[x.loc.length - 1] + ': ' : '') + (x.msg || '')).join('. ') || 'Check the values and try again.' : 'Something went wrong. Please try again.';
const api = (url, method = 'GET', body) =>
  fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined})
    .then(r => r.ok ? r.json() : r.json().catch(() => ({})).then(e => Promise.reject(msg(e.detail))));
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
  const items = [['Paid automatically', o.auto, '#0B8A5C'], ['Approved by a person', o.reviewed, '#1F4FD1'], ['Held for review', o.held, '#E08A12'], ['Declined', o.declined, '#C0362C']];
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
    <path d="${path} V${y(0)} H${x(0)} Z" fill="#1F4FD1" opacity=".08"/><path d="${path}" fill="none" stroke="#1F4FD1" stroke-width="2.5" stroke-linejoin="round"/>
    <circle cx="${x(t1)}" cy="${y(p1)}" r="5" fill="#fff" stroke="#1F4FD1" stroke-width="2.5"><title>${hm(t1)} delay pays ${num(p1)} ${cur}</title></circle>
    <circle cx="${x(t2)}" cy="${y(p2)}" r="5" fill="#fff" stroke="#1F4FD1" stroke-width="2.5"><title>${hm(t2)} delay pays ${num(p2)} ${cur}</title></circle>
    <g font-size="11" fill="#66758B" font-family="Geist">
      <text x="${L - 8}" y="${y(p1) + 4}" text-anchor="end">${num(p1)}</text><text x="${L - 8}" y="${y(p2) + 4}" text-anchor="end">${num(p2)}</text><text x="${L - 8}" y="${y(0) + 4}" text-anchor="end">0</text>
      <text x="${x(0)}" y="${H - 14}">0</text><text x="${x(t1)}" y="${H - 14}" text-anchor="middle">${hm(t1)}</text><text x="${x(t2)}" y="${H - 14}" text-anchor="middle">${hm(t2)}</text>
      <text x="${W - R}" y="${H - 2}" text-anchor="end">Delay, hours and minutes. Payout in ${cur}.</text></g></svg>`;
}

/* Decorative route map for the first screen: arcs between airports over a graticule. */
function routeArt() {
  const pts = [[40, 118], [118, 70], [196, 96], [262, 52], [330, 104]];
  const arc = (a, b) => `M${a[0]},${a[1]} Q${(a[0] + b[0]) / 2},${Math.min(a[1], b[1]) - 46} ${b[0]},${b[1]}`;
  return `<svg viewBox="0 0 370 150" aria-hidden="true" style="width:100%;height:auto;display:block">
    ${[30, 60, 90, 120].map(v => `<line x1="0" x2="370" y1="${v}" y2="${v}" stroke="#25405F" stroke-width="1"/>`).join('')}
    ${[60, 120, 180, 240, 300].map(v => `<line y1="0" y2="150" x1="${v}" x2="${v}" stroke="#25405F" stroke-width="1"/>`).join('')}
    ${pts.slice(0, -1).map((p, i) => `<path d="${arc(p, pts[i + 1])}" fill="none" stroke="#FFB224" stroke-width="1.6" stroke-dasharray="3 4"/>`).join('')}
    ${pts.map((p, i) => `<circle cx="${p[0]}" cy="${p[1]}" r="${i === 3 ? 5 : 3.5}" fill="${i === 3 ? '#FFB224' : '#0C1B2E'}" stroke="#FFB224" stroke-width="1.6"/>`).join('')}</svg>`;
}
