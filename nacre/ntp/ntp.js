// Nacre new tab: elements, customization sheet and the bridge to the browser (nacre://newtab/api/*).
(() => {
const S = window.STATE, C = S.cfg;
const $ = (s, el = document) => el.querySelector(s);
const h = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k.startsWith('on') ? e.addEventListener(k.slice(2), v) : k === 'class' ? e.className = v
    : k === 'html' ? e.innerHTML = v : e.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) e.append(k.nodeType ? k : document.createTextNode(k));
  return e; };
const icon = n => h('span', { class: 'i' }, n);
const api = (path, opts) => fetch('nacre://newtab/api/' + path, opts).then(r => r.json()).catch(() => ({}));
const set = (k, v) => { C[k] = v; return api('set?k=' + encodeURIComponent(k) + '&v=' + encodeURIComponent(JSON.stringify(v))); };

const ELEMENTS = {
  clock: { name: 'Clock', icon: 'schedule', area: 'center' }, greeting: { name: 'Greeting', icon: 'waving_hand', area: 'center' },
  search: { name: 'Search', icon: 'search', area: 'center' }, shortcuts: { name: 'Shortcuts', icon: 'apps', area: 'center' },
  media: { name: 'Now playing', icon: 'music_note', area: 'cards' }, weather: { name: 'Weather', icon: 'partly_cloudy_day', area: 'cards' },
  notes: { name: 'Notes', icon: 'sticky_note_2', area: 'cards' }, reading: { name: 'Reading list', icon: 'chrome_reader_mode', area: 'cards' },
  recent: { name: 'Top sites', icon: 'trending_up', area: 'cards' },
};
const BACKGROUNDS = [['none', 'None', 'block'], ['shapes', 'Shapes', 'category'], ['blobs', 'Blobs', 'bubble_chart'],
  ['particles', 'Constellation', 'hub'], ['waves', 'Waves', 'waves'], ['dots', 'Dot grid', 'grid_on']];

// ── theme bridge (called by the browser when the scheme changes) ──
window.nacreTheme = v => { for (const k in v) document.documentElement.style.setProperty('--' + k, v[k]); NacreBg.readPalette(); };
window.nacreSetShortcuts = v => { S.shortcuts = v; render(); };

// ── background ──
function applyBackground() {
  if (C['ntp.bg'] === 'gooey') C['ntp.bg'] = 'blobs';
  const img = $('#bgimg');
  img.classList.toggle('on', !!C['ntp.bg_image']);
  img.style.backgroundImage = C['ntp.bg_image'] ? `url("nacre://newtab/bg?v=${encodeURIComponent(C['ntp.bg_image'])}")` : '';
  img.style.filter = C['ntp.bg_blur'] ? `blur(${C['ntp.bg_blur']}px)` : '';
  img.style.inset = C['ntp.bg_blur'] ? `-${C['ntp.bg_blur'] * 2}px` : '0';
  $('#dim').style.opacity = C['ntp.bg_image'] ? C['ntp.bg_dim'] : 0;
  NacreBg.start(C['ntp.bg'], { density: C['ntp.bg_density'], speed: C['ntp.bg_speed'], mouse: C['ntp.bg_mouse'], motion: S.motion });
}

// ── elements ──
const R = {
  clock() {
    const t = h('div', { class: 't' }), d = h('div', { class: 'd' });
    const tick = () => { const n = new Date();
      const opts = { hour: '2-digit', minute: '2-digit', hour12: !C['ntp.clock24'] };
      if (C['ntp.seconds']) opts.second = '2-digit';
      const parts = n.toLocaleTimeString([], opts).split(' ');
      t.innerHTML = ''; t.append(parts[0]); if (parts[1]) t.append(h('small', {}, parts[1]));
      d.textContent = n.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' }); };
    tick(); clearInterval(R._clock); R._clock = setInterval(tick, 1000);
    return h('div', { class: 'clock' }, t, d);
  },
  greeting() {
    const hr = new Date().getHours(), part = hr < 5 ? 'Good night' : hr < 12 ? 'Good morning' : hr < 18 ? 'Good afternoon' : 'Good evening';
    return h('div', { class: 'greet' }, part + (C['ntp.name'] ? ', ' + C['ntp.name'] : ''));
  },
  search() {
    const f = h('form', { class: 'search', action: S.engine.url, method: 'get' }, icon('search'),
      h('input', { name: 'q', autocomplete: 'off', placeholder: `Search ${S.engine.name} or type a URL` }), h('button', { class: 'go i' }, 'arrow_forward'));
    f.addEventListener('submit', e => { const s = f.q.value.trim();
      if (/^[\w-]+(\.[\w-]+)+(:\d+)?(\/\S*)?$/.test(s)) { e.preventDefault(); location.href = 'https://' + s; }
      else if (/^\w+:\/\//.test(s)) { e.preventDefault(); location.href = s; } });
    setTimeout(() => f.q.focus(), 50);
    return f;
  },
  shortcuts() {
    const box = h('div', { class: 'tiles' });
    S.shortcuts.forEach(([n, u], i) => box.append(h('a', { class: 't', href: u },
      h('i', { title: 'Edit shortcut', onclick: e => { e.preventDefault(); editShortcut(i); } }, 'edit'),
      h('b', {}, (n || '?')[0].toUpperCase()), h('span', {}, n))));
    if (S.shortcuts.length < 24) box.append(h('a', { class: 't add', href: '#', onclick: e => { e.preventDefault(); editShortcut(-1); } },
      h('b', {}, 'add'), h('span', {}, 'Add shortcut')));
    return box;
  },
  media() { return mediaCard(); },
  weather() {
    const card = h('div', { class: 'card weather' }, h('h3', {}, icon('partly_cloudy_day'), 'Weather'));
    const body = h('div', {}, h('div', { class: 'sub' }, C['ntp.city'] ? 'Loading…' : 'Set your city in ⚙ Customize'));
    card.append(body);
    if (C['ntp.city']) loadWeather(body);
    return card;
  },
  notes() {
    const ta = h('textarea', { class: 'notes', placeholder: 'Jot something down…' }); ta.value = C['ntp.notes'] || '';
    let t = 0; ta.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => set('ntp.notes', ta.value), 500); });
    return h('div', { class: 'card' }, h('h3', {}, icon('sticky_note_2'), 'Notes'), ta);
  },
  reading() {
    const items = S.reading.filter(r => !r.read).slice(0, 5);
    return h('div', { class: 'card list' }, h('h3', {}, icon('chrome_reader_mode'), 'Reading list'),
      items.length ? items.map(r => h('a', { href: r.url }, icon('article'), h('span', {}, r.title || r.url)))
                   : h('div', { class: 'sub' }, 'Nothing saved — use “Add to reading list” in the menu'));
  },
  recent() {
    return h('div', { class: 'card list' }, h('h3', {}, icon('trending_up'), 'Top sites'),
      S.recent.length ? S.recent.map(r => h('a', { href: r.url }, icon('public'), h('span', {}, r.title || r.host)))
                      : h('div', { class: 'sub' }, 'Sites you visit often appear here'));
  },
};

// ── now playing: a clone of the toolbar player (cookie art + real visualizer, wavy seek bar, full controls) ──
function mediaCard() {
  const TAU = Math.PI * 2, N = 56, LOBES = 9, AMP = 0.055, SIZE = 196, dpr = Math.min(2, devicePixelRatio || 1);
  const cv = h('canvas', { class: 'cookie', width: SIZE * dpr, height: SIZE * dpr }), cx = cv.getContext('2d');
  const wave = h('canvas', { class: 'wave' }), wx = wave.getContext('2d');
  const title = h('div', { class: 'tt' }, 'Nothing playing'), artist = h('div', { class: 'ar' }, 'Play something in a tab'), album = h('div', { class: 'al' });
  const tpos = h('span', { class: 'time' }, '0:00'), tdur = h('span', { class: 'time' }, '0:00');
  const btn = (n, cls, fn, tip) => h('button', { class: 'ic ' + cls, title: tip || '', onclick: fn }, n);
  const shuffle = btn('shuffle', 'tonal', null, "Shuffle isn't exposed by websites"); shuffle.disabled = true;
  const prev = btn('skip_previous', '', () => act('prev'), 'Previous'), play = btn('play_arrow', 'play', () => act('toggle'), 'Play / pause');
  const next = btn('skip_next', '', () => act('next'), 'Next'), loop = btn('repeat', 'tonal', () => act('loop'), 'Repeat');
  const chip = h('button', { class: 'src', onclick: () => api('media?act=focus') }, icon('tab'), h('span', {}, ''));
  const card = h('div', { class: 'card player' }, cv, h('div', { class: 'meta' }, title, artist, album,
    h('div', { class: 'prog' }, tpos, wave, tdur), h('div', { class: 'ctl' }, shuffle, prev, play, next, loop), chip));
  let st = null, img = null, artUrl = '', rot = 0, omega = 0, amp = 0, phase = 0, drag = null, last = performance.now();
  const lv = new Float32Array(N), target = new Float32Array(N);
  const fmt = s => { s = Math.max(0, Math.floor(s || 0)); return s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  const act = (a, v) => api('media?act=' + a + (v != null ? '&v=' + v : '')).then(apply);
  function apply(s) {
    st = s && s.title ? s : null; const playing = !!(st && !st.paused);
    card.classList.toggle('playing', playing); card.classList.toggle('idle', !st);
    title.textContent = st ? st.title : 'Nothing playing'; artist.textContent = st ? (st.artist ? 'by ' + st.artist : '') : 'Play something in a tab';
    album.textContent = st && st.album || ''; play.textContent = playing ? 'pause' : 'play_arrow';
    loop.classList.toggle('on', !!(st && st.loop)); tdur.textContent = fmt(st && st.dur);
    chip.lastChild.textContent = st ? st.host : 'No media'; [prev, play, next, loop].forEach(b => b.disabled = !st);
    const a = st && st.art || '';
    if (a !== artUrl) { artUrl = a; img = null; if (a) { const im = new Image(); im.onload = () => { if (artUrl === a) img = im; };
      im.src = 'nacre://newtab/art?u=' + encodeURIComponent(a); } }
  }
  // seek bar
  const fracAt = e => { const r = wave.getBoundingClientRect(); return Math.max(0, Math.min(1, (e.clientX - r.left - 6) / (r.width - 12))); };
  wave.addEventListener('pointerdown', e => { if (!st || !st.dur) return; drag = fracAt(e); wave.setPointerCapture(e.pointerId); });
  wave.addEventListener('pointermove', e => { if (drag != null) drag = fracAt(e); });
  wave.addEventListener('pointerup', () => { if (drag != null && st) { st.pos = drag * st.dur; act('seek', st.pos.toFixed(2)); } drag = null; });

  function drawCookie(dt) {
    const c = cx, s = SIZE, R = s * 0.335, mid = s / 2, P = getComputedStyle(document.documentElement);
    const col = n => P.getPropertyValue('--' + n).trim();
    const playing = st && !st.paused;
    omega += ((playing ? 0.42 : 0) - omega) * Math.min(1, dt * 2.5); rot = (rot + omega * dt) % TAU;
    for (let i = 0; i < N; i++) { const t = playing ? target[i] : 0; lv[i] += (t - lv[i]) * (t > lv[i] ? 0.55 : 0.13); }
    c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, s, s);
    const surf = th => R * (1 + AMP * Math.cos(LOBES * (th - rot)));
    c.beginPath(); for (let i = 0; i <= 240; i++) { const th = TAU * i / 240, r = surf(th);
      i ? c.lineTo(mid + r * Math.cos(th), mid + r * Math.sin(th)) : c.moveTo(mid + r * Math.cos(th), mid + r * Math.sin(th)); }
    c.closePath(); c.save(); c.clip();
    if (img) { const ext = R * (1 + AMP), k = Math.max(2 * ext / img.width, 2 * ext / img.height);       // cover, upright
      c.drawImage(img, mid - img.width * k / 2, mid - img.height * k / 2, img.width * k, img.height * k); }
    else { c.fillStyle = col('pc'); c.fillRect(0, 0, s, s); c.fillStyle = col('opc'); c.font = "64px 'Material Symbols Rounded'";
      c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('music_note', mid, mid); }
    c.restore();
    const maxlen = s / 2 - R * (1 + AMP) - 10; c.lineCap = 'round'; c.lineWidth = 3.2; c.strokeStyle = col('p');
    for (let i = 0; i < N; i++) { const th = TAU * i / N + Math.PI / 2, r0 = surf(th) + 7, r1 = r0 + 2.5 + lv[i] * maxlen;
      c.globalAlpha = 0.55 + 0.45 * lv[i]; c.beginPath(); c.moveTo(mid + r0 * Math.cos(th), mid + r0 * Math.sin(th));
      c.lineTo(mid + r1 * Math.cos(th), mid + r1 * Math.sin(th)); c.stroke(); }
    c.globalAlpha = 1;
  }
  function drawWave(dt) {
    const w = wave.clientWidth, H = wave.clientHeight; if (!w) return;
    if (wave.width !== w * dpr) { wave.width = w * dpr; wave.height = H * dpr; }
    const c = wx, P = getComputedStyle(document.documentElement), col = n => P.getPropertyValue('--' + n).trim();
    const playing = st && !st.paused; amp += ((playing ? 1 : 0) - amp) * Math.min(1, dt * 3); phase = (phase + dt * TAU * 0.8) % TAU;
    if (playing && st.dur && drag == null) st.pos = Math.min(st.dur, (st.pos || 0) + dt);
    const frac = drag != null ? drag : st && st.dur ? (st.pos || 0) / st.dur : 0;
    tpos.textContent = fmt(drag != null && st ? drag * st.dur : st && st.pos);
    c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, H);
    const x0 = 6, x1 = w - 6, cy = H / 2, px = x0 + (x1 - x0) * frac, gap = 7, A = amp * H * 0.115, end = px - gap;
    c.lineCap = 'round'; c.lineJoin = 'round'; c.lineWidth = 4;
    if (end > x0) { c.beginPath(); c.moveTo(x0, cy);
      for (let x = x0; x < end; x += 1.5) { const xx = Math.min(end, x + 1.5), tp = Math.min(1, (xx - x0) / 10, (end - xx) / 10 + 0.15);
        c.lineTo(xx, cy + A * tp * Math.sin((xx - x0) / 26 * TAU - phase)); }
      c.strokeStyle = col('p'); c.stroke(); }
    if (x1 - (px + gap) > 0) { c.beginPath(); c.moveTo(px + gap, cy); c.lineTo(x1, cy); c.strokeStyle = col('sc'); c.stroke();
      c.fillStyle = col('p'); c.beginPath(); c.arc(x1, cy, 2, 0, TAU); c.fill(); }
    const th = H * 0.72 + (drag != null ? 4 : 0); c.fillStyle = col('p'); c.beginPath(); c.roundRect(px - 2, cy - th / 2, 4, th, 2); c.fill();
  }
  let lvBusy = false, started = false;
  (function loop(now) {
    if (!card.isConnected) { if (!started) requestAnimationFrame(loop); return; }   // not on the page yet
    started = true; const dt = Math.min(0.05, ((now || performance.now()) - last) / 1000); last = now || performance.now();
    if (!document.hidden) { drawCookie(dt); drawWave(dt);
      if (st && !st.paused && !lvBusy) { lvBusy = true; api('levels').then(v => { if (Array.isArray(v)) for (let i = 0; i < N; i++) {
        const d = Math.min(i, N - i) / (N / 2); target[i] = v[Math.min(v.length - 1, Math.floor(d * v.length))]; }
        setTimeout(() => lvBusy = false, 50); }); } }
    requestAnimationFrame(loop);
  })();
  const poll = () => !document.hidden && api('media').then(apply);
  poll(); clearInterval(R._media); R._media = setInterval(poll, 1000);
  return card;
}

const WMO = [[0, 'clear_day', 'clear_night', 'Clear'], [2, 'partly_cloudy_day', 'partly_cloudy_night', 'Partly cloudy'],
  [3, 'cloud', 'cloud', 'Overcast'], [48, 'foggy', 'foggy', 'Fog'], [57, 'rainy_light', 'rainy_light', 'Drizzle'],
  [67, 'rainy', 'rainy', 'Rain'], [77, 'weather_snowy', 'weather_snowy', 'Snow'], [82, 'rainy_heavy', 'rainy_heavy', 'Showers'],
  [86, 'weather_snowy', 'weather_snowy', 'Snow showers'], [99, 'thunderstorm', 'thunderstorm', 'Thunderstorm']];
const units = () => C['ntp.units'] === 'f' || (C['ntp.units'] === 'auto' && S.imperial) ? 'f' : 'c';
async function loadWeather(body) {
  const loc = C['ntp.city_loc'];
  if (!loc || loc.lat == null) {                       // older setting with only a name: resolve it once
    const g = await api('geocode?q=' + encodeURIComponent(C['ntp.city']));
    const r = (g.results || [])[0];
    if (!r) return body.replaceChildren(h('div', { class: 'sub' }, `Couldn't find “${C['ntp.city']}” — pick it again in ⚙`));
    await set('ntp.city_loc', r); return loadWeather(body);
  }
  const w = await api(`weather?lat=${loc.lat}&lon=${loc.lon}&units=${units()}`);
  if (w.error || w.temp == null) return body.replaceChildren(h('div', { class: 'sub' }, 'Weather unavailable right now'));
  const [, day, night, label] = WMO.find(([max]) => w.code <= max) || WMO[0];
  const r = v => Math.round(v);
  body.replaceChildren(
    h('div', { class: 'tmp' }, icon(w.day ? day : night), r(w.temp) + w.unit.slice(0, 1)),
    h('div', { class: 'wl' }, label + ' · feels like ' + r(w.feels) + '°'),
    h('div', { class: 'stats' },
      h('span', {}, icon('thermostat'), `${r(w.hi)}° / ${r(w.lo)}°`), h('span', {}, icon('umbrella'), (w.rain ?? 0) + '%'),
      h('span', {}, icon('humidity_percentage'), w.humidity + '%'), h('span', {}, icon('air'), r(w.wind) + ' ' + w.wind_unit)),
    h('div', { class: 'sub' }, icon('location_on'), ' ', loc.label || C['ntp.city']));
  clearTimeout(R._wx); R._wx = setTimeout(() => body.isConnected && loadWeather(body), 15 * 60 * 1000);
}

// place picker with suggestions: "38382", "Trenton, TN", "Paris, France"…
function placeField() {
  const box = h('div', { class: 'place' }), input = h('input', { type: 'text', placeholder: 'City or ZIP', value: C['ntp.city'] || '' });
  const list = h('div', { class: 'sugg', hidden: '' }); let t = 0, results = [];
  const choose = r => { set('ntp.city', r.label); set('ntp.city_loc', r); input.value = r.label; list.hidden = true; render(); };
  input.addEventListener('input', () => { clearTimeout(t); t = setTimeout(async () => {
    const g = await api('geocode?q=' + encodeURIComponent(input.value)); results = g.results || [];
    list.replaceChildren(...results.map(r => h('button', { onclick: () => choose(r) }, h('b', {}, r.label), h('span', {}, r.detail))));
    list.hidden = !results.length; }, 300); });
  input.addEventListener('keydown', e => { if (e.key === 'Enter' && results[0]) choose(results[0]); if (e.key === 'Escape') list.hidden = true; });
  box.append(input, list); return box;
}

function render() {
  // one flow in the user's order; neighbouring cards share a wrapping row
  const flow = $('#center'); flow.replaceChildren(); $('#cards').replaceChildren();
  let row = null;
  for (const [id, on] of C['ntp.elements']) {
    if (!on || !R[id]) continue;
    if (ELEMENTS[id].area === 'cards') {
      if (!row) { row = h('div', { class: 'cardrow' }); flow.append(row); }
      row.append(R[id]());
    } else { row = null; flow.append(R[id]()); }
  }
}

// ── shortcut editor dialog ──
function editShortcut(i) {
  const dlg = $('#dlg'), scrim = $('#scrim'), cur = i < 0 ? ['', ''] : S.shortcuts[i];
  const n = h('input', { maxlength: 40, value: cur[0] }), u = h('input', { value: cur[1] });
  const close = () => { dlg.hidden = true; scrim.classList.remove('on'); };
  const save = () => { let url = u.value.trim(), name = n.value.trim(); if (!url) return u.focus();
    if (!/^[a-z][\w+.-]*:/i.test(url)) url = 'https://' + url;
    if (!name) try { name = new URL(url).hostname.replace(/^www\./, ''); } catch (e) { name = url; }
    i < 0 ? S.shortcuts.push([name, url]) : S.shortcuts[i] = [name, url]; commit(); close(); };
  const commit = () => { api('shortcuts?set=' + encodeURIComponent(JSON.stringify(S.shortcuts))); render(); };
  dlg.replaceChildren(h('h2', {}, i < 0 ? 'Add shortcut' : 'Edit shortcut'), h('label', {}, 'Name', n), h('label', {}, 'URL', u),
    h('div', { class: 'r' }, i < 0 ? h('span') : h('button', { class: 'tb d', onclick: () => { S.shortcuts.splice(i, 1); commit(); close(); } }, 'Remove'),
      h('span'), h('button', { class: 'tb', onclick: close }, 'Cancel'), h('button', { class: 'tb f', onclick: save }, 'Save')));
  dlg.onkeydown = e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') close(); };
  scrim.onclick = close; dlg.hidden = false; scrim.classList.add('on'); n.focus();
}

// ── customize sheet ──
const sw = (key, after) => { const e = h('div', { class: 'sw' + (C[key] ? ' on' : '') });
  e.onclick = () => { set(key, !C[key]); e.classList.toggle('on', C[key]); after && after(); }; return e; };
const seg = (key, opts, after) => h('div', { class: 'seg' }, opts.map(([v, l]) => { const b = h('button', { class: C[key] === v ? 'on' : '' }, l);
  b.onclick = () => { set(key, v); b.parentNode.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b)); after && after(); }; return b; }));
const row = (ic, label, ctl) => h('div', { class: 'row' }, ic ? icon(ic) : null, h('label', {}, label), ctl);
const text = (key, ph, after) => { const e = h('input', { type: 'text', placeholder: ph, value: C[key] || '' });
  e.addEventListener('change', () => { set(key, e.value.trim()); after && after(); }); return e; };

function openSheet() {
  const sheet = $('#sheet'), bgChips = h('div', { class: 'chips' });
  const redrawChips = () => bgChips.replaceChildren(...BACKGROUNDS.map(([v, l, ic]) => h('button', { class: 'chip' + (C['ntp.bg'] === v ? ' on' : ''),
    onclick: () => { set('ntp.bg', v); redrawChips(); applyBackground(); } }, icon(ic), l)));
  redrawChips();
  const file = h('input', { type: 'file', accept: 'image/*', hidden: '' });
  file.onchange = async () => { const f = file.files[0]; if (!f) return;
    if (f.size > 40e6) return alert('Pick an image under 40 MB');
    const r = await api('bg', { method: 'POST', headers: { 'Content-Type': f.type || 'application/octet-stream' }, body: f });
    if (r.ok) { C['ntp.bg_image'] = r.name; applyBackground(); openSheet(); } };
  const dim = h('input', { type: 'range', min: 0, max: 0.8, step: 0.05, value: C['ntp.bg_dim'] });
  dim.oninput = () => { C['ntp.bg_dim'] = +dim.value; applyBackground(); }; dim.onchange = () => set('ntp.bg_dim', +dim.value);
  const blur = h('input', { type: 'range', min: 0, max: 30, step: 1, value: C['ntp.bg_blur'] });
  blur.oninput = () => { C['ntp.bg_blur'] = +blur.value; applyBackground(); }; blur.onchange = () => set('ntp.bg_blur', +blur.value);
  const elements = h('div', { class: 'grp' }, C['ntp.elements'].map(([id, on], i) => {
    const s = h('div', { class: 'sw' + (on ? ' on' : '') });
    s.onclick = () => { C['ntp.elements'][i][1] = !C['ntp.elements'][i][1]; set('ntp.elements', C['ntp.elements']); render(); openSheet(); };
    const move = d => () => { const L = C['ntp.elements'], j = i + d; if (j < 0 || j >= L.length) return;
      [L[i], L[j]] = [L[j], L[i]]; set('ntp.elements', L); render(); openSheet(); };
    return h('div', { class: 'row' }, icon(ELEMENTS[id].icon), h('label', {}, ELEMENTS[id].name),
      h('button', { class: 'ic', title: 'Move up', onclick: move(-1) }, 'arrow_upward'),
      h('button', { class: 'ic', title: 'Move down', onclick: move(1) }, 'arrow_downward'), s);
  }));
  sheet.replaceChildren(
    h('h2', {}, 'Customize', h('button', { class: 'ic', onclick: () => sheet.hidden = true }, 'close')),
    h('h4', {}, 'BACKGROUND'), bgChips,
    h('div', { class: 'grp', style: 'margin-top:10px' },
      row('density_medium', 'Amount', seg('ntp.bg_density', [['low', 'Few'], ['medium', 'Some'], ['high', 'Lots']], applyBackground)),
      row('speed', 'Speed', seg('ntp.bg_speed', [['slow', 'Slow'], ['normal', 'Normal'], ['fast', 'Fast']], applyBackground)),
      row('touch_app', 'React to the cursor', sw('ntp.bg_mouse', applyBackground))),
    h('h4', {}, 'IMAGE OR GIF'),
    h('div', { class: 'grp' },
      row('image', C['ntp.bg_image'] ? 'Custom image set' : 'No image',
        h('span', {}, h('button', { class: 'btn f', onclick: () => file.click() }, C['ntp.bg_image'] ? 'Change' : 'Upload'), ' ',
          C['ntp.bg_image'] ? h('button', { class: 'btn', onclick: () => api('bg?clear=1').then(() => { C['ntp.bg_image'] = ''; applyBackground(); openSheet(); }) }, 'Remove') : null)),
      row('contrast', 'Dim', dim), row('blur_on', 'Blur', blur)), file,
    h('h4', {}, 'ELEMENTS'), elements,
    h('h4', {}, 'DETAILS'),
    h('div', { class: 'grp' },
      row('schedule', '24-hour clock', sw('ntp.clock24', render)), row('timer', 'Show seconds', sw('ntp.seconds', render)),
      row('badge', 'Your name', text('ntp.name', 'for the greeting', render)),
      row('location_city', 'Weather place', placeField()),
      row('device_thermostat', 'Units', seg('ntp.units', [['auto', 'Auto'], ['c', '°C'], ['f', '°F']], render))));
  sheet.hidden = false;
}
$('#gear').onclick = () => $('#sheet').hidden ? openSheet() : ($('#sheet').hidden = true);
addEventListener('keydown', e => { if (e.key === 'Escape') $('#sheet').hidden = true; });

applyBackground();
render();
})();
