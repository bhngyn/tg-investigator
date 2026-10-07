# Telegram Investigator

A local workspace for investigating public Telegram channels. It discovers channel networks, collects and preserves posts, media and (with a login) comments, searches them across languages and spellings, reads text out of images and speech, and packages what you find as verifiable evidence. It is built for researchers, journalists and human-rights or HR investigators who need a documented method, not just search results.

> **Read this first.** Everything runs on your own Mac, and there is no telemetry. The only network traffic is: (1) requests to Telegram's public web preview (`t.me/s/...`) from your IP address, and (2) only if you log in yourself, requests to the Telegram API from your account, for comments, full-size video and channel discovery. Nothing else is contacted while it runs: no CDN, no fonts, no update checks, no cloud services. Downloading the optional speech model is a one-time manual step you run yourself. Material you collect may be graphic or illegal in some places; read **Wellbeing** and **Evidence handling** below before you start, and check the law and your organisation's rules on collecting it.

## Requirements

| Need | For | Notes |
|---|---|---|
| macOS | everything | OCR and speech need **Apple silicon** (Apple Vision and MLX). The core app is plain Python and should run elsewhere, but that is untested. |
| Python 3.12 or newer | the app | Developed on 3.14. |
| `ffmpeg`, `exiftool` | media scanning | `brew install ffmpeg exiftool`. Runs after every media download (perceptual hashes, embedded metadata). Without exiftool the embedded-metadata step is skipped. |
| Node 20+ | rebuilding the UI only | The built UI ships in `investigator/web/`. You only need Node to change `ui/`. |
| `osmium-tool` | rebuilding the dictionary only | `brew install osmium-tool`. `dictionary.db` ships prebuilt. |

Disk: the app itself is small. `dictionary.db` is about 50 MB (included). The optional OCR/speech worker is about 1.3 GB, plus the Whisper model, about 1.6 GB. Collected evidence grows with the media you download.

## Install and start

```bash
git clone <this repository> && cd <the folder you cloned into>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m investigator
```

A browser tab opens at http://127.0.0.1:8765/. Stop the app with Ctrl-C.

| Flag | Meaning |
|---|---|
| `-w, --workspace PATH` | Workspace folder (default `workspace/`, or `$INVESTIGATOR_HOME`). Use a separate one per investigation. |
| `-p, --port N` | Port (default 8765). |
| `--no-browser` | Don't open a browser tab. |
| `--import RUN_DIR ...` | Import finished crawl folders from the command line. Add `--network-only` to keep only the channel network. |

The workspace holds `investigator.db`, `store/` (evidence files, named by SHA-256), `runs/` (raw crawl output) and `derived/` (OCR and transcripts). It is plain files: back it up and encrypt the disk.

Optional: [OCR and speech setup](#ocr-and-speech), [logged-in features](#comments-and-logged-in-features).

### First run

1. **Create a case.** A case is a named set of channels with its own triage, tags and notes. Data is collected once into a shared archive and can belong to several cases.
2. **Add channels** (Sources, *Add channels*). Paste handles or `t.me` links, one per line. Each is resolved with a live preview so you can check it is the channel you mean.
3. **Choose steps per channel:** posts and media, comments, similar channels, a post watch (re-crawl on a schedule), a comment schedule. Steps that use your Telegram login ask for confirmation first and name what will run.
4. **Collect.** Jobs run one at a time and are imported when they finish. The Collect page shows progress, rate limits and a log. **Stop after this channel** finishes the current item and imports; **Stop now** imports what was collected.
5. **Triage** in the Inbox, **search**, and **export** evidence when you have something worth keeping.

Public-preview crawls read only recent history (about 20 posts per page, 10 pages by default). Raise the page count for more.

## Using it

Terms: a **post** is a channel message, a **comment** is a message in a channel's linked discussion group, an **item** is either. The **Inbox** is the review queue. The **case switcher** in the top bar scopes every page; **Whole archive** shows the shared pool with its own separate triage.

The left rail has four groups:

- **Triage:** Inbox, Search, Search history (with Saved searches), Comments.
- **Sources:** Channels, Add channels, Discovery, Collect.
- **Analyse:** Network, Analysis (Changes, Coordination, Spread, Media, Coordinates, Places mentioned, Identifiers, People, Community).
- **Evidence:** Overview, Alerts, Incidents, Exports, Settings.

### Triage and the Inbox

The Inbox shows one item at a time. `r` relevant, `v` needs verification, `i` irrelevant, `u` undo, `j`/`k` next/previous, `n` note, `o` details, space reveals media, `t` then a tag key toggles a tag. Verdicts, tags and notes made inside a case belong to that case and never leak into others. **New since last visit** narrows the queue to what arrived after your previous look. `/` jumps to a page's search box; `g` then a letter jumps between pages.

Post details show the full text, media, earlier versions, the evidence trail, where the same post was forwarded and where identical media appears elsewhere.

### Search

Free-text search over posts and comments (and, optionally, earlier versions and the OCR / speech text of images and video, in posts and in comment sections). Filters cover date, channel, media, forwards, review status, tag and views.

| Syntax | Meaning |
|---|---|
| `word1 word2` | Both must appear (AND). Words are prefix-matched. |
| `"exact phrase"` | The phrase, in order. |
| `a OR b` | Either. |
| `-word` | Exclude. |
| `a NEAR/5 b` | Both within 5 words of each other (`NEAR` alone means 10). Operands are words or phrases. |

- **Prefix and stem matching.** Arabic ignores diacritics and tatweel, treats أ/إ/آ/ا, ة/ه and ى/ي as equal and strips one attached prefix (`الفاشر` finds `بالفاشر`). Russian ё/е match. Inflected Russian and Ukrainian forms are stemmed. An excluded word is not stemmed, so an exclusion never hides more than you typed.
- **Obfuscation folding.** Text is normalised for matching only (stored text is never changed), so `b o m b`, `b.o.m.b`, `ca$h`, `b0mb`, stretched letters (`boooomb`) and Latin/Cyrillic look-alike letters mixed into one word still match. URLs, `@handles` and e-mail addresses are left alone.
- **Highlighting** uses the terms that matched. The *Terms in these results* panel lists the words in the results; click one to add it to the query or to the dictionary.
- **Matched-in badges** show whether a hit came from the text, an earlier version, OCR (with frame time) or speech (with timestamp). The *Search in* filter switches those sources on or off.
- **Identifiers and Places mentioned** facets filter by extracted identifiers and tagged places; **Near a place** keeps items that mention a dictionary place within a radius.

### Multilingual search and the dictionary

By default a search is expanded with equivalents from a local dictionary, shown as removable chips ("Also searching"):

- `Bakhmut` also finds `Бахмут` and the historic `Артемовск`; `El Fasher`, `Al-Fashir` and `الفاشر` are one place; `airstrike` also finds `авиаудар` and `غارة`.
- Click **×** on a chip to **drop** one alternative (it stays visible, struck through, and can be **restored**). × on a group skips the whole group. **Match other languages/spellings** turns expansion off, so the search is literal.
- Words in `"quotes"` or `-excluded` are never expanded.
- **Cyrillic transliteration works without the dictionary:** Latin input also finds its Cyrillic spelling (`Kharkiv` finds `Харків`). Arabic is not romanised: to find Arabic text, type Arabic or a dictionary place name.
- Every search records the exact expression that ran and the dictionary version. That is what makes it reproducible (see Search history). Expansion is a search aid. A matching place name is not an identification of the place.

**The dictionary** (`investigator/data/gazetteer/dictionary.db`, Settings, *Dictionary*) covers Ukraine (Russian and Ukrainian to English, including historic and Soviet/Russian names) and Sudan (Arabic to English), plus a seeded vocabulary of military, humanitarian and violation terms. Coverage caveats: small Sudanese villages are often missing an Arabic name, so check *Settings, Dictionary* for coverage numbers. You can add names, hide names or whole entries and add new terms or places. Edits are stored in the workspace, audited and undoable, and never change the shipped file.

It is read-only at runtime and nothing is downloaded. If the file is missing, only Cyrillic transliteration is available. To rebuild it (only needed to change sources): `.venv/bin/python -m investigator.gazetteer_build`, which needs `osmium` and the input downloads described in `investigator/data/gazetteer/README.md`. **When a rebuild is needed:** after an upgrade that changes the matching keys, Settings shows a red *Dictionary needs rebuilding* banner (key version mismatch). Until then, expansion may miss names.

The vocabulary **contains slurs, dehumanising terms and violent slang on purpose**, so investigators can find and document that language. It is a search vocabulary, not evidence or a statement about who uses a word.

### Search history and saved searches

Every search is logged: who, when, the parameters, the exact expression (`fts`), the dictionary version and a hash of the ordered result ids. For methodology this matters: you can show what you searched, how it was expanded and what it returned at the time.

- **Open this search** restores the parameters. **Re-run and compare** runs it again with the same expansion and lists results added and removed since. Browsing without a query is not logged.
- **Saved searches** can be **frozen** (the expansion is fixed when saved, so later dictionary edits don't change it) or **live** (re-expanded each run). *Run all* reports totals and what is new since each was last run. A saved search can become an alert.

### Alerts

Rules are checked after every job: keywords in new posts and/or comments (Search syntax), deleted posts and comments, edited posts, channel changes and comment-group admin changes, a channel newly linked by several of a case's channels, and files already seen in another channel. A rule can be limited to a case or channels and can show a macOS notification. A dry-run preview shows what it would have caught. **Keyword rules expand once, when saved** (frozen). Later dictionary edits don't change them; *Re-expand with current dictionary* is explicit.

### Identifiers

Analysis, *Identifiers* finds patterns in post and comment text: phone numbers, crypto wallets, payment cards (**masked**), military unit numbers, call signs and vehicle plates. These are **patterns found in text, claims to check, not verified identities**; some will be false positives. Snippets are hidden until you confirm a reveal (the reveal is audited). Run the backfill once after upgrading to index older material.

### Places mentioned and coordinates

- **Places mentioned** tags dictionary place names found in text and plots them offline. It is heuristic: ambiguous names (a common village name, a word that is also a place) are marked, and a tag is **not a verified location**. The **Near a place** search filter builds on this.
- **Coordinates** lists coordinates written in posts and comments (decimal, degrees-minutes-seconds, map links) as GeoJSON. They are **claims to check**. MGRS references are listed but not converted.

### Other analysis

- **Changes:** edits, deletions, channel renames and description changes, disabled previews, subscriber jumps. A post is marked **deleted** only if it was captured earlier, its message number lies inside the range the crawl actually read, and it is no longer there.
- **Coordination:** the same text, file, visually identical file or forwarded post across channels, and channel pairs that repeatedly post the same thing within an hour.
- **Spread:** everywhere one post's content went.
- **Media:** perceptual hashes (survive re-encoding and resizing) and embedded metadata (camera, GPS, dates) from the local ffmpeg/exiftool scan.
- **People and Community:** who comments where (matched by permanent Telegram ID), admins, and rough phone-country, language and activity-by-hour summaries. Needs collected comments.
- **Network:** who forwards, links to or mentions whom, in three views: amplification, shared people, discovery.
- **Discovery:** candidate channels ranked by how many of your case's channels point at them (no login), plus Telegram's *similar channels* and keyword search over public channels and posts (login).
- **Incidents:** record an event with dates, place and coordinates and how each was established, and link the posts, comments, files, channels and accounts that document it.
- **Watchlists:** re-crawl a channel's posts on a schedule (15 minutes to daily) within a re-check window (7 days by default). Media already stored isn't downloaded again.

### OCR and speech

Text in images and video frames (Apple Vision) and speech in audio and video (Whisper) become searchable. Everything runs on your Mac, offline.

1. Follow [tools/models/README.md](tools/models/README.md): create the worker venv (`tools/models/.venv`, about 1.3 GB) and download the Whisper model once by hand (about 1.6 GB, the only network step).
2. Settings, *OCR & transcription* shows status (a *Whisper model missing* pill if the model isn't there) and runs it. **Run** asks for confirmation. Nothing runs automatically unless you switch on *auto after media download* in Settings (off by default).
3. **Languages:** OCR defaults to Apple's automatic detection, which reads Arabic, Cyrillic and Latin, even mixed in one image. You can override OCR languages and set a Whisper language hint per channel. Whisper auto-detects by default.

**Comment sections are read too.** Photos and videos attached to comments (collected with *media* on in a comment collection) are read exactly like post media: Settings, *OCR & transcription* has a *Where* choice (posts and comment sections, channel posts only, or comment sections only), and a hit shows on the comment card as an OCR or speech badge with the matching line. A file that appears in both a post and a comment is read once. Comment search has *Search in* boxes for comment text, OCR and speech (names are opt-in).

Results are stored as derived files keyed by the media's SHA-256 under `workspace/derived/`. Originals are never modified. The database rows can be rebuilt from those files.

**Machine-read text is a search aid, not a transcript.** OCR and Whisper make mistakes (Arabic dialect, handwriting, poor audio, small or stylised text). **Whisper can invent plausible sentences over music, chanting, silence or noise.** Always check against the original before you rely on or quote it. Video OCR reads keyframes only (one per 5 seconds by default), so text that appears between keyframes is missed.

### Evidence packages

Exports (case, incident or selection) produce a zip:

- `items.json` / `items.csv`: every item with its collection record and your review.
- `media/`: original files named by SHA-256, byte-identical to what was downloaded.
- `pages/` and `api/`: the raw preview pages and API responses the items were read from, named by their SHA-256.
- `capture_log.csv`, `audit.csv`, `manifest.json`, `incidents.json`.
- `search_log.csv`: the searches run in the case (query, exact expression, dictionary version, result count and result hash).
- `derived/`: the OCR and speech records for the exported media (`ocr.json`, `whisper.json`). Each is checked
  against the hash recorded when it was made. They are machine output, so check any quote against the media itself.
- `SHA256SUMS`: check with `shasum -a 256 -c SHA256SUMS`. Every line should end in `OK`.

The package's own hash is recorded, and Settings, *Verify all files* re-checks the whole store later. If you used dictionary expansion or place tagging, credit the data sources in your report (see [NOTICE.md](NOTICE.md): OpenStreetMap and GeoNames).

### Comments and logged-in features

These need a Telegram login that **you** create and run yourself; the web UI never asks for credentials. They are clearly marked and always ask for confirmation.

**Setup (once, in a terminal):** create an app at https://my.telegram.org (*API development tools*) to get an api_id and api_hash, then run `.venv/bin/python tgmedia.py login`. Credentials and the session are stored in `~/.config/tgmedia/`, owner-only, outside the project. Use a **secondary account**: accounts that bulk-download can be rate-limited or restricted. See *tgmedia* below.

- **Full-size video:** the public preview refuses most videos ("Media is too big"); the logged-in API can fetch the originals.
- **Comments:** a channel's comments live in a linked discussion group that only the logged-in account can read. It is read **without joining**, so its admins can't see you. The first run takes the newest N messages (200 by default); later runs take everything new. A schedule (5 minutes to daily) catches comments before they are deleted; a schedule due while the app was closed runs once on reopening.
- **Deletions and edits:** each run re-checks the last 7 days (Settings, *Comment collection*). A comment is marked deleted only when it was captured earlier and Telegram no longer returns it, and the page shows the window in which it disappeared. Comments deleted before the first capture can't be recovered. Edited text is kept under *Earlier versions*.
- **Accounts:** senders are recorded by permanent Telegram ID with name, username and (where privacy allows, a few percent) phone. Every change is a dated snapshot. Recording phones can be turned off in Settings. Comment-group admin lists are recorded with roles; the channel's own admin list is not readable unless you are an admin.
- **Discovery (login):** similar channels and search over public channels and posts, read-only: nothing is joined, followed or sent.
- Logged-in jobs share one session and take turns; public crawls run alongside them.

## Evidence handling

- **Hashes for everything captured.** Every media file is SHA-256 hashed and copied into `workspace/store/`, named by its hash and read-only. Every raw Telegram page fetched during a crawl is kept (gzip) with the SHA-256 of the HTML as received, and each post links to its page.
- **Comments** are also kept exactly as the API returned them (JSON lines, gzip) with a SHA-256. Each comment links to that raw copy and records which account collected it.
- **Capture log.** Each item records URL, UTC capture time, method (public preview or logged-in API), tool and job, and for logged-in collection the account used.
- **Nothing is overwritten.** Re-crawling adds a new capture. Changed text keeps its earlier version.
- **Audit log.** Every review decision, tag, note, status change, export and job is recorded with who and when. Set your name in Settings first: everything is attributed to it.
- **Integrity check.** Settings, *Verify all files* re-hashes the store against the recorded values.
- **Derived data** (perceptual hashes, OCR, transcripts, place and identifier tags) is separate from originals and can be rebuilt. It is a working aid, not evidence in itself.

## Wellbeing

- **All media is blurred until clicked** (configurable). Telegram's own "sensitive" flag misses most graphic content, so the default blurs everything. Videos load only on click and start muted. Optional grayscale for revealed media.
- **The exposure meter** in the sidebar counts media you have revealed today. Treat it as a prompt to stop.
- **Text can be as distressing as media.** Comments are shown unfiltered. OCR and transcript text sits behind a content note ("Show transcript/OCR text"), but graphic wording can still appear in search excerpts. The built-in vocabulary contains slurs and violent slang.
- Take breaks and work with a colleague where you can. The Berkeley Protocol on Digital Open Source Investigations has guidance on vicarious trauma.

## Security model

- **Local only:** the server listens on 127.0.0.1.
- **Other websites can't use it:** every change needs a token issued when the app starts, and requests with a foreign `Host` header are rejected (blocks DNS rebinding).
- **Nothing is loaded from the internet:** no CDN scripts or fonts, and no images fetched from Telegram's servers.
- **Links inside posts are shown as text**, so you never visit them by accident. The only outbound link is an explicit "Open in Telegram" button.
- **Your IP address.** Public-preview requests come from this Mac's IP; logged-in requests come from your Telegram account. Consider a VPN and a secondary account.
- **The Telegram login stays in `~/.config/tgmedia/`**, owner-only. The UI never asks for credentials.
- **Other software on this Mac.** Reads on the local API need no token, so any process running as you can read collected data. Evidence is stored unencrypted in the workspace: use disk encryption and an account you trust.

## Known limits

- **The layout check is a canary, not a guarantee.** It catches a preview that stops parsing; it can't know about a field that quietly moved inside a post it can still read. It assumes real preview pages end with `</html>` (a page that doesn't is retried once, then flagged). Read the parser warnings of a job before relying on a crawl. Treat the thresholds (for example "views missing on more than half the posts") as tuned on a handful of channels.
- **A channel cut off part-way** (the run stopped mid-read) has no finished record, so a resume reads it again from its first page. Its posts are updated on import, not duplicated, but its media is downloaded again.
- Deletion detection only covers the pages a crawl reads. Posts older than the re-check window, or deleted before first capture, can't be caught. Telegram's preview occasionally hides a post that still exists; if it reappears, the flag is cleared and the audit log says so.
- Public-preview crawls see only recent history, only channels with web preview enabled, and only small videos (larger ones need the logged-in tools). Counts on the preview are rounded.
- Perceptual hashes match the same picture or footage, not the same scene filmed twice. Text matching is exact after normalising, so it catches copies and light edits, not paraphrases.
- **No machine translation.** OCR and speech are search aids that make mistakes (see above). Video OCR reads keyframes only. Files that are not images or video (documents, stickers) are not read.
- **Place and identifier tagging is heuristic.** Ambiguous names, false positives and misses are expected. Coordinates come from text as written. Known examples:
  - "قناة الجزيرة" (the Al Jazeera TV channel) gets tagged as the Gezira region.
  - The Russian adverb "ровно" ("exactly") is never tagged as Rivne, so Russian mentions of that city are missed.
  - A few source records carry the wrong country, e.g. Amhara (Ethiopia) under Sudan.
  - A single Latin-script word is only tagged when it matches a town or larger. Occasional odd matches remain
    (for example "Kiwi" and "Kyiv" share a key).
  - Treat a place mention as a lead to check, and use "Only unambiguous" when counts matter.
- **Dictionary coverage is uneven:** Ukraine is far denser than Sudan, small Sudanese villages often lack Arabic names, and Arabic is not romanised for Latin input. Only Ukraine and Sudan are covered.
- *Community* phone countries come from the calling code (where the SIM was issued, not where the person is) and only for accounts that show a number. Languages are guessed from the alphabet and common words, rough for short comments.
- Overlap analysis only covers sections you've collected.
- Importing a large job briefly locks the database, so the UI may pause for a few seconds.
- Single user, single machine. OCR and speech run on Apple-silicon macOS only.

## Data and licences

- **Project licence:** proprietary, all rights reserved. Private; not licensed for use, copying or redistribution.
- **`dictionary.db` is a derivative of OpenStreetMap (© OpenStreetMap contributors, ODbL 1.0) and GeoNames (CC BY 4.0).** The database file is offered under the **ODbL 1.0** regardless of the code licence: if you redistribute it or a database built from it, you must keep the attribution and offer it under the ODbL (share-alike). GeoNames requires attribution. Full details, dependency licences and the note on the slur lexicon are in [NOTICE.md](NOTICE.md); build sources and hashes are in `investigator/data/gazetteer/README.md`.
- Attribution to carry with reports that used place expansion: © OpenStreetMap contributors, ODbL 1.0, https://www.openstreetmap.org/copyright; GeoNames, CC BY 4.0, https://www.geonames.org.

## For maintainers: before publishing

- **Never publish** `workspace*/` (real evidence), every `*_run/` crawl folder (including the development crawl already in this tree), `tgspider_out/`, `tools/models/.venv`, `.venv`, `ui/node_modules`, raw OSM/GeoNames downloads (`investigator/data/gazetteer/raw/`), `.claude/` (local paths), or anything from `~/.config/tgmedia/`. Move crawl folders out of the project directory rather than relying on `.gitignore`.
- Publish from a **fresh `git init`**, not by zipping the folder. Then run `git status --ignored` and `git ls-files | xargs du -ch | tail -1` and check nothing surprising is tracked. `dictionary.db`, the lexicons and the curated place lists are not included; build them locally.
- Search the tree for phone numbers, tokens and personal paths (`grep -rn "/Users/"`).
- The channel names in these docs are public channels used as examples of syntax, not a statement about them. Prefer neutral placeholders in anything you add.
- Development notes for the UI are in [UI.md](UI.md); the local JSON API is in [API.md](API.md).

---

# Command-line tools

The UI drives these scripts. They also work on their own, from the project folder. The logged-in ones (tgmedia, tgcomments, tgdiscover) need the login from *Comments and logged-in features* above.

## tgspider

Find Telegram channels by spidering out from one or more seed channels. No login is needed: it reads Telegram's public web preview (`https://t.me/s/<channel>`).

For each channel it reads, it records the other channels it points to:

| Signal | What it is |
|---|---|
| `forward` | a "Forwarded from X" post |
| `link` | a `t.me/X` link in a post, button or link preview |
| `mention` | `@X` in post text |
| `description` | a link or `@X` in the channel's bio |

While it crawls, it also **saves every post it reads** (text, date, views, reactions, forwards, links, media) and can optionally download photos and videos. This costs no extra page requests.

Each username it finds is checked and labelled `channel`, `group`, `bot`, `user` or `unknown`. Only channels get crawled further.

Uses only the Python standard library (3.9+ is enough).

### Usage

```bash
# One channel: read its posts, list what it links to
python3 tgspider.py example_channel

# Spider: crawl the seed, then crawl every channel it links to
python3 tgspider.py example_channel --depth 2

# Also download photos (and video thumbnails) as it crawls; use --media all for videos too
python3 tgspider.py example_channel --depth 2 --media photos

# A list of seeds (one per line; @name, name or t.me link; # for comments)
python3 tgspider.py -f seeds.txt --depth 2 -o example_run
```

| Option | Default | Meaning |
|---|---|---|
| `-d, --depth` | 1 | Hops to crawl. 1 = seeds only, 2 = seeds + what they link to, and so on. Names one hop past the last crawled layer are still classified. |
| `-p, --pages` | 10 | Preview pages per channel (~20 posts each, newest first). |
| `-m, --max-channels` | 50 | Stop crawling after this many channels. |
| `--max-classify` | 500 | Limit on outer-edge names to classify per run (0 = no limit). |
| `--no-classify-leaves` | off | Skip classifying the outer edge (faster, less informative). |
| `--no-posts` | off | Only discover channels; don't save posts. |
| `--media` | `none` | `photos` = photos + video thumbnails, `all` = also videos. |
| `--max-video-mb` | 50 | Skip videos bigger than this. |
| `--delay` | 1.5 | Seconds between requests. Keep it ≥1 or Telegram may rate-limit you. It widens by itself while Telegram pushes back (429 or 5xx) and eases back after 15 clean requests. |
| `--retries` | 5 | Retries of a failed request (waits of 2, 4, 8 … seconds, up to 5 minutes) before that channel is given up on. |
| `--max-runtime` | 0 | Stop cleanly after this many seconds (0 = no limit). The app passes 12 hours. |
| `--max-requests` | 0 | Stop cleanly after this many requests to Telegram (0 = no limit). The app passes a generous cap based on the crawl's size. |
| `--resume-from RUN_DIR` | | Continue an earlier run **folder** (repeat for a chain, oldest first) without touching it: channels it finished are skipped, ones that failed, were cut off or never reached are visited, and new captures go to `--out`. The app's `resume_of` job option uses this (see API.md). |
| `-o, --out` | `tgspider_out` | Output folder. |
| `--report-only` | | Rebuild the CSVs from an existing folder without fetching anything. |
| `--state` | | Re-crawl state from the investigator (JSON): per channel, `recheck_min_id` (stop paging once past it) and `have_media_ids` (don't download these again). Each crawled channel's `covered` id range is recorded in `nodes.jsonl` for deletion detection. |

**Resuming and going deeper:** re-run with the same `--out` folder (or `--resume-from`, above, to continue into a new folder). Anything already checked is skipped. You can raise `--depth`, `--pages` or `--max-channels`, or add seeds, and it continues from where it stopped. Ctrl-C saves progress.

**When Telegram pushes back or the page layout changes.** A crawl never fails silently:

- *Rate limits and outages.* A failed channel is recorded as an error and retried by the next run or a resume, never over and over inside the same run. The run ends cleanly (phase `aborted`, everything collected kept) when Telegram asks us to wait more than 10 minutes, after 4 requests in a row fail for good, or at `--max-requests` / `--max-runtime`. A `STOP` file is noticed even while waiting to retry. SIGINT and SIGTERM save progress and write the reports.
- *Layout changes.* Every parser reads less when Telegram renames something, so each page is checked against what it visibly contains. A page that should have posts but yields none or few, posts without dates, a missing title, subscriber count or channel id, an unrecognised page, a page cut off before `</html>` and paging that goes nowhere each become a **parser warning** in `progress.json` (`warnings[]`, with the SHA-256 of the raw page that tripped it) and in the job summary. A channel with a warning gets no deletion check from that crawl. Posts from a page that lost posts, dates or text are **not saved** (the raw page is), so a broken parse can't overwrite good earlier captures; fix the parser and re-crawl.
- *Media.* Files download to a temporary name and are kept only when complete (length matches, not an error page). Expired links aren't retried. A file that could not be fetched is recorded on its post (`media_failed`), counted, and audited on import.

A run takes roughly `(channels × pages + names classified) × delay` seconds. For example, depth 2 with 20 channels at 10 pages is about 5–6 minutes.

### Output (in the `--out` folder)

- **`channels.csv` / `channels.json`**: one row per username, seeds first, then channels ranked by how many crawled channels point to them (`linked_from`) and how often (`total_signals`). It includes the title, subscriber count, type, the crawl depth where it was found, and `linked_from_list`.
- **`posts.csv`**: one row per post from every crawled channel, with URL, date, `edited`, views, full text, forwarded-from (username, name, original post), reply-to, hashtags, links, media types, the `sensitive` flag (Telegram's blur), photo/video URLs, `media_files` and reactions (per emoji plus total). `posts.jsonl` has the same data with lists kept as lists.
- **`media/<channel>/`**: downloaded files named `<post id>_<n>_<photo|thumb|video>.<ext>` (with `--media`).
- **`edges.csv`**: `Source → Target` per signal type with a count (`Weight`), first and last seen dates, and up to 5 example post links. It can be imported directly into Gephi as an edge table.
- `nodes.jsonl` / `edges.jsonl` / `posts.jsonl`: raw crawl state used for resuming. Posts are written page by page, so an interrupted crawl keeps what it read, and resuming never duplicates a post.

## tgmedia: full-size media with a logged-in account

The public preview refuses most videos ("Media is too big"). `tgmedia.py` downloads the originals through Telegram's logged-in API, for the posts a `tgspider.py` run already found. Everything else stays no-login.

**Setup (once, by the account owner, in a terminal):**

1. Install the app's requirements into the venv (see *Install and start* above): `.venv/bin/pip install -r requirements.txt`. Telethon is only needed for the logged-in tools.
2. Go to https://my.telegram.org, open **API development tools** and create an app (any name). Note the **api_id** and **api_hash**.
3. Log in:
   ```bash
   .venv/bin/python tgmedia.py login
   ```
   It asks for the api_id and api_hash (the hash is typed hidden) and saves them. Then it asks for your phone number, the code Telegram sends you and your 2FA password. Both the credentials and the login session are stored in `~/.config/tgmedia/`, outside this folder so they don't end up in iCloud or git. Only your user account can read them.

**After that nothing prompts**, so the tool can be run unattended:

```bash
.venv/bin/python tgmedia.py status                       # which account is logged in
.venv/bin/python tgmedia.py -o tgspider_out --dry-run     # what would be downloaded
.venv/bin/python tgmedia.py -o tgspider_out               # download
python3 tgspider.py <same seeds> -o tgspider_out --report-only   # put results into posts.csv
.venv/bin/python tgmedia.py logout                        # end the session (also on Telegram's side)
```

If the login is missing or has been revoked, commands stop with a message instead of waiting for input. `TG_API_ID` / `TG_API_HASH` environment variables override the saved credentials.

| Option | Default | Meaning |
|---|---|---|
| `--what` | `videos` | `videos`: the videos the preview refused. `missing`: all media in those posts. `all`: full-res originals of every post's media. |
| `-c, --channels` | all | Only these channels. |
| `--max-mb` | 500 | Skip files bigger than this (0 = no limit). |
| `-n, --limit` | all | Stop after this many posts. |
| `--retry-errors` | off | Retry posts that failed on an earlier run. |

Files are saved as `media/<channel>/<post id>_<n>_full.<ext>`, and every item of an album is downloaded. Each post's result goes into `media_full.jsonl`, which the next `--report-only` adds to `posts.csv` as `media_files_full` and `media_full_status`. Re-running skips posts that are already done.

**Account safety:**
- Use a **secondary Telegram account**. Accounts that bulk-download from many channels can get rate-limited or restricted.
- The script waits out Telegram's rate limits (the "flood wait" messages) automatically.
- `~/.config/tgmedia/tgmedia.session` holds your login. Anyone with that file can use your account, so don't share it or put it in cloud-synced folders. End it with `tgmedia.py logout`, or under Telegram → Settings → Devices.

### Limits

- Only public channels **with web preview enabled** can be read. Channels with preview disabled are still classified, and marked in `note`.
- Only recent history is read (`--pages` × ~20 posts). Raise `--pages` to go further back.
- Channels that never forward, link or mention (some official or one-way channels) produce nothing to follow. Finding related channels for those would need the logged-in API (Telegram's "similar channels"), which this tool deliberately doesn't use.
- Forwards from channels whose source is hidden or private aren't linked, so they can't be followed.
- **Videos are only downloadable when they're small.** The public preview only provides a download link for small videos (roughly under 10–20 MB; in testing even a 1-minute HD clip was refused). Others show "Media is too big", which is recorded in `media_unavailable`, and only their thumbnail is saved. Use `tgmedia.py` (above) to get the full files.
- Media URLs in the posts files are temporary links from Telegram's servers and **expire**. Use `--media` if you need the files themselves.
- A channel that has already been crawled isn't re-read on resume, so posts made after the crawl aren't picked up. Use a fresh `--out` folder for a new snapshot.
- Subscriber, view and reaction counts on the preview are rounded (e.g. `275K`).

## tgcomments

Collect a channel's comment section with the logged-in account (log in once with `tgmedia.py login`). It reads the linked discussion group without joining it.

```bash
# newest 200 messages of the comment section, text only
.venv/bin/python tgcomments.py example_channel -o comments_run

# with photos (files over 20 MB skipped)
.venv/bin/python tgcomments.py example_channel -o comments_run --media photos --max-mb 20
```

| Option | Default | Meaning |
|---|---|---|
| `--limit` | 200 | First collection: newest N messages (0 = all). |
| `--state` | | `state.json` with `max_msg_id` (fetch only newer messages, oldest first) and `recheck_ids` (check whether these still exist). The investigator writes this for each run. |
| `--max-new` | 5000 | Later collections: at most N new messages per run. |
| `--media` | `none` | `photos`, or `all` for photos, videos and files. |
| `--max-mb` | 50 | Skip files bigger than this. |
| `--max-files` | 0 | Stop downloading after N files (0 = no cap). |
| `--no-phones` | off | Don't record phone numbers. |
| `--no-admins` | off | Skip the admin list. |

Output: `group.json` (channel, group, account), `messages.jsonl`, `rechecks.jsonl`, `users.jsonl`, `chats.jsonl`, `admins.jsonl`, `raw/messages.jsonl` (the API's own JSON, kept as evidence), `media/`, `progress.json`. Import with `.venv/bin/python -m investigator --import <folder>`.

## tgdiscover

Find channels with the logged-in account (read-only: nothing is joined, followed or sent).

```bash
echo '{"method": "similar", "channels": ["example_channel"]}' > spec.json
.venv/bin/python tgdiscover.py --spec spec.json -o discover_run
```

Methods:
- `similar`: Telegram's similar channels for each entry in `channels`.
- `search_channels`: public channels and groups whose name matches each entry in `queries`.
- `search_posts`: public posts matching each query.

Output: `discover.json`, `discoveries.jsonl`, `raw/responses.jsonl` (the API's own JSON, kept as evidence) and `progress.json`.

## mediascan

Perceptual hashes and embedded metadata for media files, using ffmpeg and exiftool on this laptop (no network).

```bash
echo '[{"sha256": "…", "path": "/path/to/file.mp4", "mime": "video/mp4"}]' > files.json
python3 mediascan.py --list files.json -o scan_run
```

Output: `mediascan.jsonl`, one row per file: `phashes` (16-hex dHash; videos at 10/50/90% of their length), `meta` (duration, size, codec), `embedded` (camera, GPS, dates, software).
