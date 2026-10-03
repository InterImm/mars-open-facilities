// Mars Open Facilities: reads the static data the update workflow publishes (data/) and draws
// the overview and one page per facility. No build step, no framework; charts use uPlot.

const DATA = 'data/';
const REPO = 'https://github.com/InterImm/mars-open-facilities';
const SOL_MS = 88775244.147;
const STATUS = {
  operating: 'Operating', reduced: 'Reduced output', maintenance: 'Maintenance', offline: 'Offline',
};

// Mars time from the InterImm mars-clock library, with a local fallback for the Mars Sol Date.
let mt = null;
import('https://cdn.jsdelivr.net/gh/InterImm/mars-clock@gh-pages/lib/marstime.js')
  .then((m) => { mt = m; tickClock(); })
  .catch(() => {});
const msdOf = (ms) => (mt ? mt.marsSolDate(ms) : ((ms / 86400000 + 2440587.5 + 69.184 / 86400) - 2405522.0028779) / 1.0274912517);

const $ = (sel, root = document) => root.querySelector(sel);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null) continue;
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
};
const cache = new Map();
async function get(path, kind = 'json') {
  if (!cache.has(path)) {
    cache.set(path, fetch(DATA + path, { cache: 'no-cache' }).then((r) => {
      if (!r.ok) throw new Error(`${path}: ${r.status}`);
      return kind === 'json' ? r.json() : r.text();
    }));
  }
  return cache.get(path);
}

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  const head = lines[0].split(',');
  const cols = head.map(() => []);
  for (let i = 1; i < lines.length; i++) {
    const parts = lines[i].split(',');
    for (let j = 0; j < head.length; j++) {
      const v = parts[j];
      cols[j].push(j === 0 && head[0] === 'time' ? Date.parse(v) / 1000 : (v === '' || v == null ? null : Number(v)));
    }
  }
  return Object.fromEntries(head.map((h, j) => [h, cols[j]]));
}

const nf = (v, digits = 3) => {
  if (v == null || !Number.isFinite(v)) return '–';
  const a = Math.abs(v);
  const d = a >= 1000 ? 0 : a >= 100 ? 1 : a >= 10 ? Math.min(2, digits) : digits;
  return v.toLocaleString('en-US', { maximumFractionDigits: d, minimumFractionDigits: 0 });
};
const unitOf = (t) => t.display_unit || t.unit;
const scaled = (t, v) => (v == null ? v : v * (t.scale || 1));
const utc = (ms) => new Date(ms).toISOString().slice(0, 16).replace('T', ' ');

// --- the clock bar -------------------------------------------------------------------
// The site runs in story time: real time plus a fixed shift from the data (2026 reads as 2219).
let dataTime = null;
let shift = null;
function tickClock() {
  if (shift == null) return;
  const now = Date.now() + shift;
  $('#clock-utc').textContent = `${utc(now)}`;
  $('#clock-msd').textContent = msdOf(now).toFixed(4);
  if (mt) {
    const ii = mt.interimmTime(now);
    $('#clock-ii').textContent = `${ii.iso} ${ii.clock}`;
  }
  if (dataTime) {
    const mins = Math.round((now - dataTime) / 60000);
    $('#clock-data').textContent = `${utc(dataTime)} (${mins < 90 ? `${mins} min` : `${Math.round(mins / 60)} h`} ago)`;
  }
}
setInterval(tickClock, 1000);

// --- overview --------------------------------------------------------------------------
async function overview(view) {
  view.replaceChildren($('#tpl-overview').content.cloneNode(true));
  const index = await get('index.json');
  shift = (index.story_shift_s || 0) * 1000;
  dataTime = Math.max(...index.facilities.map((f) => Date.parse(f.time) || 0));
  tickClock();
  const cards = $('#cards', view);
  for (const f of index.facilities) cards.append(card(f));
  drawMap($('#map', view), index.facilities);
  // the sparklines load after the cards are on screen
  for (const f of index.facilities) sparkline(f).catch(() => {});
}

function statusPill(status) {
  return el('span', { class: `status status-${status}`, text: STATUS[status] || status });
}

function kpiTile(k, big = false) {
  const share = k.design ? Math.max(0, Math.min(1.2, (k.sol_mean ?? k.value ?? 0) / k.design)) : null;
  const meter = share != null && (k.kind || 'output') !== 'level'
    ? el('span', { class: 'meter', title: `sol mean ${nf(k.sol_mean)} of design ${nf(k.design)} ${k.unit}` }, el('span', { style: `width:${Math.min(100, share * 100).toFixed(1)}%` }))
    : null;
  return el('div', { class: `kpi${big ? ' kpi-big' : ''}${k.kind === 'input' ? ' kpi-input' : ''}`, title: k.note },
    el('span', { class: 'kpi-label', text: k.label }),
    el('span', { class: 'kpi-value' }, nf(k.value), el('small', { text: ` ${k.unit}` })),
    meter,
    el('span', { class: 'kpi-sub', text: k.sol_mean != null ? `sol mean ${nf(k.sol_mean)} · design ${nf(k.design)}` : `design ${nf(k.design)}` }));
}

function card(f) {
  const c = el('article', { class: 'card card-link-wrap facility-card', id: `card-${f.id}` },
    el('div', { class: 'card-top' },
      el('p', { class: 'card-where', text: `${f.city} · ${f.region.replace(' Metropolitan Area', '')}${f.since ? ` · since ${f.since}` : ''}` }),
      statusPill(f.status)),
    el('h3', { class: 'card-title' }, el('a', { class: 'card-link', href: `#/${f.id}`, text: f.name })),
    el('p', { class: 'card-zh', lang: 'zh', text: f.name_zh }),
    el('div', { class: 'spark', 'aria-hidden': 'true' }),
    el('div', { class: 'kpis' }, ...f.kpis.slice(0, 2).map((k) => kpiTile(k))),
    el('p', { class: 'card-cta' }, 'Open ', el('span', { text: '→' })));
  return c;
}

async function sparkline(f) {
  const d = parseCsv(await get(`${f.id}/recent.csv`, 'text'));
  const k = f.kpis[0];
  const ys = d[k.tag];
  const host = $(`#card-${f.id} .spark`);
  if (!host || !ys?.length) return;
  const w = 300; const h = 54;
  const vals = ys.filter((v) => v != null);
  const lo = Math.min(0, ...vals); const hi = Math.max(k.design || 0, ...vals) * 1.05 || 1;
  const step = Math.max(1, Math.floor(ys.length / w));
  let p = '';
  for (let i = 0; i < ys.length; i += step) {
    if (ys[i] == null) continue;
    const x = (i / (ys.length - 1)) * w; const y = h - ((ys[i] - lo) / (hi - lo)) * h;
    p += `${p ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
  }
  const dy = k.design ? (h - ((k.design - lo) / (hi - lo)) * h).toFixed(1) : null;
  host.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${k.label}, last 14 days">
    ${dy ? `<line x1="0" x2="${w}" y1="${dy}" y2="${dy}" class="spark-design"/>` : ''}
    <path d="${p}" class="spark-line" vector-effect="non-scaling-stroke"/></svg>
    <span class="spark-cap">${k.label}, 14 days</span>`;
}

function drawMap(fig, facilities) {
  const W = 720; const H = 360;
  const x = (lon) => ((((lon + 180) % 360) + 360) % 360) / 360 * W;
  const y = (lat) => (90 - lat) / 180 * H;
  const regions = [
    ['Isidis', 88, 13], ['Amazonis', -163, 10], ['Meridiani', 0, 12], ['Hellas', 72, -42],
  ];
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Map of Mars with the facilities">`;
  s += `<rect width="${W}" height="${H}" class="map-bg" rx="12"/>`;
  for (let lo = -150; lo <= 150; lo += 30) s += `<line x1="${x(lo)}" x2="${x(lo)}" y1="0" y2="${H}" class="map-grid"/>`;
  for (let la = -60; la <= 60; la += 30) s += `<line y1="${y(la)}" y2="${y(la)}" x1="0" x2="${W}" class="map-grid${la === 0 ? ' map-eq' : ''}"/>`;
  for (const [n, lo, la] of regions) s += `<text x="${Math.max(48, x(lo))}" y="${y(la) - 24}" class="map-region" text-anchor="middle">${n}</text>`;
  // labels go right of their dot and step down until they clear the ones already placed
  const placed = [];
  const dots = [...facilities].sort((a, b) => b.lat - a.lat || a.lon - b.lon);
  for (const f of dots) {
    const label = `${f.city.replace(' City', '')} · ${f.name.replace(new RegExp(`^${f.city.split(' ')[0]} `), '')}`;
    const w = label.length * 6.6;
    const cx = x(f.lon); const cy = y(f.lat);
    const lx = cx + w + 12 > W ? cx - 10 - w : cx + 10;
    let ly = cy + 4;
    while (placed.some((p) => Math.abs(p.y - ly) < 14 && lx < p.x + p.w && p.x < lx + w)) ly += 14;
    placed.push({ x: lx, y: ly, w });
    s += `<a href="#/${f.id}"><circle cx="${cx}" cy="${cy}" r="6" class="map-dot status-${f.status}"/>`;
    if (Math.abs(ly - cy - 4) > 1) s += `<line x1="${cx}" y1="${cy}" x2="${lx}" y2="${ly - 4}" class="map-leader"/>`;
    s += `<text x="${lx}" y="${ly}" class="map-label">${label}</text></a>`;
  }
  s += '</svg><figcaption>Equirectangular, longitude east. Positions from the InterImm city map.</figcaption>';
  fig.innerHTML = s;
}

// --- one facility --------------------------------------------------------------------
let charts = [];
async function facility(view, id) {
  view.replaceChildren($('#tpl-facility').content.cloneNode(true));
  const [meta, log, recentText] = await Promise.all([
    get(`${id}/meta.json`), get(`${id}/log.json`).catch(() => []), get(`${id}/recent.csv`, 'text'),
  ]);
  shift = (meta.story_shift_s || 0) * 1000;
  dataTime = Date.parse(meta.time); tickClock();
  document.title = `${meta.name} | Mars Open Facilities`;
  const f = (k) => $(`[data-f="${k}"]`, view);
  f('region').textContent = `${meta.region} · ${meta.region_zh || ''}`;
  f('name').textContent = meta.name;
  f('where').textContent = `${meta.name_zh} · ${meta.city} (${meta.city_zh}) · ${Math.abs(meta.lat).toFixed(1)}°${meta.lat >= 0 ? 'N' : 'S'} ${((meta.lon + 360) % 360).toFixed(1)}°E · operated by ${meta.operator}${meta.since ? ` · in service since ${meta.since}` : ''}`;
  f('status').replaceWith(statusPill(meta.status));
  f('mode').textContent = meta.mode ? `Mode: ${meta.mode.replace(/_/g, ' ')}` : '';
  f('summary').textContent = meta.summary;
  f('kpis').append(...meta.kpis.map((k) => kpiTile(k, true)));
  f('process').append(...meta.process.map((p) => el('li', { text: p })));
  f('story').textContent = meta.since_note ? `${meta.story} ${meta.since_note}` : meta.story;
  f('book').href = meta.book;
  f('sources').append(...meta.sources.map((s) => el('li', {}, el('a', { href: s.url, text: s.label }))));
  const when = (t) => `${utc(Date.parse(t))} UTC · sol ${Math.floor(msdOf(Date.parse(t)))}`;
  const up = meta.upcoming.length
    ? meta.upcoming.map((u) => el('li', {}, el('time', { text: when(u.time) }),
      el('span', { text: `${u.active ? 'In progress: ' : ''}${taskName(u.task)} (${Array.isArray(u.target) ? u.target.join(', ') : u.target})${u.duration_h ? `, ${nf(u.duration_h)} h` : ''}${u.until ? `, until ${utc(Date.parse(u.until))}` : ''}` })))
    : [el('li', { class: 'muted', text: 'Nothing planned.' })];
  f('upcoming').append(...up);
  const entries = log.slice(-25).reverse();
  f('log').append(...(entries.length ? entries.map((e) => el('li', {}, el('time', { text: when(e.time) }), el('span', { text: e.event }))) : [el('li', { class: 'muted', text: 'No entries yet.' })]));
  f('downloads').append(
    el('li', {}, el('a', { href: `${DATA}${id}/recent.csv`, text: 'recent.csv' }), ' last 14 days, every 10 minutes'),
    el('li', {}, el('a', { href: `${DATA}${id}/sols.csv`, text: 'sols.csv' }), ' one row per sol, whole history'),
    el('li', {}, el('a', { href: `${DATA}${id}/meta.json`, text: 'meta.json' }), ' tags, status and plan'),
    el('li', {}, el('a', { href: `${REPO}/blob/main/${meta.scenario}`, text: 'scenario.yaml' }), ` the Homeostat model (homeostat ${meta.homeostat})`));

  const recent = parseCsv(recentText);
  let sols = null;
  const groups = [];
  for (const t of meta.tags) {
    let g = groups.find((x) => x.name === t.group);
    if (!g) groups.push(g = { name: t.group, tags: [] });
    g.tags.push(t);
  }
  const draw = async (range) => {
    for (const b of view.querySelectorAll('[data-range]')) b.setAttribute('aria-pressed', String(b.dataset.range === String(range)));
    let src = recent;
    if (range === 'all') {
      if (!sols) {
        const s = parseCsv(await get(`${id}/sols.csv`, 'text'));
        // a sol mean is drawn at the middle of its sol
        s.time = s.msd.map((m) => (((m + 0.5) * 1.0274912517 + 2405522.0028779 - 69.184 / 86400) - 2440587.5) * 86400);
        sols = s;
      }
      src = sols;
    }
    let lo = 0;
    if (range !== 'all') {
      const end = src.time[src.time.length - 1];
      const start = end - (range === 14 ? 14 * 86400 : range * SOL_MS / 1000);
      lo = src.time.findIndex((t) => t >= start);
    }
    renderCharts(f('charts'), groups, src, Math.max(0, lo));
  };
  for (const b of view.querySelectorAll('[data-range]')) {
    b.addEventListener('click', () => draw(b.dataset.range === 'all' ? 'all' : Number(b.dataset.range)));
  }
  await whenUplot();
  draw(3);
}

const taskName = (t) => ({
  stop: 'Shutdown for maintenance', clean: 'Cleaning', replace_catalyst: 'Catalyst or stack replacement',
  calibrate: 'Instrument calibration', replace_valve: 'Valve replacement',
})[t] || t;

function whenUplot() {
  return new Promise((resolve) => {
    const check = () => (window.uPlot ? resolve() : setTimeout(check, 50));
    check();
  });
}

const PALETTE = ['#ff6b3d', '#3d8bff', '#22a77a', '#c48a00', '#a05cff', '#e0457b'];

function renderCharts(host, groups, src, lo) {
  for (const c of charts) c.destroy();
  charts = [];
  host.replaceChildren();
  const xs = src.time.slice(lo);
  const styles = getComputedStyle(document.documentElement);
  const muted = styles.getPropertyValue('--muted').trim() || '#888';
  const grid = styles.getPropertyValue('--border').trim() || '#ddd';
  for (const g of groups) {
    // one chart per unit inside a group, so scales stay honest
    const byUnit = new Map();
    for (const t of g.tags) {
      const u = unitOf(t);
      if (!byUnit.has(u)) byUnit.set(u, []);
      byUnit.get(u).push(t);
    }
    for (const [unit, tags] of byUnit) {
      const box = el('div', { class: 'chart card' },
        el('h3', { class: 'chart-title', text: `${g.name}` }, el('small', { text: ` ${unit}` })));
      host.append(box);
      const width = Math.max(260, box.clientWidth - 32);
      const series = [{ value: (u, v) => (v == null ? '' : `${utc(v * 1000)} · sol ${Math.floor(msdOf(v * 1000))}`) }];
      const data = [xs];
      tags.forEach((t, i) => {
        series.push({ label: `${t.label} (${t.tag})`, stroke: PALETTE[i % PALETTE.length], width: 1.5, spanGaps: false, value: (u, v) => (v == null ? '–' : `${nf(v)} ${unit}`) });
        data.push((src[t.tag] || []).slice(lo).map((v) => scaled(t, v)));
      });
      const opts = {
        width, height: 220, series, legend: { live: true },
        cursor: { drag: { x: true, y: false } },
        scales: { x: { time: true } },
        axes: [
          { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid } },
          { stroke: muted, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, size: 60, values: (u, vs) => vs.map((v) => nf(v)) },
        ],
      };
      charts.push(new window.uPlot(opts, data, box));
    }
  }
}

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    for (const c of charts) c.setSize({ width: Math.max(260, c.root.parentElement.clientWidth - 32), height: 220 });
  }, 150);
});

// --- routing ----------------------------------------------------------------------------
async function route() {
  const view = $('#view');
  const id = location.hash.replace(/^#\/?/, '');
  try {
    if (id) await facility(view, id); else { document.title = 'Mars Open Facilities | InterImm'; await overview(view); }
  } catch (err) {
    view.replaceChildren(el('section', { class: 'section' }, el('div', { class: 'wrap' },
      el('h2', { text: 'The data is not here yet' }),
      el('p', { class: 'muted', text: `The update workflow publishes it on its first run. (${err.message})` }))));
  }
  if (id) window.scrollTo({ top: $('#view').offsetTop - 80 });
}
window.addEventListener('hashchange', route);
tickClock();
route();
