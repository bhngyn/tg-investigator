<script module>
  // Shared helpers for the Sources pages (Channels, Channel, AddChannels, Collect).
  import { ago, pj, num } from '../lib/fmt.js';

  export const ACTIVE = ['queued', 'running', 'importing'];
  export const MEDIA_LABEL = { none: 'no media', photos: 'photos', all: 'photos + videos' };
  export const KIND_LABEL = { crawl: 'Posts crawl', comments: 'Comments', discover: 'Similar / discovery', media: 'Media download', mediascan: 'Media scan' };
  /** Which lane a job kind runs in. */
  export const LANE = { crawl: 'web', comments: 'api', discover: 'api', media: 'api', mediascan: 'local' };
  export const LANE_LABEL = {
    web: 'Public web (no login)',
    api: 'Your Telegram account (logged in)',
    local: 'This computer only (no network)',
  };
  export const STATUS_CLASS = { queued: '', running: 'info', importing: 'info', done: 'ok', stopped: 'warn', failed: 'bad', cancelled: '', interrupted: 'warn' };

  export const INTERVALS = [
    [15, 'every 15 min'], [30, 'every 30 min'], [60, 'hourly'], [180, 'every 3 h'], [360, 'every 6 h'],
    [720, 'every 12 h'], [1440, 'daily'], [4320, 'every 3 days'], [10080, 'weekly'],
  ];
  export function intervalLabel(min) {
    if (min === null || min === undefined) return '–';
    const hit = INTERVALS.find(([m]) => m === min);
    if (hit) return hit[1];
    if (min < 60) return `every ${min} min`;
    if (min % 1440 === 0) return `every ${min / 1440} d`;
    if (min % 60 === 0) return `every ${min / 60} h`;
    return `every ${min} min`;
  }

  /** Channels a job touches (params.channel, params.seeds, params.channels). */
  export function jobChannels(job) {
    const p = pj(job.params, {}) || {};
    return [p.channel, ...(p.seeds || []), ...(p.channels || [])].filter(Boolean);
  }

  /** One-line description of what a job was asked to do. */
  export function jobDesc(job) {
    const p = pj(job.params, {}) || {};
    const who = jobChannels(job);
    const names = who.length > 3 ? `${who.slice(0, 3).map((x) => '@' + x).join(', ')} +${who.length - 3}` : who.map((x) => '@' + x).join(', ');
    if (job.kind === 'crawl') {
      const bits = [`${p.pages ?? '?'} pages`];
      if (p.recheck_days) bits.push(`re-check ${p.recheck_days} d`);
      if (p.network_only) bits.push('network only');
      else if (p.media) bits.push(MEDIA_LABEL[p.media] || p.media);
      if ((p.depth || 1) > 1) bits.push(`follows links (depth ${p.depth})`);
      return `${names} · ${bits.join(' · ')}`;
    }
    if (job.kind === 'comments') return `${names} · ${p.limit ? `up to ${num(p.limit)} messages` : 'new messages'} · ${MEDIA_LABEL[p.media] || 'no media'}`;
    if (job.kind === 'discover') return `${p.method || 'discover'} ${names || (p.queries || []).join(', ')}`;
    if (job.kind === 'media') return `${p.channel ? '@' + p.channel : 'selected posts'} · ${p.what || 'videos'}`;
    return names;
  }

  /** Plain-language result of a finished job. */
  export function jobSummary(job) {
    const s = pj(job.summary, null);
    if (!s) return '';
    if (s.error) return s.error;
    if (s.note) return s.note;
    if (job.kind === 'discover') return `${num(s.found)} found · ${num(s.discoveries_new)} new`;
    if (job.kind === 'mediascan') return `${num(s.scanned)} files scanned`;
    if (s.network_only) return `${num(s.edges_new)} links · ${num(s.channels_new)} new channels`;
    const bits = [];
    if ('posts_new' in s) bits.push(`${num(s.posts_new)} new posts`);
    if (s.posts_changed) bits.push(`${num(s.posts_changed)} edited`);
    if (s.posts_deleted) bits.push(`${num(s.posts_deleted)} found deleted`);
    if (s.comments_new !== undefined) bits.push(`${num(s.comments_new)} new comments`);
    if (s.media_new) bits.push(`${num(s.media_new)} media`);
    if (s.channels_new) bits.push(`${num(s.channels_new)} new channels`);
    return bits.join(' · ');
  }

  export function fmtDur(sec) {
    if (sec === null || sec === undefined || Number.isNaN(sec)) return '–';
    sec = Math.max(0, Math.round(sec));
    if (sec < 60) return `${sec}s`;
    if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`;
    return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`;
  }

  /** Time until an ISO time ("in 3 h"), or "due now". */
  export function until(iso) {
    if (!iso) return '–';
    const s = (Date.parse(iso) - Date.now()) / 1000;
    if (s <= 60) return 'due now';
    if (s < 3600) return `in ${Math.round(s / 60)} min`;
    if (s < 86400) return `in ${Math.round(s / 3600)} h`;
    return `in ${Math.round(s / 86400)} d`;
  }

  /**
   * How fresh is a channel's collected data?
   * With an enabled watch, freshness is judged against its interval; without one, nothing
   * updates by itself, so old data is simply "stale".
   * @returns {{ label: string, cls: 'ok'|'warn'|'bad'|'', stale: boolean, never: boolean, age: number|null }}
   */
  export function freshness(iso, watch) {
    if (!iso) return { label: 'never crawled', cls: 'bad', stale: true, never: true, age: null };
    const ageMin = (Date.now() - Date.parse(iso)) / 60000;
    if (watch && watch.enabled) {
      const iv = Math.max(15, watch.interval_min || 360);
      if (ageMin <= iv * 1.5 + 20) return { label: ago(iso), cls: 'ok', stale: false, never: false, age: ageMin };
      if (ageMin <= iv * 3 + 20) return { label: ago(iso), cls: 'warn', stale: true, never: false, age: ageMin };
      return { label: ago(iso), cls: 'bad', stale: true, never: false, age: ageMin };
    }
    if (ageMin <= 2 * 1440) return { label: ago(iso), cls: 'ok', stale: false, never: false, age: ageMin };
    if (ageMin <= 14 * 1440) return { label: ago(iso), cls: '', stale: false, never: false, age: ageMin };
    return { label: ago(iso), cls: 'warn', stale: true, never: false, age: ageMin };
  }
</script>

<script>
  // Small "last crawled X ago" pill, coloured by freshness (see freshness()).
  import { dt } from '../lib/fmt.js';
  /** @type {{ iso: string|null, watch?: any, label?: string }} */
  let { iso, watch = null, label = '' } = $props();
  const f = $derived(freshness(iso, watch));
  const hint = $derived(
    iso
      ? `Last crawled ${dt(iso)}. ${watch && watch.enabled ? `Watch ${intervalLabel(watch.interval_min)}.` : 'No watch: nothing refreshes this channel unless you collect it.'}`
      : 'This channel has never been crawled. Collect it to fetch posts.'
  );
</script>

<span class="pill {f.cls}" title={hint}>{label}{f.label}{#if f.stale && !f.never} · stale{/if}</span>
