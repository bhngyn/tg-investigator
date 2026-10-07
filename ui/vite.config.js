import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Built into investigator/web/, served by FastAPI: "/" returns index.html (with the
// per-launch token substituted), assets come from the /app mount. No dev server:
// the server's Host guard + token make proxying fiddly, so use `npm run watch`.
export default defineConfig({
  plugins: [svelte()],
  base: '/app/',
  build: {
    outDir: '../investigator/web',
    emptyOutDir: true,
    assetsInlineLimit: 0,        // CSP: no data: scripts; keep every asset a file
    modulePreload: { polyfill: false },  // the polyfill is an inline script, which CSP blocks
    chunkSizeWarningLimit: 1500,
  },
});
