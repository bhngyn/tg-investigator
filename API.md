# Telegram Investigator: HTTP API

The local server (`.venv/bin/python -m investigator`, port 8765) serves the UI (built from `ui/` into
`investigator/web/`; the legacy UI in `investigator/static/` is still available) and this JSON API. It is the
contract for any frontend.

## Conventions

- **Local only.** The server binds to 127.0.0.1. Requests must carry a `Host` of `127.0.0.1:<port>`
  or `localhost:<port>`.
- **Token.** Every non-GET request needs the header `x-token: <token>`. The token is in the served
  page as `<meta name="x-token" content="…">`. A bundled frontend should read it from that meta tag,
  so keep serving `index.html` through `/`, which substitutes `__TOKEN__`.
- **JSON in, JSON out.** Errors come back as `{"detail": "human-readable message"}` with 400/404/409.
  The message is written for the investigator and can be shown as-is.
- **Times** are ISO 8601 UTC strings (`2026-09-28T19:01:55+00:00`), and compare correctly as strings.
- **Audit.** Every state change writes an `audit` row (who, what, when). Nothing investigators create is
  hard-deleted, except alert rules and schedules. Cases and incidents are archived or closed.
- **Media** files: `GET /media/<path>`, where `path` is the `path` field of a media row. Served with `X-Content-Type-Options: nosniff`; anything that is not an image, video or audio file (and SVG) is sent as `Content-Disposition: attachment`.
- **Remote resources.** The UI must never load them. Vendor every dependency into the bundle.

## Data model in one paragraph

Everything collected lives in one shared pool. **Channels** are keyed by lowercase username, with
`tg_id` as the permanent id that survives renames. Channels have **posts** (from the public
preview, no login) and optionally a **comment section** (a linked discussion group read with the
logged-in account: `comment_groups`, `comments`, `tg_users` for commenters, `group_admins`). A
**case** is a named set of channels. A channel can be in several cases, and is collected once.
**Triage** (review status), **tags** and **notes** exist twice:
- **Global (legacy):** `posts.review_status`, `post_tags` and `notes` with `case_id IS NULL`. Used when no case is selected.
- **Per case:** `case_reviews`, `case_tags` and `notes.case_id`. Used inside a case, so a post can be relevant in one case and irrelevant in another.

Pass `case=<id>` to the list endpoints below to scope them to a case and get that case's triage.

Status values: review `unreviewed | relevant | irrelevant | verify`; channel status
`seed | watched | candidate | dismissed`; case channel role `seed | watched | context`; job status
`queued | running | importing | done | stopped | failed | cancelled | interrupted`.
A crawl's status comes from what the crawler says about itself (its `progress.json` phase), not only its exit code:
`done` = finished (it can still carry warnings), `stopped` = a STOP request finished it, `cancelled` = cancelled by the user,
`interrupted` = it ended before finishing and can be resumed (Telegram said to wait, repeated failures, `--max-*` limit, stalled,
killed, or found unfinished after a server restart), `failed` = the crawler crashed.

---

## Cases

| Method | Path | Notes |
|---|---|---|
| GET | `/api/cases?archived=1` | Case list with dashboard counts: `channels, posts, new_posts, new_comments` (since `last_opened`), `unreviewed, relevant, verify, deleted_posts, comments, unread_alerts, incidents, last_collected, active_jobs[]`. Archived cases are hidden unless `archived=1`.<br>**Counts are exact and match the case's Search.** `reviewed, unreviewed, relevant, verify` are POST counts (`unreviewed` is never negative; only real posts of the case's channels count). Explicit keys: `post_reviewed, post_unreviewed, post_relevant, post_verify, post_irrelevant` and `comment_reviewed, comment_unreviewed, comment_relevant, comment_verify, comment_irrelevant`. `comments` / `new_comments` count what the comment Search lists (no mirrors of channel posts, no service messages). |
| POST | `/api/cases` | Create `{name, description?, color?}`. Edit `{id, name?, description?, color?, status: active\|archived}`. Returns the case row with counts. 409 on a duplicate name. |
| GET | `/api/cases/{id}` | The case plus `channels_detail[]`. Each channel has `role, note, title, type, subscribers, preview, last_crawled, posts, new_posts, deleted_posts, first_post, last_post, unreviewed` (in this case), `comments, new_comments, comments_checked, linked_from, links_to, watch` (row or null), `comment_schedule` (row or null), `active_jobs[]`, `events[]` (latest channel changes) and `other_cases[]`. The case also carries `alerts[]` (20 latest), `incidents_list[]` and `history[]` (audit). |
| POST | `/api/cases/{id}/open` | Stamps `last_opened = now` and returns `{previous}`. Call this when the user enters a case, then pass `since=previous` to search/comments to show "new since last visit" for the session. |
| POST | `/api/cases/{id}/channels` | `{add: ["@x", "t.me/y", …], role?, note?}`, `{remove: [...]}`, `{update: {channel, role?, note?}}`. Returns `{added, already, invalid, removed}`. Adding promotes a candidate or dismissed channel to `watched`. |
| GET | `/api/channels/{name}/cases` | Which cases a channel is in. |
| POST | `/api/cases/{id}/review` | Bulk triage: `{item_type: post\|comment, ids: [..], status}`. 404 if any id is not a real post/comment (nothing is written), 400 if an item is not in one of the case's channels or ids are not whole numbers. |
| GET | `/api/cases/{id}/reviews?type=post\|comment&ids=1,2,3` | Case triage state for exactly these ids (max 1000): `{case, type, reviews: {id: status}, tags: {id: [tag_id]}, notes: {id: count}}`. Every requested id is present; never-triaged ones are `unreviewed`. |
| POST | `/api/cases/{id}/tags` | `{item_type, id, tag_id, on: bool}` returns `{tags: [tag_id…]}`. 400 on missing/invalid `id` or `tag_id`; 404/400 if the item does not exist / is not in the case. |
| GET | `/api/cases/{id}/notes?item_type=post&id=12` | Notes in the case, also filterable by `channel=`. |
| POST | `/api/cases/{id}/notes` | `{text, item_type: post\|comment\|channel\|case, id?, channel?}`. |
| POST | `/api/resolve` | Paste box: `{handles: "a, @b\nt.me/c" or [..], live?: bool}` returns, per input: `{input, username, known (DB row or null), cases[], error?, live?}`. `live: true` fetches each public t.me page (no login, max 25 handles), giving type (channel/group/user/bot/unknown), preview availability, title, subscribers and latest post date. |

**Case scoping on existing endpoints.** `case=<id>` is accepted by `/api/search`, `/api/export.csv`,
`/api/comments`, `/api/comments/export.csv`, `/api/channels`, `/api/graph`, `/api/overlap` and
`/api/community`. In search and comments it:
- restricts results to the case's channels;
- makes `review=` and `tag=` / `untagged=1` filters use the case's triage;
- adds `case_review`, `case_tags[]` and `case_notes` (count) to every result card;
- `GET /api/posts/{id}?case=<id>` and `GET /api/comments/{id}?case=<id>` add the same three fields; for posts, `notes[]` is then only this case's notes and `history[]` leaves out audit rows of other cases.

`GET /api/comments` and `GET /api/comment_threads` also take `in=text,ocr,speech,names` (default text + OCR + speech, names only when asked; OCR / speech are left out of the default while no comment media has been read, so a plain search stays the classic text-only query). `/api/comments` results carry `matched_in` (`text|ocr|speech|names`) and `media_hits` (up to 3: `{kind, t_start, sha256, media_text_id, excerpt}`) like posts, and the response has `sources: {available, used}`. Identifier (`entity=`) and place (`place=`, `near=`) filters, facets and the places map also see the OCR / speech text of a comment's media.

`GET /api/comments` also takes `review=relevant,verify,...` (comma list). Inside a case it filters on the case's comment triage (unreviewed = never triaged), like posts. Outside a case comments have no triage (all count as `unreviewed`). Both `/api/search` and `/api/comments` take `ids=1,2,3` (max 500; more is a 400, never a silent cut) to fetch exactly those items (other filters still apply). `GET /api/cases/{cid}/reviews?ids=` takes at most 1000.

`/api/graph?case=` returns the case's channels plus their direct neighbours, and marks nodes with `in_case`.

New search/comment filters: `since=<iso>` (collected since), and on posts `deleted=1`
(found deleted). Post cards now include `last_seen` and `deleted_detected`.

## Collecting a channel: one call, chosen steps

`POST /api/channels/{name}/collect` accepts any subset of:

```json
{"posts": {"pages": 10, "media": "photos", "depth": 1},
 "comments": {"limit": 200, "media": "none"},
 "similar": true,
 "watch": {"interval_min": 360, "recheck_days": 7, "media": "photos"},
 "comment_schedule": {"interval_min": 10, "media": "none"},
 "case": 3, "role": "seed"}
```

It returns `{channel, jobs: {posts, comments, similar}, watch?, comment_schedule?, case?}`. If a crawl
or comment job for the channel is already queued or running, that job's id is returned instead of
queuing a duplicate. The individual endpoints below still exist.

## Watchlists (scheduled post re-crawls)

A watch re-crawls one channel's posts every `interval_min` (minimum 15). Each run reads from the
newest post back past the **re-check window** (`recheck_days`, i.e. posts dated within the last N
days), then stops. `pages` is only a cap. Media already stored is not downloaded again.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/watches?case=` | Rows plus `last_status, last_summary, title, last_crawled, posts, deleted, last_post`. |
| POST | `/api/watches` | Upsert by `{channel, interval_min?, pages?, recheck_days?, media?, enabled?}`, edit by `{id, …}`, delete with `{id, delete: true}`. |
| POST | `/api/watches/{id}/run` | Queue now. Returns `{queued: [job ids]}`, which is empty if a crawl of that channel is already running. |

Comment sections keep their own schedules: `GET/POST /api/schedules`, `POST /api/schedules/{id}/run`.

### Edits, deletions and channel changes

- **Post deleted.** A post is marked `deleted_detected = <check time>` only when all three hold:
  - it was captured before;
  - its message id lies inside the id range the crawl actually read (above the lowest id read; the first page is always the newest, so everything above is covered);
  - it wasn't on those pages.

  `last_seen` is the last check where it existed. If it later reappears, the flag is cleared and a `post_reappeared` audit entry is written.
- **Post edited.** When text or media types change, the previous state is kept in `post_versions` (`text, views, edited, media_types, captured_at`).
- **Channel events** (`channel_events`): `renamed, title_changed, description_changed, preview_disabled, preview_restored, unavailable, subscribers_jump` (±25% and at least 500), each with `old`/`new`. Read them via `GET /api/channels/{name}/events`.
- `GET /api/changes?case=&since=&limit=` is a feed of everything that changed on Telegram: `items[]` with `kind` = `post_deleted | post_edited` (with `previous_text`) `| comment_deleted | channel_<event>`, sorted newest first, plus `counts`.

## Alerts

Rules are checked after every job's import. Each alert is raised once per rule and item, and only
for things collected after the rule was created.

| kind | fires when | extra fields |
|---|---|---|
| `keyword` | new posts/comments match `query` (Search syntax) | `scope: posts\|comments\|both` |
| `post_deleted` / `comment_deleted` | an item is found deleted | |
| `post_edited` | a post's text or media changed | |
| `channel_change` | any channel event above | |
| `admin_change` | an admin was added to or removed from a comment group | |
| `new_candidate` | a channel outside the case is linked from ≥ `min_links` case channels (default 2; 1 fires on every single link, including each similar-channels result) | needs `case_id` |
| `media_reused` | a newly collected file's SHA-256 already exists in another channel | |

**Keyword rules expand once.** When a keyword rule is created, when its `query` is edited, or when an edit sends `reexpand: true`, the query is expanded and the final FTS expression is frozen in `query_fts` (with `expansion` and `dict_version`). Later dictionary edits never change a saved rule; re-save with `reexpand: true` to pick them up. Rules created before this feature have no `query_fts` and keep matching literally (unchanged) until their query is edited or re-expanded. Renaming or toggling a rule keeps its frozen expression.

Every rule can be limited by `case_id` and/or `channels: [..]`. With `notify: true` it also shows a
macOS notification, generated locally.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/alert_rules?case=` | Rules plus `alerts, unread` counts, and for keyword rules `query_fts`, `dict_version` and `expansion` (the parsed expansion object, or `null`). |
| POST | `/api/alert_rules` | Create `{kind, name?, query?, scope?, case_id?, channels?, min_links?, notify?}`, edit `{id, …}`, delete `{id, delete: true}` (removes its alerts). Keyword rules also take `expand` (default true), `drop`, `skip` (lists or comma strings, as in search) and, on edit, `reexpand: true`. |
| POST | `/api/alert_rules/preview` | Dry run of a rule over the last `days` (default 7), expanded the same way as on save (`expand`, `drop`, `skip`). Returns `{since, count, matches, expansion}` and saves nothing. |
| GET | `/api/alerts?case=&unread=1&rule=&limit=&offset=` | `{total, unread, alerts[]}`. Each alert has `kind, item_type (post/comment/channel/account), item_id, channel, title, detail (object), created, read_at`. For posts and comments, `item_id` starts with the numeric id: `"12"` or `"12:v3"`. |
| POST | `/api/alerts/read` | `{ids: [..]}` or `{all: true, case?}`. Add `unread: true` to mark them unread again. |
| POST | `/api/alerts/check` | Run every rule now over the last day. |

## Multilingual search and the dictionary

A local dictionary (`investigator/data/gazetteer/dictionary.db`: OpenStreetMap + GeoNames places for Ukraine and Sudan, plus a seeded lexicon of terms; built once by `python -m investigator.gazetteer_build`, read-only at runtime, never fetched) links names of the same place or term across scripts and spellings. Investigator edits live in the workspace (`dict_edits`), never in the dictionary file. Set `INVESTIGATOR_DICT=/path/to/dictionary.db` to use another file. If the file is missing, searches still work: expansion then yields only the Cyrillic-romanisation groups below, and `/api/dictionary/stats` says `available: false`.

**Expansion is ON by default** for `GET /api/search`, `GET /api/comments`, `GET /api/comment_threads`, `GET /api/export.csv`, `GET /api/comments/export.csv` and `GET /api/dictionary/lookup`. Extra params on all of them:

| Param | Meaning |
|---|---|
| `expand=0` | Expansion off: exactly the literal search of earlier versions. |
| `drop=a,b` | Comma list of alternatives to leave out, each exactly as returned in `alts[].term` (compared after normalisation; a term that itself contains a comma cannot be dropped). |
| `skip=x,y` | Comma list of group spans (`groups[].span`) not to expand at all. |

What is expanded: only plain, unquoted, non-negated words. `"exact phrases"` and `-excluded` words are never expanded, and `OR` splits a query into independent runs.
- In each run the longest spans (up to 5 words) are looked up in the dictionary, so `El Fasher`, `Al-Fashir`, `alfashir` (joined article), `Fasher` and `الفاشر` all reach the same group. `Bakhmut` reaches `Артемовск` (historic names are included).
- Lexicon terms beat places. Places sharing a spelling merge into one group (`"12 places named Mykolaivka"`); the top 5 by importance supply the alternatives (`more_entries` counts the rest).
- Guards: a single Latin word shorter than 4 letters or in a stop-word list (about 250 common English words) is never treated as a place. Lexicon terms are exempt (`RSF` works).
- Every other single Latin word of at least 4 letters (and not a stop word) gets a `translit` group whose only alternative is its Cyrillic spelling key (`term: "Cyrillic ≈ rusich"`). The search index stores a romanised key (`zz` + key) for every Cyrillic word, so `Rusich`/`Rusych` find `Русич` without a dictionary entry. Only post and comment *text* is romanised; the channel-name/forward-name column is not. Arabic is never romanised.
- Per group: at most 16 alternatives, and 120 alternatives in total. Anything cut sets `"truncated": true`, `truncated_count` (number of names cut) and lists the cut names in that group's `omitted: [terms]` (they were NOT searched; `omitted` is `[]` on every uncut group).

Every response above carries:

```json
"expansion": {
  "enabled": true,
  "dict_version": "<dictionary meta version>[+e<hash of active edits>]",
  "fts": "(\"bakhmut\"* OR \"бахмут\"* OR \"артемовск\"* ...)",
  "truncated": true,
  "truncated_count": 5,
  "dictionary": {"available": true, "key_version_ok": true},
  "groups": [
    {"span": "el fasher", "kind": "place|term|translit", "label": "El Fasher", "detail": "city · North Darfur · SD",
     "skipped": true,
     "entries": [{"id": "place:123", "label": "El Fasher", "category": "city", "country": "SD", "admin1": "North Darfur"}],
     "more_entries": 0,
     "alts": [{"term": "الفاشر", "lang": "ar", "historic": false, "dropped": false}],
     "omitted": []}
  ]}
```

- `fts` is the exact FTS5 expression that ran (with `expand=0`, the literal one; `null` for an empty query). `dict_version` says which dictionary state produced it, so a search can be re-run the same way.
- `dictionary` is on EVERY expansion object (also `expand=0`, frozen ones, and `/api/dictionary/lookup`): `{available, key_version_ok, message?}`. `available: false` = no place dictionary installed, so only the typed words (and their Cyrillic key) are searched; `key_version_ok: false` = the dictionary was built with an older key version and names in other spellings may be missed. `message` is a ready-to-show sentence when either is false (Search can show a banner). Frozen expansions report the dictionary's state now, not at freeze time.
- `truncated` (with `truncated_count`, and per-group `omitted`) appears only when a cap cut something. Alert rules (`/api/alert_rules`) and saved searches (`/api/saved_searches`) that froze a truncated expansion return `truncated: true` and `truncated_count` (read from the stored expansion, so they keep saying so); `to_alert` returns them too. `skipped: true` appears on a group listed in `skip=` (its alternatives are still listed so the UI can offer to turn it back on).
- `alts[].lang` is `en|ar|ru|uk|latn|other`, or `translit`. Alternatives equal to what the user typed are not repeated.
- `terms` (highlighting) is now the typed words plus every non-dropped alternative of non-skipped groups (never the romanised keys).
- `GET /api/comment_threads` returns `expansion` next to `threads`.
- CSV exports honour the same params and write an audit entry `search_export` `{q, fts, dict_version, expand, rows, scope: posts|comments}` beside the existing `export_csv` one.

### Dictionary endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/api/dictionary/stats` | `{available, edits (active), edits_total}`; when available also `built_at, version, sources[] ({name, file, sha256, date, license, attribution}), entries, names, by_country{}, by_kind{}, by_lang{}`. |
| GET | `/api/dictionary/lookup?q=&expand=&drop=&skip=` | The `expansion` object for a query without searching (what the search box previews). |
| GET | `/api/dictionary/entries?q=&kind=&country=&category=&limit=50&offset=0` | Entries whose label or any name starts with `q` (any script, spelling-insensitive), exact matches and investigator-made entries first, then by importance. `kind` is `place\|term`. Returns `{total, offset, entries[]}`; each entry has `id, kind, country, category, label, admin1, lat, lon, population, rank, notes, sources[], user (true for entries you added), hidden, names[]`. Each name is `{name, lang, historic, expand, hidden, user, edit_id?}` (`expand: false` names only match what the user types; `user: true` names were added by the investigator). |
| GET | `/api/dictionary/entry/{id}` | One entry as above plus `edits[]` (its whole edit history, undone ones included). 404 if unknown. |
| GET | `/api/dictionary/edits?limit=&offset=` | Every edit, newest first. |
| POST | `/api/dictionary/edit` | One of `{action: "add_name", entry_id, name, lang?}`, `{action: "hide_name", entry_id, name}`, `{action: "add_entry", kind: "place\|term", label, country?, category?, names: [{name, lang?}]}`, `{action: "hide_entry", entry_id}`, `{action: "undo", edit_id}`. Returns `{ok, edit (the dict_edits row), dict_version}`. `lang` defaults from the script. An added entry gets id `user:<edit id>` and its label is added as a name. Errors: 400 (name under 3 letters, bad action), 404 (unknown entry or edit), 409 (name already present, already hidden, already undone). |

Edits take effect immediately for searches and change `dict_version`. Hiding a name also stops it matching the user's input. Every edit is audited (`dict_add_name`, `dict_hide_name`, `dict_add_entry`, `dict_hide_entry`, `dict_undo`). Rows are never deleted: undo flags `undone` (undoing an added entry also undoes its names). Alert rules that were already saved are not affected (see below).

**Search index.** At first start after this version, `posts_fts` and `comments_fts` are rebuilt once in a single transaction to add the romanised tokens (guarded by the setting `fts_version`, audited as `fts_reindex` with row counts; only the derived `stems` column changes).

## Search core: normalisation, syntax, indexes, place matching
### Normalisation (db.normalize, index AND query time; stored text never changes)
NFKC, Arabic marks out, zero-width / direction marks / soft hyphen / BOM removed, Persian letters folded (ک→ك ی→ي ە ۀ→ه), lower-case, then: `@`/`$` between two Latin/Cyrillic letters -> a / s, a lone `0` next to a Latin/Cyrillic letter -> o (numbers untouched), runs of >=4 single letters joined when separated by one repeated separator (space . - _ *), mixed Latin+Cyrillic tokens folded into the majority script (a e o p c x y k m t b h i and Cyrillic twins), 3+ identical letters -> 1. URLs, `@usernames`, `t.me/...` and e-mails are left as written. Arabic single-letter words are never joined.
Known limits: `g`/`r`/`s` have no Cyrillic twin (вagнер stays "ваgнер"); ties (equal Latin/Cyrillic letters) are left alone; a Latin word glued to a Cyrillic one after zero-width removal (наTelegram) is folded to the majority script.

### Query syntax additions
- `a NEAR/10 b`, `a NEAR b` (default 10), `"phrase" NEAR/3 word`, `"a b"* NEAR c`, chains `a NEAR b NEAR c` (one NEAR over all, largest distance). Keyword is UPPER-CASE only (`near` stays a word). Operands: words or "phrases". A NEAR without two operands is ignored.
- Literal: `NEAR("a"* "b"*, n)`. Expanded (`lexicon.expand`): an OR of NEAR() combinations over each word operand's alternatives (dictionary names, Cyrillic romanisation key, stems); phrases and hyphenated words stay literal. More than 24 combinations -> each operand is cut back to its most useful alternatives (original word, its romanised `zz` key, then native-script names by entry rank, Cyrillic and Arabic alternating) until at most 24 remain; `expansion.near_reduced = true` and `expansion.near_used = [{near, used: [{operand, alts: [terms]}]}]` say what ran. `near_literal` is no longer produced. `expansion.groups` lists the operand groups as usual (skip/drop work).
- **Inflected lexicon lookup:** a single plain word with no dictionary match by key is retried by stem, matching lexicon TERMS only (never places): Russian/Ukrainian stem (обстрелом, обстрелы -> обстрел), Arabic prefix/suffix-stripped form, also with ة restored (الغارات -> غاره) and as the first word of a multi-word term name (الغارات -> "غارات جوية").
- **Romanised alt inside groups:** a single plain Latin word (>=4 letters, not a stop word) that matches a dictionary group gets an extra alt `{term: "Cyrillic ≈ <key>", lang: "translit"}` (FTS `"zz<key>"*`) in that same group, droppable like any alt (`drop=cyrillic ≈ key`), so `Legion` finds Легион in OCR/speech text. It is not highlighted and counts as the second priority in NEAR reduction.
- Highlighting: `lexicon.highlight_terms` drops `NEAR/n` and trailing `*`.
- Stemming (query side): a Russian/Ukrainian word also matches `"stem"*`; an Arabic word also matches its article-stripped form and its suffix-stripped stem (prefix match only for stems of >=4 letters, exact for 3). Excluded words (`-word`) get no stems. Index side: `stems` column = Arabic prefix stems (in text order), then extra stems (Arabic suffix, RU/UK), then `zz` romanised keys.
- Arabic suffixes: spec list + `ت` and `وا` (past-tense verb endings, so قصفت meets قصف).
- Alternative names from the dictionary: any token of <=3 characters (any script) is matched exactly, never as a prefix (`"сво"`, `"гру"`, `"ew"`); in multi-token alts only the last token keeps `*` and only if >=4 characters. The user's own typed words are unchanged.

### Search indexes (fts_version "3": rebuilt once at the next start, audited as `fts_reindex`)
- `posts_fts`, `comments_fts`: norm/stems/meta recomputed from the stored original text.
- `post_versions_fts(norm, stems)` rowid = post_versions.id, written by `db.index_version(con, version_id, text)` (called in ingest when an edit stores a version). Deleted comments/posts are never removed from their FTS tables (verified; `deleted_detected` is only a flag).
- `media_text` (created `IF NOT EXISTS` with the agreed columns + index on sha256) and `media_text_fts(norm, stems)` rowid = media_text.id, written by `db.index_media_text(con, media_text_id, text)` - **the media-text writer must call this after each insert/update** (it replaces the previous entry).
- `db.versions_match_sql()` -> `SELECT DISTINCT v.post_id post_id FROM post_versions_fts f JOIN post_versions v ...  WHERE post_versions_fts MATCH ?` (use as `p.id IN (<sql>)`).
- `db.media_text_match_sql()` -> rows `(post_id, media_text_id, kind, t_start, sha256)` for `media_text_fts MATCH ?`, joined `media.sha256 -> media.post_id`; a file used by two posts yields a row for each. (comment_media is not included.)
- `db.INDEX_HOOKS`: list of `fn(con, item_type, item_id, text)`; `text` is the ORIGINAL text (normalise yourself with `db.normalize`); called at the end of index_post ('post'), index_comment ('comment'), index_media_text ('media_text'), also when an edit re-indexes the item, so hooks must replace their earlier rows. Exceptions are printed as a warning and swallowed. Hooks are not run by `reindex_fts` (backfill is S2's job).

### Places (lexicon.py)
- `lexicon.find_places(norm_text, con=None) -> [{entry_id, name, start_tok, end_tok (exclusive), candidates}]`. One item per candidate entry of a span (candidates = n; >1 means ambiguous). Token indices are `db._TOKEN.findall(norm_text)` positions; `name` is the span as written (normalised). Pass `con` (the workspace connection) from hooks; without it the ctx registered by `lexicon.register()` is used (edits + frequency guard); with neither, edits are ignored and the frequency guard is off.
- Cyrillic single words also match by stem (Бахмуте/Бахмута -> Бахмут); Latin joined articles (alfashir) and leading al-/el- are handled; hidden names/entries and added names/entries from dict_edits apply; the map rebuilds when the dictionary file or `dict_edits` (count, max id, undone) change. Only kind='place' entries and names with expand=1 or lang in ar/ru/uk/en.
- Guards (counted in `lexicon.guard_stats` = {short, stopword, frequent, ambiguous}): single token with key <4 chars; for entries ranked <7: stopwords (English list + `COMMON_NON_EN` ar/ru/uk list, also by stem), words in >2% of posts (posts_fts, cached 5 min), and words shared by >10 low-rank places (`MAX_LOW_CANDIDATES`, e.g. 40 hamlets called Nove) when no rank 7+ entry shares it. Rank >= 7 entries pass these guards.
- Cyrillic single words must equal an entry's exact spelling (the match key folds е/и and о/у: были ~ Биле, турецких ~ Торецк, мирные ~ Мирное); only inflected forms of rank 7+ places are reached via the stem (Бахмуте -> Бахмут). Latin/Arabic use the lossy key as before. Even so, real-dictionary noise remains (villages named like ordinary words, e.g. Веселое, 87 candidates): S2 must treat `candidates > 1` as ambiguous and label all tags "places mentioned in text".
- Precision rules (round 2): a single Arabic-script word tags a place outside SD/TD only when the entry is curated or rank >= 9 (`RANK_AR_ANY`); a single Cyrillic word tags a non-UA/RU/BY place only at rank >= 8 (`RANK_CY_FOREIGN`); a Cyrillic stem match through an adjective/plural-like ending (-ом -ой -ое -ая -ый -ий -ие -ые -ого -ому -ым -им -ых -ов -ев -ах -не -на ...) needs rank >= 8 (`RANK_ADJ`), so "Красном море" no longer reaches the village Krasne; common first names (Arabic: اسماعيل محمد احمد علي عمر عثمان ابراهيم يوسف ...; Russian/Ukrainian) and a few ordinary words (ровно, свобода, широкий ...) are in the never-tag list `COMMON_WORDS`. Known trade-off: Russian "Ровно" (Rivne) is no longer tagged.
- `lexicon.place(entry_id) -> {entry_id, label, country, category, admin1, lat, lon, rank}` or None (works for `user:` entries; lat/lon None there).
- Speed: real dictionary (115k entries): lazy map build 1.3 s once; 851 real items tagged in ~1.9 s (2 ms/item, incl. guard lookups). Real-dictionary run: 6.2k tags, 4.5k of them ambiguous, guard rejections {short 1118, stopword 174, frequent 1037, ambiguous 22}.

### /api/dictionary/stats
Adds `key_version` (dictionary meta), `key_version_ok` (== `translit.KEY_VERSION`, now **2**; a dictionary without `key_version` counts as not ok), `key_version_message` when not ok, and `coverage` = the builder's meta `counts` JSON as-is (`{}` if absent/unparseable).
`translit.KEY_VERSION` = 2 because db.normalize (used by match_key) changed: rebuild dictionary.db with the builder.

### Versions / timing
`db.FTS_VERSION = "3"`. Reindex is a full rebuild from source text: 851 rows 0.29 s, 20k posts 5.7 s (~0.28 ms/post) -> about 30 s for 100k posts, once, at first start.

### Suppressed "common word" alternatives (expansion)
A single-word alternative name of a PLACE entry (never lexicon terms) is not emitted when it is a stop word (English list, or `COMMON_NON_EN`: e.g. مدني, جديد, победа, слобода; any rank) or, for entries ranked below 7, occurs in more than 2% of posts (posts_fts, exact token, also without the Arabic article). It stays in the group's `alts` with `dropped: true, reason: "common word"`. Plain user drops have no `reason`.
Restore: send `drop=+term` (the term as shown in `alts[].term`; other drops in the same list still apply; `drop=+слобода,foo`), i.e. a `+` prefix in the existing `drop` param - no server change needed. Frozen alert/saved-search expansions keep whatever was emitted at save time. High-rank (7+) names are exempt from the frequency test on purpose (الخرطوم is in 30% of Sudanese posts). Caveat: in a Sudan-heavy corpus the frequency test also suppresses alts of low-rank localities (e.g. كردفان, سنار, الفاشر for rank 5-6 entries); users restore them with `+`.

### Other
- `NEAR` is a keyword only outside "quotes" (`"the NEAR east"` is a plain phrase).
- `from_params()` still returns `(enabled, drop, skip)` (savedsearch.py unpacks 3 values).

## Search features: search log, terms, entities, places, saved searches
All new tables are derived/record tables created with CREATE TABLE IF NOT EXISTS in each module's `register()`.
Mutating calls need the usual `x-token`. New modules registered in `server.py`: searchlog, entities, places, terms, savedsearch.

### Shared search path (server.py)
One code path builds every post/comment search: `search_sql` / `comment_sql`, used by /api/search, /api/comments,
/api/comment_threads, both CSV exports, saved searches, the search log and re-runs, terms, places/map, facets.
Exposed to modules as `ctx.execute(kind, params) -> {ids, total, capped, exp}` (kind posts|comments|threads, ids ordered, cap 50,000),
`ctx.subquery(params, scope, cols, order, limit)`, `ctx.query_expansion`, `ctx.qparams(request)`.
Repeated `entity=` / `place=` query params are merged (`qparams`); comma lists also work for place. An unescaped `+` in
`entity=phone:+249...` arrives as a space; it is repaired (a leading `+` is added).

#### New search params (posts; entity/place/near also apply to /api/comments and /api/comment_threads)
| param | meaning |
|---|---|
| `in=text,versions,ocr,speech` | where to match. Default all available. text = posts_fts (bm25 order); versions = earlier text of edited posts; ocr / speech = media_text. Non-text matches rank after text matches (newest first). |
| `entity=kind:value` | ANDed when repeated. kinds phone btc eth trx card unit callsign plate. Matches the post's own text or the OCR/speech text of its media. card value is the masked `first6…last4`. callsign value is lower-cased. |
| `place=<entry_id>` | ORed (comma or repeated). |
| `near=lat,lon,km` or `near=<entry_id>,km` | posts mentioning a place within km (haversine on the dictionary's lat/lon). Unreadable value matches nothing. |
| `unambiguous=1` | with place/near, and for the places facet/map: only mentions whose name maps to exactly one place. |
| `log=0` | do not write this search to the search log (for live typing). |

#### /api/search response additions
- each result: `matched_in: ["text","versions","ocr","speech","names"]` (subset, only when a query ran), `version_hit: bool`,
  `media_hits: [{kind, t_start, sha256, media_text_id, excerpt}]` (max 3).
- `entities: [{kind, value, n}]` top values among all results (claims in text). **Deviation:** the spec said "facet"; the existing
  `facets` key stays the channel list, so the new facets are the top-level keys `entities` and `places`.
- `places: [{entry_id, label, country, category, admin1, lat, lon, n, ambiguous_n}]`, `places_note` = "Places mentioned in text (not verified locations)". Empty when no dictionary is loaded.
- **Text search matches text only.** A query is matched against the post/comment text columns (`norm`, `stems`), never the channel handle/title, forward-from names or hashtag list (`meta` column), by wrapping the expression as `{norm stems} : (expr)` at match time (search, exports, saved searches, search-log re-runs, alert rules incl. frozen `query_fts`, previews; stored strings are not rewritten). NEAR, prefixes and stems work inside the filter. `wagner` no longer returns every post of @example_channel. Opt-in source **`in=names`** ("channel & forward names") matches the `meta` column instead/in addition (`in=names`, or `in=text,names`); it is always listed in `sources.available`, never in the default `used`, and yields `matched_in: ["names"]`. `/api/comments` and thread search honour `in=text` / `in=names` (default text only); other `in` values are ignored there and no `sources` object is returned.
- `sources: {available, used}`: requested ∩ available. Note: sources whose index is empty (no versions / no media text yet) are skipped
  inside the query, so `used` can list a source that matched nothing because it holds nothing.
- `search_run`: id of the search-log row (null when not logged).
- /api/comments also returns `entities`, `places`, `places_note`, `search_run`; /api/comment_threads returns `search_run`.
- Plain queries with default params return the same ids/order as before when no versions/OCR/speech text is indexed (identical SQL path).

### Search log (searchlog.py)
Table `search_runs(id, ts, actor, endpoint posts|comments|threads|export|saved, case_id, q, params JSON, fts, dict_version, key_version, total,
result_sha256, first_id, repeat, saved_search_id, last_ts, rerun_of, ids BLOB)`. (`last_ts`, `rerun_of`, `ids` are additions; ids = zlib JSON, cap 50,000.)
- Logged: first page (offset 0) of /api/search, /api/comments, /api/comment_threads when the search has `q`, `entity`, `place` or `near`
  (plain filter-only browsing is NOT logged: decision, tell me if you want it); every CSV export (always); every saved-search run. Never /api/dictionary/lookup.
- `result_sha256` = sha256 over the ordered ids (one per line), threads as `group_id:top`.
- Dedupe: same endpoint + params + fts + total + hash + saved_search_id within 10 minutes of the last repeat (sliding window) bumps `repeat`, updates `last_ts`.
- `GET /api/search_log?case=&q=&endpoint=&saved=&limit=&offset=` -> `{total, runs: [...]}` (no ids).
- `GET /api/search_log/{id}[?ids=1]` -> run, `reruns: [...]`, optional ordered ids.
- `POST /api/search_log/{id}/rerun` -> `{same, total_then, total_now, added, removed, added_ids[<=200], removed_ids[<=200], dict_version_then, dict_version_now, fts, run_id}`.
  Uses the frozen fts (no re-expansion) and the stored params; the re-run is itself logged (`rerun_of`) and audited (`search_rerun`).
- Evidence packages (case scope, incident scope via its case, selection with `case`): `search_log.csv` (id, time_utc, last_repeat_utc, repeats, by, endpoint, query, fts, dict_version, key_version, total, result_sha256, first_id, saved_search_id, params), listed in SHA256SUMS, plus a "How items were found" section in README.txt; manifest counts.search_runs.
- Export audit rows (`search_export`) now carry `search_run`.

### Terms like this (terms.py)
`GET /api/search/terms?<search params>&limit=40[&scope=comments]` -> `{scope, results_used, corpus_docs, method, terms: [{term, in_results, in_corpus, score, script, dictionary: {entry_id,label}|null}]}`. `dictionary` links only to lexicon terms or places of rank >= 7 (a match-only alias of some village, e.g. мали -> Natashyno, is not a meaning of the word).
Log-odds with informative Dirichlet prior (Monroe et al.), doc frequencies from `posts_fts_vocab` / `comments_fts_vocab` (fts5vocab 'row', created on first use). Result set capped at 5,000, in search order. `score` is the z-score (>2 notable).

### Entities (entities.py) — claims in text, not verified identifiers
- `extract(text)` pure function; tests at scratchpad/wave2/S2/test_entities.py (44 positives, 32 negatives).
- Table `entities(id, item_type post|comment|media_text, item_id, kind, value, raw, start, "end", meta JSON, created)`; indexes (kind,value) and (item_type,item_id). Card: `value` masked, full number only in `raw`, never in audit/server log; API hides card `raw`/excerpt unless `?reveal=1` (audited with the masked value).
- `GET /api/entities?kind=&q=&case=&limit=&offset=` -> `{total, entities: [{kind, value, n (items), mentions, n_channels, channels[<=8], first_seen, last_seen}], by_kind, note}`.
- `GET /api/entities/{kind}/{value}?case=&limit=&offset=&reveal=` -> `{total, items: [{id,item_type,item_id,raw,start,end,meta,channel,date,url,post_id,excerpt}]}`.
- `GET /api/entities/status`; `POST /api/entities/backfill` (idempotent, synchronous, in batches; posts, comments without mirrors, media_text; audited with counts only).
- Hook: `db.INDEX_HOOKS` gets `entities.hook` when the server starts (creates its table if needed). `python -m investigator --import` does not run the server, so run the backfill afterwards.

### Places (places.py) — "places mentioned in text (not verified locations)"
- Table `item_places(id, item_type, item_id, entry_id, name, start_tok, candidates, created)`; one row per candidate entry of a span; `candidates>1` = ambiguous.
- Tagging via `lexicon.find_places(db.normalize(text), con)`; hook registered like entities; `POST /api/places/backfill` idempotent (returns `available:false` + message without dictionary). `GET /api/places/status`, `GET /api/places?case=` (top places), `GET /api/places/map?<search params>[&scope=comments&top=300&unambiguous=1]` -> GeoJSON FeatureCollection (properties entry_id,label,country,category,admin1,n,ambiguous_n) + `label`, `available`, `message`.
- SQL function `place_km(entry_id, lat, lon)` registered on the connection (no bounding-box prefilter; fine for current sizes, scaling follow-up).
- No dictionary: nothing tagged, facets/map empty, messages say why. Existing tags stay in the table.

### Saved searches (savedsearch.py)
Table `saved_searches(id, case_id, name, q, params JSON, freeze, fts, expansion JSON, dict_version, notes, created, created_by, last_run_at, last_total, last_result_sha256)`.
- `GET /api/saved_searches?case=`; `POST /api/saved_searches` create `{name,q,params:{...},case_id,freeze,notes}` / edit `{id,...}` / delete `{id,delete:true}` (audited; `reexpand:true` refreshes a frozen expansion). `params.scope` = posts (default) | comments. List params (entity, place, channels) are accepted and normalised.
- `POST /api/saved_searches/{id}/run` -> `{total, total_before, first_run, new_since_last (null on first run), new_ids[<=200], added, removed, added_ids[<=200], removed_ids[<=200], previous_run_id, truncated, truncated_count, changed, result_sha256, run_id, fts, dict_version, frozen}`. **`new_since_last` vs `added`/`removed`:** `new_since_last` counts hits *collected* since the last run (`first_captured` after it); `added`/`removed` compare the result *sets* with the previous run's stored id list (search_runs), so a total that grew 243 -> 267 because OCR text was indexed or a filter changed shows `added: 24, new_since_last: 0`. `added`/`removed` are `null` when there is no previous run with stored ids (first run, legacy). Ids are strings for thread searches (`group:top`); writes a search_run (endpoint `saved`) and an audit row.
- `POST /api/saved_searches/run_all?case=` -> `{ran, with_new, changed, searches: [...]}`.
- `POST /api/saved_searches/{id}/to_alert` {name?, notify?, drop_filters?} -> creates a keyword alert rule (query, frozen expansion, scope, case, channels). An alert matches query text only, so if the saved search has any other filter (entity, place, near, date_from/date_to, in, fwd, media, min_views, ...) it answers **409** `{detail: "<sentence naming the filters>", dropped_filters: ["date_from", "entity", ...]}` and creates nothing, unless the body has `{"drop_filters": true}` (then the rule is made and the response lists `dropped_filters`). `sort` and `near_label` do not count. Response also has `truncated`, `truncated_count`.
- freeze=1 runs use the stored expansion; freeze=0 expands live each run.

### Alerts (alerts.py)
- `POST /api/alert_rules/preview` with `{"id": N, ...}`: previews the existing rule. If the body's `query` is unchanged (or absent) and `reexpand` is not set and the rule has `query_fts`, the preview uses the rule's frozen `query_fts`/`expansion`/`dict_version` (what the rule will really match), not a fresh dictionary expansion. Without `id`, or with a changed query / `reexpand:true`, it expands live. Unknown id -> 404.
- `alerts.insert_rule(ctx, fields)` helper (also used by saved searches).

## Media text (OCR and speech)

Reads the text in collected photos and video frames (Apple Vision) and transcribes video audio (mlx-whisper), all
locally and offline. **Never automatic** unless the setting below is switched on: a run is queued as a `mediatext`
job in the local lane (no network, one worker at a time). Results are stored per file hash, not per post:
`<workspace>/derived/<sha[:2]>/<sha>/ocr.json`, `whisper.json`, `whisper.srt` (originals are never modified; each JSON
records tool + version, model, macOS version, languages, parameters, creation time and the input's sha256; the
database stores the sha256 of each derived JSON). The derived JSON is the record; `media_text` rows are rebuildable.

Which files: every full-size photo/`full` image once (a thumbnail only when its post has nothing bigger); every
video (keyframes at most every `interval_s` seconds, blank and near-duplicate frames skipped, real timestamps;
plus audio transcription). The web-preview video is skipped when the post also has a logged-in original. **Media
attached to comments (`comment_media`) are read the same way**: a file is one entry whichever posts and comments hold
it (its hash keys the derived files), photos and videos only (documents and stickers are skipped). Scope keys:
`post_ids` and `comment_ids` narrow to those items; `channel`, `case` and `all_missing` cover posts and comment
sections; `only: "posts"|"comments"` limits a scope to one side.

Tables: `media_text(id, sha256, kind 'ocr'|'speech', seq, t_start, t_end, text, lang, conf, bbox)` (one row per OCR
line / speech segment; `t_*` are seconds into the video, NULL for photos; `bbox` is Vision's normalised
[x, y, w, h], origin bottom-left; `lang` is the dominant script for OCR, the detected language for speech) and
`media_text_runs(id, sha256, kind, status ok|no_audio|no_text|error, tool, model, languages, params, output_path,
output_sha256, error, job_id, created)`.

| Endpoint | |
|---|---|
| `POST /api/mediatext/run` | `{"kind": "ocr"\|"speech"\|"both", "scope": {"post_ids": [..]} \| {"comment_ids": [..]} \| {"channel": ".."} \| {"case": id} \| {"all_missing": true}` (+ optional `"only": "posts"\|"comments"`), "languages"?: ["ar-SA", ..], "language_hint"?: "ar", "redo"?: false, "limit"?: N}` -> `{"id": job id, "queued": {"ocr": n, "speech": m}}`; `id` is null when everything in scope is already read. `languages` = Vision codes or `["auto"]`; `language_hint` = Whisper language for this run (overrides per-channel hints). `redo` re-reads files whose recorded settings differ (the old output is kept as `*.prev-<time>.json`). 503 with a clear message if the worker venv or the Whisper model is missing. |
| `GET /api/mediatext/status` | `{worker_ready, model, model_available, job_supported, settings, ocr: {eligible, done, missing, comments: {eligible, done, missing}}, speech: {...}, errors, running: {id, status, params, progress: {phase, detail, totals, peak_rss_gb}} \| null}` |
| `GET /api/mediatext/{sha256}` | one file: `{media: [posts using it], comment_media: [comments using it], runs: [...], segments: {ocr: [...], speech: [...]}, derived_dir, srt}` |
| `GET /api/post/{id}/mediatext` | every media file of a post: `{media: [{media_id, kind, sha256, mime, runs, ocr_text, speech_text, segments}]}` |
| `GET /api/comments/{id}/mediatext` | the same for the media attached to one comment: `{comment_id, media: [...]}` |
| `GET /api/mediatext/settings`, `POST /api/mediatext/settings` | `{languages: ["auto"], interval_s: 5, channel_ocr_languages: {"channel": ["ru-RU","uk-UA","en-US"]}, whisper_hints: {"channel": "ar"}, auto_after_media_download: false}` (POST takes any subset; audited). |

Language guidance (from the bake-off): with a list of OCR languages only the first one drives recognition, so
`["auto"]` (default) is best for mixed Arabic/Cyrillic/Latin feeds; for a channel known to be Russian/Ukrainian use
`["ru-RU","uk-UA","en-US"]`; for Arabic-only use `["ar-SA","en-US"]`. Whisper auto-detects the language from the first
30 s; a per-channel hint avoids misdetection on short or noisy clips.

Search: segments are indexed in `media_text_fts` (via `db.index_media_text`), so OCR text and transcripts are found
by the normal search and tied back to posts through `media.sha256`.

## Discovery

| Method | Path | Notes |
|---|---|---|
| GET | `/api/discovery/candidates?case=&limit=&users=1` | No login. Channels outside the scope (the case, or seed/watched channels), ranked by `score`. The score counts how many scope channels point at them and weights forwards, mentions, links, description links, comment-section links, `similar` results and keyword-search hits, plus a recency bonus. Each has `sources[], by_type{}, reasons[]` (plain English), `title, type, subscribers, status, posts`. Dismissed channels are excluded; users and bots only with `users=1`. |
| POST | `/api/discovery` | Logged-in, read-only job: `{method: "similar", channels: [..]}` gives Telegram's similar channels; `{method: "search_channels"\|"search_posts", queries: [..], limit?}` searches public channel names or public posts. Add `case` to tag the job. Returns `{id}` (job). Similar results also become `similar` links in `/api/graph`. |
| GET | `/api/discoveries?method=&query=&case=` | What discovery jobs found: `method, query, username, tg_id, title, subscribers, type, detail{rank, post?}, status, cases`. |

To act on a candidate, add it to a case (`POST /api/cases/{id}/channels`) or dismiss it
(`POST /api/channels/{name}/status {status: "dismissed"}`).

## Incidents and evidence packages

| Method | Path | Notes |
|---|---|---|
| GET | `/api/incidents?case=&status=` | Includes an `items` count. |
| GET | `/api/incidents/{id}` | Includes `items[]`, each with the linked `item`, plus `history[]` and `exports[]`. |
| POST | `/api/incidents` | Create/edit `{id?, title, description, date_start, date_end, date_note, location, lat, lon, location_note, status: open\|verified\|disputed\|closed, case_id}`. `date_note` and `location_note` record *how* the date and place were established. |
| POST | `/api/incidents/{id}/items` | `{add: [{item_type: post\|comment\|media\|comment_media\|channel\|account, item_id, relation?, note?}], remove: [...]}`. |
| GET | `/api/items/{item_type}/{item_id}/incidents` | Which incidents an item is linked to. |
| POST | `/api/exports` | Build an evidence package: `{scope: "case", id, statuses?: ["relevant"], media?: true, include_incidents?: true}`, `{scope: "incident", id}` or `{scope: "selection", posts: [..], comments: [..], case?}`. Returns `{id, path, sha256, bytes, items, files, missing_files, download}`. |
| GET | `/api/exports` | Past packages, each with `exists`. |
| GET | `/api/exports/{id}/download` | The zip (audited). |

`statuses` in a case export may include `unreviewed`: it then also adds posts and comments of the case's channels that have no triage row (never-triaged items are unreviewed).

A package is written to `<workspace>/exports/` under a name claimed atomically (`evidence-<scope>-<UTC stamp>[-n].zip`), so two exports in the same second never share a file. It contains:
- `items.json` and `items.csv`: full collection records;
- `media/` (named by SHA-256), `pages/` and `api/`: raw captures;
- `derived/<media sha256>/ocr.json` and `whisper.json`: the newest OCR / speech record per exported media file (included even with `media: false`), each checked against the `output_sha256` recorded when it was made;
- `search_log.csv`: the searches run in the case (see Search features);
- `capture_log.csv`, `audit.csv`, `incidents.json`, `manifest.json`, `README.txt`;
- `SHA256SUMS`, checkable with `shasum -a 256 -c SHA256SUMS`.

**Spreadsheet safety.** Every server-generated CSV (`/api/export.csv`, `/api/comments/export.csv`, `/api/overlap/export.csv` and the package's `items.csv`, `capture_log.csv`, `audit.csv`, `search_log.csv`) starts any text cell that begins with `=`, `+`, `-`, `@`, tab or CR with an apostrophe so Excel/LibreOffice cannot run it as a formula. Cells that are only a plain number (`-12.5`, `+249912345678`) are left alone. `items.json` and the other JSON keep the original text.

**Data attribution.** When place or dictionary-derived data shaped an export, the OpenStreetMap (ODbL 1.0) and GeoNames (CC BY 4.0) attribution travels with it: the package `manifest.json` has `data_attribution` (text or null) and `README.txt` gets a "Data attribution" section; `/api/export.csv` and `/api/comments/export.csv` send it in the `X-Data-Attribution` response header when `place=`, `near=` or dictionary expansion of `q` was used.

The export re-hashes every stored file it includes (media directly; raw pages and API responses after decompressing) and refuses to proceed if any no longer matches its recorded hash.

## Analysis

| Method | Path | Notes |
|---|---|---|
| GET | `/api/analysis/coordination?case=&days=90&window_min=60&min_channels=2` | `groups[]`: the same normalised text, byte-identical file, visually identical file (`similar_file`, needs a media scan) or same forwarded original across ≥ `min_channels` channels. Each group has `kind, channels, items (time-ordered), first, spread_minutes`. `pairs[]`: channel pairs that posted the same thing within `window_min` of each other, with `count, kinds, examples`. |
| GET | `/api/analysis/spread/{post_id}` | Everywhere this post's content appears (same file, same forwarded original, same text), in posts and comments, oldest first. Each item has `how[]`. |
| GET | `/api/analysis/coordinates?case=` | GeoJSON `FeatureCollection` of coordinates written in posts and comments (decimal, degrees-minutes-seconds, Google/Apple/OSM/Yandex links) plus incidents with lat/lon. `unconverted[]` lists MGRS grid references found. These are claims to check, not verified locations. A map needs offline tiles, because the UI must not load remote resources. |
| POST | `/api/analysis/mediascan` | Queue a local scan (job kind `mediascan`, `local` lane, no network) of collected files not scanned yet: `{channel?, case?, limit?}`. It computes perceptual hashes (images once; videos at 10/50/90%) plus ffprobe details and whatever EXIF/container metadata the file still carries. It runs automatically after any job that downloads media, unless the setting `auto_mediascan` is `"0"`. |
| GET | `/api/analysis/similar_media/{sha256}?max_distance=7` | Posts and comments carrying a file that *looks* the same (re-encoded, resized, turned into a video). Each match has `distance` (bits of 64: 0–3 is almost certainly the same, 4–7 very likely, confirm by eye) and `identical`. Also returns the file's `meta` and `embedded`. Coordination adds `similar_file` groups and spread adds `similar file` hits once files are scanned. |
| GET | `/api/analysis/embedded?case=&gps=1` | Scanned files that still carry camera, GPS, date or software metadata, with the channels that posted them. |
| GET | `/api/overlap?case=` | Accounts, channel identities and forward sources seen in several comment sections; admins of one section commenting in another; shared-commenter matrix. |
| GET | `/api/overlap/export.csv` | |
| GET | `/api/community?channel=&case=` | Commenter community: phone countries, languages (local heuristic), activity by hour and weekday (UTC), top accounts. |

## Existing endpoints

- **Overview:** `GET /api/overview`, `GET /api/audit`.
- **Posts:**
  - `GET /api/search`: params (`date_from`/`date_to` must be real `YYYY-MM-DD` dates and `min_views` a whole number >= 0, else **400**; empty values are ignored) `q, channels, date_from, date_to, fwd, forwarded, media, review, tag, untagged, sensitive, min_views, has_notes, edited, sort, limit, offset`, plus `case, since, deleted` and the expansion params `expand, drop, skip` (see Multilingual search). The response has `terms` and `expansion`.
  - `GET /api/export.csv` (same filters and expansion params; audited as `export_csv` and `search_export`).
  - `GET /api/posts/{id}[?case=<id>]`: returns versions, captures, page capture, `same_forward`, `same_media`.
  - `POST /api/posts/{id}/review|tags|notes`: global triage.
- **Channels:** `GET /api/channels?status=&type=&q=&sort=&case=`, `GET /api/channels/{name}`, `POST /api/channels/{name}/status|notes`, `GET /api/graph?types=&min_weight=&hide_dismissed=&channels_only=&case=`.
- **Comments:** `GET /api/comment_groups`, `GET /api/comments` (params `q, channels, sender, post, date_from, date_to, deleted, forwarded, edited, media, sender_type, admins, mirrors, sort, in`, plus `case, since` and the expansion params `expand, drop, skip`), `GET /api/comments/export.csv`, `GET /api/comments/{id}[?case=<id>]`, `GET /api/comments/{id}/mediatext`, `GET /api/tg_users/{id}`.
- **Comment threads:** a thread is keyed by `(group_id, top)` where `top = COALESCE(thread_top_id, msg_id)`: the post's mirror in the group, or a plain group message that got replies.
  - `GET /api/comment_threads` takes the same filters as `/api/comments` plus `limit` (default 20, max 100), `offset` and `sort=oldest`. It returns `{total, offset, threads[]}`, where each thread has `group_id, top, channel, matching` (comments that matched), `ids[]` (their ids), `first, last` (dates of matches), `comments, deleted, last_comment` (whole thread), `root` (comment card or null when not captured) and `post` (`{id, url, date, text}` when the channel post was collected). Threads are ordered by the latest match.
  - `GET /api/comment_threads/{group_id}/{top}?case=` returns `{group_id, top, channel, group_title, root, post, comments[]}`. `comments` is every captured message in the thread except the root, oldest first, as case-annotated cards. Nest them client-side by `reply_to_msg_id`.
  - Comment cards now also carry `channel_post` (for mirrors, the channel post id).
  - `is_mirror` marks the group's automatic copy of a channel post. The collector reads it from the message's `fwd_from.saved_from_peer` (the linked channel) and takes `channel_post` from `saved_from_msg_id`. When the channel post is itself a repost, the mirror's `fwd_*` fields still name the original source (`fwd_id` ≠ `sender_id`), so the UI shows it as a repost. Rows imported before this rule are corrected from their stored raw API dump at server start and after each comments import (audit action `backfill_mirrors`). Flipped rows drop out of comment lists and counts, and their `comment_forward` edge hits are removed.
- **Jobs:**
  - `GET /api/jobs`, `GET /api/jobs/active`, `GET /api/jobs/{id}/progress|log`.
  - `POST /api/jobs {kind: crawl|media|comments, …}`.
  - `POST /api/jobs {resume_of: <job id>}` queues a crawl that **continues** a finished one (same seeds and settings, new run folder; the earlier run folders are only read). Channels already read are skipped; failed, cut-off and never-reached ones are visited. Returns `{id}`. Errors: 404 no such job, 400 not a crawl or a bad id, 409 the job is still active, a crawl of the same seeds is already queued or running, or nothing is left to resume. The new job's `params.resume_of` chains back through earlier resumes.
  - `POST /api/jobs/{id}/stop|cancel`.
  - **Crawl job params** (beyond the form's): `resume_of`, `delay` and `retries` (passed to tgspider), `max_runtime` (default 12 h) and `max_requests` (default from the crawl's size). The app always passes the last two as safety nets.
  - **Crawl job `summary`** = the import stats plus: `phase`, `exit_code`, `parser_warnings` (count), `warnings[]` (`code, channel, message, count, page_sha256`), `failed_channels[]` (`username, error`), `remaining {crawl, classify, channels[]}` (what a resume would visit), `abort {reason, kind: stop|limit|breaker|rate_limit|crash}`, `stalled`, `resume_of`, and a `note` set **only** when something was abnormal (aborted, failures, warnings, held-back posts, failed media). **The UI shows `note` instead of the counts when it exists, so the note begins with the counts** ("12 new posts · … · stopped early: …"). A clean `done` has no note. Optional import stats keys (only when non-zero): `channels_failed, parse_warnings, media_failed, torn_lines`.
  - A crawl whose `progress.json` is not touched for 10 minutes is stopped (SIGINT, then SIGTERM, then SIGKILL) and marked `interrupted` with `stalled: true`. Judged on the monotonic clock, so a sleeping laptop never counts.
  - **`progress.json`** (read by `GET /api/jobs/{id}/progress`) gained: phases `aborted`, `failed` and `cancelled`; `abort`; `warnings[]`; `resumed_from[]`; totals `parser_warnings`, `posts_held_back`, `media_failed`; and a `waiting` object while backing off. Audit actions added on import: `channel_crawl_failed`, `crawl_parser_warning`, `media_download_failed` (each once per run, however often it is imported). A failed visit no longer rewrites a channel's `type`/`preview`; a page that lost a post's date or view count no longer blanks the stored one.
  - **UI follow-ups (not built; the redesign session owns `ui/`):** a *Resume* button on `interrupted`, `stopped`, `failed` and `cancelled` crawls in the job monitor and the Collect list (`POST /api/jobs {resume_of: id}`); list `progress.warnings[]` (code, message, channel, page hash) in `SrcJobMonitor.svelte`; make `jobSummary` show the counts together with the note (or rely on the note starting with them); optionally flag the `parser_warnings` and `posts_held_back` tiles as bad.
  - `GET /api/telegram`: login status.
  - Job kinds are `crawl` (web lane), `media | comments | discover` (logged-in lane, one at a time) and `mediascan` (local lane).
- **Settings:** `GET/POST /api/tags`, `GET/POST /api/settings`, `POST /api/exposure`, `POST /api/integrity` (verify all stored files), `POST /api/import`.

`GET /favicon.ico` serves the app icon (SVG), so browsers no longer log a 404.
