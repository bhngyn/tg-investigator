<script>
  // "Which places are mentioned?" Places named in posts/comments (matched against the dictionary), plotted offline:
  // an equirectangular SVG with a graticule, points sized by number of mentions. No map tiles or remote data.
  // GET /api/places/map?<search params> (GeoJSON), POST /api/places/backfill.
  import { untrack } from 'svelte';
  import { get, post } from '../lib/api.js';
  import { route, href, setQuery } from '../lib/router.svelte.js';
  import { scoped, attempt, toast } from '../lib/store.svelte.js';
  import { num, plural, dt } from '../lib/fmt.js';
  import { POST_FILTER_KEYS, PLACES_CAVEAT, UNAMBIGUOUS_HELP } from '../lib/searchparams.js';
  import Loading from './Loading.svelte';
  import Empty from './Empty.svelte';
  import AnaHead from './AnaHead.svelte';
  import DataAttribution from './DataAttribution.svelte';

  const Q = $derived(route.query);
  // the same filters as the Search page (linked from its "Places mentioned" facet)
  const filt = $derived.by(() => { const o = {}; for (const k of POST_FILTER_KEYS) if (Q[k] && k !== 'sort' && k !== 'new') o[k] = Q[k]; return o; });
  const filtKey = $derived(JSON.stringify(filt));

  let stat = $state(null);
  const loadStat = () => get('/api/places/status').then((r) => (stat = r)).catch(() => {});
  loadStat();
  const lastScan = $derived.by(() => { try { return stat?.backfill?.value ? JSON.parse(stat.backfill.value) : null; } catch { return null; } });

  let data = $state(null), error = $state(''), loading = $state(true);
  let seq = 0;
  async function load() {
    const my = ++seq;
    loading = true; error = '';
    try { const r = await get('/api/places/map', scoped(filt)); if (my === seq) data = r; }
    catch (e) { if (my === seq) { error = e.message; data = null; } }
    if (my === seq) loading = false;
  }
  $effect(() => { filtKey; untrack(load); });

  const pts = $derived.by(() => (data?.features || []).filter((f) => Array.isArray(f.geometry?.coordinates) && Math.abs(f.geometry.coordinates[1]) <= 90 && Math.abs(f.geometry.coordinates[0]) <= 180)
    .map((f) => {
      const p = f.properties || {};
      return { id: p.entry_id ?? p.id, label: p.label || p.name || String(p.entry_id ?? ''), country: p.country || '', admin1: p.admin1 || '', category: p.category || '',
        n: Number(p.n ?? p.count ?? 0), amb: Number(p.ambiguous_n ?? p.ambiguous ?? 0), lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] };
    }).sort((a, b) => b.n - a.n));
  const totalMentions = $derived(pts.reduce((a, p) => a + p.n, 0));

  // ---- view: fit to data, then zoom in/out -------------------------------------------------------------
  const W = 760, H = 460, PAD = { l: 46, r: 12, t: 10, b: 26 };
  let zoom = $state(1), cx = $state(null), cy = $state(null);
  let focusC = $state('');           // '' = all; else a country code: zoom to that country's points
  const cList = $derived([...pts.reduce((m, p) => m.set(p.country || '?', (m.get(p.country || '?') || 0) + p.n), new Map())].sort((a, b) => b[1] - a[1]));
  const fitPts = $derived(focusC ? pts.filter((p) => (p.country || '?') === focusC) : pts);
  $effect(() => { if (focusC && !cList.some(([c]) => c === focusC)) focusC = ''; });
  const fit = $derived.by(() => {
    const P = fitPts;
    if (!P.length) return null;
    let x0 = Math.min(...P.map((p) => p.lon)), x1 = Math.max(...P.map((p) => p.lon));
    let y0 = Math.min(...P.map((p) => p.lat)), y1 = Math.max(...P.map((p) => p.lat));
    const m = Math.max(x1 - x0, y1 - y0, 0.5) * 0.12;
    return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, w: x1 - x0 + 2 * m, h: y1 - y0 + 2 * m };
  });
  $effect(() => { fit; zoom = 1; cx = null; cy = null; });
  const box = $derived.by(() => {
    if (!fit) return null;
    const ccx = cx ?? fit.cx, ccy = cy ?? fit.cy;
    const w = fit.w / zoom, h = fit.h / zoom;
    const cl = Math.cos((ccy * Math.PI) / 180) || 1;
    const pw = W - PAD.l - PAD.r, ph = H - PAD.t - PAD.b;
    const s = Math.min(pw / (w * cl), ph / h);          // px per degree of latitude
    const hx = pw / s / cl / 2, hy = ph / s / 2;
    return { x0: ccx - hx, x1: ccx + hx, y0: ccy - hy, y1: ccy + hy, cl, s };
  });
  const px = (lon) => PAD.l + (lon - box.x0) * box.cl * box.s;
  const py = (lat) => PAD.t + (box.y1 - lat) * box.s;
  function ticks(a, b) {
    const raw = (b - a) / 5;
    const steps = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30];
    const st = steps.find((x) => x >= raw) || 30;
    const out = [];
    for (let v = Math.ceil(a / st) * st; v <= b; v += st) out.push(+v.toFixed(6));
    return out;
  }
  const xt = $derived(box ? ticks(box.x0, box.x1) : []);
  const yt = $derived(box ? ticks(box.y0, box.y1) : []);
  const fmtT = (arr, v) => v.toFixed(arr.length > 1 && Math.abs(arr[1] - arr[0]) < 1 ? 1 : 0);
  const maxN = $derived(Math.max(1, ...pts.map((p) => p.n)));
  const rad = (n) => 4 + 14 * Math.sqrt(n / maxN);
  const inView = (p) => box && p.lon >= box.x0 && p.lon <= box.x1 && p.lat >= box.y0 && p.lat <= box.y1;

  // admin1 and country labels come from the data itself (centre of the mentioned places in each)
  function centroids(keyFn) {
    const m = new Map();
    for (const p of pts) { const k = keyFn(p); if (!k) continue; const g = m.get(k) || { k, n: 0, x: 0, y: 0 }; g.n += p.n || 1; g.x += p.lon * (p.n || 1); g.y += p.lat * (p.n || 1); m.set(k, g); }
    return [...m.values()].map((g) => ({ label: g.k.split('|').pop(), lon: g.x / g.n, lat: g.y / g.n, n: g.n }));
  }
  // region names only when zoomed in enough for them not to pile up
  const admin = $derived(spanDeg < 14 ? centroids((p) => (p.admin1 ? `${p.country}|${p.admin1}` : '')).filter((g) => inView(g)) : []);
  const countries = $derived(centroids((p) => p.country).filter((g) => inView(g)));
  // point labels: most-mentioned first, skipping any that would overlap one already placed
  const topLabels = $derived.by(() => {
    if (!box) return [];
    const placed = [], out = [];
    for (const p of pts.filter(inView)) {
      const x = px(p.lon) + rad(p.n) + 3, y = py(p.lat), w = String(p.label).length * 5.6 + 4;
      if (placed.some((r) => x < r.x + r.w && x + w > r.x && Math.abs(y - r.y) < 11)) continue;
      placed.push({ x, y, w }); out.push(p);
      if (out.length >= 30) break;
    }
    return out;
  });
  const spanDeg = $derived(box ? box.x1 - box.x0 : 360);

  let hov = $state(null);
  const shown = $derived(hov);

  // ---- backfill ------------------------------------------------------------------------------------
  let busy = $state(false);
  async function backfill() {
    if (!confirm('Tag place names in all collected posts and comments?\n\nThis reads text already in the archive and matches it against the place dictionary (nothing is fetched and nothing original is changed). Matches are name matches only, not verified locations, and it is safe to repeat.')) return;
    busy = true;
    const r = await attempt(() => post('/api/places/backfill', {}));
    busy = false;
    if (r === undefined) return;
    if (r.available === false) { toast(r.message || 'No place dictionary is loaded.', 'error', 7000); return; }
    toast('Place scan finished', 'ok', 5000);
    load(); loadStat();
  }
  const searchFor = (p) => href('search', { ...filt, place: String(p.id) });
</script>

<AnaHead question="Which place names are mentioned in the collected text?">
  <b>{PLACES_CAVEAT}</b> A place name in a post is matched to the dictionary by spelling; it does not show that something happened there, and many names belong to several places
  (marked <b>?</b> where a mention could be another place of the same name). Points are the dictionary's coordinates for the place, not where anything was observed. Ordinary words and personal names can be mistaken for place names (an Arabic given name that is also a village name, a Russian adjective that resembles a place); read the posts before relying on a point. This tab is about place <i>names</i>; coordinates written in posts are on the <a href={href('analysis', 'places')}>Claimed coordinates</a> tab.
  {#if Object.keys(filt).some((k) => k !== 'unambiguous')}<br />Showing places mentioned in the posts of the search: {#each Object.entries(filt).filter(([k]) => k !== 'unambiguous') as [k, v]}<code>{k}={v}</code> {/each}<a href={href('analysis', 'mentions')}>Clear</a>{/if}
</AnaHead>

<div class="row controls">
  <span class="small muted">{#if data}{plural(pts.length, 'place')} · {num(totalMentions)} mentions{/if}{#if stat} · {num(stat.tagged_items)} items tagged in all{/if}{#if lastScan} · last full scan {dt(lastScan.at)}{/if}</span>
  <span class="spacer"></span>
  <label class="check small" title={UNAMBIGUOUS_HELP}><input type="checkbox" checked={Q.unambiguous === '1'} onchange={(e) => setQuery({ unambiguous: e.currentTarget.checked ? '1' : null })} /> Only unambiguous place names</label>
  <span class="tiny faint">{UNAMBIGUOUS_HELP}</span>
  <button onclick={backfill} disabled={busy} title="Tag place names in existing posts and comments (explicit, idempotent)">{busy ? 'Scanning…' : 'Scan archive for places'}</button>
</div>

{#if error}
  <div class="callout bad" role="alert">{error} <button class="sm" onclick={load}>Retry</button></div>
{:else if loading && !data}
  <Loading />
{:else if !pts.length}
  <Empty title="No places mentioned yet">
    Place names are tagged as posts are collected, using the place dictionary (Settings → Dictionary). To tag what is already in the archive, use “Scan archive for places”.
    {#if data?.message || stat?.message}<div class="callout warn small">{data?.message || stat?.message}</div>{/if}
  </Empty>
{:else}
  {#if cList.length > 1}
    <div class="row wrap focus" role="group" aria-label="Zoom to a country">
      <span class="small muted">Zoom to</span>
      <button class="chip" class:on={!focusC} aria-pressed={!focusC} onclick={() => { focusC = ''; zoom = 1; cx = null; cy = null; }}>All</button>
      {#each cList as [c, n]}<button class="chip" class:on={focusC === c} aria-pressed={focusC === c} onclick={() => { focusC = c; zoom = 1; cx = null; cy = null; }}>{c === '?' ? 'no country' : c} <span class="faint">{num(n)}</span></button>{/each}
    </div>
  {/if}
  <figure class="panel plot" class:dim={loading}>
    <div class="zoom row" role="group" aria-label="Zoom">
      <button class="sm" onclick={() => (zoom = Math.min(64, zoom * 2))} aria-label="Zoom in">＋</button>
      <button class="sm" onclick={() => (zoom = Math.max(1, zoom / 2))} disabled={zoom <= 1} aria-label="Zoom out">−</button>
      <button class="sm ghost" onclick={() => { zoom = 1; cx = null; cy = null; }} disabled={zoom === 1 && cx == null}>Fit to data</button>
      {#if zoom > 1}
        <button class="sm ghost" onclick={() => (cx = (cx ?? fit.cx) - (box.x1 - box.x0) / 3)} aria-label="Pan west">←</button>
        <button class="sm ghost" onclick={() => (cx = (cx ?? fit.cx) + (box.x1 - box.x0) / 3)} aria-label="Pan east">→</button>
        <button class="sm ghost" onclick={() => (cy = (cy ?? fit.cy) + (box.y1 - box.y0) / 3)} aria-label="Pan north">↑</button>
        <button class="sm ghost" onclick={() => (cy = (cy ?? fit.cy) - (box.y1 - box.y0) / 3)} aria-label="Pan south">↓</button>
      {/if}
    </div>
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="Places mentioned in the text plotted on a blank graticule, no basemap">
      <defs><clipPath id="mclip"><rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} /></clipPath></defs>
      <rect x={PAD.l} y={PAD.t} width={W - PAD.l - PAD.r} height={H - PAD.t - PAD.b} class="frame" />
      {#each xt as v}<line x1={px(v)} x2={px(v)} y1={PAD.t} y2={H - PAD.b} class="grat" /><text x={px(v)} y={H - PAD.b + 14} text-anchor="middle" class="tick">{fmtT(xt, v)}°</text>{/each}
      {#each yt as v}<line y1={py(v)} y2={py(v)} x1={PAD.l} x2={W - PAD.r} class="grat" /><text x={PAD.l - 5} y={py(v) + 3} text-anchor="end" class="tick">{fmtT(yt, v)}°</text>{/each}
      <g clip-path="url(#mclip)">
        {#each countries as g (g.label)}<text x={px(g.lon)} y={py(g.lat)} text-anchor="middle" class="country">{g.label}</text>{/each}
        {#each admin as g (g.label + g.lon)}<text x={px(g.lon)} y={py(g.lat) + 12} text-anchor="middle" class="admin" dir="auto">{g.label}</text>{/each}
        {#each pts.filter(inView) as p (p.id)}
          <circle cx={px(p.lon)} cy={py(p.lat)} r={rad(p.n)} class="pt" class:amb={p.amb > 0 && p.amb >= p.n / 2} class:sel={shown?.id === p.id}
            role="link" tabindex="0" aria-label="{p.label}: {p.n} mentions. Search posts mentioning it"
            onmouseenter={() => (hov = p)} onmouseleave={() => (hov = null)} onfocus={() => (hov = p)} onblur={() => (hov = null)}
            onclick={() => (location.hash = searchFor(p))} onkeydown={(e) => e.key === 'Enter' && (location.hash = searchFor(p))}>
            <title>{p.label}{p.country ? ', ' + p.country : ''} · {plural(p.n, 'mention')}{p.amb ? ` (${p.amb} ambiguous)` : ''} — click to search</title>
          </circle>
        {/each}
        {#each topLabels as p (p.id)}<text x={px(p.lon) + rad(p.n) + 3} y={py(p.lat) + 3} class="plabel" dir="auto">{p.label}</text>{/each}
      </g>
    </svg>
    <figcaption class="small muted row">
      <span>Circle area = number of mentions. Dashed = mostly ambiguous names.</span>
      <span class="spacer"></span>
      {#if shown}<span><b dir="auto">{shown.label}</b> {[shown.admin1, shown.country, shown.category].filter(Boolean).join(' · ')} — {plural(shown.n, 'mention')}{shown.amb ? `, ${shown.amb} ambiguous` : ''}</span>
      {:else}<span class="faint">Hover a point for details; click it to search the posts. Blank grid (no basemap), x scaled by cos(latitude).</span>{/if}
      <div style="flex-basis:100%"><DataAttribution long /></div>
    </figcaption>
  </figure>

  <div class="section-title">Places <span class="faint">most mentioned first</span></div>
  <div class="table-wrap panel">
    <table class="t">
      <thead><tr><th scope="col">Place</th><th scope="col">Where</th><th scope="col">Kind</th><th scope="col" class="num">Mentions</th><th scope="col" class="num" title="Mentions where the name could also mean another place">Ambiguous</th><th scope="col"></th></tr></thead>
      <tbody>
        {#each pts.slice(0, 200) as p (p.id)}
          <tr onmouseenter={() => (hov = p)} onmouseleave={() => (hov = null)} class:sel={hov?.id === p.id}>
            <td dir="auto"><b>{p.label}</b></td>
            <td class="small">{[p.admin1, p.country].filter(Boolean).join(' · ')}</td>
            <td class="small">{p.category}</td>
            <td class="num">{num(p.n)}</td>
            <td class="num">{p.amb ? num(p.amb) : '–'}</td>
            <td class="right"><a class="btn sm ghost" href={searchFor(p)}>Search posts</a></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  {#if pts.length > 200}<p class="faint small">Showing the 200 most mentioned of {num(pts.length)}.</p>{/if}
{/if}

<style>
  .controls { margin-bottom: 10px; }
  .focus { gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
  .plot { margin: 0 0 12px; padding: 8px; position: relative; }
  .plot.dim { opacity: .6; }
  .zoom { position: absolute; top: 12px; right: 14px; z-index: 2; gap: 4px; background: var(--panel); border-radius: var(--radius-sm); padding: 2px; }
  svg { width: 100%; height: auto; max-height: 520px; display: block; }
  .frame { fill: var(--panel-2); stroke: var(--line-strong); }
  .grat { stroke: var(--line); stroke-width: 1; }
  .tick { fill: var(--faint); font-size: 10px; font-family: var(--mono); }
  .country { fill: var(--faint); font-size: 22px; font-weight: 700; letter-spacing: .12em; opacity: .35; pointer-events: none; }
  .admin { fill: var(--muted); font-size: 10.5px; opacity: .7; pointer-events: none; }
  .plabel { fill: var(--text); font-size: 10.5px; pointer-events: none; paint-order: stroke; stroke: var(--panel-2); stroke-width: 3px; }
  .pt { fill: var(--accent); fill-opacity: .55; stroke: var(--accent); stroke-width: 1; cursor: pointer; }
  .pt.amb { stroke-dasharray: 3 2; fill: var(--warn); stroke: var(--warn); }
  .pt:hover, .pt.sel { fill-opacity: .85; stroke: var(--text); stroke-width: 2; }
  tr.sel { background: var(--accent-soft); }
</style>
