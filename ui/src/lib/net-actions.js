// Channel actions shared by the Network and Discovery pages.
import { post } from './api.js';

/** Add channels to a case. Returns the server's {added, already, invalid}. */
export const addToCase = (caseId, names, role = 'watched') =>
  post(`/api/cases/${caseId}/channels`, { add: names, role });

/** Dismiss channels pool-wide (hidden from discovery in every case). */
export async function dismiss(names, reason = 'dismissed from discovery') {
  for (const n of names) await post(`/api/channels/${encodeURIComponent(n)}/status`, { status: 'dismissed', reason });
  return names.length;
}

/** One public t.me lookup (no login). Visible to Telegram like any page visit. */
export async function previewLive(name) {
  const r = await post('/api/resolve', { handles: [name], live: true });
  return r?.[0] || null;
}

/** One-line explanation of each logged-in discovery method. */
export const METHODS = [
  { id: 'similar', label: 'Similar channels', key: 'channels', one: "Telegram's own “similar channels” list for each channel you give.", placeholder: '@channel or t.me/channel, one per line' },
  { id: 'search_channels', label: 'Search channel names', key: 'queries', one: 'Public channels and groups whose name or username matches each keyword.', placeholder: 'keyword, one per line' },
  { id: 'search_posts', label: 'Search public posts', key: 'queries', one: "Telegram's global search over public channel posts; finds channels that posted each keyword.", placeholder: 'keyword or phrase, one per line' },
];
export const METHOD_LABEL = Object.fromEntries(METHODS.map((m) => [m.id, m.label]));
