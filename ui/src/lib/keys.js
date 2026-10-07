import { prefs } from './prefs.svelte.js';
// Layout-independent shortcut keys. On a Latin layout e.key already is the letter (so QWERTZ/AZERTY/Dvorak
// users keep the letters printed on their keys). On an Arabic, Cyrillic, Hebrew ... layout e.key is a
// local letter, so fall back to the physical key (e.code "KeyR" -> "r").
export function keyOf(e) {
  const k = e.key;
  if (k && k.length === 1 && /[a-z0-9/?]/i.test(k)) return k;   // unchanged: Shift+R stays "R" and matches nothing, as before
  if (e.shiftKey) return k;
  const c = e.code || '';
  if (/^Key[A-Z]$/.test(c)) return c.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(c)) return c.slice(5);
  if (c === 'Slash') return '/';
  return k;
}
/** True when the event came from something that handles Space/Enter itself (button, link, control). */
export const isInteractive = (e) => !!e.target?.closest?.('button, a, summary, [role=button], input, textarea, select, [contenteditable]');
/** Single-key shortcuts are on (Settings > General > Keyboard shortcuts). Every page handler checks this first. */
export const shortcutsOn = () => prefs.shortcuts;
