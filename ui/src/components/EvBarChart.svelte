<script>
  // Inline SVG bar chart of counts per day. data = [{d:'2026-07-01', n:3}, ...]; gaps are filled with zeros.
  import { num } from '../lib/fmt.js';
  let { data = [], maxDays = 180, label = 'posts per day', color = 'var(--accent)' } = $props();

  const series = $derived.by(() => {
    if (!data.length) return [];
    const m = new Map(data.map((r) => [r.d, r.n]));
    const days = [...m.keys()].sort();
    const end = new Date(days[days.length - 1] + 'T00:00:00Z');
    let start = new Date(days[0] + 'T00:00:00Z');
    const cap = new Date(end); cap.setUTCDate(cap.getUTCDate() - (maxDays - 1));
    if (start < cap) start = cap;
    const out = [];
    for (let t = new Date(start); t <= end; t.setUTCDate(t.getUTCDate() + 1)) {
      const d = t.toISOString().slice(0, 10);
      out.push({ d, n: m.get(d) || 0 });
    }
    return out;
  });
  const W = 800, H = 150, PADL = 30, PADB = 20, PADT = 8;
  const max = $derived(Math.max(1, ...series.map((s) => s.n)));
  const bw = $derived(series.length ? (W - PADL) / series.length : 0);
  const total = $derived(series.reduce((a, s) => a + s.n, 0));
</script>

{#if series.length}
  <svg viewBox="0 0 {W} {H}" role="img" aria-label="{label}: {num(total)} in total between {series[0].d} and {series[series.length - 1].d}" style="width:100%;height:auto;display:block">
    <line x1={PADL} x2={W} y1={H - PADB} y2={H - PADB} stroke="var(--line-strong)" />
    <line x1={PADL} x2={W} y1={PADT} y2={PADT} stroke="var(--line)" stroke-dasharray="3 3" />
    <text x={PADL - 4} y={PADT + 4} text-anchor="end" font-size="10" fill="var(--faint)">{max}</text>
    <text x={PADL - 4} y={H - PADB} text-anchor="end" font-size="10" fill="var(--faint)">0</text>
    {#each series as s, i}
      {#if s.n}
        {@const h = ((H - PADB - PADT) * s.n) / max}
        <rect x={PADL + i * bw + 0.5} y={H - PADB - h} width={Math.max(1, bw - 1)} height={h} fill={color} rx="1"><title>{s.d}: {s.n}</title></rect>
      {/if}
    {/each}
    <text x={PADL} y={H - 5} font-size="10" fill="var(--faint)">{series[0].d}</text>
    <text x={W} y={H - 5} text-anchor="end" font-size="10" fill="var(--faint)">{series[series.length - 1].d}</text>
  </svg>
{/if}
