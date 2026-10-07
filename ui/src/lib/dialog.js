// Svelte action shared by Modal and Drawer: makes an overlay behave like a real modal dialog.
//   <div class="layer" use:dialogLayer={{ close: requestClose, initial: '[data-autofocus]' }}> <div role="dialog" tabindex="-1">…
// - moves the layer to <body> (outside the inert app shell)
// - registers with lib/layers.js (shell inert, Esc only for the top layer)
// - moves focus in (first [data-autofocus]/[autofocus] element, else the dialog panel itself), traps Tab, and puts
//   focus back on the element that opened it when the layer goes away
import { pushLayer, popLayer, isTopLayer } from './layers.js';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"]), [contenteditable=true]';
const visible = (el) => !el.closest('[hidden]') && (el.offsetWidth || el.offsetHeight || el.getClientRects().length);

/** Unsaved text: any non-empty field marked data-draft (note editors that start empty) inside the layer. Returns true when it is fine to close. */
export function okToDiscard(root) {
  const filled = [...(root?.querySelectorAll('[data-draft]') || [])].some((t) => (t.value || '').trim());
  return !filled || confirm('You have unsaved text here. Close and discard it?');
}

export function dialogLayer(node, opts = {}) {
  let o = opts;
  const opener = document.activeElement;
  document.body.appendChild(node);
  const id = pushLayer(node);
  const panel = node.querySelector('[role=dialog]') || node;
  // Toasts live outside the shell and the layer; keep their dismiss buttons reachable at the end of the Tab cycle.
  const focusables = () => [...panel.querySelectorAll(FOCUSABLE), ...document.querySelectorAll('.toasts button')].filter(visible);
  queueMicrotask(() => {
    const first = panel.querySelector('[data-autofocus], [autofocus]');
    (first && visible(first) ? first : panel).focus({ preventScroll: true });
  });
  function key(e) {
    if (!isTopLayer(id)) return;
    if (e.isComposing || e.keyCode === 229) return;   // Esc/Tab while an IME composition is active belongs to the IME
    if (e.key === 'Escape') {
      if (e.defaultPrevented) return;
      e.stopPropagation(); e.preventDefault();
      if (okToDiscard(panel)) o.close?.(true);
      return;
    }
    if (e.key !== 'Tab') return;
    const f = focusables();
    if (!f.length) { e.preventDefault(); panel.focus(); return; }
    const a = document.activeElement;
    const i = f.indexOf(a === panel ? null : a);
    // Explicit cycle over the panel's controls plus any toast dismiss buttons (which live outside the layer).
    e.preventDefault();
    if (i < 0) f[e.shiftKey ? f.length - 1 : 0].focus();
    else f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
  }
  window.addEventListener('keydown', key);
  return {
    update(n) { o = n; },
    destroy() {
      window.removeEventListener('keydown', key);
      popLayer(id);
      // The node was moved to <body>, so Svelte's own teardown (which walks siblings inside the page) can miss it
      // when the whole page unmounts on a route change. Remove it ourselves.
      node.remove();
      // Restore focus once the shell is interactive again.
      queueMicrotask(() => {
        const target = opener && opener.isConnected && visible(opener) ? opener : document.querySelector('main');
        target?.focus?.({ preventScroll: true });
      });
    },
  };
}
