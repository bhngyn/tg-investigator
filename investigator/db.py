"""
Database layer: schema, text normalisation for search, and queries.

One SQLite file per workspace. Investigator work (review status, tags, notes)
lives in its own columns/tables and is never touched by re-imports.
"""

import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timezone

from . import translit

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    username      TEXT PRIMARY KEY,
    tg_id         INTEGER,
    title         TEXT,
    type          TEXT,              -- channel / group / bot / user / unknown
    subscribers   INTEGER,
    description   TEXT,
    preview       INTEGER,
    status        TEXT NOT NULL DEFAULT 'candidate',  -- seed / watched / candidate / dismissed
    status_reason TEXT,
    first_seen    TEXT,
    last_crawled  TEXT,
    posts_scanned INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS channels_tg_id ON channels(tg_id);

CREATE TABLE IF NOT EXISTS posts (
    id                INTEGER PRIMARY KEY,
    channel           TEXT NOT NULL,
    msg_id            INTEGER NOT NULL,
    url               TEXT,
    date              TEXT,
    edited            INTEGER,
    views             INTEGER,
    text              TEXT,
    fwd_username      TEXT,
    fwd_name          TEXT,
    fwd_url           TEXT,
    reply_to_url      TEXT,
    author_signature  TEXT,
    hashtags          TEXT,          -- JSON lists / objects below
    links             TEXT,
    link_preview_url  TEXT,
    media_types       TEXT,
    sensitive         INTEGER,
    video_durations   TEXT,
    media_unavailable TEXT,
    reactions         TEXT,
    reactions_total   INTEGER,
    first_captured    TEXT,
    last_captured     TEXT,
    capture_count     INTEGER DEFAULT 1,
    review_status     TEXT NOT NULL DEFAULT 'unreviewed', -- unreviewed / relevant / irrelevant / verify
    reviewed_at       TEXT,
    page_sha256       TEXT,          -- raw page this capture was read from
    UNIQUE(channel, msg_id)
);
CREATE INDEX IF NOT EXISTS posts_channel_date ON posts(channel, date);
CREATE INDEX IF NOT EXISTS posts_date ON posts(date);
CREATE INDEX IF NOT EXISTS posts_fwd ON posts(fwd_username);
CREATE INDEX IF NOT EXISTS posts_review ON posts(review_status);

-- Search index: normalised text + prefix-stripped Arabic stems
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    norm, stems, meta, tokenize = 'unicode61 remove_diacritics 2');

-- Search index over earlier (edited-away) versions of a post's text; rowid = post_versions.id
CREATE VIRTUAL TABLE IF NOT EXISTS post_versions_fts USING fts5(
    norm, stems, tokenize = 'unicode61 remove_diacritics 2');

-- OCR / speech text of media (written by the media-text pipeline); its search index is media_text_fts
CREATE TABLE IF NOT EXISTS media_text (
    id      INTEGER PRIMARY KEY,
    sha256  TEXT NOT NULL,
    kind    TEXT NOT NULL,           -- ocr / speech
    seq     INTEGER,
    t_start REAL,
    t_end   REAL,
    text    TEXT,
    lang    TEXT,
    conf    REAL,
    bbox    TEXT
);
CREATE INDEX IF NOT EXISTS media_text_sha ON media_text(sha256);
CREATE VIRTUAL TABLE IF NOT EXISTS media_text_fts USING fts5(
    norm, stems, tokenize = 'unicode61 remove_diacritics 2');

-- Earlier versions of a post's text, kept when a re-capture finds it changed
CREATE TABLE IF NOT EXISTS post_versions (
    id          INTEGER PRIMARY KEY,
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    captured_at TEXT,
    text        TEXT,
    views       INTEGER,
    edited      INTEGER
);

CREATE TABLE IF NOT EXISTS edges (
    source     TEXT NOT NULL,
    target     TEXT NOT NULL,
    type       TEXT NOT NULL,       -- forward / link / mention / description
    weight     INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT,
    last_seen  TEXT,
    PRIMARY KEY (source, target, type)
);
CREATE INDEX IF NOT EXISTS edges_target ON edges(target);

-- Individual signal occurrences, so edge weights can be rebuilt without double counting
CREATE TABLE IF NOT EXISTS edge_hits (
    source  TEXT NOT NULL,
    target  TEXT NOT NULL,
    type    TEXT NOT NULL,
    msg_id  INTEGER NOT NULL DEFAULT -1,
    date    TEXT,
    PRIMARY KEY (source, target, type, msg_id)
);

CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY,
    post_id     INTEGER NOT NULL REFERENCES posts(id),
    kind        TEXT NOT NULL,       -- photo / thumb / video (web preview) / full (logged-in original)
    idx         INTEGER,
    sha256      TEXT NOT NULL,
    path        TEXT NOT NULL,       -- relative to the store
    bytes       INTEGER,
    mime        TEXT,
    captured_at TEXT,
    method      TEXT,                -- web / api
    source_url  TEXT,
    UNIQUE(post_id, kind, idx, sha256)  -- a re-capture with different bytes is kept alongside
);
CREATE INDEX IF NOT EXISTS media_sha ON media(sha256);

-- Capture log: one row per thing collected, with its hash
CREATE TABLE IF NOT EXISTS captures (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,       -- page / media / import
    url         TEXT,
    sha256      TEXT,
    path        TEXT,
    bytes       INTEGER,
    captured_at TEXT,
    method      TEXT,
    tool        TEXT,
    account     TEXT,
    job_id      INTEGER,
    run_dir     TEXT,
    note        TEXT,
    UNIQUE(kind, sha256, url, captured_at)
);

CREATE TABLE IF NOT EXISTS tags (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    category    TEXT,
    color       TEXT,
    description TEXT,
    key         TEXT                 -- keyboard shortcut in review
);
CREATE TABLE IF NOT EXISTS post_tags (
    post_id  INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    added_by TEXT,
    added_at TEXT,
    PRIMARY KEY (post_id, tag_id)
);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY,
    post_id    INTEGER REFERENCES posts(id),
    channel    TEXT,
    text       TEXT NOT NULL,
    author     TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY,
    ts     TEXT NOT NULL,
    actor  TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,         -- crawl / media / import
    params    TEXT,
    status    TEXT NOT NULL,         -- queued / running / importing / done / failed / cancelled
    created   TEXT,
    started   TEXT,
    finished  TEXT,
    run_dir   TEXT,
    log_path  TEXT,
    pid       INTEGER,
    summary   TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ---- Comments (logged-in API) ------------------------------------------------
-- A channel's comment section is a separate "discussion" group. It usually has no
-- username, so it's keyed by its numeric id and tied back to the channel.
CREATE TABLE IF NOT EXISTS comment_groups (
    group_id       INTEGER PRIMARY KEY,
    channel        TEXT,             -- linked channel's username
    channel_id     INTEGER,
    title          TEXT,
    username       TEXT,
    participants   INTEGER,
    hidden_members INTEGER,
    max_msg_id     INTEGER DEFAULT 0,  -- newest message collected (incremental polls start here)
    first_seen     TEXT,
    last_checked   TEXT
);
CREATE INDEX IF NOT EXISTS comment_groups_channel ON comment_groups(channel);

CREATE TABLE IF NOT EXISTS comments (
    id               INTEGER PRIMARY KEY,
    group_id         INTEGER NOT NULL,
    msg_id           INTEGER NOT NULL,
    channel          TEXT,           -- linked channel's username
    date             TEXT,
    edit_date        TEXT,
    text             TEXT,
    sender_type      TEXT,           -- user / channel / group (anonymous admin) / none
    sender_id        INTEGER,
    post_author      TEXT,           -- signature shown on anonymous admin messages
    reply_to_msg_id  INTEGER,
    thread_top_id    INTEGER,        -- group message the thread hangs off (the post's mirror)
    is_mirror        INTEGER DEFAULT 0,  -- automatic copy of a channel post, not a comment
    channel_post     INTEGER,        -- for mirrors: the channel post id
    fwd_type         TEXT,           -- user / channel / hidden
    fwd_id           INTEGER,
    fwd_name         TEXT,
    fwd_username     TEXT,
    fwd_post         INTEGER,
    fwd_date         TEXT,
    via_bot_id       INTEGER,
    grouped_id       INTEGER,
    media_types      TEXT,
    reactions_total  INTEGER,
    first_captured   TEXT,
    last_captured    TEXT,
    last_seen        TEXT,           -- last time the message was confirmed to still exist
    capture_count    INTEGER DEFAULT 1,
    deleted_detected TEXT,           -- first check that found it gone (deleted between last_seen and this)
    raw_sha256       TEXT,           -- API dump this capture was read from
    UNIQUE(group_id, msg_id)
);
CREATE INDEX IF NOT EXISTS comments_channel_date ON comments(channel, date);
CREATE INDEX IF NOT EXISTS comments_sender ON comments(sender_id);
CREATE INDEX IF NOT EXISTS comments_thread ON comments(group_id, thread_top_id);
CREATE INDEX IF NOT EXISTS comments_fwd ON comments(fwd_id);

CREATE VIRTUAL TABLE IF NOT EXISTS comments_fts USING fts5(
    norm, stems, meta, tokenize = 'unicode61 remove_diacritics 2');

CREATE TABLE IF NOT EXISTS comment_versions (
    id          INTEGER PRIMARY KEY,
    comment_id  INTEGER NOT NULL REFERENCES comments(id),
    captured_at TEXT,
    text        TEXT,
    edit_date   TEXT
);

CREATE TABLE IF NOT EXISTS comment_media (
    id          INTEGER PRIMARY KEY,
    comment_id  INTEGER NOT NULL REFERENCES comments(id),
    kind        TEXT NOT NULL,       -- photo / video / file
    idx         INTEGER,
    sha256      TEXT NOT NULL,
    path        TEXT NOT NULL,
    bytes       INTEGER,
    mime        TEXT,
    captured_at TEXT,
    UNIQUE(comment_id, kind, idx, sha256)
);
CREATE INDEX IF NOT EXISTS comment_media_sha ON comment_media(sha256);

-- Telegram accounts seen in comment sections, keyed by their permanent numeric id.
-- Names/usernames/phones change: every change is kept in tg_user_snapshots.
CREATE TABLE IF NOT EXISTS tg_users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    phone       TEXT,
    bot         INTEGER,
    premium     INTEGER,
    deleted     INTEGER,
    scam        INTEGER,
    fake        INTEGER,
    verified    INTEGER,
    first_seen  TEXT,
    last_seen   TEXT
);
CREATE INDEX IF NOT EXISTS tg_users_username ON tg_users(username);

CREATE TABLE IF NOT EXISTS tg_user_snapshots (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    captured_at TEXT,
    username    TEXT,
    first_name  TEXT,
    last_name   TEXT,
    phone       TEXT,
    bot         INTEGER,
    premium     INTEGER,
    deleted     INTEGER,
    scam        INTEGER,
    fake        INTEGER,
    verified    INTEGER
);
CREATE INDEX IF NOT EXISTS tg_user_snapshots_user ON tg_user_snapshots(user_id);

-- Channels/groups referenced from comments (senders, forward sources) by numeric id
CREATE TABLE IF NOT EXISTS tg_chats (
    chat_id    INTEGER PRIMARY KEY,
    username   TEXT,
    title      TEXT,
    broadcast  INTEGER,
    first_seen TEXT,
    last_seen  TEXT
);

CREATE TABLE IF NOT EXISTS group_admins (
    group_id    INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    role        TEXT,               -- creator / admin
    rank        TEXT,               -- custom title
    rights      TEXT,               -- JSON list of granted rights
    promoted_by INTEGER,
    since       TEXT,               -- when Telegram says they were promoted
    first_seen  TEXT,
    last_seen   TEXT,
    removed_detected TEXT,          -- first check where they were no longer listed
    PRIMARY KEY (group_id, user_id)
);

-- Recurring comment collection ("poll this channel's comments every N minutes")
CREATE TABLE IF NOT EXISTS schedules (
    id           INTEGER PRIMARY KEY,
    channel      TEXT NOT NULL UNIQUE,
    interval_min INTEGER NOT NULL DEFAULT 10,
    media        TEXT NOT NULL DEFAULT 'none',   -- none / photos / all
    enabled      INTEGER NOT NULL DEFAULT 1,
    created      TEXT,
    last_run     TEXT,
    next_run     TEXT,
    last_job     INTEGER
);
"""

# Starter codebook for conflict / atrocity documentation. Editable in the UI.
DEFAULT_TAGS = [
    ("Unlawful killing", "Violation", "#b3261e", "Killing of civilians, captives or persons hors de combat", "1"),
    ("Torture / ill-treatment", "Violation", "#c2410c", "Beating, humiliation, abuse of detainees", "2"),
    ("Detention / captives", "Violation", "#a16207", "Captured persons, prisoners, enforced disappearance", "3"),
    ("Sexual violence", "Violation", "#86198f", "Any form of conflict-related sexual violence", "4"),
    ("Attack on civilians / objects", "Violation", "#9f1239", "Shelling, airstrikes, attacks on homes, markets, hospitals, schools", "5"),
    ("Looting / destruction", "Violation", "#854d0e", "Pillage, burning, destruction of property", "6"),
    ("Displacement", "Violation", "#0e7490", "Forced displacement, flight, siege", "7"),
    ("Incitement / hate speech", "Violation", "#6d28d9", "Dehumanising language, calls to violence, ethnic targeting", "8"),
    ("Perpetrator self-documentation", "Evidence", "#1d4ed8", "Footage filmed or posted by the perpetrating side", "9"),
    ("Unit / commander identified", "Evidence", "#0f766e", "Named unit, insignia, commander or individual", "u"),
    ("Location identifiable", "Evidence", "#15803d", "Enough landmarks to attempt geolocation", "l"),
    ("Weapon / equipment", "Evidence", "#475569", "Identifiable weapons, vehicles, munitions", "w"),
    ("Victim identifiable", "Sensitive", "#be123c", "Victim face/identity visible: handle with care", "x"),
]

# --------------------------------------------------------------------------- #
# Normalisation (applied identically to documents and queries)
# --------------------------------------------------------------------------- #

_AR_MARKS = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_INVISIBLE = "\u200b\u200c\u200d\u200e\u200f\u2060\u00ad\ufeff"     # zero-width, direction marks, soft hyphen
_TRANS = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه",
    "ک": "ك", "ی": "ي", "ە": "ه", "ۀ": "ه",            # Persian/Urdu variants -> Arabic letters
    "ё": "е", "Ё": "е",
    **{chr(0x0660 + i): str(i) for i in range(10)},   # Arabic-Indic digits
    **{chr(0x06F0 + i): str(i) for i in range(10)},   # Persian digits
    **{c: None for c in _INVISIBLE},
})
_AR_PREFIXES = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ف", "ب", "ل", "ك", "س")
_ARABIC = re.compile("[؀-ۿ]")
_TOKEN = re.compile(r"[\w؀-ۿ]+", re.U)

# ---- obfuscation folding (index time AND query time; stored text is never changed) ----
_LC = "a-z\u0400-\u04ff"                                   # Latin + Cyrillic letters (text is lowercased)
_PROTECT = re.compile(r"(?<!\S)(?:https?://|www\.|t\.me/|@)\S+|[\w.+-]+@[\w-]+(?:\.\w[\w-]*)+")   # urls, @users, e-mails
_LEET_AT = re.compile(rf"(?<=[{_LC}])[@$](?=[{_LC}])")           # w@gner, ca$h
_LEET_0 = re.compile(rf"(?<=[{_LC}])0(?!\d)|(?<!\d)0(?=[{_LC}])")  # b0mb; a lone 0 next to a letter, never inside numbers
_LEET_MAP = {"@": "a", "$": "s"}
_SPACED = re.compile(rf"(?<![^\W_])[{_LC}]([ \-_.*·•])(?:[{_LC}]\1){{2,}}[{_LC}](?![^\W_])")   # >=4 single letters, one separator
_SEPS = re.compile(r"[ \-_.*·•]")
_MIXED = re.compile(r"(?<![^\W_])(?=[^\W_]*[a-z])(?=[^\W_]*[\u0400-\u04ff])[^\W_]+")   # token with both Latin and Cyrillic
_REPEAT = re.compile(r"([^\W\d_])\1{2,}")                      # 3+ identical letters -> 1 (doubles are real)
_LAT_TWINS = "aeopcxykmtbhi"
_CYR_TWINS = "аеорсхукмтвні"
_TO_CYR = str.maketrans(_LAT_TWINS, _CYR_TWINS)
_TO_LAT = str.maketrans(_CYR_TWINS, _LAT_TWINS)


# ---- Phase 2: cases, watchlists, alerts, incidents, discovery, exports ----------
# Kept apart from SCHEMA so the original tables stay exactly as they were. Everything
# here is additive: the old global review status, tags and notes keep working, and
# cases add their own per-case layer on top.
SCHEMA_PHASE2 = """
-- A case is a set of channels (a channel can belong to several cases: scrape once, use everywhere).
CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    color       TEXT,
    status      TEXT NOT NULL DEFAULT 'active',   -- active / archived
    created     TEXT,
    created_by  TEXT,
    last_opened TEXT                              -- "new since your last visit" counts from here
);
CREATE TABLE IF NOT EXISTS case_channels (
    case_id  INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    channel  TEXT NOT NULL,
    role     TEXT NOT NULL DEFAULT 'watched',    -- seed / watched / context
    added    TEXT,
    added_by TEXT,
    note     TEXT,
    PRIMARY KEY (case_id, channel)
);
CREATE INDEX IF NOT EXISTS case_channels_channel ON case_channels(channel);

-- Per-case triage. A post can be relevant in one case and irrelevant in another.
CREATE TABLE IF NOT EXISTS case_reviews (
    case_id     INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    item_type   TEXT NOT NULL DEFAULT 'post',     -- post / comment
    item_id     INTEGER NOT NULL,
    status      TEXT NOT NULL,                    -- unreviewed / relevant / irrelevant / verify
    reviewed_at TEXT,
    reviewer    TEXT,
    PRIMARY KEY (case_id, item_type, item_id)
);
CREATE TABLE IF NOT EXISTS case_tags (
    case_id   INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    item_type TEXT NOT NULL DEFAULT 'post',
    item_id   INTEGER NOT NULL,
    tag_id    INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    added_by  TEXT,
    added_at  TEXT,
    PRIMARY KEY (case_id, item_type, item_id, tag_id)
);

-- Watchlist: re-crawl a channel's posts on a schedule (comments have their own `schedules`).
CREATE TABLE IF NOT EXISTS watches (
    id           INTEGER PRIMARY KEY,
    channel      TEXT NOT NULL UNIQUE,
    interval_min INTEGER NOT NULL DEFAULT 360,
    pages        INTEGER NOT NULL DEFAULT 3,      -- newest pages to fetch at most (~20 posts each)
    recheck_days INTEGER NOT NULL DEFAULT 7,      -- re-read posts this recent to catch edits and deletions
    media        TEXT NOT NULL DEFAULT 'photos',  -- none / photos / all
    enabled      INTEGER NOT NULL DEFAULT 1,
    created      TEXT,
    created_by   TEXT,
    last_run     TEXT,
    next_run     TEXT,
    last_job     INTEGER
);

-- Changes to a channel noticed between crawls (title, description, username, preview gone ...)
CREATE TABLE IF NOT EXISTS channel_events (
    id          INTEGER PRIMARY KEY,
    channel     TEXT NOT NULL,
    kind        TEXT NOT NULL,     -- renamed / title_changed / description_changed / preview_disabled /
                                   -- preview_restored / unavailable / subscribers_jump
    detected_at TEXT,
    old         TEXT,
    new         TEXT,
    job_id      INTEGER
);
CREATE INDEX IF NOT EXISTS channel_events_channel ON channel_events(channel, detected_at);

CREATE TABLE IF NOT EXISTS alert_rules (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,   -- NULL: all channels
    kind       TEXT NOT NULL,     -- keyword / post_deleted / post_edited / comment_deleted /
                                  -- channel_change / admin_change / new_candidate / media_reused
    query      TEXT,              -- keyword rules: same syntax as Search
    scope      TEXT NOT NULL DEFAULT 'posts',   -- keyword rules: posts / comments / both
    channels   TEXT,              -- optional JSON list narrowing the rule further
    min_links  INTEGER,           -- new_candidate: linked from at least this many case channels
    notify     INTEGER NOT NULL DEFAULT 0,      -- also show a macOS notification
    enabled    INTEGER NOT NULL DEFAULT 1,
    created    TEXT,
    created_by TEXT,
    query_fts  TEXT,              -- keyword rules: FTS5 expression frozen when saved (NULL = legacy, literal)
    expansion  TEXT,              -- JSON: the dictionary expansion that produced query_fts
    dict_version TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    rule_id     INTEGER REFERENCES alert_rules(id) ON DELETE CASCADE,
    case_id     INTEGER,
    kind        TEXT NOT NULL,
    item_type   TEXT NOT NULL,    -- post / comment / channel / account
    item_id     TEXT NOT NULL,
    channel     TEXT,
    title       TEXT,
    detail      TEXT,
    created     TEXT,
    job_id      INTEGER,
    read_at     TEXT,
    UNIQUE (rule_id, kind, item_type, item_id)
);
CREATE INDEX IF NOT EXISTS alerts_unread ON alerts(read_at, created);

-- Incidents: one event (an attack, a killing, a displacement) and the material documenting it.
CREATE TABLE IF NOT EXISTS incidents (
    id          INTEGER PRIMARY KEY,
    case_id     INTEGER REFERENCES cases(id) ON DELETE SET NULL,
    title       TEXT NOT NULL,
    description TEXT,
    date_start  TEXT,
    date_end    TEXT,
    date_note   TEXT,              -- how the date was established
    location    TEXT,
    lat         REAL,
    lon         REAL,
    location_note TEXT,            -- how the place was established (geolocated, claimed, ...)
    status      TEXT NOT NULL DEFAULT 'open',   -- open / verified / disputed / closed
    created     TEXT,
    created_by  TEXT,
    updated     TEXT
);
CREATE TABLE IF NOT EXISTS incident_items (
    id          INTEGER PRIMARY KEY,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    item_type   TEXT NOT NULL,     -- post / comment / media / comment_media / channel / account
    item_id     TEXT NOT NULL,
    relation    TEXT,              -- e.g. primary footage, claim of responsibility, reposted, context
    note        TEXT,
    added_by    TEXT,
    added_at    TEXT,
    UNIQUE (incident_id, item_type, item_id)
);

-- Channels found by discovery jobs (similar channels, keyword search) and how.
CREATE TABLE IF NOT EXISTS discoveries (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER,
    method      TEXT NOT NULL,     -- similar / search_channels / search_posts
    query       TEXT,              -- keyword, or the channel whose similar channels these are
    username    TEXT,
    tg_id       INTEGER,
    title       TEXT,
    subscribers INTEGER,
    type        TEXT,
    detail      TEXT,              -- JSON: matching post for post search, rank, ...
    found_at    TEXT,
    UNIQUE (method, query, tg_id)
);
CREATE INDEX IF NOT EXISTS discoveries_username ON discoveries(username);

-- Local media analysis (mediascan.py): perceptual hashes survive re-encoding, unlike SHA-256
CREATE TABLE IF NOT EXISTS media_scan (
    sha256     TEXT PRIMARY KEY,
    phashes    TEXT,               -- JSON list of 16-hex dHashes (videos: frames at 10/50/90%)
    meta       TEXT,               -- JSON: duration, width, height, codec, has_audio
    embedded   TEXT,               -- JSON: EXIF / container metadata still in the file (camera, GPS, dates)
    has_gps    INTEGER DEFAULT 0,
    error      TEXT,
    scanned_at TEXT,
    job_id     INTEGER
);

-- Investigator edits to the (read-only) multilingual dictionary. Rows are never deleted: undo sets `undone`.
CREATE TABLE IF NOT EXISTS dict_edits (
    id         INTEGER PRIMARY KEY,
    action     TEXT NOT NULL,      -- add_name / hide_name / add_entry / hide_entry
    entry_id   TEXT,               -- dictionary entry id, or 'user:<id>' for add_entry
    name       TEXT,
    lang       TEXT,
    kind       TEXT,               -- add_entry
    country    TEXT,
    category   TEXT,
    label      TEXT,
    undone     INTEGER NOT NULL DEFAULT 0,
    created    TEXT,
    created_by TEXT
);

-- Evidence packages written to disk
CREATE TABLE IF NOT EXISTS exports (
    id          INTEGER PRIMARY KEY,
    scope       TEXT NOT NULL,     -- case / incident / selection
    scope_id    INTEGER,
    path        TEXT NOT NULL,
    sha256      TEXT,
    bytes       INTEGER,
    items       INTEGER,
    files       INTEGER,
    created     TEXT,
    created_by  TEXT
);
"""


def _migrate_phase2(con):
    con.executescript(SCHEMA_PHASE2)
    have = {r[1] for r in con.execute("PRAGMA table_info(posts)")}
    for col in ("last_seen TEXT", "deleted_detected TEXT"):
        if col.split()[0] not in have:
            con.execute(f"ALTER TABLE posts ADD COLUMN {col}")
    have = {r[1] for r in con.execute("PRAGMA table_info(post_versions)")}
    if "media_types" not in have:
        con.execute("ALTER TABLE post_versions ADD COLUMN media_types TEXT")
    have = {r[1] for r in con.execute("PRAGMA table_info(notes)")}
    if "case_id" not in have:
        con.execute("ALTER TABLE notes ADD COLUMN case_id INTEGER")    # NULL = not tied to a case
    have = {r[1] for r in con.execute("PRAGMA table_info(notes)")}
    if "comment_id" not in have:
        con.execute("ALTER TABLE notes ADD COLUMN comment_id INTEGER")
    have = {r[1] for r in con.execute("PRAGMA table_info(alert_rules)")}
    for col in ("query_fts TEXT", "expansion TEXT", "dict_version TEXT"):    # frozen keyword expansion
        if col.split()[0] not in have:
            con.execute(f"ALTER TABLE alert_rules ADD COLUMN {col}")
    con.commit()

def _fix_mixed(m):
    """Homoglyph token (вagнер): map the minority script's look-alikes into the majority script."""
    w = m.group()
    cy = sum(1 for c in w if "\u0400" <= c <= "\u04ff")
    la = sum(1 for c in w if "a" <= c <= "z")
    return w.translate(_TO_CYR) if cy > la else w.translate(_TO_LAT) if la > cy else w


def _deobfuscate(t):
    if "@" in t or "$" in t:
        t = _LEET_AT.sub(lambda m: _LEET_MAP[m.group()], t)
    if "0" in t:
        t = _LEET_0.sub("o", t)
    t = _SPACED.sub(lambda m: _SEPS.sub("", m.group()), t)
    t = _MIXED.sub(_fix_mixed, t)
    return _REPEAT.sub(r"\1", t)


def normalize(text):
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = _AR_MARKS.sub("", t)
    t = t.translate(_TRANS).lower()
    if not ("@" in t or "://" in t or "www." in t or "t.me/" in t) or not _PROTECT.search(t):
        return _deobfuscate(t)
    out, pos = [], 0                                     # urls, @usernames and e-mails stay as written
    for m in _PROTECT.finditer(t):
        out.append(_deobfuscate(t[pos:m.start()]))
        out.append(m.group())
        pos = m.end()
    out.append(_deobfuscate(t[pos:]))
    return "".join(out)


def strip_prefix(tok):
    """Light Arabic stemming: drop one attached prefix (and/the/in/to/like/will)."""
    if not _ARABIC.match(tok):
        return tok
    for p in _AR_PREFIXES:
        if tok.startswith(p) and len(tok) - len(p) >= 3:
            return tok[len(p):]
    return tok


def stems(norm_text):
    out = []
    for tok in _TOKEN.findall(norm_text):
        s = strip_prefix(tok)
        if s != tok:
            out.append(s)
    return " ".join(out)


_CYR_WORD = re.compile("[\u0400-\u04ff]+")
_tok_cache = {}


def _tok_stems(tok):
    """(prefix stem or None, [extra stems], romanised key or None) for one normalised token. Memoised: the
    vocabulary is small next to the token count, so reindexing 100k posts stays cheap."""
    r = _tok_cache.get(tok)
    if r is None:
        pre, extra, zz = None, [], None
        if _ARABIC.match(tok):
            s1 = strip_prefix(tok)
            pre = s1 if s1 != tok else None
            s2 = translit.ar_suffix(s1)
            if s2 != s1:
                extra.append(s2)
        elif _CYR_WORD.fullmatch(tok):
            st = translit.cy_stem(tok)
            if st != tok:
                extra.append(st)
        if len(tok) >= 3 and translit._CYRILLIC.search(tok):
            zz = translit.ROMAN_PREFIX + translit.cy_key(tok)
        r = (pre, tuple(extra), zz)
        if len(_tok_cache) > 300000:
            _tok_cache.clear()
        _tok_cache[tok] = r
    return r


def index_stems(norm_text):
    """The `stems` column: Arabic prefix-stripped tokens in text order (so phrases over them still work),
    then extra stems (Arabic light suffix, Russian/Ukrainian stems), then ROMAN_PREFIX keys of Cyrillic words."""
    pre, extra, zz = [], {}, {}
    for tok in _TOKEN.findall(norm_text):
        p, ex, z = _tok_stems(tok)
        if p:
            pre.append(p)
        for e in ex:
            extra[e] = 1
        if z:
            zz[z] = 1
    return " ".join([*pre, *extra, *zz])


def _star(x):
    """A stem as a query term: prefix match, except 3-letter stems (too many words start with them)."""
    return f'"{x}"*' if len(x) >= 4 else f'"{x}"'


def word_forms(w, stem=True):
    """Query forms of one plain word: [typed word, article-stripped Arabic, suffix-stripped Arabic / Russian or
    Ukrainian stem], each an FTS term. Same stemming as the `stems` column, so inflected forms meet."""
    forms = [f'"{w}"*']
    if _ARABIC.match(w):
        s = strip_prefix(w)
        if s != w and len(w) - len(s) >= 2:      # query side only drops the article family (ال، وال، بال…);
            forms.append(f'"{s}"*')              # a single leading letter may belong to the root (فاشر)
        else:
            s = w
        x = translit.ar_suffix(s) if stem else s
        if x != s:
            forms.append(_star(x))
    elif stem and _CYR_WORD.fullmatch(w):
        st = translit.cy_stem(w)
        if st != w:
            forms.append(_star(st))
    return forms


def fts_word(w, stem=True):
    """FTS5 expression for one plain query token (prefix match; article-stripped / stemmed alternatives).
    stem=False (used for -excluded words) leaves the stems out, so an exclusion never hides more than was typed."""
    f = word_forms(w, stem)
    return f[0] if len(f) == 1 else "(" + " OR ".join(f) + ")"


_NEAR_RE = re.compile(r"NEAR(?:/([0-9]{1,4}))?")   # upper case only: "near" alone is a real word
_NEAR_MARK = re.compile("\ue000([0-9]+)\ue001")
DEFAULT_NEAR = 10


def near_operand_expr(o):
    """Literal FTS phrase for one NEAR operand: a word is prefix-matched, a "phrase" exact (or prefix with a trailing *)."""
    if o["kind"] == "phrase":
        return '"' + " ".join(o["words"]) + '"' + ("*" if o.get("prefix") else "")
    return '"' + " ".join(o["words"]) + '"*'


def near_expr(exprs, n):
    return f"NEAR({' '.join(exprs)}, {n})"


def fts_parts(q):
    """Split a user query into parts: {"op": "OR"} or {"kind": "word"|"phrase", "neg": bool,
    "words": [tokens], "expr": the part's own FTS expression}, or (for `a NEAR/n b`)
    {"kind": "near", "neg": False, "operands": [{"kind", "words", "prefix"?}], "n": int, "words": [...],
    "expr": literal NEAR(...)}. `fts_assemble` joins them."""
    # NEAR is only a keyword outside "quotes": mark it before normalising (which lower-cases it)
    q = normalize(" ".join(
        f"\ue000{m.group(1) or DEFAULT_NEAR}\ue001" if not p.startswith('"') and (m := _NEAR_RE.fullmatch(p)) else p
        for p in re.findall(r'"[^"]*"\*?|\S+', q or ""))).strip()
    out = []
    for p in re.findall(r'"[^"]*"\*?|\S+', q):
        if p.upper() == "OR":
            out.append({"op": "OR"})
            continue
        m = _NEAR_MARK.fullmatch(p)
        if m:
            out.append({"op": "NEAR", "n": min(int(m.group(1)), 1000)})
            continue
        neg = p.startswith("-") and len(p) > 1
        if neg:
            p = p[1:]
        words = _TOKEN.findall(p)
        if not words:
            continue
        if p.startswith('"'):
            out.append({"kind": "phrase", "neg": neg, "words": words, "expr": '"' + " ".join(words) + '"',
                        "prefix": p.endswith("*")})
        else:
            each = [fts_word(w, not neg) for w in words]
            out.append({"kind": "word", "neg": neg, "words": words,
                        "expr": each[0] if len(each) == 1 else "(" + " AND ".join(each) + ")"})
    return _fold_near(out)


def _fold_near(parts):
    """Fold `a NEAR/n b [NEAR/m c]` into one "near" part. A NEAR without two plain operands is dropped."""
    out, i = [], 0
    while i < len(parts):
        p = parts[i]
        if p.get("op") == "NEAR":
            prev = out[-1] if out else None
            nxt = parts[i + 1] if i + 1 < len(parts) else None
            if prev and nxt and "kind" in nxt and not nxt["neg"] and nxt["kind"] != "near" \
                    and prev["kind"] in ("word", "phrase", "near") and not prev["neg"]:
                if prev["kind"] != "near":
                    out[-1] = prev = {"kind": "near", "neg": False, "operands": [prev], "n": p["n"], "words": list(prev["words"])}
                prev["operands"].append(nxt)
                prev["n"] = max(prev["n"], p["n"])
                prev["words"] += nxt["words"]
                prev["expr"] = near_expr([near_operand_expr(o) for o in prev["operands"]], prev["n"])
                i += 2
                continue
            i += 1
            continue
        if p.get("kind") == "near":
            p = dict(p, expr=near_expr([near_operand_expr(o) for o in p["operands"]], p["n"]))
        out.append(p)
        i += 1
    for p in out:                                    # a lone marker never survives; set the literal expr
        if p.get("kind") == "near" and "expr" not in p:
            p["expr"] = near_expr([near_operand_expr(o) for o in p["operands"]], p["n"])
    return [p for p in out if p.get("op") != "NEAR"]


def fts_assemble(items):
    """items: "OR" or (expr, negated). Same joining rules as always: no leading NOT, no dangling OR/NOT."""
    out = []
    for it in items:
        if it == "OR":
            if out and out[-1] != "OR":
                out.append("OR")
            continue
        expr, neg = it
        if neg:
            if not out or out[-1] == "OR":
                continue                      # FTS5 can't start with NOT
            out.append("NOT")
        out.append(expr)
    while out and out[-1] in ("OR", "NOT"):
        out.pop()
    return " ".join(_and_join(out)) or None


def fts_query(q):
    """User query -> safe FTS5 expression (literal: no dictionary expansion, see lexicon.expand).

    - words are ANDed; each is prefix-matched against text and stems (plus its Arabic/Russian/Ukrainian stem)
    - "quoted phrases" match exactly
    - OR between terms, and -word to exclude
    - a NEAR/10 b: both within 10 tokens (NEAR alone = 10); operands are words or "phrases"
    """
    return fts_assemble("OR" if "op" in p else (p["expr"], p["neg"]) for p in fts_parts(q))


def fts_text(expr):
    """Restrict an FTS5 expression to the text columns (norm, stems) of posts_fts / comments_fts, so a search
    never matches the `meta` column (channel handle/title, forward names, hashtags). Applied at match time, so
    frozen/stored expressions stay as they were saved."""
    return "{norm stems} : (" + expr + ")"


def fts_names(expr):
    """The same expression restricted to the `meta` column (channel and forward names): the opt-in in=names source."""
    return "{meta} : (" + expr + ")"


def fts_scoped(expr, text=True, names=False):
    """Expression for posts_fts / comments_fts: text columns, names column, or either."""
    parts = ([fts_text(expr)] if text else []) + ([fts_names(expr)] if names else [])
    return " OR ".join(parts) if len(parts) > 1 else (parts[0] if parts else None)


def _and_join(tokens):
    res = []
    for t in tokens:
        if res and res[-1] not in ("OR", "NOT") and t not in ("OR", "NOT"):
            res.append("AND")
        res.append(t)
    return res


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_NUMBER = re.compile(r"[+-]?\d+(?:[.,]\d+)?\Z")


def csv_cell(v):
    """Neutralise spreadsheet formula injection in one CSV cell. Text starting with = + - @ tab or CR gets a
    leading ' so Excel/LibreOffice show it instead of running it. Real numbers (and text that is only a plain
    number, e.g. -12.5 or a phone +249912345678) are left alone. The JSON copies keep the original text."""
    if isinstance(v, str) and v and v[0] in "=+-@\t\r" and not _NUMBER.match(v):
        return "'" + v
    return v


def csv_row(row):
    return {k: csv_cell(v) for k, v in row.items()}


def csv_rows(rows):
    return [csv_row(r) for r in rows]


class _Rows:
    """A statement's results, read in full while the connection lock was held (see LockedConnection)."""
    __slots__ = ("_rows", "_i", "lastrowid", "rowcount", "description")

    def __init__(self, cur):
        self._rows = cur.fetchall() if cur.description else []
        self._i = 0
        self.lastrowid, self.rowcount, self.description = cur.lastrowid, cur.rowcount, cur.description

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        self._i += 1
        return self._rows[self._i - 1]

    def fetchall(self):
        rest, self._i = self._rows[self._i:], len(self._rows)
        return rest

    def __iter__(self):
        while (r := self.fetchone()) is not None:
            yield r


class LockedConnection(sqlite3.Connection):
    """One connection shared by FastAPI's worker threads and the job runner. Every statement runs, and its rows are
    read, under one re-entrant lock, so a module that calls con.execute() directly can't interleave with another
    thread's statement on the same connection (that raised sqlite3.InterfaceError under concurrent searches).
    Multi-statement transactions still hold `con.lock` around the whole unit (server.write)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.lock = threading.RLock()

    def execute(self, sql, args=()):
        with self.lock:
            return _Rows(super().execute(sql, args))

    def executemany(self, sql, seq):
        with self.lock:
            return _Rows(super().executemany(sql, seq))

    def executescript(self, script):
        with self.lock:
            return super().executescript(script)

    def commit(self):
        with self.lock:
            return super().commit()

    def rollback(self):
        with self.lock:
            return super().rollback()


def connect(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=30, check_same_thread=False, factory=LockedConnection)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    _migrate_phase2(con)
    reindex_fts(con)
    if not con.execute("SELECT 1 FROM tags LIMIT 1").fetchone():
        con.executemany("INSERT INTO tags(name, category, color, description, key) VALUES (?,?,?,?,?)",
                        DEFAULT_TAGS)
        con.commit()
    return con


def _migrate(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(posts)")}
    if "page_sha256" not in cols:
        con.execute("ALTER TABLE posts ADD COLUMN page_sha256 TEXT")
        con.commit()


FTS_VERSION = "3"      # 3: obfuscation-folded norm, stemmed `stems`, post_versions_fts, media_text_fts
                       # (2: `stems` also held romanised Cyrillic tokens)


def _post_meta(channel, fwd_username, fwd_name, hashtags):
    return normalize(" ".join(filter(None, [channel, fwd_username, fwd_name, " ".join(hashtags or [])])))


def _comment_meta(meta_parts):
    return normalize(" ".join(str(m) for m in meta_parts if m))


def _hashtags(raw):
    try:
        v = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [str(h) for h in v] if isinstance(v, list) else []


def reindex_fts(con):
    """One-time rebuild of the derived search indexes when their format changed (guarded by settings.fts_version).
    norm/stems/meta are recomputed from the stored original text (normalisation itself changed), for posts,
    comments, earlier post versions and media text. One transaction, audited. It is derived data, not
    evidence: the original tables are only read."""
    have = con.execute("SELECT value FROM settings WHERE key = 'fts_version'").fetchone()
    if have and have[0] == FTS_VERSION:
        return
    t0 = time.time()
    try:
        counts = {}
        con.execute("DELETE FROM posts_fts")
        con.executemany(
            "INSERT INTO posts_fts(rowid, norm, stems, meta) VALUES (?,?,?,?)",
            ((r[0], n, index_stems(n), _post_meta(r[2], r[3], r[4], _hashtags(r[5])))
             for r in con.execute("SELECT id, text, channel, fwd_username, fwd_name, hashtags FROM posts")
             for n in (normalize(r[1]),)))
        counts["posts"] = con.execute("SELECT COUNT(*) FROM posts_fts").fetchone()[0]
        con.execute("DELETE FROM comments_fts")
        con.executemany(
            "INSERT INTO comments_fts(rowid, norm, stems, meta) VALUES (?,?,?,?)",
            ((r[0], n, index_stems(n), _comment_meta(r[2:6]))
             for r in con.execute("SELECT id, text, channel, fwd_username, fwd_name, post_author FROM comments")
             for n in (normalize(r[1]),)))
        counts["comments"] = con.execute("SELECT COUNT(*) FROM comments_fts").fetchone()[0]
        for fts, src in (("post_versions_fts", "post_versions"), ("media_text_fts", "media_text")):
            con.execute(f"DELETE FROM {fts}")
            con.executemany(f"INSERT INTO {fts}(rowid, norm, stems) VALUES (?,?,?)",
                            ((r[0], n, index_stems(n))
                             for r in con.execute(f"SELECT id, text FROM {src}") for n in (normalize(r[1]),)))
            counts[fts] = con.execute(f"SELECT COUNT(*) FROM {fts}").fetchone()[0]
        audit(con, "system", "fts_reindex", None,
              {"from": have[0] if have else "1", "to": FTS_VERSION, **counts, "seconds": round(time.time() - t0, 2)})
        con.execute("INSERT INTO settings(key, value) VALUES ('fts_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (FTS_VERSION,))
        con.commit()
    except Exception as e:                 # searching still works on the old index
        con.rollback()
        print(f"warning: search reindex failed ({e}); will retry at next start")


def audit(con, actor, action, target=None, detail=None):
    con.execute("INSERT INTO audit(ts, actor, action, target, detail) VALUES (?,?,?,?,?)",
                (now(), actor, action, target,
                 json.dumps(detail, ensure_ascii=False) if detail is not None else None))


# fn(con, item_type, item_id, text), called at the end of index_post ('post'), index_comment ('comment') and
# index_media_text ('media_text') with the ORIGINAL text (not normalised). Called again when an item is
# re-indexed after an edit, so hooks must replace what they wrote before. Registered by feature modules
# (entities, places) from their register().
INDEX_HOOKS = []


def _run_hooks(con, item_type, item_id, text):
    for fn in list(INDEX_HOOKS):
        try:
            fn(con, item_type, item_id, text)
        except Exception as e:             # a hook must never break ingest
            print(f"warning: index hook {getattr(fn, '__name__', fn)} failed on {item_type} {item_id}: {e}")


def index_post(con, post_id, text, channel, fwd_username, fwd_name, hashtags):
    norm = normalize(text)
    con.execute("DELETE FROM posts_fts WHERE rowid = ?", (post_id,))
    con.execute("INSERT INTO posts_fts(rowid, norm, stems, meta) VALUES (?,?,?,?)",
                (post_id, norm, index_stems(norm), _post_meta(channel, fwd_username, fwd_name, hashtags)))
    _run_hooks(con, "post", post_id, text)


def index_comment(con, comment_id, text, meta_parts):
    norm = normalize(text)
    con.execute("DELETE FROM comments_fts WHERE rowid = ?", (comment_id,))
    con.execute("INSERT INTO comments_fts(rowid, norm, stems, meta) VALUES (?,?,?,?)",
                (comment_id, norm, index_stems(norm), _comment_meta(meta_parts)))
    _run_hooks(con, "comment", comment_id, text)


def index_version(con, version_id, text):
    """Index an earlier post version (post_versions.id) so edited-away text stays searchable."""
    norm = normalize(text)
    con.execute("DELETE FROM post_versions_fts WHERE rowid = ?", (version_id,))
    con.execute("INSERT INTO post_versions_fts(rowid, norm, stems) VALUES (?,?,?)",
                (version_id, norm, index_stems(norm)))


def index_media_text(con, media_text_id, text):
    """Index one media_text row (OCR / speech segment), then run the 'media_text' hooks."""
    norm = normalize(text)
    con.execute("DELETE FROM media_text_fts WHERE rowid = ?", (media_text_id,))
    con.execute("INSERT INTO media_text_fts(rowid, norm, stems) VALUES (?,?,?)",
                (media_text_id, norm, index_stems(norm)))
    _run_hooks(con, "media_text", media_text_id, text)


def unindex_media_text(con, ids):
    """Forget media_text rows that are being replaced (a file re-read with other settings): their search
    rows, and the entity / place tags the index hooks derived from them."""
    ids = [(i,) for i in ids]
    con.executemany("DELETE FROM media_text_fts WHERE rowid = ?", ids)
    for table in ("entities", "item_places"):
        if con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
            con.executemany(f"DELETE FROM {table} WHERE item_type = 'media_text' AND item_id = ?", ids)


def versions_match_sql():
    """Post ids whose EARLIER versions match one FTS expression (one `?`): `p.id IN (<this>)`."""
    return ("SELECT DISTINCT v.post_id post_id FROM post_versions_fts f JOIN post_versions v ON v.id = f.rowid "
            "WHERE post_versions_fts MATCH ?")


def media_text_match_sql():
    """Rows (post_id, media_text_id, kind, t_start, sha256) of media_text matching one FTS expression (one `?`),
    tied to posts through media.sha256 -> media.post_id (a file shared by several posts yields a row for each)."""
    return ("SELECT m.post_id post_id, t.id media_text_id, t.kind kind, t.t_start t_start, t.sha256 sha256 "
            "FROM media_text_fts f JOIN media_text t ON t.id = f.rowid JOIN media m ON m.sha256 = t.sha256 "
            "WHERE media_text_fts MATCH ?")


def comment_media_text_match_sql():
    """Rows (comment_id, media_text_id, kind, t_start, sha256) of media_text matching one FTS expression (one `?`),
    tied to comments through comment_media.sha256 (the comment-section twin of media_text_match_sql)."""
    return ("SELECT m.comment_id comment_id, t.id media_text_id, t.kind kind, t.t_start t_start, t.sha256 sha256 "
            "FROM media_text_fts f JOIN media_text t ON t.id = f.rowid JOIN comment_media m ON m.sha256 = t.sha256 "
            "WHERE media_text_fts MATCH ?")


def rebuild_edges(con, sources=None):
    """Recompute aggregated edge weights from edge_hits."""
    where, args = "", []
    if sources:
        where = f"WHERE source IN ({','.join('?' * len(sources))})"
        args = list(sources)
        con.execute(f"DELETE FROM edges {where}", args)
    else:
        con.execute("DELETE FROM edges")
    con.execute(f"""
        INSERT INTO edges(source, target, type, weight, first_seen, last_seen)
        SELECT source, target, type, COUNT(*), MIN(date), MAX(date)
        FROM edge_hits {where} GROUP BY source, target, type""", args)
