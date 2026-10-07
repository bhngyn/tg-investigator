import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
import { safeUrl } from './lib/fmt.js';

mount(App, { target: document.getElementById('app') });

// Defence in depth: no link (whatever data it came from) may navigate to a javascript:, data:, file: ... URL.
// Only in-app (#, /) and http(s)/tg links are followed; anything else is swallowed.
function guardLinks(e) {
  const a = e.target?.closest?.('a[href]');
  if (!a) return;
  const h = a.getAttribute('href') || '';
  if (h.startsWith('#') || h.startsWith('/') || safeUrl(h)) return;
  e.preventDefault();
}
document.addEventListener('click', guardLinks, true);
document.addEventListener('auxclick', guardLinks, true);
