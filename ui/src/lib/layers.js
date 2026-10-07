// Stack of open overlays (drawers, modals). Only the topmost one reacts to Esc, so a modal
// opened over a drawer closes alone. Page shortcuts can check anyLayerOpen() to stay quiet.
// While any layer is open the app shell (.shell) is `inert` (no focus, no clicks, hidden from screen readers),
// and every layer except the top one is inert too, so focus cannot wander behind a dialog.
const stack = [];
const nodes = new Map();
let seq = 0;
function sync() {
  if (typeof document === 'undefined') return;
  const shell = document.querySelector('.shell');
  if (shell) shell.inert = stack.length > 0;
  stack.forEach((id, i) => { const n = nodes.get(id); if (n) n.inert = i < stack.length - 1; });
}
export function pushLayer(node) { const id = ++seq; stack.push(id); if (node) nodes.set(id, node); sync(); return id; }
export function popLayer(id) { const i = stack.indexOf(id); if (i >= 0) stack.splice(i, 1); nodes.delete(id); sync(); }
export const isTopLayer = (id) => stack[stack.length - 1] === id;
export const anyLayerOpen = () => stack.length > 0;
