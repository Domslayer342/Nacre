// Nacre new tab backgrounds — canvas engines that follow the Material You palette and react to the cursor.
(() => {
const TAU = Math.PI * 2;
const rnd = (a, b) => a + Math.random() * (b - a);
const pick = a => a[Math.floor(Math.random() * a.length)];

const Bg = {
  canvas: null, ctx: null, w: 0, h: 0, dpr: 1, t: 0, raf: 0, engine: null, opts: {},
  mouse: { x: -1e4, y: -1e4, sx: -1e4, sy: -1e4, inside: false },
  palette: {},

  readPalette() {
    const cs = getComputedStyle(document.documentElement), v = n => cs.getPropertyValue('--' + n).trim() || '#888';
    this.palette = { p: v('p'), pc: v('pc'), sc: v('sc'), tc: v('tc'), outline: v('outline'), fg: v('fg'), bg: v('bg'),
                     var: v('var'), high: v('high') };
    this.engine && this.engine.colors && this.engine.colors(this);
  },

  start(name, opts) {
    this.stop();
    this.opts = Object.assign({ density: 'medium', speed: 'normal', mouse: true, motion: 1 }, opts || {});
    this.canvas = document.getElementById('bg');
    this.canvas.style.filter = ''; this.canvas.style.opacity = '';
    if (name === 'gooey') name = 'blobs';
    this.ctx = this.canvas.getContext('2d');
    this.engine = ENGINES[name] ? Object.create(ENGINES[name]) : null;
    this.resize();
    if (!this.engine) { this.ctx.clearRect(0, 0, this.w, this.h); return; }
    this.readPalette();
    this.engine.init(this);
    this.last = performance.now();
    const loop = now => {
      const dt = Math.min(0.05, (now - this.last) / 1000) * this.speed(); this.last = now; this.t += dt;
      const m = this.mouse, k = Math.min(1, dt * 9);
      m.sx += (m.x - m.sx) * k; m.sy += (m.y - m.sy) * k;          // smoothed cursor
      this.engine.frame(this, dt);
      if (this.opts.motion > 0) this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  },
  stop() { cancelAnimationFrame(this.raf); this.raf = 0; },
  speed() { return { slow: 0.5, normal: 1, fast: 1.8 }[this.opts.speed] || 1; },
  count(base) { return Math.round(base * ({ low: 0.55, medium: 1, high: 1.7 }[this.opts.density] || 1) * (this.w * this.h) / (1440 * 900)); },
  resize() {
    if (!this.canvas) return;
    this.dpr = Math.min(2, devicePixelRatio || 1);
    this.w = innerWidth; this.h = innerHeight;
    this.canvas.width = this.w * this.dpr; this.canvas.height = this.h * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.engine && this.engine.resize && this.engine.resize(this);
  },
  near(x, y, r) {             // 0..1 closeness of the cursor to a point
    if (!this.opts.mouse || !this.mouse.inside) return 0;
    const d = Math.hypot(x - this.mouse.sx, y - this.mouse.sy);
    return d < r ? 1 - d / r : 0;
  },
};

// ── shape helpers (M3 Expressive) ─────────────────────────────────────────
function polar(ctx, r, fn, steps = 96) {
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) { const a = TAU * i / steps, rr = r * fn(a);
    i ? ctx.lineTo(rr * Math.cos(a), rr * Math.sin(a)) : ctx.moveTo(rr * Math.cos(a), rr * Math.sin(a)); }
  ctx.closePath();
}
function roundPoly(ctx, r, n, rad) {
  const pts = []; for (let i = 0; i < n; i++) { const a = TAU * i / n - Math.PI / 2; pts.push([r * Math.cos(a), r * Math.sin(a)]); }
  ctx.beginPath(); const m = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], s = m(pts[n - 1], pts[0]); ctx.moveTo(s[0], s[1]);
  for (let i = 0; i < n; i++) { const p = pts[i], q = pts[(i + 1) % n], mm = m(p, q); ctx.arcTo(p[0], p[1], mm[0], mm[1], rad); }
  ctx.closePath();
}
const SHAPES = {
  cookie4: (c, r) => polar(c, r, a => 1 + 0.10 * Math.cos(4 * a)),
  cookie6: (c, r) => polar(c, r, a => 1 + 0.08 * Math.cos(6 * a)),
  cookie9: (c, r) => polar(c, r, a => 1 + 0.06 * Math.cos(9 * a)),
  cookie12: (c, r) => polar(c, r, a => 1 + 0.045 * Math.cos(12 * a)),
  flower: (c, r) => polar(c, r, a => 0.78 + 0.22 * Math.abs(Math.cos(4 * a)), 160),
  clover: (c, r) => polar(c, r, a => 0.72 + 0.28 * Math.pow(Math.abs(Math.cos(2 * a)), 0.7), 160),
  triangle: (c, r) => roundPoly(c, r * 1.1, 3, r * 0.35),
  square: (c, r) => roundPoly(c, r * 1.05, 4, r * 0.38),
  pentagon: (c, r) => roundPoly(c, r, 5, r * 0.3),
  pill: (c, r) => { c.beginPath(); c.roundRect(-r * 1.25, -r * 0.55, r * 2.5, r * 1.1, r * 0.55); },
  circle: (c, r) => { c.beginPath(); c.arc(0, 0, r, 0, TAU); },
};

const ENGINES = {
  // floating M3 shapes with parallax; they part around the cursor and spring back
  shapes: {
    init(B) {
      this.items = [];
      const n = Math.max(8, B.count(26));
      for (let i = 0; i < n; i++) this.items.push(this.make(B));
      this.colors(B);
    },
    make(B) {
      const depth = rnd(0.25, 1);
      return { x: rnd(0, B.w), y: rnd(0, B.h), ox: 0, oy: 0, vx: rnd(-8, 8), vy: rnd(-6, 6), depth,
               r: rnd(22, 70) * (0.55 + depth * 0.6), rot: rnd(0, TAU), vr: rnd(-0.25, 0.25),
               kind: pick(Object.keys(SHAPES)), tone: pick(['pc', 'sc', 'tc', 'p']), a: rnd(0.35, 0.85), outline: Math.random() < 0.18 };
    },
    colors(B) { this.items && this.items.forEach(s => s.color = B.palette[s.tone]); },
    frame(B, dt) {
      const c = B.ctx; c.clearRect(0, 0, B.w, B.h);
      const px = B.mouse.inside ? (B.mouse.sx - B.w / 2) : 0, py = B.mouse.inside ? (B.mouse.sy - B.h / 2) : 0;
      for (const s of this.items.sort((a, b) => a.depth - b.depth)) {
        s.x += s.vx * dt * s.depth; s.y += s.vy * dt * s.depth; s.rot += s.vr * dt;
        const pad = s.r * 1.5;
        if (s.x < -pad) s.x = B.w + pad; if (s.x > B.w + pad) s.x = -pad;
        if (s.y < -pad) s.y = B.h + pad; if (s.y > B.h + pad) s.y = -pad;
        const cx = s.x - px * 0.04 * s.depth, cy = s.y - py * 0.04 * s.depth;
        const near = B.near(cx + s.ox, cy + s.oy, 220);
        if (near) { const dx = cx + s.ox - B.mouse.sx, dy = cy + s.oy - B.mouse.sy, d = Math.hypot(dx, dy) || 1;
          s.ox += dx / d * near * 160 * dt * 3; s.oy += dy / d * near * 160 * dt * 3; s.rot += near * dt * 1.5; }
        s.ox *= 1 - Math.min(1, dt * 1.8); s.oy *= 1 - Math.min(1, dt * 1.8);     // spring home
        c.save(); c.translate(cx + s.ox, cy + s.oy); c.rotate(s.rot);
        const scale = 1 + near * 0.18; c.scale(scale, scale);
        SHAPES[s.kind](c, s.r);
        c.globalAlpha = s.a * (0.55 + 0.45 * s.depth);
        if (s.outline) { c.lineWidth = 3; c.strokeStyle = s.color; c.stroke(); } else { c.fillStyle = s.color; c.fill(); }
        c.restore();
      }
    },
  },

  // liquid blobs: crisp full-res circles + a GPU blur→alpha-threshold filter (#goo) = clean round edges that melt
  blobs: {
    init(B) {
      const n = Math.max(6, B.count(11));
      this.items = Array.from({ length: n }, () => ({ hx: rnd(0.08, 0.92), hy: rnd(0.1, 0.9), r: rnd(46, 104),
        ax: rnd(60, 170), ay: rnd(50, 140), fx: rnd(0.03, 0.08), fy: rnd(0.025, 0.07), ph: rnd(0, TAU), x: null, y: null }));
      this.cursor = { x: B.w / 2, y: B.h / 2, r: 0 };
      B.canvas.style.filter = 'url(#goo)';
      B.canvas.style.opacity = '0.75';
      this.grad = null;
    },
    colors() { this.grad = null; },
    frame(B, dt) {
      const c = B.ctx; c.clearRect(0, 0, B.w, B.h);
      if (!this.grad || this.gw !== B.w || this.gh !== B.h) {
        this.grad = c.createLinearGradient(0, 0, B.w, B.h); this.gw = B.w; this.gh = B.h;
        this.grad.addColorStop(0, B.palette.pc); this.grad.addColorStop(0.55, B.palette.sc); this.grad.addColorStop(1, B.palette.tc);
      }
      c.fillStyle = this.grad;
      const m = B.mouse, on = B.opts.mouse && m.inside, cur = this.cursor;
      cur.x += (m.sx - cur.x) * Math.min(1, dt * 6); cur.y += (m.sy - cur.y) * Math.min(1, dt * 6);
      cur.r += ((on ? 62 : 0) - cur.r) * Math.min(1, dt * 4);
      for (const b of this.items) {
        let x = b.hx * B.w + Math.sin(B.t * b.fx * TAU + b.ph) * b.ax, y = b.hy * B.h + Math.cos(B.t * b.fy * TAU + b.ph * 1.7) * b.ay;
        const near = B.near(x, y, 280);
        if (near) { x += (cur.x - x) * near * 0.45; y += (cur.y - y) * near * 0.45; }        // stretch toward the cursor
        if (b.x === null) { b.x = x; b.y = y; }
        b.x += (x - b.x) * Math.min(1, dt * 3); b.y += (y - b.y) * Math.min(1, dt * 3);
        const pulse = 1 + 0.06 * Math.sin(B.t * 1.3 + b.ph);
        c.beginPath(); c.arc(b.x, b.y, b.r * pulse, 0, TAU); c.fill();
      }
      if (cur.r > 1) { c.beginPath(); c.arc(cur.x, cur.y, cur.r, 0, TAU); c.fill(); }
    },
  },

  // constellation: drifting points linked when close; the cursor pulls and links nearby ones
  particles: {
    init(B) { this.pts = Array.from({ length: Math.max(30, B.count(90)) }, () => ({ x: rnd(0, B.w), y: rnd(0, B.h),
      vx: rnd(-12, 12), vy: rnd(-12, 12) })); },
    frame(B, dt) {
      const c = B.ctx, P = this.pts, link = 130; c.clearRect(0, 0, B.w, B.h);
      for (const p of P) { p.x += p.vx * dt; p.y += p.vy * dt;
        if (p.x < 0 || p.x > B.w) p.vx *= -1; if (p.y < 0 || p.y > B.h) p.vy *= -1;
        const near = B.near(p.x, p.y, 200); if (near) { p.x += (B.mouse.sx - p.x) * near * dt * 0.6; p.y += (B.mouse.sy - p.y) * near * dt * 0.6; } }
      c.lineWidth = 1.2;
      for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
        const d = Math.hypot(P[i].x - P[j].x, P[i].y - P[j].y);
        if (d < link) { c.globalAlpha = (1 - d / link) * 0.5; c.strokeStyle = B.palette.outline;
          c.beginPath(); c.moveTo(P[i].x, P[i].y); c.lineTo(P[j].x, P[j].y); c.stroke(); } }
      if (B.opts.mouse && B.mouse.inside) for (const p of P) { const n = B.near(p.x, p.y, 200);
        if (n) { c.globalAlpha = n; c.strokeStyle = B.palette.p; c.beginPath(); c.moveTo(p.x, p.y); c.lineTo(B.mouse.sx, B.mouse.sy); c.stroke(); } }
      c.globalAlpha = 1;
      for (const p of P) { const n = B.near(p.x, p.y, 200); c.fillStyle = n ? B.palette.p : B.palette.var;
        c.beginPath(); c.arc(p.x, p.y, 2 + n * 2.5, 0, TAU); c.fill(); }
    },
  },

  // layered M3 wavy lines; they swell under the cursor
  waves: {
    init(B) { this.lines = Array.from({ length: 6 }, (_, i) => ({ y: 0.22 + i * 0.12, amp: rnd(10, 22), len: rnd(90, 170),
      sp: rnd(0.5, 1.2) * (i % 2 ? 1 : -1), tone: ['p', 'tc', 'sc', 'p', 'pc', 'tc'][i], w: rnd(2.5, 5) })); },
    frame(B) {
      const c = B.ctx; c.clearRect(0, 0, B.w, B.h); c.lineCap = 'round';
      for (const l of this.lines) {
        c.beginPath(); const y0 = l.y * B.h;
        for (let x = -20; x <= B.w + 20; x += 6) {
          const bump = B.opts.mouse && B.mouse.inside ? Math.exp(-((x - B.mouse.sx) ** 2) / 26000) * Math.max(0, 1 - Math.abs(B.mouse.sy - y0) / 300) : 0;
          const y = y0 + Math.sin(x / l.len * TAU + B.t * l.sp * TAU * 0.25) * l.amp * (1 + bump * 2.5);
          x === -20 ? c.moveTo(x, y) : c.lineTo(x, y);
        }
        c.globalAlpha = 0.55; c.lineWidth = l.w; c.strokeStyle = B.palette[l.tone]; c.stroke();
      }
      c.globalAlpha = 1;
    },
  },

  // dot grid that magnifies (and brightens) around the cursor
  dots: {
    init() {},
    frame(B) {
      const c = B.ctx, gap = { low: 40, medium: 30, high: 22 }[B.opts.density] || 30; c.clearRect(0, 0, B.w, B.h);
      for (let y = gap / 2; y < B.h; y += gap) for (let x = gap / 2; x < B.w; x += gap) {
        const wob = Math.sin(x * 0.02 + B.t * 1.2) * Math.cos(y * 0.02 + B.t) * 0.5 + 0.5;
        const n = B.near(x, y, 180);
        let dx = 0, dy = 0;
        if (n) { const d = Math.hypot(x - B.mouse.sx, y - B.mouse.sy) || 1; dx = (x - B.mouse.sx) / d * n * 14; dy = (y - B.mouse.sy) / d * n * 14; }
        c.globalAlpha = 0.35 + n * 0.65; c.fillStyle = n > 0.05 ? B.palette.p : B.palette.outline;
        c.beginPath(); c.arc(x + dx, y + dy, 1.4 + wob * 0.8 + n * 4, 0, TAU); c.fill();
      }
      c.globalAlpha = 1;
    },
  },
};

addEventListener('resize', () => Bg.resize());
addEventListener('pointermove', e => { const m = Bg.mouse; if (!m.inside) { m.sx = e.clientX; m.sy = e.clientY; }
  m.x = e.clientX; m.y = e.clientY; m.inside = true; });
document.addEventListener('pointerleave', () => Bg.mouse.inside = false);
document.addEventListener('visibilitychange', () => {        // no work while the tab is hidden
  if (document.hidden) Bg.stop(); else if (Bg.engine && !Bg.raf && Bg.opts.motion > 0) Bg.start(Bg.name, Bg.opts);
});
const start = Bg.start.bind(Bg); Bg.start = (name, opts) => { Bg.name = name; start(name, opts); };
Bg.ENGINES = Object.keys(ENGINES);
window.NacreBg = Bg;
})();
