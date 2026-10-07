// App-wide state: cases, settings, tags, jobs, alerts, toasts. Svelte 5 runes.
import { api, get, post, onStaleToken } from './api.js';
import { route, caseId } from './router.svelte.js';

export const app = $state({
  cases: [],              // /api/cases rows (active only)
  casesLoaded: false,
  casesError: false,      // the last /api/cases call failed (so an unknown case id is not proof it does not exist)
  settings: {},           // /api/settings (blur, grayscale, exposure_today, ...)
  tags: [],               // /api/tags (codebook)
  jobs: { job: null, progress: null, queued: 0, running: 0, others: [] },  // /api/jobs/active
  telegram: null,         // /api/telegram {ok, message}
  unreadAlerts: 0,
  since: {},              // caseId -> ISO time of the previous visit (from /open)
  toasts: [],
  announcement: '',
  offline: false,         // background polls keep failing: the local server is unreachable
  stale: false,           // the page token is stale: the server restarted, reload
  jobsTick: 0,            // bumps whenever a job finishes: pages can $effect on it to refresh
});

/** The current case row, or null in "all" scope. */
export function currentCase() {
  const id = caseId();
  return id == null ? null : app.cases.find((c) => c.id === id) || null;
}

/** Params with the current case added (for endpoints that accept `case=`). */
export function scoped(params = {}) {
  const id = caseId();
  return id == null ? params : { ...params, case: id };
}

let toastSeq = 0;
export function toast(message, kind = 'info', ms = 4000) {
  const id = ++toastSeq;
  if (kind === 'error') ms = Math.max(ms, 10000);   // WCAG 2.2.1: give time to read, and errors also have a dismiss button
  app.toasts.push({ id, message, kind });
  setTimeout(() => { app.toasts = app.toasts.filter((t) => t.id !== id); }, ms);
}
/** Polite screen-reader announcement (rendered by App in a live region that outlives dialogs). */
export function announce(msg) {
  app.announcement = '';
  setTimeout(() => { app.announcement = msg; }, 30);
}
/** Run an async action, toast its error. Returns the result or undefined. */
export async function attempt(fn, okMessage) {
  try {
    const r = await fn();
    if (okMessage) toast(okMessage, 'ok');
    return r;
  } catch (e) {
    toast(e.message || String(e), 'error', 7000);
    return undefined;
  }
}

export async function loadCases() {
  try { app.cases = await get('/api/cases'); app.casesError = false; } catch (e) { app.casesError = true; toast(e.message, 'error'); }
  app.casesLoaded = true;
}
/** Refresh case counts shortly after triage (debounced so a fast review run makes one call). */
let casesTimer = null;
export function refreshCasesSoon() {
  clearTimeout(casesTimer);
  casesTimer = setTimeout(loadCases, 1200);
}

export async function loadSettings() {
  try { app.settings = await get('/api/settings'); } catch (e) { toast(e.message, 'error'); }
}
export async function loadTags() {
  try { app.tags = await get('/api/tags'); } catch (e) { toast(e.message, 'error'); }
}

async function loadAlertsCount() {
  try {
    const r = await get('/api/alerts', { unread: 1, limit: 1, ...(caseId() != null ? { case: caseId() } : {}) });
    app.unreadAlerts = r.unread ?? r.total ?? 0;
  } catch { /* the jobs poller owns the offline indicator; keep the old count */ }
}

// ---- entering a case: stamp last_opened once per browser session --------------
const SINCE_KEY = 'ti.since';
function readSince() { try { return JSON.parse(sessionStorage.getItem(SINCE_KEY) || '{}'); } catch { return {}; } }
app.since = readSince();

export async function enterCase(id) {
  if (id == null || app.since[id] !== undefined) return;
  app.since[id] = null;   // mark as opened so we don't call twice
  try {
    const r = await post(`/api/cases/${id}/open`);
    app.since[id] = r.previous || null;
    try { sessionStorage.setItem(SINCE_KEY, JSON.stringify(app.since)); } catch { /* private mode */ }
  } catch (e) { toast(e.message, 'error'); }
}
/** "New since last visit" timestamp for the current case (null = first visit / all scope). */
export const sinceForCase = () => (caseId() != null ? app.since[caseId()] || null : null);

// ---- polling -----------------------------------------------------------------
let lastActive = 0;
let failures = 0;
onStaleToken(() => { app.stale = true; });

async function pollJobs() {
  try {
    const j = await get('/api/jobs/active');
    const active = (j.running || 0) + (j.queued || 0) + (j.job ? 1 : 0);
    if (lastActive > 0 && active < lastActive) {       // something finished
      app.jobsTick++;
      loadCases();
      loadAlertsCount();
    }
    lastActive = active;
    // Only replace the object when something changed, so pages that depend on it do not reload every poll.
    if (JSON.stringify(j) !== JSON.stringify(app.jobs)) app.jobs = j;
    failures = 0; app.offline = false;
  } catch {
    failures++;
    if (failures >= 2) app.offline = true;
  }
}

// One self-scheduling loop: 3 s normally, backing off to 30 s while the server is unreachable,
// and paused entirely while the tab is hidden (it catches up the moment the tab is visible again).
let jobsTimer = null;
function scheduleJobs() {
  clearTimeout(jobsTimer);
  if (document.hidden) return;
  const delay = Math.min(30000, 3000 * 2 ** Math.max(0, failures - 1));
  jobsTimer = setTimeout(async () => { await pollJobs(); scheduleJobs(); }, delay);
}
let alertsTimer = null;
function scheduleAlerts() {
  clearTimeout(alertsTimer);
  if (document.hidden) return;
  alertsTimer = setTimeout(async () => { if (!app.offline) await loadAlertsCount(); scheduleAlerts(); }, 20000);
}

export function startPolling() {
  pollJobs().then(scheduleJobs);
  loadAlertsCount();
  scheduleAlerts();
  get('/api/telegram').then((t) => (app.telegram = t)).catch(() => {});
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { clearTimeout(jobsTimer); clearTimeout(alertsTimer); return; }
    pollJobs().then(scheduleJobs); loadAlertsCount(); scheduleAlerts();
  });
}
export const refreshAlertsCount = loadAlertsCount;

// ---- exposure (vicarious trauma counter) ----------------------------------------
export async function recordExposure() {
  app.settings.exposure_today = (app.settings.exposure_today || 0) + 1;
  try { await post('/api/exposure'); } catch { /* counter only */ }
}

export { route, caseId, api };
