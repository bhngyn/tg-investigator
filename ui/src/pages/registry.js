// Page registry + rail navigation. Pages read everything they need from `route` (router)
// and `app` (store); they take no props.
import Home from './Home.svelte';
import Settings from './Settings.svelte';
import Overview from './Overview.svelte';
import Inbox from './Inbox.svelte';
import Search from './Search.svelte';
import Comments from './Comments.svelte';
import Channels from './Channels.svelte';
import Channel from './Channel.svelte';
import AddChannels from './AddChannels.svelte';
import Discovery from './Discovery.svelte';
import Collect from './Collect.svelte';
import Network from './Network.svelte';
import Analysis from './Analysis.svelte';
import Alerts from './Alerts.svelte';
import Incidents from './Incidents.svelte';
import Incident from './Incident.svelte';
import Exports from './Exports.svelte';
import SearchLog from './SearchLog.svelte';
import NotFound from './NotFound.svelte';

export const PAGES = {
  home: Home, cases: Home, settings: Settings,
  overview: Overview, inbox: Inbox, search: Search, comments: Comments,
  channels: Channels, channel: Channel, add: AddChannels, discovery: Discovery, collect: Collect,
  network: Network, analysis: Analysis,
  alerts: Alerts, searchlog: SearchLog, incidents: Incidents, incident: Incident, exports: Exports,
  notfound: NotFound,
};

// Organised by what the investigator is trying to do, not by data type.
export const NAV = [
  { label: 'Case', items: [
    { page: 'overview', label: 'Overview', icon: '◉' },
  ] },
  { label: 'Triage', items: [
    { page: 'inbox', label: 'Review queue', icon: '☰' },
    { page: 'search', label: 'Search posts', icon: '⌕' },
    { page: 'comments', label: 'Comments & people', icon: '💬' },
    { page: 'searchlog', label: 'Search history', icon: '⏱' },
  ] },
  { label: 'Sources', items: [
    { page: 'channels', label: 'Channels', icon: '▤', also: ['channel'] },
    { page: 'add', label: 'Add channels', icon: '＋' },
    { page: 'discovery', label: 'Discover new', icon: '✦' },
    { page: 'collect', label: 'Collection', icon: '⟳' },
  ] },
  { label: 'Analyse', items: [
    { page: 'network', label: 'Network', icon: '⋈' },
    { page: 'analysis', label: 'Patterns & changes', icon: '∿' },
  ] },
  { label: 'Evidence', items: [
    { page: 'alerts', label: 'Alerts', icon: '!' },
    { page: 'incidents', label: 'Incidents', icon: '⚑', also: ['incident'] },
    { page: 'exports', label: 'Evidence packages', icon: '⬇' },
  ] },
];
