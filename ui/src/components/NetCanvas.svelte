<script>
  // Force-directed graph on <canvas> (d3-force for layout, hand-drawn for speed with ~400 nodes).
  // Props:
  //   nodes  [{id, label, ...}]          links [{source, target, weight, byType, dashed?, faint?, undirected?}]
  //   style(node, pal) -> {fill, stroke?, hollow?, shape?: 'circle'|'square'|'diamond'}
  //   edgeColor(link, pal) -> css colour
  //   radius(node) -> px (world units)
  //   selected: id | null, highlight: id | null (search hit), frozen: bool, directed: bool
  //   onselect(node|null), onopen(node), tooltip snippet (node) => markup
  // Methods (bind:this): fit(), focus(id), reheat()
  import { reducedMotion } from '../lib/motion.js';
  import { onMount, onDestroy, untrack } from 'svelte';
  import { forceSimulation, forceLink, forceManyBody, forceCollide, forceX, forceY } from 'd3-force';
  import { readTheme, alpha } from '../lib/net-theme.js';

  let {
    nodes = [], links = [], style, edgeColor, radius = () => 6,
    selected = null, highlight = null, frozen = false, directed = true, labelBudget = 40,
    onselect, onopen, tooltip,
  } = $props();

  let wrap = $state();
  let canvas = $state();
  let hoverNode = $state.raw(null);   // raw: identity compare with node objects
  let tipPos = $state({ x: 0, y: 0 });
  let size = { w: 0, h: 0, dpr: 1 };
  let ctx = null;
  let pal = null;
  let view = { x: 0, y: 0, k: 1 };
  let sim = null;
  let simNodes = [];
  let simLinks = [];
  let byId = new Map();
  let adj = new Map();            // id -> Set(neighbour ids)
  let raf = 0;
  let fitted = false;
  let destroyed = false;

  // ---------------------------------------------------------------- build / layout
  function rebuild(ns, ls) {
    const prev = byId;
    sim?.stop();
    simNodes = ns.map((n) => {
      const p = prev.get(n.id);
      return { id: n.id, data: n, r: radius(n), x: p?.x, y: p?.y, vx: 0, vy: 0, fx: p?.fx ?? null, fy: p?.fy ?? null };
    });
    byId = new Map(simNodes.map((n) => [n.id, n]));
    const pairs = new Set(ls.map((l) => l.source + '\u0000' + l.target));
    simLinks = ls
      .filter((l) => byId.has(l.source) && byId.has(l.target) && l.source !== l.target)
      .map((l) => ({ source: l.source, target: l.target, data: l, w: l.weight, curve: !l.undirected && pairs.has(l.target + '\u0000' + l.source) }));
    adj = new Map(simNodes.map((n) => [n.id, new Set()]));
    for (const l of simLinks) { adj.get(l.source)?.add(l.target); adj.get(l.target)?.add(l.source); }
    const fresh = simNodes.filter((n) => n.x === undefined).length;
    const isNew = fresh > simNodes.length / 2;
    if (isNew) fitted = false;
    // Seed unknown positions on a spiral near their neighbours' centre.
    let i = 0;
    for (const n of simNodes) {
      if (n.x !== undefined) continue;
      const nb = [...(adj.get(n.id) || [])].map((id) => byId.get(id)).filter((m) => m && m.x !== undefined);
      const a = i * 2.39996, rr = 12 * Math.sqrt(++i);
      n.x = (nb.length ? nb.reduce((s, m) => s + m.x, 0) / nb.length : 0) + Math.cos(a) * rr;
      n.y = (nb.length ? nb.reduce((s, m) => s + m.y, 0) / nb.length : 0) + Math.sin(a) * rr;
    }
    const maxW = Math.max(1, ...simLinks.map((l) => l.w));
    sim = forceSimulation(simNodes)
      .force('link', forceLink(simLinks).id((d) => d.id)
        .distance((l) => 36 + l.source.r + l.target.r)
        .strength((l) => 0.15 + 0.5 * Math.log1p(l.w) / Math.log1p(maxW) / Math.max(1, Math.min(adj.get(l.source.id)?.size || 1, adj.get(l.target.id)?.size || 1))))
      .force('charge', forceManyBody().strength((d) => -60 - d.r * 6).distanceMax(500).theta(0.9))
      .force('collide', forceCollide((d) => d.r + 3).iterations(1))
      .force('x', forceX(0).strength(0.04))
      .force('y', forceY(0).strength(0.04))
      .alphaDecay(0.035)
      .alphaMin(0.004)
      .on('tick', schedule)
      .on('end', () => { if (!fitted) { fit(); fitted = true; } schedule(); });
    if (isNew) {
      sim.alpha(1);
      sim.tick(Math.min(200, 40 + Math.round(12000 / Math.max(20, simNodes.length))));   // warm-up without drawing
      fit();
    } else {
      sim.alpha(0.25);
    }
    if (reducedMotion()) { sim.alpha(1); sim.tick(300); sim.stop(); if (!fitted) { fit(); fitted = true; } }   // no animated layout
    if (frozen) sim.stop();
    schedule();
  }

  $effect(() => {
    const ns = nodes, ls = links;
    if (!ctx) return;
    untrack(() => rebuild(ns, ls));
  });
  // Radius changes (size-by toggle) without relayout.
  $effect(() => {
    const rf = radius;
    for (const n of simNodes) n.r = rf(n.data);
    if (sim) untrack(() => { sim.force('collide', forceCollide((d) => d.r + 3).iterations(1)); if (!frozen && !reducedMotion()) sim.alpha(Math.max(sim.alpha(), 0.15)).restart(); });
    schedule();
  });
  $effect(() => {
    if (!sim) return;
    if (frozen) sim.stop();
    else if (sim.alpha() > sim.alphaMin() && !reducedMotion()) sim.restart();
  });
  $effect(() => { void selected; void highlight; void style; void edgeColor; schedule(); });

  export function reheat() {
    if (!sim || frozen) return;
    if (reducedMotion()) { sim.alpha(0.6); sim.tick(150); sim.stop(); schedule(); } else sim.alpha(0.6).restart();
  }

  export function fit(pad = 40) {
    if (!simNodes.length || !size.w) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of simNodes) {
      x0 = Math.min(x0, n.x - n.r); y0 = Math.min(y0, n.y - n.r);
      x1 = Math.max(x1, n.x + n.r); y1 = Math.max(y1, n.y + n.r);
    }
    const k = Math.max(0.08, Math.min(2.2, Math.min((size.w - pad * 2) / Math.max(1, x1 - x0), (size.h - pad * 2) / Math.max(1, y1 - y0))));
    view = { k, x: size.w / 2 - k * (x0 + x1) / 2, y: size.h / 2 - k * (y0 + y1) / 2 };
    schedule();
  }

  export function focus(id) {
    const n = byId.get(id);
    if (!n) return false;
    const k = Math.max(view.k, 1.4);
    view = { k, x: size.w / 2 - k * n.x, y: size.h / 2 - k * n.y };
    schedule();
    return true;
  }

  // ---------------------------------------------------------------- drawing
  function schedule() {
    if (raf || destroyed) return;
    raf = requestAnimationFrame(() => { raf = 0; draw(); });
  }

  function shapePath(c, shape, x, y, r) {
    c.beginPath();
    if (shape === 'square') { const s = r * 0.9; c.rect(x - s, y - s, s * 2, s * 2); }
    else if (shape === 'diamond') { c.moveTo(x, y - r * 1.2); c.lineTo(x + r * 1.2, y); c.lineTo(x, y + r * 1.2); c.lineTo(x - r * 1.2, y); c.closePath(); }
    else c.arc(x, y, r, 0, Math.PI * 2);
  }

  function draw() {
    if (!ctx || !pal) return;
    const { w, h, dpr } = size;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = pal.panel;
    ctx.fillRect(0, 0, w, h);
    ctx.setTransform(dpr * view.k, 0, 0, dpr * view.k, dpr * view.x, dpr * view.y);
    const k = view.k;
    const focusId = hoverNode?.id ?? selected;
    const near = focusId != null ? adj.get(focusId) : null;
    const dimming = focusId != null && byId.has(focusId);

    // links
    for (const l of simLinks) {
      const s = l.source, t = l.target;
      const touches = dimming && (s.id === focusId || t.id === focusId);
      const a = dimming ? (touches ? 0.95 : 0.08) : l.data.faint ? 0.5 : 0.6;
      const col = edgeColor(l.data, pal);
      const lw = Math.min(9, 0.7 + 1.25 * Math.log2(1 + l.w)) * (touches ? 1.15 : 1) / k;
      const dx = t.x - s.x, dy = t.y - s.y;
      const dist = Math.hypot(dx, dy) || 1;
      const ux = dx / dist, uy = dy / dist;
      // control point for reciprocal pairs, so A→B and B→A don't overlap
      const off = l.curve ? Math.min(40, dist * 0.18) : 0;
      const cx = (s.x + t.x) / 2 - uy * off, cy = (s.y + t.y) / 2 + ux * off;
      // end direction (tangent at target)
      let ex = t.x - cx, ey = t.y - cy;
      const el = Math.hypot(ex, ey) || 1; ex /= el; ey /= el;
      const head = directed && !l.data.undirected ? Math.min(14, 4 + lw * k * 1.6) / k : 0;
      const endX = t.x - ex * (t.r + 1.5 / k), endY = t.y - ey * (t.r + 1.5 / k);
      const lineEndX = endX - ex * head * 0.8, lineEndY = endY - ey * head * 0.8;
      ctx.strokeStyle = alpha(col, a);
      ctx.lineWidth = lw;
      ctx.setLineDash(l.data.dashed ? [5 / k, 4 / k] : []);
      ctx.beginPath();
      ctx.moveTo(s.x, s.y);
      if (off) ctx.quadraticCurveTo(cx, cy, lineEndX, lineEndY); else ctx.lineTo(lineEndX, lineEndY);
      ctx.stroke();
      if (head) {
        ctx.setLineDash([]);
        ctx.fillStyle = alpha(col, Math.min(1, a + 0.15));
        const hw = head * 0.55;
        ctx.beginPath();
        ctx.moveTo(endX, endY);
        ctx.lineTo(endX - ex * head - ey * hw, endY - ey * head + ex * hw);
        ctx.lineTo(endX - ex * head + ey * hw, endY - ey * head - ex * hw);
        ctx.closePath();
        ctx.fill();
      }
    }
    ctx.setLineDash([]);

    // nodes
    for (const n of simNodes) {
      const st = style(n.data, pal);
      const on = !dimming || n.id === focusId || near?.has(n.id);
      ctx.globalAlpha = on ? 1 : 0.22;
      shapePath(ctx, st.shape, n.x, n.y, n.r);
      if (st.hollow) { ctx.fillStyle = pal.panel; ctx.fill(); ctx.strokeStyle = st.fill; ctx.lineWidth = 2 / k; ctx.stroke(); }
      else {
        ctx.fillStyle = st.fill; ctx.fill();
        ctx.strokeStyle = st.stroke || pal.panel; ctx.lineWidth = (st.stroke ? 2 : 1.2) / k; ctx.stroke();
      }
      if (n.id === selected || n.id === highlight) {
        ctx.globalAlpha = 1;
        shapePath(ctx, st.shape, n.x, n.y, n.r + 4 / k);
        ctx.strokeStyle = n.id === selected ? pal.text : pal.warn;
        ctx.lineWidth = 2.2 / k;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // labels: forced (selected, hover, search hit, neighbours of focus) then largest nodes, no overlaps
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const placed = [];
    const forced = new Set([selected, highlight, hoverNode?.id].filter((x) => x != null));
    if (near && near.size <= 40) for (const id of near) forced.add(id);
    const order = [...simNodes].sort((a, b) => (forced.has(b.id) - forced.has(a.id)) || b.r - a.r);
    let budget = labelBudget;
    ctx.textBaseline = 'middle';
    for (const n of order) {
      const isForced = forced.has(n.id);
      if (!isForced) {
        if (budget <= 0) break;
        if (dimming) continue;
        if (n.r * k < 5.5) continue;
      }
      const sx = view.x + n.x * k, sy = view.y + n.y * k;
      if (sx < -100 || sx > w + 100 || sy < -20 || sy > h + 20) continue;
      let text = n.data.label || n.id;
      if (text.length > 30) text = text.slice(0, 29) + '…';
      const strong = n.id === selected || n.id === hoverNode?.id;
      ctx.font = `${strong ? 600 : 500} ${strong ? 12.5 : 11.5}px ${pal.font}`;
      const tw = ctx.measureText(text).width;
      const lx = sx + n.r * k + 5, ly = sy;
      const box = [lx - 2, ly - 8, lx + tw + 2, ly + 8];
      if (!isForced && placed.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      placed.push(box);
      ctx.lineWidth = 3.5;
      ctx.strokeStyle = alpha(pal.panel, 0.9);
      ctx.strokeText(text, lx, ly);
      ctx.fillStyle = strong ? pal.text : pal.muted;
      ctx.fillText(text, lx, ly);
      if (!isForced) budget--;
    }
  }

  // ---------------------------------------------------------------- interaction
  const world = (e) => {
    const r = canvas.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    return { sx, sy, x: (sx - view.x) / view.k, y: (sy - view.y) / view.k };
  };
  function nodeAt(p) {
    let best = null, bd = Infinity;
    const slop = 4 / view.k;
    for (const n of simNodes) {
      const d = Math.hypot(n.x - p.x, n.y - p.y) - n.r;
      if (d < slop && d < bd) { best = n; bd = d; }
    }
    return best;
  }

  let drag = null;   // {mode: 'node'|'pan', node?, start, moved}
  function down(e) {
    if (e.button !== 0) return;
    const p = world(e);
    const n = nodeAt(p);
    drag = { mode: n ? 'node' : 'pan', node: n, sx: p.sx, sy: p.sy, vx: view.x, vy: view.y, moved: false };
    canvas.setPointerCapture(e.pointerId);
  }
  function move(e) {
    const p = world(e);
    if (drag) {
      if (!drag.moved && Math.hypot(p.sx - drag.sx, p.sy - drag.sy) < 3) return;
      drag.moved = true;
      fitted = true;
      if (drag.mode === 'pan') { view = { ...view, x: drag.vx + p.sx - drag.sx, y: drag.vy + p.sy - drag.sy }; schedule(); }
      else {
        const n = drag.node;
        n.fx = p.x; n.fy = p.y;
        if (frozen || reducedMotion()) { n.x = p.x; n.y = p.y; schedule(); }
        else sim.alphaTarget(0.2).restart();
        hoverNode = null;
      }
      return;
    }
    const n = nodeAt(p);
    if ((n?.data || null) !== hoverNode) { hoverNode = n?.data || null; schedule(); }
    tipPos = { x: Math.max(4, Math.min(p.sx + 14, size.w - 290)), y: p.sy + 14 > size.h - 120 ? p.sy - 110 : p.sy + 14 };
    canvas.style.cursor = n ? 'pointer' : 'grab';
  }
  function up(e) {
    if (!drag) return;
    const d = drag; drag = null;
    try { canvas.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (d.mode === 'node' && d.moved) {
      if (!frozen) { sim.alphaTarget(0); d.node.fx = null; d.node.fy = null; }
      return;
    }
    if (!d.moved) onselect?.(d.node ? d.node.data : null);
  }
  function dbl(e) {
    const n = nodeAt(world(e));
    if (n) onopen?.(n.data);
  }
  function wheel(e) {
    e.preventDefault();
    fitted = true;
    const p = world(e);
    const f = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0018));
    const k = Math.max(0.05, Math.min(8, view.k * f));
    view = { k, x: p.sx - p.x * k, y: p.sy - p.y * k };
    schedule();
  }
  function leave() { if (!drag && hoverNode) { hoverNode = null; schedule(); } }

  export function zoomBy(f) {
    const k = Math.max(0.05, Math.min(8, view.k * f));
    const cx = size.w / 2, cy = size.h / 2;
    view = { k, x: cx - ((cx - view.x) / view.k) * k, y: cy - ((cy - view.y) / view.k) * k };
    schedule();
  }

  // ---------------------------------------------------------------- lifecycle
  let ro, mql;
  const onScheme = () => { pal = readTheme(); schedule(); };
  function resize() {
    const r = wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    size = { w: Math.max(1, r.width), h: Math.max(1, r.height), dpr };
    canvas.width = Math.round(size.w * dpr);
    canvas.height = Math.round(size.h * dpr);
    canvas.style.width = size.w + 'px';
    canvas.style.height = size.h + 'px';
    if (!fitted && simNodes.length) fit();
    schedule();
  }
  onMount(() => {
    pal = readTheme();
    ctx = canvas.getContext('2d');
    resize();
    ro = new ResizeObserver(resize);
    ro.observe(wrap);
    mql = matchMedia('(prefers-color-scheme: dark)');
    mql.addEventListener('change', onScheme);
    canvas.addEventListener('wheel', wheel, { passive: false });
    rebuild(nodes, links);
  });
  onDestroy(() => {
    destroyed = true;
    sim?.stop();
    sim?.on('tick', null).on('end', null);
    if (raf) cancelAnimationFrame(raf);
    ro?.disconnect();
    mql?.removeEventListener('change', onScheme);
    canvas?.removeEventListener('wheel', wheel);
  });
</script>

<div class="netcanvas" bind:this={wrap}>
  <canvas bind:this={canvas} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}
    ondblclick={dbl} onpointerleave={leave} aria-label="Network graph. Use the table view for a text version."></canvas>
  {#if hoverNode && tooltip}
    <div class="tip" style:left="{tipPos.x}px" style:top="{tipPos.y}px">{@render tooltip(hoverNode)}</div>
  {/if}
</div>

<style>
  .netcanvas { position: absolute; inset: 0; overflow: hidden; }
  canvas { display: block; touch-action: none; cursor: grab; }
  canvas:active { cursor: grabbing; }
  .tip { position: absolute; z-index: 5; pointer-events: none; max-width: 280px; background: var(--panel); border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm); box-shadow: var(--shadow-lg); padding: 7px 9px; font-size: 12px; }
</style>
