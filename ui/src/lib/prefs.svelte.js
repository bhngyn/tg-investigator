// Per-browser preferences (localStorage, every access guarded: storage can be blocked).
function read(key, dflt) { try { const v = localStorage.getItem(key); return v === null ? dflt : v === '1'; } catch { return dflt; } }
function write(key, on) { try { localStorage.setItem(key, on ? '1' : '0'); } catch { /* storage blocked: the choice lasts until reload */ } }

export const prefs = $state({
  // WCAG 2.1.4: single-key shortcuts (/, g+letter, j k r v i u n o t x, Space) can be switched off. On by default.
  shortcuts: read('ti.shortcuts', true),
});
export function setShortcuts(on) { prefs.shortcuts = !!on; write('ti.shortcuts', on); }
