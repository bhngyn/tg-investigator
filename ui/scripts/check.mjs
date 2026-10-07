// Compile-check .svelte/.svelte.js files without a full build:
//   node scripts/check.mjs                      (every file under src/)
//   node scripts/check.mjs src/pages/Search.svelte src/components/PostDetail.svelte
// Prints errors and warnings (a11y etc). Exit code 1 on any error or warning: the UI is kept warning-free.
import { compile, compileModule } from 'svelte/compiler';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
    d.isDirectory() ? walk(join(dir, d.name)) : /\.svelte(\.js)?$/.test(d.name) ? [join(dir, d.name)] : []);
}
const files = process.argv.length > 2 ? process.argv.slice(2) : walk(new URL('../src', import.meta.url).pathname);
let failed = 0, warned = 0;
for (const f of files) {
  const src = readFileSync(f, 'utf8');
  try {
    const r = f.endsWith('.svelte') ? compile(src, { filename: f, generate: 'client' }) : compileModule(src, { filename: f, generate: 'client' });
    for (const w of r.warnings) { warned++; console.log(`WARN ${f}:${w.start?.line ?? '?'} ${w.code}: ${w.message}`); }
    console.log(`ok   ${f}`);
  } catch (e) {
    failed++;
    console.log(`ERR  ${f}:${e.start?.line ?? '?'} ${e.message}`);
  }
}
console.log(`${files.length} files, ${failed} errors, ${warned} warnings`);
process.exit(failed || warned ? 1 : 0);
