// Triage actions that work in both scopes.
//   In a case  -> per-case triage (case_reviews / case_tags / notes.case_id)
//   All scope  -> global (legacy) triage on posts; comments have no global triage.
import { post, get } from './api.js';
import { caseId } from './router.svelte.js';
import { refreshCasesSoon } from './store.svelte.js';

export const REVIEW = ['unreviewed', 'relevant', 'verify', 'irrelevant'];
export const REVIEW_LABEL = { unreviewed: 'Unreviewed', relevant: 'Relevant', verify: 'Needs verification', irrelevant: 'Not relevant' };

/** Current review status of a result card in the active scope. */
export function reviewOf(item) {
  if (caseId() != null) return item.case_review || 'unreviewed';
  return item.review_status || 'unreviewed';
}
/** Tag ids on a result card in the active scope. */
export function tagsOf(item) {
  if (caseId() != null) return (item.case_tags || []).map((t) => (typeof t === 'object' ? t.id ?? t.tag_id : t));
  return (item.tags || []).map((t) => (typeof t === 'object' ? t.id ?? t.tag_id : t));
}

/** Can this item be triaged in the current scope? */
export function canTriage(itemType) {
  return itemType === 'post' || caseId() != null;
}

export async function setReview(itemType, ids, status) {
  ids = Array.isArray(ids) ? ids : [ids];
  const cid = caseId();
  if (cid != null) {
    const r = await post(`/api/cases/${cid}/review`, { item_type: itemType, ids, status });
    refreshCasesSoon();
    return r;
  }
  if (itemType !== 'post') throw new Error('Comments can only be triaged inside a case. Pick a case first.');
  // No bulk endpoint in the global scope: one call per post. Report how far it got if one fails.
  let done = 0;
  for (const id of ids) {
    try { await post(`/api/posts/${id}/review`, { status }); done++; }
    catch (e) { throw new Error(ids.length > 1 ? `${e.message} (${done} of ${ids.length} applied; the rest were not changed)` : e.message); }
  }
  return { ok: true };
}

export async function setTag(itemType, id, tagId, on) {
  const cid = caseId();
  if (cid != null) return post(`/api/cases/${cid}/tags`, { item_type: itemType, id, tag_id: tagId, on });
  if (itemType !== 'post') throw new Error('Comments can only be tagged inside a case.');
  return post(`/api/posts/${id}/tags`, { tag_id: tagId, on });
}

export async function addNote(itemType, id, text, channel) {
  const cid = caseId();
  if (cid != null) return post(`/api/cases/${cid}/notes`, { text, item_type: itemType, id, channel });
  if (itemType === 'post') return post(`/api/posts/${id}/notes`, { text });
  if (itemType === 'channel') return post(`/api/channels/${encodeURIComponent(channel || id)}/notes`, { text });
  throw new Error('Notes on comments need a case.');
}

export async function listNotes(itemType, id) {
  const cid = caseId();
  if (cid != null) return get(`/api/cases/${cid}/notes`, { item_type: itemType, id });
  return null;   // global notes come back inside /api/posts/{id}
}
