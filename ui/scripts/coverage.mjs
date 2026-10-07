// API coverage: every route the backend exposes should have a UI surface.
//   node scripts/coverage.mjs        (run from ui/)
// Reads the FastAPI decorators from ../investigator/*.py and greps src/ for each path.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const PY = '../investigator';
const routes = new Set();
for (const f of readdirSync(PY).filter((f) => f.endsWith('.py'))) {
  const src = readFileSync(join(PY, f), 'utf8');
  for (const m of src.matchAll(/@app\.(get|post)\("(\/api\/[^"]+)"/g)) routes.add(`${m[1].toUpperCase()} ${m[2]}`);
}

function walk(d, out = []) {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) walk(p, out); else if (/\.(svelte|js)$/.test(f)) out.push(p);
  }
  return out;
}
const files = walk('src').map((p) => ({ p, s: readFileSync(p, 'utf8') }));

// "/api/cases/{cid}/open" -> regex matching `/api/cases/${x}/open` or '/api/cases/' + id + '/open'
function pattern(path) {
  const parts = path.split(/\{[^}]+\}/).map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  return new RegExp(parts.join("[^'\"`]*?(?:['\"`][^'\"`]*?['\"`])?[^'\"`]*?"));
}

// Routes that are deliberately not called (documented reason).
const EXEMPT = {
};

let missing = 0;
const rows = [];
for (const r of [...routes].sort((a, b) => a.split(' ')[1].localeCompare(b.split(' ')[1]))) {
  const path = r.split(' ')[1];
  const re = pattern(path);
  const hits = files.filter((f) => re.test(f.s)).map((f) => f.p.replace(/^src\//, ''));
  if (!hits.length && !EXEMPT[r]) missing++;
  rows.push(`${hits.length ? 'ok  ' : EXEMPT[r] ? 'skip' : 'MISS'} ${r.padEnd(52)} ${hits.slice(0, 3).join(', ')}${EXEMPT[r] ? ' — ' + EXEMPT[r] : ''}`);
}
console.log(rows.join('\n'));
console.log(`\n${routes.size} routes, ${missing} without a UI surface`);
process.exit(missing ? 1 : 0);
