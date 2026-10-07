'use strict';
/* Small force-directed graph on canvas (no external libraries). */

class Graph {
  constructor(canvas, data, opts = {}) {
    this.c = canvas;
    this.ctx = canvas.getContext('2d');
    this.opts = opts;
    this.selected = null;
    this.hover = null;
    const byName = {};
    this.nodes = data.nodes.map((n, i) => {
      const a = (i / data.nodes.length) * Math.PI * 2;
      const node = { ...n, x: Math.cos(a) * 200 + Math.random() * 20, y: Math.sin(a) * 200 + Math.random() * 20,
        vx: 0, vy: 0, inW: 0, outW: 0 };
      byName[n.username] = node;
      return node;
    });
    this.byName = byName;
    this.links = data.links.filter(l => byName[l.source] && byName[l.target])
      .map(l => ({ ...l, s: byName[l.source], t: byName[l.target] }));
    for (const l of this.links) { l.s.outW += l.weight; l.t.inW += l.weight; }
    for (const n of this.nodes) n.r = 5 + Math.sqrt(Math.log10((n.subscribers || 0) + 10)) * 5;
    this.maxW = Math.max(1, ...this.links.map(l => l.weight));
    this.view = { x: 0, y: 0, k: 1 };
    this.alpha = 1;
    this.running = true;
    this.dark = matchMedia('(prefers-color-scheme: dark)').matches;
    this._resize();
    this._bind();
    this._tick = this._tick.bind(this);
    requestAnimationFrame(this._tick);
    setTimeout(() => this.fit(), 900);
  }

  stop() {
    this.running = false;
    window.removeEventListener('resize', this._onResize);
  }

  color(n) {
    if (n.type === 'group') return '#8b5cf6';
    if (n.status === 'seed' || n.status === 'watched') return '#2b59c3';
    if (n.crawled) return '#0f9d8a';
    return '#9aa3b2';
  }

  updateNode(name, attrs) {
    const n = this.byName[name];
    if (n) Object.assign(n, attrs);
    this.draw();
  }

  _resize() {
    const r = this.c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.c.width = r.width * dpr;
    this.c.height = r.height * dpr;
    this.w = r.width;
    this.h = r.height;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.draw();
  }

  _simulate() {
    const nodes = this.nodes, a = this.alpha;
    // repulsion
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const m = nodes[j];
        let dx = n.x - m.x, dy = n.y - m.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = Math.random(); dy = Math.random(); d2 = 1; }
        const f = (2400 * a) / d2;
        const d = Math.sqrt(d2);
        n.vx += (dx / d) * f; n.vy += (dy / d) * f;
        m.vx -= (dx / d) * f; m.vy -= (dy / d) * f;
      }
    }
    // springs
    for (const l of this.links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const target = 90 + l.s.r + l.t.r;
      const f = (d - target) * 0.02 * a;
      l.s.vx += (dx / d) * f; l.s.vy += (dy / d) * f;
      l.t.vx -= (dx / d) * f; l.t.vy -= (dy / d) * f;
    }
    for (const n of nodes) {
      n.vx -= n.x * 0.004 * a; n.vy -= n.y * 0.004 * a;   // gravity to centre
      if (n === this.dragNode) { n.vx = n.vy = 0; continue; }
      n.vx *= 0.6; n.vy *= 0.6;
      n.x += Math.max(-30, Math.min(30, n.vx));
      n.y += Math.max(-30, Math.min(30, n.vy));
    }
    this.alpha = Math.max(0, this.alpha * 0.985);
  }

  _tick() {
    if (!this.running) return;
    if (this.alpha > 0.01 || this.dragNode) {
      this._simulate();
      this.draw();
    }
    requestAnimationFrame(this._tick);
  }

  toScreen(x, y) { return [x * this.view.k + this.view.x + this.w / 2, y * this.view.k + this.view.y + this.h / 2]; }
  toWorld(x, y) { return [(x - this.view.x - this.w / 2) / this.view.k, (y - this.view.y - this.h / 2) / this.view.k]; }

  fit() {
    if (!this.nodes.length) return;
    const xs = this.nodes.map(n => n.x), ys = this.nodes.map(n => n.y);
    const minX = Math.min(...xs) - 40, maxX = Math.max(...xs) + 40, minY = Math.min(...ys) - 40, maxY = Math.max(...ys) + 40;
    const k = Math.min(this.w / (maxX - minX), this.h / (maxY - minY), 2.5);
    this.view.k = k;
    this.view.x = -((minX + maxX) / 2) * k;
    this.view.y = -((minY + maxY) / 2) * k;
    this.draw();
  }

  draw() {
    const ctx = this.ctx, k = this.view.k;
    ctx.clearRect(0, 0, this.w, this.h);
    const focus = this.hover || this.selected;
    const near = new Set();
    if (focus) {
      near.add(focus);
      for (const l of this.links) { if (l.s === focus) near.add(l.t); if (l.t === focus) near.add(l.s); }
    }
    // links
    for (const l of this.links) {
      const [x1, y1] = this.toScreen(l.s.x, l.s.y), [x2, y2] = this.toScreen(l.t.x, l.t.y);
      const lit = focus && (l.s === focus || l.t === focus);
      ctx.strokeStyle = lit ? (this.dark ? 'rgba(122,162,247,.9)' : 'rgba(43,89,195,.8)')
        : (this.dark ? 'rgba(160,170,190,.28)' : 'rgba(80,90,110,.22)');
      if (focus && !lit) ctx.strokeStyle = this.dark ? 'rgba(160,170,190,.08)' : 'rgba(80,90,110,.07)';
      ctx.lineWidth = Math.max(0.8, Math.sqrt(l.weight / this.maxW) * 6) * Math.min(1.5, k);
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      // arrowhead at target edge
      const ang = Math.atan2(y2 - y1, x2 - x1), rr = l.t.r * k + 2;
      const ax = x2 - Math.cos(ang) * rr, ay = y2 - Math.sin(ang) * rr, s = 5 + ctx.lineWidth;
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath(); ctx.moveTo(ax, ay);
      ctx.lineTo(ax - Math.cos(ang - 0.4) * s, ay - Math.sin(ang - 0.4) * s);
      ctx.lineTo(ax - Math.cos(ang + 0.4) * s, ay - Math.sin(ang + 0.4) * s);
      ctx.closePath(); ctx.fill();
    }
    // nodes
    for (const n of this.nodes) {
      const [x, y] = this.toScreen(n.x, n.y), r = n.r * k;
      ctx.globalAlpha = focus && !near.has(n) ? 0.25 : 1;
      ctx.fillStyle = this.color(n);
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      if (n.relevant) { ctx.strokeStyle = '#d93025'; ctx.lineWidth = 2.5; ctx.stroke(); }
      if (n === this.selected) { ctx.strokeStyle = this.dark ? '#fff' : '#111'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, r + 3, 0, Math.PI * 2); ctx.stroke(); }
      if (k > 0.55 || near.has(n) || n.status === 'seed' || n.status === 'watched') {
        ctx.fillStyle = this.dark ? '#e6e9ef' : '#1c2330';
        ctx.font = `${n === focus ? '600 ' : ''}12px -apple-system, "Segoe UI", sans-serif`;
        ctx.textAlign = 'center';
        ctx.fillText('@' + n.username, x, y + r + 13);
      }
      ctx.globalAlpha = 1;
    }
  }

  nodeAt(px, py) {
    const [wx, wy] = this.toWorld(px, py);
    let best = null, bd = Infinity;
    for (const n of this.nodes) {
      const d = Math.hypot(n.x - wx, n.y - wy);
      if (d < n.r + 4 / this.view.k && d < bd) { best = n; bd = d; }
    }
    return best;
  }

  _bind() {
    const c = this.c;
    let down = null, moved = false;
    const pos = e => { const r = c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    c.addEventListener('mousedown', e => {
      const [x, y] = pos(e);
      down = { x, y, vx: this.view.x, vy: this.view.y };
      moved = false;
      this.dragNode = this.nodeAt(x, y);
      if (this.dragNode) this.alpha = Math.max(this.alpha, 0.3);
      c.classList.add('dragging');
    });
    window.addEventListener('mousemove', this._mm = e => {
      if (!this.running) return;
      const [x, y] = pos(e);
      if (down) {
        if (Math.hypot(x - down.x, y - down.y) > 3) moved = true;
        if (this.dragNode) {
          const [wx, wy] = this.toWorld(x, y);
          this.dragNode.x = wx; this.dragNode.y = wy;
        } else {
          this.view.x = down.vx + (x - down.x);
          this.view.y = down.vy + (y - down.y);
        }
        this.draw();
      } else if (e.target === c) {
        const h = this.nodeAt(x, y);
        if (h !== this.hover) { this.hover = h; c.style.cursor = h ? 'pointer' : 'grab'; this.draw(); }
      }
    });
    window.addEventListener('mouseup', this._mu = () => {
      if (!this.running || !down) return;
      c.classList.remove('dragging');
      if (!moved) {
        this.selected = this.dragNode || null;
        this.opts.onSelect && this.opts.onSelect(this.selected);
      }
      this.dragNode = null;
      down = null;
      this.draw();
    });
    c.addEventListener('dblclick', e => {
      const n = this.nodeAt(...pos(e));
      if (n && this.opts.onOpen) this.opts.onOpen(n);
    });
    c.addEventListener('mouseleave', () => { this.hover = null; this.draw(); });
    c.addEventListener('wheel', e => {
      e.preventDefault();
      const [x, y] = pos(e);
      const [wx, wy] = this.toWorld(x, y);
      this.view.k = Math.max(0.15, Math.min(5, this.view.k * Math.exp(-e.deltaY * 0.0015)));
      const [sx, sy] = this.toScreen(wx, wy);
      this.view.x += x - sx; this.view.y += y - sy;
      this.draw();
    }, { passive: false });
    this._onResize = () => this._resize();
    window.addEventListener('resize', this._onResize);
  }
}
window.Graph = Graph;
