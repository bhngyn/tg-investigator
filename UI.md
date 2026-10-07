# Telegram Investigator: UI contributor guide

The UI is written in **Svelte 5 + Vite**. Its sources are in `ui/`, and it builds to `investigator/web/`. The data contract is `API.md`.

## Build and run

```bash
cd ui && npm install      # first time only; all deps are bundled, nothing loads remotely
npm run build             # writes investigator/web/
npm run watch             # rebuild on save (reload the browser yourself)
.venv/bin/python -m investigator    # then open http://127.0.0.1:8765/
```

The server hook in `investigator/server.py` is small:
- `/` serves `web/index.html` if it exists, and the classic UI otherwise.
- `/classic` always serves the old UI.
- `/app/*` serves the built assets.

The mount is decided at startup, so restart the server after the *first* build. There is no Vite dev server, because the Host guard and token make proxying fiddly. The token is read from `<meta name="x-token">`, which the server fills in.

## Product model

- **Cases.** A shared pool, tagged to cases. The **case switcher** in the top bar scopes every page, and **All cases** (`#/`) is the home page.
  - The **Whole archive** scope (`#/all/...`) is the pool with global/legacy triage.
- **Triage and analysis are equal first-class areas.** The rail is organised by task (Triage / Sources / Analyse / Evidence), not by data type.
- **New channels.** Paste handles, resolve them with a live preview, choose steps per channel, then collect.
- **The network graph was rebuilt around three questions:** amplification, shared people, and discovery.

## URL scheme

`#/` All cases · `#/settings` · `#/<scope>/<page>/<args>?query`. The scope is a case id or `all`.
Pages: `overview, inbox, search, searchlog, comments, channels, channel/<name>, add, discovery, collect, network, analysis/<tab>, alerts, incidents, incident/<id>, exports`.
Analysis tabs: `changes, coordination, spread, media, places` (claimed coordinates), `mentions` (places mentioned), `entities` (identifiers), `people, community`.

## Code layout and conventions (read before adding a page)

- `src/lib/api.js`: `api(path, {params, body, method})`, `get`, `post`, `qs`, `mediaUrl`, `csvUrl`. Errors throw `ApiError` whose `.message` is safe to show.
- `src/lib/router.svelte.js`: `route` ($state: `scope, page, args, query`), `caseId()`, `href(page, ...args, {query})` in the current scope, `hrefIn(scope, page, ...)`, `go(url)`, `setQuery(patch)` (replaces the query without adding history; use it for filters so they survive reload).
- `src/lib/store.svelte.js`: `app` holds `cases, settings, tags, jobs, unreadAlerts, jobsTick, toasts`.
  - `scoped(params)` adds `case=` when inside a case.
  - `currentCase()`, `sinceForCase()` gives the previous-visit time for "new since last visit".
  - `toast(msg, kind)`; `attempt(fn, okMsg)` runs an action and toasts its error.
  - `loadCases()` refreshes the case list and counts.
  - `app.jobsTick` bumps when a job finishes. `$effect` on it to refresh.
- `src/lib/triage.js`: `setReview/setTag/addNote/listNotes/reviewOf/tagsOf/canTriage`. These use case triage inside a case and global triage in `all`. Comments have no global triage.
- `src/lib/fmt.js`: `num, compact, bytes, dt, day, ago, plural, pj (parse JSON column), isRtl, tme`.
- Shared components (`src/components/`): `PostCard, Media (blur/reveal/exposure), TriageBar, TagPicker, ChannelLink, Drawer, Modal, Empty, Loading, Toasts`.
- Styling:
  - Use the tokens and classes in `src/app.css`: `.page .page-head .panel .panel-head .panel-body .stats .stat .row .col .grid.two`, `table.t` inside `.table-wrap`, `.tabs`, `.pill.(ok|warn|bad|info|accent)`, `.badge`, `.chip`, `.callout(.warn|.bad|.ok)`, `.empty`, `.muted .faint .small .tiny`, `button.primary|danger|ghost|sm`, `.seg`, `label.field`, `.form-grid`, `.help`, `.section-title`.
  - Component `<style>` is scoped. Use `var(--token)` colours only, so dark mode works.
- **Hard rules.**
  - **Never load remote resources.** No CDN, fonts or tiles. External links are `<a target="_blank" rel="noreferrer noopener">`. URLs that come from Telegram data (links, `fwd_url`, `reply_to_url`, ...) go through `safeUrl()` in `lib/fmt.js`: only http(s)/tg are clickable, anything else is plain text, non-Telegram hosts get an "external" marker, and `main.js` swallows clicks on any other scheme as defence in depth.
  - **No inline `<script>` in index.html.** The CSP is `script-src 'self'`.
  - **Media is blurred by default.** Always render media through `Media.svelte`, so reveals are counted.
  - **Label legal-evidence caveats honestly**, e.g. coordinates found in text are "claims to check", and heuristics are "heuristic".
  - **Never fire logged-in-lane jobs** (`comments`, `media`, `discover`) automatically. Only fire them on an explicit user click with a confirmation that names what will run.
  - **Page-local keyboard shortcuts** use `keyOf(e)` from `lib/keys.js` (Latin letter as printed, else the physical key via `e.code`, so they work on Arabic/Cyrillic layouts), ignore events from inputs, and never take Space from a focused button (`isInteractive`). The global keys are `/` (focuses the element with `data-page-search`) and `g`+letter navigation (App.svelte).

## Page map

| Area | Pages | Helpers |
|---|---|---|
| Triage | Inbox (review queue, keyboard), Search, SearchLog (Search history + Saved searches tabs), Comments (List/Threads toggle via `view=threads`, + sections, people) | PostDetail, CommentDetail, AccountDrawer, TriageDetailDrawer (one drawer with a Back stack; item types post/comment/account/thread), TriageCommentCard (`onthread` adds a Thread button), CommentThread (post + nested replies, `focus`, `match` dims non-matches), TriagePhone |
| Sources | Channels, Channel (tabs: posts, comments, links, collection, changes, notes), AddChannels (paste → preview → steps → confirm → live status), Collect (lanes, progress, log, history, watches, schedules) | SrcStepChooser, SrcConfirm, SrcJobMonitor, SrcScheduleEditor, SrcFresh |
| Analyse | Network (canvas + d3-force; Amplification / Shared people / Discovery; table view), Discovery, Analysis (changes, coordination, spread, media, places, people, audience) | Net*, lib/net-*.js, Ana* |
| Evidence | Home (all cases), Overview, Alerts (+ rules with preview), Incidents, Incident, Exports, Settings | AddToIncident, Ev* |

Checks: `node scripts/check.mjs` compiles every component (or only the files you name) and fails on any error or warning. `node scripts/coverage.mjs` lists every backend route with the UI file that calls it, and fails if any route has none.

**Keyboard.**
- **Review queue:** `r` / `v` / `i` set a verdict and advance, and `u` undoes it. `j` / `k` move, `n` opens a note, `o` opens details, space reveals media, and `t` followed by a tag key toggles that tag.
- **Search:** `j` / `k` move, `x` selects, `o` opens details, and `r` / `v` / `i` set a verdict.
- **Everywhere:** `/` jumps to the page's search box, and `g` followed by `o`, `i`, `s`, `c`, `h`, `n`, `a`, `l` or `j` jumps to a page.
- **Esc** closes only the top overlay (`lib/layers.js`).

## Multilingual search UI

Contract: `API.md` (`expansion` on search, comments search, `/api/dictionary/*`, alert rules).
- **`lib/expansion.js`**: `expansionFromQuery(Q)` / `expansionToQuery(state)` / `expansionParams(state)` / `normaliseExpansion(x)`. State is `{expand, drop[], skip[]}`. In the URL it is `expand=0` (only when off; default is on), `drop=a,b`, `skip=x,y`, so a search survives reload and is shareable. Those three keys are in Search's and Comments' filter lists, so the API call and the CSV export link both carry them.
- **`components/ExpansionChips.svelte`**: the "Also searching" row (group span → label/detail, alt chips with a language badge, dashed border plus "historic" marker, `dir="auto"` for Arabic). Alt × goes to `drop`, group × to `skip`, dropped alts stay visible struck through and can be restored. Translit groups render as "Cyrillic spelling ≈ key". A `truncated` warning and an "Exact query" disclosure (FTS + `dict_version`, copy button) are built in. `readonly` renders frozen rules and the dictionary tester without controls. The previous response's chips stay on screen (dimmed) while a new search loads, so the layout doesn't jump.
- **Search / Comments**: chips sit under the query box; the toggle is "Match other languages/spellings". Highlighting uses the response's `terms`; PostCard also stems long Cyrillic terms like the search does. Comment cards highlight like post cards.
- **Alerts**: the keyword editor calls `GET /api/dictionary/lookup` (debounced 300 ms) and sends `expand`, `drop`, `skip` on save and preview. An existing rule whose words are unchanged shows its frozen expansion read-only and sends no expansion fields. The rules list shows frozen chips, the note "Frozen when saved — dictionary edits won't change this rule", a confirm-first "Re-expand with current dictionary" (`{id, reexpand: true}`), and "Literal match (created before multilingual search)" for legacy rules.
- **Settings → Dictionary tab** (`components/DictSettings.svelte`): stats and sources with licence/attribution, a query tester, an entry browser with a drawer (add name, hide name, hide entry, history with undo) and "Add term or place". All edits go through `POST /api/dictionary/edit` and are toasted. The caveat about incomplete Sudanese village coverage in Arabic is shown at the top.
- Expansion is a search aid: matching a place name is not an identification of the place. The UI says so on the Dictionary tab.

## Search, identifiers and places

Contract: `API.md`. All list parsing is defensive: list endpoints are read with `lib/searchparams.js#listOf`, so a bare array or `{results|items|rows|<name>: [...]}` both work.

- **Shared libs**: `lib/highlight.js` (`highlightParts`, `excerptParts`, `clock`; used by PostCard, TriageCommentCard and media hits), `lib/searchparams.js` (`POST_FILTER_KEYS`, `IN_SOURCES`, entity/place labels and caveats, `paramsToQuery` = logged params -> Search URL, `isCommentsRun`).
- **Search history** (`pages/SearchLog.svelte`, rail item under Triage, `#/<scope>/searchlog`, tabs History / Saved searches). Rows come from `GET /api/search_log` (case-scoped by the switcher; whole archive in `all`). The drawer shows the frozen `fts`, params, dictionary/key version and result hash; **Open this search** restores the params into the Search (or Comments) URL, **Re-run and compare** calls `POST /api/search_log/{id}/rerun` and lists added/removed ids as buttons that open the post/comment drawer. The Search page carries the note "Searches are logged for your methodology record". Ancillary calls the page makes on its own (the channel-facet call) send `log=0`; plain browsing without a query is not logged by the server.
- **Saved searches** (`SaveSearch.svelte` modal on Search; `SavedSearches.svelte` on the Overview and in the Search history tab): Run, Run all (per-search totals, "new since last run", changed), Open, Make alert (confirm), Edit, Delete (confirm). `freeze` is explained in the dialog.
- **Terms in these results** (`SearchTerms.svelte`, Search and Comments): collapsed `<details>`, requests `/api/search/terms` only when opened and again when the search changes. Click a term = add to query (AND), "or" button = add with OR, "Add to dictionary" opens the shared `AddDictEntry.svelte` (the same dialog Settings > Dictionary uses), a known term links to `#/settings?tab=dictionary&entry=<id>` (DictSettings opens that entry's drawer).
- **Identifiers and places**:
  - Search sidebar facets `Identifiers in results` and `Places mentioned` (`SearchFacets.svelte`) filter with `entity=kind:value` (comma-joined for several, ANDed) and `place=<entry id>` (comma-joined, ORed); the **Near a place** filter (`NearFilter.svelte`) picks a dictionary place plus a radius and writes `near=lat,lon,km` (`near_label` is a UI-only URL key for the chip text).
  - Analysis > **Identifiers** (`AnaEntities.svelte`, list grouped by kind, kind/search filters, detail drawer with excerpts; card excerpts stay hidden until an audited, confirmed reveal) and Analysis > **Places mentioned** (`AnaMentions.svelte`, offline SVG plot with graticule, admin1/country labels computed from the data, fit/zoom/pan buttons, hover readout, click = search that place). "Show these on a map" on the Search facet carries the current search filters.
  - The backfill buttons (`POST /api/entities/backfill`, `POST /api/places/backfill`) are explicit and confirm first. Labels: "Patterns found in text — claims to check, not verified identities" and "Places mentioned in text (not verified locations); ambiguous names are marked".
- **Matched sources**: Search filter "Search in" (text, earlier versions, OCR, speech -> `in=`; omitted when all are on). PostCard shows `matched_in` badges ("earlier version", "OCR 0:42", "speech 3:10") and up to three highlighted `media_hits`; clicking a hit opens the drawer at that file and time (`item.media = {sha256, t, kind}` -> `PostDetail focusMedia` -> `MediaText focus`). The syntax help documents NEAR, stemming, obfuscation folding and `in`. `expansion.near_literal` shows a warning.
- **Media text** (`MediaText.svelte` in PostDetail): per file OCR lines (frame time for video) and speech segments (time range, detected language), rtl-aware. Collapsed behind a content note "Show transcript/OCR text" because the text can be as graphic as the media; media itself still goes through Media.svelte.
- **Settings > OCR & transcription** (`MediaTextSettings.svelte`): status counts, OCR languages (auto or ar-SA/ars-SA/ru-RU/uk-UA/en-US), video frame interval, per-channel Whisper hints, and a **Run** that only starts after a confirm dialog naming the scope and that local models run on this Mac. The screen can turn `auto_after_media_download` off but never on.
- **Comments**: comment cards and threads now take `terms` and highlight (same routine as posts).
- **Comment media text**: the Comments page filter bar has "Search in" boxes (comment text / OCR / speech / names; `COMMENT_IN_SOURCES` in `lib/searchparams.js`; OCR and speech are disabled with a hint while `sources.available` lacks them). `TriageCommentCard` shows `matched_in` badges and up to three `media_hits` like PostCard; clicking a hit opens `CommentDetail` at that file (`item.media` -> `focusMedia` -> `MediaText commentId focus`). `MediaText` takes `postId` or `commentId`. Settings > OCR & transcription has a "Where" choice (`scope.only`) and shows how many of the files are in comment sections. List cards get hits; the Threads view does not (its comments come from the thread endpoint).
- **Dictionary tab**: shows `stats.coverage` (Sudan Arabic name coverage, parsed defensively) and a red banner when `stats.key_version_ok === false` ("Dictionary needs rebuilding: run python -m investigator.gazetteer_build").

## Testing

Run the app against a throw-away copy of a workspace, never a real evidence workspace: `.venv/bin/python -m investigator -w <copy> --port <free port> --no-browser`. Do not click anything that starts collection jobs while testing.

## Behaviour worth knowing

- **Errors and state.** `api.js` turns a network failure into "server not reachable", a 403 token error into the `app.stale` banner ("The server restarted. Reload the page"), and 422 arrays into readable text. `ApiError.data` keeps the response body (used for the 409 `dropped_filters`). Pollers in `store.svelte.js` are one self-scheduling loop: 3 s, backing off to 30 s on failure, paused while the tab is hidden; `app.offline` shows a banner and `app.jobs` is only replaced when it changed. A bad `%` sequence in the hash does not throw; an unknown or archived case id shows "Case not found / archived" with a way back.
- **Counts.** Case tiles use the explicit `post_*` / `comment_*` keys. Home and Overview show first-run guidance for a workspace with nothing collected.
- **Media.** `Media` takes `sensitive` (undefined = unknown, treated as sensitive in "blur only flagged" mode). The header meter reads "Reveal clicks today".
- **Filters as text.** `describeParams()` (`lib/searchparams.js`) turns search params into readable chips for history rows, saved searches, the save dialog and the make-alert confirmations. Never show raw parameter keys or API detail text in a dialog.
- **Multilingual search.** Expansion chips list `omitted` spellings as "not searched"; freezing a truncated expansion asks first. `DictBanner` shows when the dictionary is missing or out of date, and the "Match other languages" toggle and dictionary editing are disabled while it is missing. `in=names` is an opt-in "Channel & forward names" source.
- **Licences and caveats.** `DataAttribution` (OpenStreetMap ODbL share-alike, GeoNames CC BY) is shown on the mentions map, the place facet and Settings > Dictionary. Settings > Dictionary carries a content note (the lexicon includes slurs on purpose). The Whisper caveat ("can invent text over music or silence") is on speech badges, in MediaText and in the OCR settings.
- **Triage.** Dismissing a channel from the status select asks first (it is pool-wide). Review verdicts are one at a time (key auto-repeat is ignored); global bulk review reports how many were applied on failure. Case review lookups by id ask in chunks of 500, and an id the server did not return is "unknown", never "unreviewed".
- **Client CSVs.** Every client-side CSV (`components/AnaUtil.js`, `lib/net-data.js`) neutralises spreadsheet formulas in text cells (a leading `= + - @` tab or CR gets a quote prefix; numbers stay numeric) and starts with a UTF-8 BOM.

## Accessibility conventions (follow these on every new page)

- **Colour tokens (`app.css`).** Text on a solid accent/ok/warn/bad fill uses `var(--on-fill)` (white in light, near-black in dark), never `#fff`. `--faint` is a real text colour (>=4.5:1 on every surface), and input/select/textarea/`.seg` borders use `--control-border` (>=3:1). Highlights use `<mark>`, styled globally with `--mark-bg/--mark-fg` (do not add page-local mark styles, no horizontal padding: it breaks Arabic joining). Check contrast with the WCAG formula on the token pairs when you add a colour.
- **Focus.** One global `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }` (Highlight in forced-colors). Never `outline:none` without a replacement, and never use `box-shadow` as the only indicator. Inside `overflow:hidden` parents use `outline-offset:-2px`. "Selected" cards use an outline too.
- **Dialogs.** Always use `Modal`/`Drawer` (they use `lib/dialog.js`): the layer is moved to `<body>`, `.shell` becomes `inert`, focus goes to `[data-autofocus]` or the panel, Tab is trapped, focus returns to the opener, `aria-labelledby` points at the title, Esc closes only the top layer. Mark note fields that start empty with `data-draft`: closing (Esc, backdrop, X) with text in them asks first. Use `confirm()` for short yes/no questions.
- **Shortcuts.** Single-key shortcuts must check `shortcutsOn()` (`lib/keys.js`; Settings > General "Keyboard shortcuts", stored in localStorage `ti.shortcuts`), ignore inputs, and never take Space or arrows from a focused control (`isInteractive`).
- **Route changes.** `App.svelte` sets `document.title` ("Page · Case · Telegram Investigator"), focuses the page `<h1>` (give every page exactly one `<h1>`), and announces via a live region. `announce(msg)` in the store does the same for other events. Skip link goes to `#main`.
- **Live regions.** Result counts/loading/empty states: `<p class="sr-only" role="status" aria-live="polite">` outside the `{#if}` chain (see Search, Comments). Error callouts that come from `{error}` get `role="alert"`; toasts: errors `role="alert"` (10 s, dismissable), others `role="status"`.
- **Text direction.** User text: `dir="auto"` (the `.text-body` class adds `unicode-bidi: plaintext`), never `isRtl(x) ? 'rtl' : ...` (`isRtl` now means "first strong letter is Arabic/Hebrew" and is only for picking a style). Handles/usernames/forward names: `<bdi dir="ltr">@{name}</bdi>`. `[dir=auto]`, `bdi` isolate. `.text-body` line-height is 1.65 (Arabic diacritics); line-clamped blocks have `padding-block:.1em`.
- **Tables.** `<th scope="col">`, `aria-sort` on sortable headers, and a real `<button class="rowbtn">` in the primary cell of any clickable row (`tr.click` stays as a mouse convenience only). Never rely on `tr onclick` alone.
- **Controls.** Every input has a visible label or `aria-label` (placeholders are not names). Segmented controls / tab-like buttons are `role="group"` + `aria-pressed`; nav-like tabs are links with `aria-current="page"`. No `role=tablist/listbox` without the full keyboard model.
- **Media.** Always through `Media.svelte`, passing `channel` and `date`: the hidden veil is named "Hidden photo from @x, posted ... Press to reveal", the hidden `<img>` is `aria-hidden` with empty alt (blur must also hide content from screen readers), reveal moves focus to the tile and announces it.
- **Charts.** SVGs get `role="img"` and an `aria-label` that states the peak/total, plus a table alternative (Search timeline: "Show as table" with filter buttons). Point types differ by shape as well as colour (circle post, square comment, diamond incident) with a legend.
- **Motion.** `prefers-reduced-motion` disables CSS transitions/animations globally; JS uses `lib/motion.js` (`scrollBehavior()`, `reducedMotion()`); the network graph is laid out synchronously and not animated.
- **Reflow.** No fixed pixel widths on inputs: `width:min(100%,260px)`. The exposure meter ("Reveal clicks today", compact "Reveals N" under 980 px) is visible at every width; the top bar wraps at <=760 px and the mobile rail has "All cases" / "Whole archive".
- **Licences.** `ui/public/THIRD-PARTY-LICENSES.txt` (Svelte, clsx, esm-env: MIT; d3-force, d3-dispatch, d3-quadtree, d3-timer: ISC) is copied to `investigator/web/` by the build and linked from Settings. Regenerate it if a dependency is added.
- **Unambiguous places.** `unambiguous=1` (URL, CSV links, saved searches) is exposed as "Only unambiguous place names" on the Search and Comments place facet and on Analysis > Place names mentioned.
