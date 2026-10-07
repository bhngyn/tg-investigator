<script>
  // Alerts inbox + rules manager.
  import { untrack } from 'svelte';
  import { app, toast, refreshAlertsCount, currentCase } from '../lib/store.svelte.js';
  import { get, post } from '../lib/api.js';
  import { route, caseId, setQuery } from '../lib/router.svelte.js';
  import { num, pj, plural } from '../lib/fmt.js';
  import Loading from '../components/Loading.svelte';
  import Empty from '../components/Empty.svelte';
  import Modal from '../components/Modal.svelte';
  import EvAlertItem from '../components/EvAlertItem.svelte';
  import ExpansionChips from '../components/ExpansionChips.svelte';
  import DictBanner from '../components/DictBanner.svelte';
  import { normaliseExpansion, expansionParams } from '../lib/expansion.js';

  const KINDS = [
    { k: 'keyword', label: 'Keyword', help: 'Fires when newly collected posts or comments match your words (same syntax as Search).' },
    { k: 'post_deleted', label: 'Post deleted', help: 'Fires when a post we captured earlier is found missing from the channel.' },
    { k: 'comment_deleted', label: 'Comment deleted', help: 'Fires when a comment we captured earlier is found deleted.' },
    { k: 'post_edited', label: 'Post edited', help: 'Fires when a post’s text or media changes. The earlier version is kept.' },
    { k: 'channel_change', label: 'Channel change', help: 'Fires when a channel is renamed, changes title or description, turns its preview off or on, or its subscribers jump.' },
    { k: 'admin_change', label: 'Admin change', help: 'Fires when an admin is added to or removed from a channel’s comment group.' },
    { k: 'new_candidate', label: 'New candidate channel', help: 'Fires when a channel outside the case is linked from several channels inside it. Needs a case.' },
    { k: 'media_reused', label: 'Media reused', help: 'Fires when a newly collected file is byte-identical to one already stored from another channel.' },
  ];
  const kindOf = (k) => KINDS.find((x) => x.k === k);

  const cid = $derived(caseId());
  const tab = $derived(route.query.tab === 'rules' ? 'rules' : 'inbox');
  const PAGE = 50;

  // ---------- inbox ----------
  let inbox = $state({ total: 0, unread: 0, alerts: [] });
  let loading = $state(true);
  let error = $state('');
  let onlyUnread = $state(route.query.unread === '1');
  let ruleFilter = $state(route.query.rule || '');
  let offset = $state(0);
  let checking = $state(false);
  let rules = $state([]);
  let rulesLoaded = $state(false);

  let inboxSeq = 0;
  async function loadInbox() {
    const my = ++inboxSeq;   // the latest request wins when filters change quickly
    error = '';
    try {
      const r = await get('/api/alerts', { case: cid, unread: onlyUnread, rule: ruleFilter, limit: PAGE, offset });
      if (my === inboxSeq) inbox = r;
    } catch (e) { if (my === inboxSeq) error = e.message; }
    if (my === inboxSeq) loading = false;
  }
  let rulesSeq = 0;
  async function loadRules() {
    const my = ++rulesSeq;
    try { const r = await get('/api/alert_rules', { case: cid }); if (my === rulesSeq) rules = r; } catch (e) { if (my === rulesSeq) toast(e.message, 'error'); }
    if (my === rulesSeq) rulesLoaded = true;
  }
  $effect(() => {
    onlyUnread; ruleFilter; offset; cid; app.jobsTick;
    untrack(loadInbox);
  });
  $effect(() => { cid; app.jobsTick; untrack(loadRules); });
  $effect(() => { const q = { unread: onlyUnread ? '1' : null, rule: ruleFilter || null }; untrack(() => setQuery(q)); });

  async function toggleRead(a) {
    const nowRead = !a.read_at;
    try {
      await post('/api/alerts/read', nowRead ? { ids: [a.id] } : { ids: [a.id], unread: true });
      a.read_at = nowRead ? new Date().toISOString() : null;
      inbox.unread += nowRead ? -1 : 1;
      refreshAlertsCount(); loadRules();
    } catch (e) { toast(e.message, 'error'); }
  }
  async function markAll() {
    const scopeTxt = cid != null ? 'in this case' : 'across all cases';
    if (!confirm(`Mark all alerts ${scopeTxt} as read?`)) return;
    try {
      const r = await post('/api/alerts/read', cid != null ? { all: true, case: cid } : { all: true });
      toast(`${plural(r.updated, 'alert')} marked read`, 'ok');
      await loadInbox(); refreshAlertsCount(); loadRules();
    } catch (e) { toast(e.message, 'error'); }
  }
  async function checkNow() {
    checking = true;
    try {
      const r = await post('/api/alerts/check');
      toast(r.new ? `${plural(r.new, 'new alert')} raised` : 'Checked all rules: nothing new', r.new ? 'ok' : 'info');
      offset = 0; await Promise.all([loadInbox(), loadRules()]); refreshAlertsCount();
    } catch (e) { toast(e.message, 'error'); }
    checking = false;
  }

  // ---------- multilingual expansion ----------
  /** Frozen expansion stored on a rule, or null for legacy (literal) rules. */
  function frozenOf(r) {
    const x = typeof r.expansion === 'string' ? pj(r.expansion, null) : r.expansion;
    if (!x || (Array.isArray(x) ? false : !x.groups && !x.fts) ) return r.query_fts ? { groups: [], fts: r.query_fts, dict_version: r.dict_version } : null;
    return normaliseExpansion(x, { fts: r.query_fts || x.fts, dict_version: r.dict_version || x.dict_version });
  }
  const keepFrozen = $derived(!!ed.r.id && !!ed.frozen && ed.r.query.trim() === ed.origQuery && ed.r.kind === 'keyword');
  const expNote = 'Frozen when saved — dictionary edits won’t change this rule';
  // live preview of what the words will expand to (debounced); nothing is saved
  let lookupSeq = 0;
  $effect(() => {
    const q = ed.r.query.trim(), open = ed.open, kind = ed.r.kind, exp = ed.exp.expand, drop = ed.exp.drop.join(','), skip = ed.exp.skip.join(',');
    if (!open || kind !== 'keyword' || !exp || !q) { untrack(() => { ed.lookup = null; ed.looking = false; }); return; }
    const my = ++lookupSeq;
    const t = setTimeout(async () => {
      ed.looking = true;
      try {
        const r = await get('/api/dictionary/lookup', { q, drop, skip });
        if (my === lookupSeq) ed.lookup = normaliseExpansion(r);
      } catch { if (my === lookupSeq) ed.lookup = null; }
      if (my === lookupSeq) ed.looking = false;
    }, 300);
    return () => clearTimeout(t);
  });
  async function reexpand(r) {
    if (!confirm(`Re-expand "${r.name}" with the current dictionary?\n\nThe rule's frozen list of spellings is replaced by a fresh one, so it may start matching (or stop matching) different words from now on. The change is recorded in the audit log. Alerts already raised are not changed.`)) return;
    try {
      await post('/api/alert_rules', { id: r.id, reexpand: true });
      toast('Rule re-expanded with the current dictionary', 'ok');
      await loadRules();
    } catch (e) { toast(e.message, 'error'); }
  }

  // ---------- rules ----------
  const blank = () => ({ id: null, kind: 'keyword', name: '', query: '', scope: 'posts', case_id: cid ?? '', channels: '', min_links: 2, notify: false, enabled: true });
  const blankEd = (o = {}) => ({ open: false, r: blank(), busy: false, error: '', preview: null, previewing: false, days: 7,
    exp: { expand: true, drop: [], skip: [] }, lookup: null, looking: false, origQuery: null, frozen: null, ...o });
  let ed = $state(blankEd());

  function newRule() { ed = blankEd({ open: true }); }
  function editRule(r) {
    ed = blankEd({ open: true, origQuery: (r.query || '').trim(), frozen: frozenOf(r),
      r: { id: r.id, kind: r.kind, name: r.name || '', query: r.query || '', scope: r.scope || 'posts', case_id: r.case_id ?? '',
        channels: (pj(r.channels, []) || []).join(', '), min_links: r.min_links || 2, notify: !!r.notify, enabled: r.enabled !== 0 } });
  }
  function body() {
    const r = ed.r;
    const chans = r.channels.split(/[\s,;]+/).map((x) => x.replace(/^@|^.*t\.me\//i, '').trim().toLowerCase()).filter(Boolean);
    const b = { kind: r.kind, name: r.name.trim(), case_id: r.case_id || null, channels: chans, notify: r.notify, enabled: r.enabled };
    if (r.kind === 'keyword') {
      b.query = r.query.trim(); b.scope = r.scope;
      // Existing rule with unchanged words keeps its frozen expansion (the server only re-expands when the query changes).
      if (!keepFrozen) { b.expand = ed.exp.expand; b.drop = ed.exp.drop; b.skip = ed.exp.skip; }
    }
    if (r.kind === 'new_candidate') b.min_links = Number(r.min_links) || 2;
    return b;
  }
  async function preview() {
    ed.error = ''; ed.previewing = true; ed.preview = null;
    try { ed.preview = await post('/api/alert_rules/preview', { ...body(), ...(ed.r.id && keepFrozen ? { id: ed.r.id } : {}), days: ed.days }); }
    catch (e) { ed.error = e.message; }
    ed.previewing = false;
  }
  async function saveRule() {
    if (ed.r.kind === 'keyword' && !keepFrozen && ed.exp.expand && ed.lookup?.truncated
      && !confirm('The list of other spellings for these words was cut off to keep searches fast, so some spellings are NOT covered.\n\nSaving freezes this shortened list into the rule: it will keep missing those spellings until you re-expand it. Narrow the words or drop chips you do not need, or save anyway?')) return;
    ed.busy = true; ed.error = '';
    try {
      await post('/api/alert_rules', ed.r.id ? { id: ed.r.id, ...body() } : body());
      ed.open = false;
      toast(ed.r.id ? 'Rule updated' : 'Rule created. It applies to things collected from now on.', 'ok');
      await loadRules(); refreshAlertsCount();
    } catch (e) { ed.error = e.message; }
    ed.busy = false;
  }
  async function toggleRule(r) {
    try { await post('/api/alert_rules', { id: r.id, enabled: r.enabled === 0 }); await loadRules(); }
    catch (e) { toast(e.message, 'error'); }
  }
  async function deleteRule(r) {
    if (!confirm(`Delete the rule "${r.name}"?\n\nThis also removes its ${plural(r.alerts, 'alert')} from the inbox. This cannot be undone (the deletion is recorded in the audit log).`)) return;
    try {
      await post('/api/alert_rules', { id: r.id, delete: true });
      toast('Rule deleted', 'ok');
      if (ruleFilter == r.id) ruleFilter = '';
      await Promise.all([loadRules(), loadInbox()]); refreshAlertsCount();
    } catch (e) { toast(e.message, 'error'); }
  }
  const caseName = (id) => app.cases.find((c) => c.id === id)?.name || `case ${id}`;
  function limits(r) {
    const out = [];
    if (r.case_id) out.push(caseName(r.case_id));
    const ch = pj(r.channels, []) || [];
    if (ch.length) out.push(ch.length <= 3 ? ch.map((c) => '@' + c).join(', ') : `${ch.length} channels`);
    return out.length ? out.join(' · ') : 'everything';
  }
  function ruleWhat(r) {
    if (r.kind === 'keyword') return `“${r.query}” in ${r.scope || 'posts'}`;
    if (r.kind === 'new_candidate') return `linked from ≥ ${r.min_links || 2} case channels`;
    return kindOf(r.kind)?.label || r.kind;
  }
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h1>Alerts</h1>
      <p>Rules are checked after every collection. Each alert is raised once per rule and item, and only for things collected after the rule was created.</p>
    </div>
    <div class="row">
      <button onclick={checkNow} disabled={checking} title="Run every rule now over the last day (rules normally run after each collection)">{checking ? 'Checking…' : 'Check now'}</button>
      <button class="primary" onclick={() => { setQuery({ tab: 'rules' }); newRule(); }}>＋ New rule</button>
    </div>
  </div>

  <div class="tabs" role="group" aria-label="Alerts sections">
    <button class:on={tab === 'inbox'} aria-pressed={tab === 'inbox'} onclick={() => setQuery({ tab: null })}>Inbox {#if inbox.unread}<span class="badge">{num(inbox.unread)}</span>{/if}</button>
    <button class:on={tab === 'rules'} aria-pressed={tab === 'rules'} onclick={() => setQuery({ tab: 'rules' })}>Rules <span class="faint small">({rules.length})</span></button>
  </div>

  {#if tab === 'inbox'}
    <div class="row" style="margin-bottom:10px">
      <label class="check"><input type="checkbox" bind:checked={onlyUnread} onchange={() => (offset = 0)} /> Unread only</label>
      <select bind:value={ruleFilter} onchange={() => (offset = 0)} aria-label="Filter by rule">
        <option value="">All rules</option>
        {#each rules as r}<option value={String(r.id)}>{r.name}</option>{/each}
      </select>
      <span class="spacer"></span>
      <span class="muted small">{num(inbox.total)} alerts · {num(inbox.unread)} unread{cid != null && currentCase() ? ` in ${currentCase().name}` : ''}</span>
      <button class="sm" onclick={markAll} disabled={!inbox.unread}>Mark all read</button>
    </div>
    {#if loading}<Loading />
    {:else if error}<div class="callout bad" role="alert">{error} <button class="sm" onclick={loadInbox}>Retry</button></div>
    {:else if !inbox.alerts.length}
      {#if !rules.length && rulesLoaded}
        <Empty title="No alert rules yet">
          Alert rules watch new collections for what matters: a keyword, a deleted or edited post, a channel change or a newly linked channel.
          {#snippet action()}<button class="primary" onclick={() => { setQuery({ tab: 'rules' }); newRule(); }}>Create a rule</button>{/snippet}
        </Empty>
      {:else}
        <Empty title={onlyUnread || ruleFilter ? 'No alerts match these filters' : 'No alerts yet'}>
          {onlyUnread || ruleFilter ? 'Try turning off the filters.' : 'When a rule matches something newly collected it will appear here. “Check now” re-runs the rules over the last day.'}
        </Empty>
      {/if}
    {:else}
      <div class="panel">
        {#each inbox.alerts as a (a.id)}<EvAlertItem alert={a} onread={toggleRead} />{/each}
      </div>
      {#if inbox.total > PAGE}
        <div class="row" style="margin-top:10px">
          <button class="sm" disabled={offset === 0} onclick={() => (offset = Math.max(0, offset - PAGE))}>← Newer</button>
          <span class="muted small">{offset + 1}–{Math.min(offset + PAGE, inbox.total)} of {num(inbox.total)}</span>
          <button class="sm" disabled={offset + PAGE >= inbox.total} onclick={() => (offset += PAGE)}>Older →</button>
        </div>
      {/if}
    {/if}
  {:else}
    {#if !rulesLoaded}<Loading />
    {:else if !rules.length}
      <Empty title="No rules{cid != null ? ' for this case' : ''} yet">
        Pick what to be told about. Every rule can be limited to a case and to specific channels.
        {#snippet action()}<button class="primary" onclick={newRule}>Create a rule</button>{/snippet}
      </Empty>
    {:else}
      <div class="table-wrap"><table class="t">
        <thead><tr><th scope="col">Rule</th><th scope="col">Watches</th><th scope="col">Limited to</th><th scope="col" class="right">Alerts</th><th scope="col">Notify</th><th scope="col">On</th><th scope="col"></th></tr></thead>
        <tbody>
          {#each rules as r (r.id)}
            <tr class:faint={r.enabled === 0}>
              <td><b>{r.name}</b><div class="tiny muted">{kindOf(r.kind)?.label || r.kind}</div></td>
              <td class="small">{ruleWhat(r)}
                {#if r.kind === 'keyword'}
                  {@const fz = frozenOf(r)}
                  {#if fz}
                    <div class="frozen">
                      {#if fz.groups.length}<ExpansionChips expansion={fz} readonly showExact={false} />{:else}<div class="tiny faint">No other spellings found for these words.</div>{/if}
                      <div class="tiny faint">{expNote}.</div>
                      <button class="sm" onclick={() => reexpand(r)}>Re-expand with current dictionary</button>
                    </div>
                  {:else}<div class="tiny faint">Literal match (created before multilingual search)</div>{/if}
                {/if}
              </td>
              <td class="small">{limits(r)}</td>
              <td class="right num">{num(r.alerts)}{#if r.unread} <span class="badge">{r.unread}</span>{/if}</td>
              <td>{r.notify ? 'macOS' : '–'}</td>
              <td><input type="checkbox" checked={r.enabled !== 0} onchange={() => toggleRule(r)} aria-label="Rule enabled" /></td>
              <td class="right nowrap">
                {#if r.alerts}<button class="sm ghost" onclick={() => { ruleFilter = String(r.id); setQuery({ tab: null }); }}>View alerts</button>{/if}
                <button class="sm" onclick={() => editRule(r)}>Edit</button>
                <button class="sm danger" onclick={() => deleteRule(r)}>Delete</button>
              </td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {/if}
    <div class="panel panel-body" style="margin-top:14px">
      <div class="section-title" style="margin-top:0">What each kind of rule does</div>
      <dl class="kinds">{#each KINDS as k}<dt>{k.label}</dt><dd class="muted small">{k.help}</dd>{/each}</dl>
    </div>
  {/if}
</div>

<Modal open={ed.open} onclose={() => (ed.open = false)} title={ed.r.id ? 'Edit rule' : 'New alert rule'} width="640px">
  <div class="col" style="gap:12px">
    <label class="field">Kind
      <select bind:value={ed.r.kind} onchange={() => (ed.preview = null)}>{#each KINDS as k}<option value={k.k}>{k.label}</option>{/each}</select>
      <span class="help">{kindOf(ed.r.kind)?.help}</span>
    </label>
    {#if ed.r.kind === 'keyword'}
      <div class="form-grid">
        <label class="field">Search words<input type="text" dir="auto" bind:value={ed.r.query} placeholder='e.g. drone OR "El Fasher"' /><span class="help">Same syntax as Search.</span></label>
        <label class="field">Look in
          <select bind:value={ed.r.scope}><option value="posts">Posts</option><option value="comments">Comments</option><option value="both">Posts and comments</option></select></label>
      </div>
    {/if}
    {#if ed.r.kind === 'keyword'}
      {#if keepFrozen}
        <div class="expbox">
          <ExpansionChips expansion={ed.frozen} readonly hideToggle />
          <div class="tiny faint">{expNote}. Change the words to expand them again, or use “Re-expand with current dictionary” in the rules list.</div>
        </div>
      {:else if ed.r.id && !ed.frozen && ed.r.query.trim() === ed.origQuery}
        <div class="expbox tiny faint">Literal match (created before multilingual search). Change the words and save to give this rule an expansion.</div>
      {:else}
        <div class="expbox">
          <DictBanner dictionary={ed.lookup?.dictionary} />
          <ExpansionChips expansion={ed.lookup} value={ed.exp} loading={ed.looking} onchange={(v) => { ed.exp = v; ed.preview = null; }} />
          <div class="tiny faint">Expanded once when you save. {expNote}.</div>
        </div>
      {/if}
    {/if}
    {#if ed.r.kind === 'new_candidate'}
      <label class="field">Minimum links<input type="number" min="1" bind:value={ed.r.min_links} style="max-width:120px" />
        <span class="help">How many case channels must link to the candidate. 1 fires on every single link and is noisy; 2 or more is recommended.</span></label>
    {/if}
    <div class="form-grid">
      <label class="field">Limit to case
        <select bind:value={ed.r.case_id}><option value="">Any case (whole archive)</option>{#each app.cases as c}<option value={c.id}>{c.name}</option>{/each}</select>
        {#if ed.r.kind === 'new_candidate' && !ed.r.case_id}<span class="help" style="color:var(--bad)">This kind needs a case.</span>{/if}</label>
      <label class="field">Limit to channels <span class="faint">(optional)</span>
        <input type="text" bind:value={ed.r.channels} placeholder="handles, comma separated" /></label>
    </div>
    <label class="field">Name <span class="faint">(optional, generated if empty)</span><input type="text" bind:value={ed.r.name} /></label>
    <div class="row">
      <label class="check"><input type="checkbox" bind:checked={ed.r.notify} /> Show a macOS notification (generated locally)</label>
      <label class="check"><input type="checkbox" bind:checked={ed.r.enabled} /> Enabled</label>
    </div>

    <div class="panel panel-body prev">
      <div class="row">
        <b class="small">Preview</b>
        <span class="muted small">test this rule over the last</span>
        <select bind:value={ed.days} aria-label="Preview window"><option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option></select>
        <button class="sm" onclick={preview} disabled={ed.previewing}>{ed.previewing ? 'Running…' : 'Preview matches'}</button>
        <span class="faint tiny">Saves nothing.</span>
      </div>
      {#if ed.preview}
        <div style="margin-top:8px">
          <b>{plural(ed.preview.count, 'match', 'matches')}</b>{#if ed.preview.count > ed.preview.matches.length} <span class="faint small">(first {ed.preview.matches.length} shown)</span>{/if}
          {#if ['post_edited', 'channel_change', 'media_reused'].includes(ed.r.kind)}<div class="faint tiny">Edit, channel-change and reused-media rules are previewed against all history, not just the chosen days.</div>{/if}
          <div class="pv">{#each ed.preview.matches.slice(0, 20) as m}<EvAlertItem alert={m} compact />{/each}</div>
        </div>
      {/if}
    </div>
    {#if ed.error}<div class="callout bad" role="alert">{ed.error}</div>{/if}
  </div>
  {#snippet footer()}
    <button onclick={() => (ed.open = false)}>Cancel</button>
    <button class="primary" onclick={saveRule} disabled={ed.busy || (ed.r.kind === 'keyword' && !ed.r.query.trim()) || (ed.r.kind === 'new_candidate' && !ed.r.case_id)}>{ed.busy ? 'Saving…' : 'Save rule'}</button>
  {/snippet}
</Modal>

<style>
  .kinds { display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0; }
  .kinds dt { font-weight: 600; font-size: 13px; }
  .kinds dd { margin: 0; }
  .frozen { margin-top: 4px; display: flex; flex-direction: column; gap: 4px; align-items: flex-start; }
  .expbox { border: 1px solid var(--line); border-radius: var(--radius); padding: 6px 10px; background: var(--panel-2); }
  .prev { background: var(--panel-2); box-shadow: none; }
  .pv { max-height: 260px; overflow: auto; margin-top: 6px; border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--panel); }
</style>
