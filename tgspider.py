#!/usr/bin/env python3
"""
tgspider — discover Telegram channels by spidering out from seed channels.

No login needed: reads Telegram's public web preview (https://t.me/s/<channel>).
For every channel it crawls, it records which other channels it points to via:

  forward      "Forwarded from <channel>" headers
  link         t.me / telegram.me links inside posts, buttons, link previews
  mention      @username in post text
  description  links / @mentions in the channel's own bio

Every discovered username is classified (channel / group / bot / user /
unknown). Channels are crawled in turn, breadth-first, up to --depth hops
from the seeds. Output is a ranked table of channels plus the raw edges.

Runs are resumable: re-run the same command with the same --out folder and it
picks up where it stopped.

Standard library only.  Python 3.9+.
"""

import argparse
import base64
import csv
import gzip
import hashlib
import html
import http.client
import json
import os
import random
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

TOOL_VERSION = "tgspider/1.1"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126 Safari/537.36")

# Where the public preview lives. Only the tests change this (to a local fixture server).
BASE_URL = os.environ.get("TGSPIDER_BASE_URL", "https://t.me").rstrip("/")
REQUEST_DEADLINE = 90       # seconds one whole page download may take (a socket timeout only covers each read)
MEDIA_DEADLINE = 900        # same, per media file
RETRY_AFTER_MAX = 600       # if Telegram says "wait longer than this", stop the run instead of sitting idle
BREAKER_LIMIT = 4           # requests in a row that fail for good before the run stops (Telegram is refusing us)
MIN_POSTS_FOR_RATIOS = 3    # per-page field checks need at least this many posts to mean anything

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
# t.me paths that are never a public channel username
RESERVED_PATHS = {
    "s", "c", "joinchat", "addstickers", "addemoji", "addtheme", "setlanguage",
    "share", "proxy", "socks", "iv", "login", "boost", "addlist", "contact",
    "invoice", "giftcode", "m", "k", "a", "z", "dl", "apps", "blog", "faq",
    "privacy", "tos", "api", "jobs", "telegram", "i", "bg", "confirmphone",
    "passport", "stars", "nft",
}
TG_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/([^\s\"'<>?#]+)",
    re.I)
MENTION_RE = re.compile(r"(?<![A-Za-z0-9_/.])@([A-Za-z][A-Za-z0-9_]{3,31})\b")
HREF_RE = re.compile(r'<a\b[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- #
# Username normalisation
# --------------------------------------------------------------------------- #

def username_from_path(path):
    """First segment of a t.me path -> username, or None if not a public name."""
    path = urllib.parse.unquote(path).strip("/")
    if not path:
        return None
    first = path.split("/")[0]
    if first.lower() == "s":                      # t.me/s/<name>
        parts = path.split("/")
        first = parts[1] if len(parts) > 1 else ""
    if first.startswith("+") or first.lower() in RESERVED_PATHS:
        return None
    return first.lower() if USERNAME_RE.match(first) else None


def normalize_seed(raw):
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.startswith("@"):
        raw = raw[1:]
    m = TG_LINK_RE.search(raw)
    if m:
        return username_from_path(m.group(1))
    return raw.lower() if USERNAME_RE.match(raw) else None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class RunAbort(Exception):
    """The run should end cleanly now, keeping everything collected (kind: stop, limit,
    breaker or rate_limit). Not a failure of one channel, so it is never swallowed per channel."""

    def __init__(self, reason, kind):
        super().__init__(reason)
        self.reason, self.kind = reason, kind


def nap(seconds, stop_aware=False):
    """Sleep in short slices so the progress file keeps ticking (the app tells a wait from a
    hang by it) and, if asked, a STOP request is noticed. Returns True if stopped early.
    Uses the monotonic clock: a wall-clock jump (sleep, NTP) can't stretch or cut the wait."""
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return False
        if stop_aware and PROG and os.path.exists(PROG.stop_file):
            return True
        time.sleep(min(left, 2.0))
        if PROG:
            PROG.write()


def read_body(r, deadline):
    """The whole body of an open response, or an error if it is cut short or too slow.
    Reading in chunks with `amt` never raises on a connection closed early, so the length
    Telegram announced is checked here."""
    want = r.headers.get("Content-Length")
    want = int(want) if want and want.strip().isdigit() else None
    end, buf = time.monotonic() + deadline, bytearray()
    while chunk := r.read(1 << 16):
        buf += chunk
        if time.monotonic() > end:
            raise TimeoutError(f"download took longer than {deadline}s")
    if want is not None and len(buf) != want:
        raise ConnectionError(f"connection closed early: got {len(buf)} of {want} bytes")
    return bytes(buf)


class Fetcher:
    def __init__(self, delay, retries=5, verbose=True, pages_dir=None, max_requests=0):
        self.pages_dir = pages_dir       # keep every raw page (gzip + sha256) as evidence
        self.last_sha = None             # sha256 of the most recent page fetched
        self.base_delay = self.delay = delay
        self.retries = retries
        self.verbose = verbose
        self.max_requests = max_requests  # 0 = no limit
        self._last = 0.0                 # monotonic time of the last request
        self.requests = 0
        self.fail_streak = 0             # requests in a row that failed for good (retries used up)
        self.flags = []                  # oddities seen on the last page, e.g. "truncated"
        self._ok_streak = 0

    def _keep(self, url, final, raw):
        if not self.pages_dir:
            return
        digest = hashlib.sha256(raw).hexdigest()
        self.last_sha = digest
        rel = os.path.join("pages", f"{digest}.html.gz")
        dest = os.path.join(self.pages_dir, rel)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with gzip.open(dest + ".part", "wb") as f:
                f.write(raw)
            os.replace(dest + ".part", dest)
        with open(os.path.join(self.pages_dir, "pages.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "url": url, "final_url": final, "sha256": digest, "bytes": len(raw),
                "path": rel, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "tool": TOOL_VERSION}) + "\n")

    def _slow_down(self):
        """Telegram is pushing back: space requests further apart until it stops."""
        old = self.delay
        self.delay = min(max(self.delay * 1.5, self.base_delay + 1), max(self.base_delay * 6, 12))
        self._ok_streak = 0
        if PROG and int(self.delay) != int(old):
            PROG.event(f"Telegram is pushing back: now one request every {self.delay:.0f}s", "warn")

    def _ok(self):
        self.fail_streak = 0
        if self.delay > self.base_delay:
            self._ok_streak += 1
            if self._ok_streak >= 15:        # 15 clean requests: ease back toward the chosen pace
                self.delay, self._ok_streak = max(self.base_delay, self.delay / 1.5), 0

    def get(self, url):
        """Returns (final_url, html) or raises after retries. Only a page that arrived in
        full is kept as evidence and returned; `self.flags` notes anything odd about it."""
        backoff = max(self.delay, 2.0)
        for attempt in range(self.retries + 1):
            if self.max_requests and self.requests >= self.max_requests:
                raise RunAbort(f"the request limit of {self.max_requests} was reached", "limit")
            wait = self.delay * random.uniform(0.8, 1.3) - (time.monotonic() - self._last)
            if wait > 0:
                nap(min(wait, self.delay * 2 + 1))
            self._last = time.monotonic()
            self.requests += 1
            self.flags = []
            if PROG:
                PROG.inc("requests")
                PROG.cur(request=url.replace("https://", ""))
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    final = r.geturl()
                    raw = read_body(r, REQUEST_DEADLINE)
                self._keep(url, final, raw)
                if raw.strip() and b"</html>" not in raw[-2000:]:
                    self.flags.append("truncated")
                    if attempt == 0 and self.retries:        # once: a real change to the page tail would repeat
                        raise ConnectionError("the page ended before its closing </html>")
                self._ok()
                return final, raw.decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                err = e
                if e.code == 404:
                    self._ok()
                    return url, ""
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                if e.code == 429 or e.code >= 500:
                    self._slow_down()
                ra = (e.headers.get("Retry-After") or "").strip()
                if ra.isdigit() and int(ra) > RETRY_AFTER_MAX:     # even on the last try: ask nothing more
                    self.fail_streak += 1
                    raise RunAbort(f"Telegram asked us to wait {int(ra) // 60} minutes "
                                   f"(Retry-After {ra}s); stopping so nothing more is requested", "rate_limit")
                if attempt == self.retries:
                    self.fail_streak += 1
                    raise
                if ra.isdigit():
                    sleep = int(ra) + random.uniform(1, 3)
                else:
                    sleep = backoff * random.uniform(0.8, 1.3)
            except (OSError, http.client.HTTPException) as e:
                # URLError, timeouts, resets, TLS errors, and pages cut off mid-body
                err = e
                if attempt == self.retries:
                    self.fail_streak += 1
                    raise
                sleep = backoff * random.uniform(0.8, 1.3)
            log(f"    ! request failed, retrying in {sleep:.0f}s", self.verbose)
            if PROG:
                why = f"HTTP {err.code}" if isinstance(err, urllib.error.HTTPError) else type(err).__name__
                PROG.inc("retries")
                PROG.d["waiting"] = {"reason": f"{why} from Telegram; retry {attempt + 1}/{self.retries}",
                                     "seconds": int(sleep), "since": _now()}
                PROG.event(f"{why} on {url.replace('https://', '')}: waiting {sleep:.0f}s before retrying", "warn")
            stopped = nap(sleep, stop_aware=True)
            if PROG:
                PROG.d["waiting"] = None
            if stopped:
                raise RunAbort("stop requested while waiting to retry", "stop")
            backoff = min(backoff * 2, 300)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def text_of(fragment):
    return html.unescape(TAG_RE.sub("", fragment or "")).strip()


def first(pattern, s, flags=re.S):
    m = re.search(pattern, s, flags)
    return m.group(1) if m else None


def parse_count(s):
    """'274 777' -> 274777, '115K' -> 115000, '9.45M' -> 9450000."""
    if not s:
        return None
    s = s.replace(" ", " ").strip()
    m = re.match(r"([\d.,\s]+)\s*([KMB]?)", s, re.I)
    if not m:
        return None
    num = m.group(1).replace(" ", "").replace(",", "")
    try:
        val = float(num)
    except ValueError:
        return None
    return int(val * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()])


def classify_profile(page):
    """Classify a plain t.me/<name> profile page (non-channel or no preview)."""
    has_title = 'class="tgme_page_title"' in page
    title = text_of(first(r'<div class="tgme_page_title"[^>]*>(.*?)</div>', page))
    extras = [text_of(x) for x in re.findall(r'<div class="tgme_page_extra">(.*?)</div>', page, re.S)]
    extra = " ".join(e for e in extras if e)
    button = text_of(first(r'class="tgme_action_button_new[^"]*"[^>]*>(.*?)</a>', page))
    desc = first(r'<div class="tgme_page_description[^"]*"[^>]*>(.*?)</div>', page) or ""
    info = {"title": title or None, "extra": extra or None, "description_html": desc}
    low = extra.lower()
    if not has_title:
        info["type"] = "unknown"          # nonexistent, private, or hidden
    elif "subscriber" in low:
        info["type"] = "channel"
        info["subscribers"] = parse_count(extra)
    elif "member" in low:
        info["type"] = "group"
        info["subscribers"] = parse_count(extra)
    elif button.lower().startswith("start bot") or "monthly user" in low:
        info["type"] = "bot"
    elif button.lower().startswith("send message"):
        info["type"] = "user"
    else:
        info["type"] = "unknown"
    return info


BG_URL_RE = re.compile(r"background-image:url\('([^']+)'\)")


def rich_text(fragment):
    """Message HTML -> plain text, keeping line breaks."""
    return text_of(re.sub(r"<br\s*/?>", "\n", fragment or "", flags=re.I))


def scrape_post(b, own, msg_id, date, body, fwd_href, fwd_user):
    """Everything readable about one post on the preview page."""
    fwd_name = first(r'<a class="tgme_widget_message_forwarded_from_name"[^>]*>(.*?)</a>', b) \
        or first(r'<span class="tgme_widget_message_forwarded_from_name"[^>]*>(.*?)</span>', b)
    views = first(r'<span class="tgme_widget_message_views">([^<]*)</span>', b)
    meta = first(r'<span class="tgme_widget_message_meta">(.*?)</span>', b) or ""

    photos = [BG_URL_RE.search(a).group(1)
              for a in re.findall(r'<a class="tgme_widget_message_photo_wrap[^>]*>', b)
              if BG_URL_RE.search(a)]
    videos = re.findall(r'<video src="([^"]+)"', b)
    video_thumbs = [BG_URL_RE.search(t).group(1)
                    for t in re.findall(r'<i class="tgme_widget_message_video_thumb"[^>]*>', b)
                    if BG_URL_RE.search(t)]
    durations = re.findall(r'<time class="message_video_duration[^"]*">([^<]*)</time>', b)
    media_types = sorted({k for k, pat in (
        ("photo", "tgme_widget_message_photo_wrap"),
        ("video", "tgme_widget_message_video_player"),
        ("roundvideo", "tgme_widget_message_roundvideo"),
        ("voice", "tgme_widget_message_voice"),
        ("audio", "tgme_widget_message_audio"),
        ("document", "tgme_widget_message_document"),
        ("sticker", "tgme_widget_message_sticker"),
        ("poll", "tgme_widget_message_poll"),
        ("location", "tgme_widget_message_location"),
    ) if pat in b})

    reactions = {}
    for emoji, n in re.findall(
            r'<span class="tgme_reaction[^"]*">.*?<b>([^<]*)</b>.*?</i>\s*([\d.,KM]+)\s*</span>', b, re.S):
        reactions[emoji] = parse_count(n)

    links = []
    for href, _ in HREF_RE.findall(body):
        if href.startswith("?q="):                       # hashtag search link
            continue
        href = html.unescape(href)
        if href not in links:
            links.append(href)
    preview = first(r'<a class="tgme_widget_message_link_preview" href="([^"]+)"', b)
    text = rich_text(body)

    return {
        "channel": own,
        "id": msg_id,
        "url": f"https://t.me/{own}/{msg_id}",
        "date": date,
        "edited": "edited" in text_of(meta),
        "views": parse_count(views),
        "author_signature": text_of(first(
            r'<span class="tgme_widget_message_from_author"[^>]*>(.*?)</span>', b)) or None,
        "text": text,
        "forwarded_from_name": text_of(fwd_name) or None,
        "forwarded_from_username": fwd_user,
        "forwarded_from_url": fwd_href,
        "reply_to_url": first(r'<a class="tgme_widget_message_reply[^"]*" href="([^"]+)"', b),
        "hashtags": sorted(set(re.findall(r"#[^\s#.,!?؟،:;()\[\]\"']+", text))),
        "links": links,
        "link_preview_url": html.unescape(preview) if preview else None,
        "media_types": media_types,
        "sensitive": "blured" in b,
        "photos": photos,
        "videos": videos,
        "video_thumbs": video_thumbs,
        "video_durations": durations,
        # e.g. "Media is too big": the web preview won't serve that file at all
        "media_unavailable": [text_of(x) for x in re.findall(
            r'<a class="tgme_widget_message_video_player not_supported[^"]*".*?'
            r'<div class="message_media_not_supported_label">(.*?)</div>', b, re.S)],
        "reactions": reactions,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def channel_numeric_id(page):
    """Telegram's permanent channel id, from a post's data-view token.
    Usernames change; this doesn't."""
    token = first(r'data-view="([^"]+)"', page)
    if not token:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
        return abs(int(data["c"])) if "c" in data else None
    except Exception:
        return None


def parse_channel_page(page, own):
    """Parse a t.me/s/<name> page. Returns (header, messages, oldest_id)."""
    header = {
        "tg_id": channel_numeric_id(page),
        "title": text_of(first(r'<div class="tgme_channel_info_header_title"[^>]*>(.*?)</div>', page)) or None,
        "description_html": first(r'<div class="tgme_channel_info_description"[^>]*>(.*?)</div>', page) or "",
    }
    for val, kind in re.findall(
            r'<span class="counter_value">([^<]*)</span>\s*<span class="counter_type">([^<]*)</span>', page):
        if kind.startswith("subscriber") or kind.startswith("member"):
            header["subscribers"] = parse_count(val)

    messages = []
    blocks = page.split('<div class="tgme_widget_message_wrap')[1:]
    for b in blocks:
        post = first(r'data-post="([^"]+)"', b)
        if not post or "/" not in post:
            continue
        msg_id = int(post.rsplit("/", 1)[1]) if post.rsplit("/", 1)[1].isdigit() else None
        date = first(r'class="tgme_widget_message_date"[^>]*>\s*<time datetime="([^"]+)"', b)
        signals = []   # (username, type)

        # Forwarded from
        fwd = first(r'<a class="tgme_widget_message_forwarded_from_name" href="([^"]+)"', b)
        fwd_user = None
        if fwd:
            m = TG_LINK_RE.search(fwd)
            fwd_user = username_from_path(m.group(1)) if m else None
            if fwd_user:
                signals.append((fwd_user, "forward"))

        # Every other anchor in the message (text, buttons, link previews)
        mentioned_by_anchor = set()
        for href, inner in HREF_RE.findall(b):
            m = TG_LINK_RE.match(href)
            if not m:
                continue
            u = username_from_path(m.group(1))
            if not u:
                continue
            txt = text_of(inner)
            if txt.startswith("@"):
                signals.append((u, "mention"))
                mentioned_by_anchor.add(u)
            else:
                signals.append((u, "link"))

        # Plain-text @mentions / bare t.me links not wrapped in anchors
        body = first(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b) or ""
        plain = text_of(re.sub(r"<a\b.*?</a>", " ", body, flags=re.S))
        for u in MENTION_RE.findall(plain):
            u = u.lower()
            if u not in mentioned_by_anchor:
                signals.append((u, "mention"))
        for p in TG_LINK_RE.findall(plain):
            u = username_from_path(p)
            if u:
                signals.append((u, "link"))

        # Drop self-references; a forward already implies the link to its post
        clean, seen = [], set()
        for u, t in signals:
            if u == own:
                continue
            if t == "link" and u == fwd_user:
                continue
            if (u, t) in seen:
                continue
            seen.add((u, t))
            clean.append((u, t))
        messages.append({"id": msg_id, "date": date, "signals": clean,
                         "post": scrape_post(b, own, msg_id, date, body, fwd, fwd_user)})

    ids = [m["id"] for m in messages if m["id"] is not None]
    return header, messages, (min(ids) if ids else None)


def description_signals(desc_html, own):
    out, seen = [], set()
    for href, inner in HREF_RE.findall(desc_html or ""):
        m = TG_LINK_RE.match(href)
        u = username_from_path(m.group(1)) if m else None
        if u and u != own and u not in seen:
            seen.add(u)
            out.append(u)
    for u in MENTION_RE.findall(text_of(desc_html)):
        u = u.lower()
        if u != own and u not in seen:
            seen.add(u)
            out.append(u)
    return out


# --------------------------------------------------------------------------- #
# Markup-drift canary
# --------------------------------------------------------------------------- #
# Every parser above fails by returning less: a renamed class turns a busy channel into
# an "empty" one and the run still finishes "done". These checks compare what a page
# visibly contains with what was read from it, so that gap becomes a warning.

MSG_WRAP = '<div class="tgme_widget_message_wrap'
KNOWN_MARKERS = ("tgme_page", "tgme_channel_info", "tgme_widget_message")


def page_issues(page, header, messages, first_page=True):
    """Sanity-check a parsed t.me/s page. Returns [(code, message)] for things that should
    have been readable and were not. `header` is only checked on a channel's first page."""
    issues = []
    wrappers, n = page.count(MSG_WRAP), len(messages)
    if wrappers and not n:
        issues.append(("no_posts_parsed", f"the page has {wrappers} message blocks but none could be read "
                                          "(Telegram's post markup may have changed)"))
    elif wrappers - n > max(1, 0.2 * wrappers):
        issues.append(("posts_dropped", f"only {n} of {wrappers} message blocks could be read"))
    elif not wrappers and "tgme_widget_message" in page:
        issues.append(("markup_changed", "the page contains post markup but not in the expected wrapper "
                                         "(Telegram's layout may have changed)"))
    if n >= MIN_POSTS_FOR_RATIOS:
        no_date = sum(1 for m in messages if not m["date"])
        if no_date > 0.2 * n:
            issues.append(("date_missing", f"{no_date} of {n} posts have no date"))
        no_views = sum(1 for m in messages if m["post"]["views"] is None)
        if no_views > 0.5 * n:
            issues.append(("views_missing", f"{no_views} of {n} posts have no view count"))
        # neither text, media, forward nor links: the fields that carry content may have moved
        blank = sum(1 for m in messages if not (m["post"]["text"] or m["post"]["media_types"]
                                                or m["post"]["forwarded_from_name"] or m["post"]["links"]
                                                or m["post"]["link_preview_url"]))
        if blank > 0.5 * n:
            issues.append(("posts_blank", f"{blank} of {n} posts were read with no text, media or links "
                                          "(the post body markup may have changed)"))
    if first_page:
        if not header.get("title"):
            issues.append(("header_missing", "the channel title could not be found in the page header"))
        if "subscribers" not in header:
            issues.append(("subscribers_missing", "the subscriber count could not be found in the page header"))
        if n and header.get("tg_id") is None:
            issues.append(("id_missing", "the channel's permanent id could not be read (the data-view token "
                                         "is missing or changed), so a rename can't be detected"))
    return issues


def profile_issues(page, final_url):
    """Sanity-check a page that is not a channel preview (the profile of a name)."""
    if not page.strip():
        return [("empty_response", "Telegram returned an empty page")]
    if not any(m in page for m in KNOWN_MARKERS):
        title = text_of(first(r"<title[^>]*>(.*?)</title>", page, re.S | re.I))[:80]
        return [("unrecognised_layout", f"the page has none of the expected Telegram markup "
                                        f"({len(page)} bytes{', title: ' + repr(title) if title else ''}); "
                                        "it may be a block page or a layout change")]
    if "/s/" in final_url and "tgme_widget_message" in page and "tgme_channel_info" not in page:
        return [("header_changed", "the page has posts but not the channel header the crawler looks for "
                                   "(Telegram's layout may have changed)")]
    return []


def fetch_issues(fetcher):
    """Oddities the fetcher noticed on the last page it returned."""
    return [("truncated", "the page ended before its closing </html> and may be incomplete")
            for f in fetcher.flags if f == "truncated"]


def note_issues(name, issues, fetcher, page_no=None):
    """Record issues as parser warnings in the run's progress (and the raw page's hash, so the
    page that tripped the check can be found again). Returns them as records for the channel."""
    out = []
    for code, msg in issues:
        rec = {"code": code, "message": msg, "page": page_no, "page_sha256": fetcher.last_sha}
        out.append(rec)
        log(f"    ! parser warning for @{name}: {msg}", fetcher.verbose)
        if PROG:
            PROG.warn(code, f"@{name}" + (f" page {page_no}" if page_no else "") + f": {msg}",
                      channel=name, sha=fetcher.last_sha)
    return out


# --------------------------------------------------------------------------- #
# Crawl state (append-only JSONL, so runs can resume)
# --------------------------------------------------------------------------- #

def log(msg, verbose=True):
    if verbose:
        print(msg, file=sys.stderr, flush=True)


def read_jsonl(path, bad=None):
    """Records of a JSONL file. A line that doesn't parse (a write cut off by a crash or power
    loss) is skipped, and noted in `bad` as (path, line number) when a list is given."""
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                if bad is not None:
                    bad.append((path, i))
    return out


def _end_line(path):
    """A cut-off write leaves a last line with no newline; end it so the next append starts
    a line of its own instead of fusing with the fragment."""
    if os.path.exists(path) and os.path.getsize(path):
        with open(path, "rb+") as f:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")


class State:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.nodes_path = os.path.join(out_dir, "nodes.jsonl")
        self.edges_path = os.path.join(out_dir, "edges.jsonl")
        self.posts_path = os.path.join(out_dir, "posts.jsonl")
        self.nodes = {}            # username -> latest record
        self.edges = []
        self.post_keys = set()     # (channel, id) already saved
        self.bad_lines = []        # (file, line) that could not be read: cut-off writes
        for path in (self.nodes_path, self.edges_path, self.posts_path):
            _end_line(path)
        for p in read_jsonl(self.posts_path, self.bad_lines):
            self.post_keys.add((p["channel"], p["id"]))
        for rec in read_jsonl(self.nodes_path, self.bad_lines):
            self.nodes.setdefault(rec["username"], {}).update(rec)
        self.edges.extend(read_jsonl(self.edges_path, self.bad_lines))

    def preload(self, run_dirs):
        """Resume from earlier runs (oldest first) without touching their folders: names they
        finished count as done and their links seed the frontier, so only what they did not
        reach is visited. Their posts stay in their own runs; whatever this run reads is
        written to its own folder as new captures. A channel that was cut off mid-crawl has no
        finished record, so it is read again from its first page."""
        for d in run_dirs:
            merged = {}
            for rec in read_jsonl(os.path.join(d, "nodes.jsonl"), self.bad_lines):
                merged.setdefault(rec["username"], {}).update(rec)
            for name, rec in merged.items():
                if rec.get("status") in ("crawled", "classified") and \
                        self.nodes.get(name, {}).get("status") not in ("crawled", "classified"):
                    self.nodes[name] = rec
            self.edges.extend(read_jsonl(os.path.join(d, "edges.jsonl"), self.bad_lines))
        return len(self.nodes)

    def save_node(self, rec):
        self.nodes.setdefault(rec["username"], {}).update(rec)
        with open(self.nodes_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def save_posts(self, posts):
        """Append posts not already on file. Returns how many were new."""
        new = [p for p in posts if (p["channel"], p["id"]) not in self.post_keys]
        if new:
            with open(self.posts_path, "a", encoding="utf-8") as f:
                for p in new:
                    self.post_keys.add((p["channel"], p["id"]))
                    f.write(json.dumps(p, ensure_ascii=False) + "\n")
        return len(new)

    def save_edges(self, edges):
        self.edges.extend(edges)
        with open(self.edges_path, "a", encoding="utf-8") as f:
            for e in edges:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #
# Live progress (read by the investigator UI's monitor)
# --------------------------------------------------------------------------- #

class Progress:
    """Writes <out>/progress.json: what the crawler is doing right now, running
    totals, per-channel results and a feed of recent events."""

    def __init__(self, out, args, seeds):
        self.path = os.path.join(out, "progress.json")
        self.stop_file = os.path.join(out, "STOP")
        self.t0 = time.monotonic()          # elapsed and pacing use the monotonic clock: sleep or a clock jump can't skew them
        self._last_write = 0.0
        self.page_times = []
        self.profiles = self.unclassified = 0     # names checked / left "unknown" although the page had a title
        self.d = {
            "tool": "tgspider", "phase": "starting", "started_at": _now(), "updated_at": _now(),
            "seeds": seeds, "pid": os.getpid(),
            "settings": {"depth": args.depth + 1, "pages": args.pages, "max_channels": args.max_channels,
                         "media": args.media, "delay": args.delay,
                         "network_only": bool(getattr(args, "network_only", False))},
            "current": None, "waiting": None, "eta_seconds": None,
            "queue": {"crawl": 0, "classify": 0, "planned_crawl": 0},
            "totals": {k: 0 for k in (
                "requests", "pages", "posts_seen", "posts_saved", "media_files", "media_bytes",
                "media_failed", "media_skipped", "channels_crawled", "names_classified", "signals",
                "discovered", "retries", "errors")},
            "channels": [], "events": [], "warnings": [],
        }

    def warn(self, code, msg, channel=None, sha=None):
        """A parser warning: something the crawler expected on a page and did not find. Kept in
        its own list (events scroll away) and counted; repeats for the same channel and code
        are folded into one entry."""
        for w in self.d["warnings"]:
            if w["code"] == code and w["channel"] == channel:
                w["count"] += 1
                return
        self.inc("parser_warnings")
        if len(self.d["warnings"]) < 100:
            self.d["warnings"].append({"code": code, "channel": channel, "message": msg, "count": 1,
                                       "page_sha256": sha, "at": _now()})
        self.event(f"Parser warning: {msg}", "warn")

    def event(self, msg, level="info"):
        self.d["events"].append({"t": _now(), "level": level, "msg": msg})
        del self.d["events"][:-200]
        self.write(force=True)

    def inc(self, key, n=1):
        self.d["totals"][key] = self.d["totals"].get(key, 0) + n

    def cur(self, **kw):
        if self.d["current"] is not None:
            self.d["current"].update(kw)
        self.write()

    def page_done(self, seconds):
        self.page_times.append(seconds)
        del self.page_times[:-30]

    def estimate(self, args):
        """Rough time left: pages still to read x average page time (which already
        includes media downloads), plus names to classify."""
        if not self.page_times:
            return None
        avg = sum(self.page_times) / len(self.page_times)
        c = self.d["current"] or {}
        left_here = max(0, (c.get("pages_max") or 0) - (c.get("page") or 0)) if c.get("mode") == "crawl" else 0
        q = self.d["queue"]
        return int((left_here + q["planned_crawl"] * args.pages) * avg + q["classify"] * (args.delay + 0.4))

    def write(self, force=False):
        now = time.monotonic()
        if not force and now - self._last_write < 0.4:
            return
        self._last_write = now
        self.d["updated_at"] = _now()
        self.d["elapsed_seconds"] = int(now - self.t0)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False)
        os.replace(tmp, self.path)


PROG = None          # set by crawl(); functions below report to it when present


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def prog():
    return PROG


# --------------------------------------------------------------------------- #
# Crawl
# --------------------------------------------------------------------------- #

# What was found on a page that is not evidence of a bad crawl but of a bad *read*: posts from such a
# page are not saved (the raw page still is), because re-saving them would overwrite good earlier
# captures with empty or partial fields. Fix the parser, re-crawl, and they are read properly.
HOLD_BACK = {"posts_dropped", "date_missing", "posts_blank"}


def needs_work(rec, d, depth_idx, classify_leaves=True):
    """Does a name at hop `d` still need a visit? `rec` is its latest record (None = never seen).
    Errors are retried by the next run, not within the same one (see `attempted` in crawl())."""
    if rec is None or rec.get("status") == "error":
        return d <= depth_idx + (1 if classify_leaves else 0)
    # A channel only classified on an earlier, shallower run gets crawled now
    return (d <= depth_idx and rec.get("status") == "classified"
            and rec.get("type") == "channel" and rec.get("preview"))


def hop_depths(seeds, edges, nodes):
    """Depth of every known username = shortest hop count from a seed."""
    depth = {s: 0 for s in seeds}
    for e in edges:
        d = depth.get(e["source"], nodes.get(e["source"], {}).get("depth"))
        if d is not None and (e["target"] not in depth or depth[e["target"]] > d + 1):
            depth[e["target"]] = d + 1
    for n, rec in nodes.items():
        depth.setdefault(n, rec.get("depth", 0))
    return depth


def remaining_work(run_dirs, seeds, depth, max_channels, classify_leaves=True):
    """What a crawl still has to do, read from its run folders alone (a chain of resumed runs is
    given oldest first). `depth` is the job's hop count (1 = seeds only). Returns
    {"crawled": n, "crawl": [channels left to read], "classify": [names left to check],
     "failed": [{"username", "error"}]}. This is what a resume would visit."""
    nodes, edges = {}, []
    for d in run_dirs:
        merged = {}
        for rec in read_jsonl(os.path.join(d, "nodes.jsonl")):
            merged.setdefault(rec["username"], {}).update(rec)
        nodes.update(merged)                      # a later run's record replaces an earlier one
        edges.extend(read_jsonl(os.path.join(d, "edges.jsonl")))
    idx = max(1, depth) - 1
    todo = sorted((d, n) for n, d in hop_depths(seeds, edges, nodes).items()
                  if needs_work(nodes.get(n), d, idx, classify_leaves))
    crawled = sum(1 for r in nodes.values() if r.get("status") == "crawled")
    return {"crawled": crawled,
            "crawl": [n for d, n in todo if d <= idx][:max(0, max_channels - crawled)],
            "classify": [n for d, n in todo if d > idx],
            "failed": [{"username": n, "error": r.get("error")} for n, r in sorted(nodes.items())
                       if r.get("status") == "error"]}


def _tally_profile(info):
    if PROG:
        PROG.profiles += 1
        if info["type"] == "unknown" and info.get("title"):
            PROG.unclassified += 1


def visit(name, depth, args, fetcher, state, partial=None):
    """Fetch a username. If it's a channel with a public preview, crawl its
    posts. Returns the node record and a list of edges. `partial` (a dict) is filled as
    pages are read, so a failure part-way can still say how far it got."""
    partial = partial if partial is not None else {}
    final_url, page = fetcher.get(f"{BASE_URL}/s/{name}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    is_preview = "/s/" in final_url and "tgme_channel_info" in page

    if not is_preview:
        info = classify_profile(page)
        _tally_profile(info)
        warns = note_issues(name, profile_issues(page, final_url) + fetch_issues(fetcher), fetcher)
        rec = {"username": name, "type": info["type"], "title": info.get("title"),
               "subscribers": info.get("subscribers"), "extra": info.get("extra"),
               "preview": False, "depth": depth, "status": "classified",
               "posts_scanned": 0, "checked_at": now}
        if warns:
            rec["parse_warnings"] = warns
        if info["type"] == "channel":
            rec["note"] = "public preview disabled; posts not readable without login"
        edges = []
        if info["type"] in ("channel", "group"):
            for u in description_signals(info.get("description_html"), name):
                edges.append({"source": name, "target": u, "type": "description",
                              "post_id": None, "date": None})
        return rec, edges

    header, messages, oldest = parse_channel_page(page, name)
    issues = page_issues(page, header, messages, True) + fetch_issues(fetcher)
    warns = note_issues(name, issues, fetcher, 1)
    if PROG:
        PROG.cur(title=header.get("title"), subscribers=header.get("subscribers"))
    link_page(messages, fetcher)
    all_msgs = list(messages)
    t_page = time.monotonic()
    pages = 1
    _page_progress(name, pages, messages, all_msgs, t_page, args)
    saved = scrape(messages, args, state, hold=_hold(name, issues, messages, fetcher, warns, 1))
    partial.update(pages=pages, posts=len(all_msgs), saved=saved)
    _page_saved(saved, t_page, args)
    # Re-crawls (--state) stop once they've gone past the re-check window: everything
    # older was captured before and is outside the edit/deletion check.
    st = (getattr(args, "recrawl", None) or {}).get(name) or {}
    stop_at = st.get("recheck_min_id")
    while oldest and pages < args.pages and oldest > 1 and not (stop_at and oldest <= stop_at):
        t_page = time.monotonic()
        _, page = fetcher.get(f"{BASE_URL}/s/{name}?before={oldest}")
        _, msgs, new_oldest = parse_channel_page(page, name)
        issues = page_issues(page, {}, msgs, False) + fetch_issues(fetcher)
        if msgs and (new_oldest is None or new_oldest >= oldest):
            issues.append(("pagination_stuck", f"the page before post {oldest} did not go back any further"))
        warns += note_issues(name, issues, fetcher, pages + 1)
        if not msgs or new_oldest is None or new_oldest >= oldest:
            if PROG:
                if issues:
                    PROG.event(f"@{name}: stopped after {pages} page(s); the next page could not be read "
                               "as expected", "warn")
                else:
                    PROG.event(f"@{name}: reached the start of the channel after {pages} page(s)")
            break
        link_page(msgs, fetcher)
        all_msgs.extend(msgs)
        oldest = new_oldest
        pages += 1
        _page_progress(name, pages, msgs, all_msgs, t_page, args)
        saved += scrape(msgs, args, state, hold=_hold(name, issues, msgs, fetcher, warns, pages))
        partial.update(pages=pages, posts=len(all_msgs), saved=saved)
        _page_saved(saved, t_page, args)

    edges = []
    for u in description_signals(header.get("description_html"), name):
        edges.append({"source": name, "target": u, "type": "description",
                      "post_id": None, "date": None})
    for m in all_msgs:
        for u, t in m["signals"]:
            edges.append({"source": name, "target": u, "type": t,
                          "post_id": m["id"], "date": m["date"]})

    dates = sorted(m["date"] for m in all_msgs if m["date"])
    ids = [m["id"] for m in all_msgs if m["id"] is not None]
    rec = {"username": name, "type": "channel", "title": header.get("title"),
           "subscribers": header.get("subscribers"), "preview": True,
           "tg_id": header.get("tg_id"), "description": text_of(header.get("description_html")) or None,
           "depth": depth, "status": "crawled", "posts_scanned": len(all_msgs),
           "pages": pages, "oldest_post": dates[0] if dates else None,
           "newest_post": dates[-1] if dates else None, "posts_saved": saved,
           # every post with an id in this range that still exists was on the pages read
           # (the first page is always the channel's newest, so the range runs up to now).
           # Not claimed when any page did not read as expected: deletion detection must never guess.
           "covered": {"min_id": min(ids), "max_id": max(ids), "to_latest": True} if ids and not warns else None,
           "checked_at": now}
    if warns:
        rec["parse_warnings"] = warns
    return rec, edges


def _hold(name, issues, msgs, fetcher, warns, page_no):
    """Should this page's posts be kept out of posts.jsonl? (See HOLD_BACK.)"""
    if not any(code in HOLD_BACK for code, _ in issues):
        return False
    warns += note_issues(name, [("posts_not_saved", f"{len(msgs)} posts on this page were not saved because it "
                                                    "did not read as expected; the raw page is kept")],
                         fetcher, page_no)
    if PROG:
        PROG.inc("posts_held_back", len(msgs))
    return True


def _page_saved(saved, t_page, args):
    """After a page's posts (and media) are saved: update counts and the ETA."""
    if not PROG:
        return
    PROG.page_done(time.monotonic() - t_page)
    PROG.d["eta_seconds"] = PROG.estimate(args)
    PROG.cur(posts_saved=saved, activity="reading next page")


def _page_progress(name, page_no, msgs, all_msgs, t_page, args):
    if not PROG:
        return
    dates = sorted(m["date"] for m in all_msgs if m["date"])
    sig = sum(len(m["signals"]) for m in msgs)
    PROG.inc("pages")
    PROG.inc("posts_seen", len(msgs))
    PROG.inc("signals", sig)
    PROG.cur(page=page_no, posts=len(all_msgs),
             oldest=dates[0] if dates else None, newest=dates[-1] if dates else None,
             signals=(PROG.d["current"] or {}).get("signals", 0) + sig, activity="saving posts")
    PROG.event(f"@{name} page {page_no}/{args.pages}: {len(msgs)} posts, {sig} channel signals"
               + (f", back to {dates[0][:10]}" if dates else ""))


def link_page(messages, fetcher):
    """Record which saved raw page each post was read from (evidence trail)."""
    for m in messages:
        m["post"]["page_sha256"] = fetcher.last_sha


def scrape(messages, args, state, hold=False):
    """Save the posts from one preview page (and their media, if asked). `hold` keeps them out
    (the page did not read as expected; the raw page is already kept)."""
    if args.no_posts or hold:
        return 0
    posts = [m["post"] for m in messages if m["id"] is not None
             and (m["post"]["channel"], m["id"]) not in state.post_keys]
    have = {(ch, i) for ch, st in (getattr(args, "recrawl", None) or {}).items()
            for i in st.get("have_media_ids", [])}
    if args.media != "none":
        total = sum(len(p["photos"]) + len(p["video_thumbs"]) + (len(p["videos"]) if args.media == "all" else 0)
                    for p in posts if (p["channel"], p["id"]) not in have)
        if PROG and total:
            c = PROG.d["current"] or {}
            PROG.cur(media_total=c.get("media_total", 0) + total, activity="downloading media")
        for p in posts:
            # re-crawls don't fetch again what's already in the evidence store
            p["media_files"] = [] if (p["channel"], p["id"]) in have else download_media(p, args)
    n = state.save_posts(posts)
    if PROG:
        PROG.inc("posts_saved", n)
    return n


MEDIA_TRIES = 4
MEDIA_NO_RETRY = (400, 401, 403, 404, 410)     # link expired or removed: asking again won't help


def fetch_media(url, dest, kind, cap):
    """Download one file to <dest>.part and move it into place only once it is complete: the
    announced length is met, it is not empty and it is not an error page. Returns
    ("ok", bytes) or ("skipped", size) for a video over `cap`. Raises for anything else; the
    partial file is always removed, so a truncated file can never be hashed as evidence."""
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            want = r.headers.get("Content-Length")
            want = int(want) if want and want.strip().isdigit() else None
            if kind == "video" and want and want > cap:
                return "skipped", want
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
                raise ValueError(f"the server sent {ctype}, not a {kind}")
            end, written, first_chunk = time.monotonic() + MEDIA_DEADLINE, 0, True
            with open(part, "wb") as f:
                while chunk := r.read(1 << 16):
                    if first_chunk and chunk.lstrip()[:1] == b"<":
                        raise ValueError("the server sent a web page instead of a file")
                    first_chunk = False
                    written += len(chunk)
                    if kind == "video" and written > cap:       # no Content-Length: found out on the way
                        return "skipped", written
                    f.write(chunk)
                    if time.monotonic() > end:
                        raise TimeoutError(f"download took longer than {MEDIA_DEADLINE}s")
                    if PROG:
                        PROG.write()                            # heartbeat: a slow big file is not a hang
        if not written:
            raise ValueError("the server sent an empty file")
        if want is not None and written != want:
            raise ConnectionError(f"connection closed early: got {written} of {want} bytes")
        os.replace(part, dest)
        return "ok", written
    finally:
        if os.path.exists(part):
            os.remove(part)


def download_media(post, args):
    """Download a post's media. CDN links expire, so this is the only time
    they can be fetched reliably. Returns local paths relative to --out.
    A file that cannot be fetched is recorded in post["media_failed"] (with the reason), which
    is kept in posts.jsonl and audited on import, rather than only logged."""
    urls = [("photo", u) for u in post["photos"]]
    urls += [("thumb", u) for u in post["video_thumbs"]]
    if args.media == "all":
        urls += [("video", u) for u in post["videos"]]
    folder = os.path.join(args.out, "media", post["channel"])
    cap = args.max_video_mb * 1024 * 1024
    files = []
    for i, (kind, url) in enumerate(urls):
        if url.startswith("//"):
            url = "https:" + url
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1] or (".mp4" if kind == "video" else ".jpg")
        rel = os.path.join("media", post["channel"], f"{post['id']}_{i}_{kind}{ext}")
        dest = os.path.join(args.out, rel)
        if os.path.exists(dest):
            files.append(rel)
            continue
        os.makedirs(folder, exist_ok=True)
        outcome, err, pause = None, None, 2.0
        for attempt in range(MEDIA_TRIES):        # the CDN throws occasional 500s
            try:
                outcome, size = fetch_media(url, dest, kind, cap)
                err = None
                break
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code}" + (" (the link has probably expired)" if e.code in MEDIA_NO_RETRY else "")
                if e.code in MEDIA_NO_RETRY:
                    break
                ra = (e.headers.get("Retry-After") or "").strip()
                pause = min(int(ra), 120) if ra.isdigit() else pause
            except (OSError, http.client.HTTPException, ValueError) as e:
                err = f"{type(e).__name__}: {e}"
            if attempt < MEDIA_TRIES - 1:
                nap(pause * random.uniform(0.8, 1.3))
                pause = min(pause * 2, 60)
        if outcome == "skipped":
            log(f"    - skipped {size // 2**20} MB video in post {post['id']}", not args.quiet)
            if PROG:
                PROG.inc("media_skipped")
                PROG.event(f"skipped {size // 2**20} MB video in post {post['id']} (over --max-video-mb)")
        elif outcome == "ok":
            files.append(rel)
            if PROG:
                PROG.inc("media_files")
                PROG.inc("media_bytes", size)
                c = PROG.d["current"] or {}
                PROG.cur(media_done=c.get("media_done", 0) + 1,
                         media_bytes=c.get("media_bytes", 0) + size,
                         activity=f"downloading {kind} for post {post['id']}")
        else:
            log(f"    - media failed for post {post['id']}: {err}", not args.quiet)
            post.setdefault("media_failed", []).append({"kind": kind, "index": i, "url": url, "error": err})
            if PROG:
                PROG.inc("media_failed")
                c = PROG.d["current"] or {}
                PROG.cur(media_done=c.get("media_done", 0) + 1)
                PROG.event(f"{kind} for post {post['id']} could not be downloaded: {err}", "warn")
        time.sleep(0.2)
    return files


def final_checks():
    """Run-level sanity checks, once the crawl loop has ended."""
    t = PROG.d["totals"]
    if t.get("channels_crawled", 0) >= 3 and not t.get("posts_seen"):
        PROG.warn("no_posts_run", f"{t['channels_crawled']} channels were read but not one post was found "
                                  "(Telegram's markup may have changed, or these channels are empty)")
    if PROG.profiles >= 8 and PROG.unclassified >= 0.8 * PROG.profiles:
        PROG.warn("profiles_unclassified", f"{PROG.unclassified} of {PROG.profiles} names could not be told "
                                           "apart (channel, group, bot or user): the profile page layout may "
                                           "have changed")
    if t.get("media_failed"):
        PROG.warn("media_failed", f"{t['media_failed']} media files could not be downloaded (see the "
                                  "activity feed; the posts themselves were saved)")


def crawl(seeds, args):
    state = State(args.out)
    fetcher = Fetcher(args.delay, retries=getattr(args, "retries", 5), verbose=not args.quiet,
                      pages_dir=None if args.no_save_pages else args.out,
                      max_requests=getattr(args, "max_requests", 0))
    say = lambda m: log(m, not args.quiet)
    resume = [os.path.abspath(d) for d in (getattr(args, "resume_from", None) or [])]
    if resume:
        state.preload(resume)

    # depth of every known username = shortest hop count from a seed
    depth = hop_depths(seeds, state.edges, state.nodes)

    if state.nodes:
        say(f"Resuming: {len(state.nodes)} usernames already checked, "
            f"{len(state.edges)} edges on file.")

    crawled = sum(1 for r in state.nodes.values() if r.get("status") == "crawled")
    classified_this_run = 0
    t0 = time.monotonic()
    global PROG
    PROG = Progress(args.out, args, seeds)
    if resume:
        PROG.d["resumed_from"] = resume
        PROG.d["settings"]["resumed_channels"] = crawled
    PROG.event(f"Started: {len(seeds)} seed(s), depth {args.depth + 1}, {args.pages} pages per channel"
               + (f"; resuming, {crawled} channels already read" if resume else ""))
    if state.bad_lines:
        PROG.warn("state_lines_skipped", f"{len(state.bad_lines)} unreadable line(s) in earlier crawl files were "
                                         "skipped (a write cut off by a crash); a few links or names may be missing")

    attempted = set()        # a name that failed is retried by the next run (or a resume), not in this one
    stop_file = PROG.stop_file
    stopped, abort = False, None
    while True:
        # Graceful stop: the UI (or you) creates <out>/STOP; we finish the current
        # channel, then stop here with everything saved.
        if os.path.exists(stop_file):
            say("Stop requested: stopping after the channel just finished.")
            PROG.event("Stopped on request after finishing the current channel", "warn")
            stopped = True
            break
        max_runtime = getattr(args, "max_runtime", 0)
        if max_runtime and time.monotonic() - t0 > max_runtime:
            abort = (f"the time limit of {max_runtime // 60} minutes was reached", "limit")
            break
        # Pick the shallowest username still needing work
        pending = [(d, n) for n, d in depth.items()
                   if n not in attempted and needs_work(state.nodes.get(n), d, args.depth, not args.no_classify_leaves)]
        if not pending:
            break
        pending.sort()
        d, name = pending[0]
        leaf = d > args.depth
        n_crawl = sum(1 for dd, _ in pending if dd <= args.depth)
        PROG.d["queue"] = {"crawl": n_crawl, "classify": len(pending) - n_crawl,
                           "planned_crawl": max(0, min(n_crawl, args.max_channels - crawled) - (0 if leaf else 1))}
        PROG.d["totals"]["discovered"] = max(0, len(depth) - len(seeds))

        if not leaf and crawled >= args.max_channels:
            say(f"Reached --max-channels {args.max_channels}; classifying remaining names only.")
            args.depth = d - 1          # everything left becomes a leaf
            continue
        if leaf and args.max_classify and classified_this_run >= args.max_classify:
            say(f"Reached --max-classify {args.max_classify}; stopping.")
            break

        attempted.add(name)
        say(f"[d{d}] {'classify' if leaf else 'crawl   '} @{name} ...")
        t_name = time.monotonic()
        known_before = set(depth)
        PROG.d["phase"] = "classifying" if leaf else "crawling"
        PROG.d["current"] = {"channel": name, "depth": d, "mode": "classify" if leaf else "crawl",
                             "started_at": _now(), "page": 0, "pages_max": 1 if leaf else args.pages,
                             "posts": 0, "posts_saved": 0, "media_total": 0, "media_done": 0,
                             "media_bytes": 0, "signals": 0,
                             "activity": "checking what this is" if leaf else "reading first page"}
        PROG.d["eta_seconds"] = PROG.estimate(args)
        PROG.event(f"{'Checking' if leaf else 'Crawling'} @{name} (hop {d})")
        partial = {}
        try:
            if leaf:
                final_url, page = fetcher.get(f"{BASE_URL}/s/{name}")
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                if "/s/" in final_url and "tgme_channel_info" in page:
                    header, msgs, _ = parse_channel_page(page, name)
                    warns = note_issues(name, page_issues(page, header, msgs, True) + fetch_issues(fetcher),
                                        fetcher, 1)
                    rec = {"username": name, "type": "channel", "title": header.get("title"),
                           "subscribers": header.get("subscribers"), "preview": True,
                           "tg_id": header.get("tg_id"), "description": text_of(header.get("description_html")) or None,
                           "depth": d, "status": "classified", "posts_scanned": 0, "checked_at": now}
                else:
                    info = classify_profile(page)
                    _tally_profile(info)
                    warns = note_issues(name, profile_issues(page, final_url) + fetch_issues(fetcher), fetcher)
                    rec = {"username": name, "type": info["type"], "title": info.get("title"),
                           "subscribers": info.get("subscribers"), "extra": info.get("extra"),
                           "preview": False, "depth": d, "status": "classified",
                           "posts_scanned": 0, "checked_at": now}
                if warns:
                    rec["parse_warnings"] = warns
                edges = []
            else:
                rec, edges = visit(name, d, args, fetcher, state, partial)
        except RunAbort as e:                        # the whole run ends, not just this channel
            if e.kind == "stop":
                stopped = True
                PROG.event("Stopped on request", "warn")
            else:
                abort = (e.reason, e.kind)
            break
        except Exception as e:                       # keep going on one bad name
            say(f"    ! failed: {e}")
            PROG.inc("errors")
            PROG.event(f"@{name} failed{' after ' + str(partial['pages']) + ' page(s)' if partial.get('pages') else ''}: {e}",
                       "error")
            rec, edges = {"username": name, "type": "error", "depth": d, "status": "error", "error": str(e),
                          "pages_read": partial.get("pages", 0), "posts_saved": partial.get("saved", 0),
                          "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, []

        state.save_node(rec)
        if leaf:
            classified_this_run += 1
        if edges:
            state.save_edges(edges)
        new = {e["target"] for e in edges}
        for u in new:
            if u not in depth or depth[u] > d + 1:
                depth[u] = d + 1
        c = PROG.d["current"] or {}
        PROG.d["channels"].append({
            "username": name, "mode": "classify" if leaf else "crawl", "depth": d,
            "type": rec.get("type"), "title": rec.get("title"), "subscribers": rec.get("subscribers"),
            "status": rec.get("status"), "posts": rec.get("posts_scanned", 0),
            "posts_saved": rec.get("posts_saved", 0), "pages": rec.get("pages", 0),
            "signals": len(edges), "new_names": len(new - known_before),
            "media": c.get("media_done", 0), "media_bytes": c.get("media_bytes", 0),
            "oldest": rec.get("oldest_post"), "seconds": round(time.monotonic() - t_name, 1),
            "error": rec.get("error"), "warnings": len(rec.get("parse_warnings") or [])})
        if leaf:
            PROG.inc("names_classified")
        if rec.get("status") == "crawled":
            PROG.inc("channels_crawled")
            PROG.event(f"Finished @{name}: {rec['posts_scanned']} posts ({rec.get('posts_saved', 0)} new), "
                       f"{len(edges)} signals pointing to {len(new)} usernames", "good")
        elif rec.get("status") != "error":
            PROG.event(f"@{name} is a {rec['type']}" + (f" ({rec['title']})" if rec.get("title") else ""))
        PROG.d["current"] = None
        PROG.write(force=True)
        if rec.get("status") == "crawled":
            crawled += 1
            say(f"    {rec.get('title') or ''} | {rec.get('subscribers') or '?'} subs | "
                f"{rec['posts_scanned']} posts ({rec.get('posts_saved', 0)} new saved) | "
                f"{len(edges)} signals -> {len(new)} usernames")
        else:
            say(f"    {rec['type']}: {rec.get('title') or ''} {rec.get('extra') or ''}".rstrip())
        if fetcher.fail_streak >= BREAKER_LIMIT:
            abort = (f"Telegram refused {fetcher.fail_streak} requests in a row (last: {rec.get('error')}); "
                     "stopping so it is not hammered", "breaker")
            break

    PROG.d["current"] = None
    if abort:
        PROG.d["abort"] = {"reason": abort[0], "kind": abort[1]}
        say(f"Stopping early: {abort[0]}.")
        PROG.event(f"Stopped early: {abort[0]}. Everything collected is kept; the rest can be resumed.", "error")
    final_checks()
    say(f"Done in {time.monotonic() - t0:.0f}s, {fetcher.requests} requests.")
    PROG.d["phase"] = "writing reports"
    PROG.d["eta_seconds"] = 0
    PROG.write(force=True)
    write_reports(state, seeds, args)
    PROG.d["phase"] = "aborted" if abort else "stopped" if stopped else "done"
    PROG.event(f"{'Stopped early' if abort else 'Stopped' if stopped else 'Done'} in {time.monotonic() - t0:.0f}s: "
               f"{PROG.d['totals']['channels_crawled']} channels crawled, "
               f"{PROG.d['totals']['posts_saved']} new posts", "warn" if abort else "good")



# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #

def write_reports(state, seeds, args):
    out = args.out
    seedset = set(seeds)

    # Which seeds can reach each node (for "how many seeds lead here")
    children = defaultdict(set)
    for e in state.edges:
        children[e["source"]].add(e["target"])
    reach = defaultdict(set)
    for s in seedset:
        stack, seen = [s], {s}
        while stack:
            n = stack.pop()
            reach[n].add(s)
            for c in children.get(n, ()):
                if c not in seen:
                    seen.add(c)
                    stack.append(c)

    agg = defaultdict(lambda: {"signals": 0, "sources": set(),
                               "types": defaultdict(int)})
    for e in state.edges:
        a = agg[e["target"]]
        a["signals"] += 1
        a["sources"].add(e["source"])
        a["types"][e["type"]] += 1

    rows = []
    for name in set(state.nodes) | set(agg):
        n = state.nodes.get(name, {})
        a = agg.get(name, {"signals": 0, "sources": set(), "types": {}})
        rows.append({
            "username": name,
            "link": f"https://t.me/{name}",
            "type": n.get("type", "unchecked"),
            "title": n.get("title") or "",
            "subscribers": n.get("subscribers") if n.get("subscribers") is not None else "",
            "is_seed": name in seedset,
            "depth": n.get("depth", ""),
            "status": n.get("status", "unchecked"),
            "seeds_reaching": len(reach.get(name, ())) if name not in seedset else "",
            "linked_from": len(a["sources"]),
            "total_signals": a["signals"],
            "forwards": a["types"].get("forward", 0),
            "links": a["types"].get("link", 0),
            "mentions": a["types"].get("mention", 0),
            "description": a["types"].get("description", 0),
            "linked_from_list": " ".join(sorted(a["sources"])),
            "posts_scanned": n.get("posts_scanned", ""),
            "note": n.get("note") or n.get("error") or
                    (n.get("extra") or "" if n.get("type") == "unknown" else ""),
        })

    type_rank = {"channel": 0, "group": 1, "unchecked": 2, "unknown": 3, "bot": 4, "user": 5, "error": 6}
    rows.sort(key=lambda r: (not r["is_seed"], type_rank.get(r["type"], 9),
                             -r["linked_from"], -r["total_signals"],
                             -(r["subscribers"] or 0) if r["subscribers"] != "" else 0,
                             r["username"]))

    fields = list(rows[0].keys()) if rows else []
    with open(os.path.join(out, "channels.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(out, "channels.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    # Edge list collapsed per (source, target, type) — importable into Gephi
    coll = defaultdict(lambda: {"count": 0, "first": None, "last": None, "posts": []})
    for e in state.edges:
        c = coll[(e["source"], e["target"], e["type"])]
        c["count"] += 1
        if e.get("date"):
            c["first"] = min(filter(None, [c["first"], e["date"]]))
            c["last"] = max(filter(None, [c["last"], e["date"]]))
        if e.get("post_id") and len(c["posts"]) < 5:
            c["posts"].append(f"https://t.me/{e['source']}/{e['post_id']}")
    with open(os.path.join(out, "edges.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Source", "Target", "type", "Weight", "first_seen", "last_seen", "example_posts"])
        for (s, t, ty), c in sorted(coll.items(), key=lambda kv: -kv[1]["count"]):
            w.writerow([s, t, ty, c["count"], c["first"] or "", c["last"] or "", " ".join(c["posts"])])

    # posts.csv — one row per scraped post, lists joined for spreadsheets
    n_posts = 0
    posts_path = os.path.join(out, "posts.jsonl")
    full = {}                                # from tgmedia.py (logged-in downloads)
    full_path = os.path.join(out, "media_full.jsonl")
    for r in read_jsonl(full_path):
        full[(r["channel"], r["id"])] = r
    if os.path.exists(posts_path):
        cols = ["channel", "id", "url", "date", "edited", "views", "text",
                "forwarded_from_username", "forwarded_from_name", "forwarded_from_url",
                "reply_to_url", "author_signature", "hashtags", "links", "link_preview_url",
                "media_types", "sensitive", "photos", "videos", "video_durations",
                "media_unavailable", "media_files", "media_files_full", "media_full_status", "reactions", "reactions_total", "scraped_at"]
        with open(os.path.join(out, "posts.csv"), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for p in read_jsonl(posts_path):         # a line cut off by a crash is skipped, not fatal
                row = dict(p)
                for k in ("hashtags", "links", "media_types", "photos", "videos",
                          "video_durations", "media_files"):
                    row[k] = " ".join(p.get(k) or [])
                row["media_unavailable"] = "; ".join(p.get("media_unavailable") or [])
                fr = full.get((p["channel"], p["id"]))
                if fr:
                    row["media_files_full"] = " ".join(fr["files"])
                    row["media_full_status"] = fr["status"] + (f" ({fr['note']})" if fr.get("note") else "")
                row["reactions_total"] = sum(v or 0 for v in (p.get("reactions") or {}).values())
                row["reactions"] = " ".join(f"{k}{v}" for k, v in (p.get("reactions") or {}).items())
                w.writerow(row)
                n_posts += 1

    # Console summary
    chans = [r for r in rows if r["type"] == "channel" and not r["is_seed"]]
    print(f"\nFound {len(chans)} channels "
          f"({sum(1 for r in chans if r['status'] == 'crawled')} crawled), "
          f"{sum(1 for r in rows if r['type'] == 'group')} groups, "
          f"{sum(1 for r in rows if r['type'] in ('user', 'bot'))} users/bots, "
          f"{sum(1 for r in rows if r['type'] in ('unknown', 'unchecked', 'error'))} unresolved.")
    if chans:
        print(f"\n{'channel':<28}{'subs':>10}  {'from':>4}  {'fwd':>4} {'lnk':>4} {'@':>4}  title")
        for r in chans[:args.top]:
            subs = f"{r['subscribers']:,}" if r["subscribers"] != "" else "?"
            print(f"{r['username']:<28}{subs:>10}  {r['linked_from']:>4}  "
                  f"{r['forwards']:>4} {r['links']:>4} {r['mentions']:>4}  {r['title'][:40]}")
    print(f"\nWrote {out}/channels.csv, channels.json, edges.csv"
          + (f", posts.csv ({n_posts} posts)" if n_posts else ""))


# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(
        description="Discover Telegram channels by spidering public channel previews (no login).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("seeds", nargs="*", help="seed channels: name, @name or t.me link")
    p.add_argument("-f", "--seeds-file", help="text file with one seed per line")
    p.add_argument("-d", "--depth", type=int, default=1,
                   help="hops to crawl from seeds (1 = crawl seeds only; 2 = also crawl what they link to)")
    p.add_argument("-p", "--pages", type=int, default=10,
                   help="preview pages to read per channel (~20 posts each)")
    p.add_argument("-m", "--max-channels", type=int, default=50,
                   help="stop crawling after this many channels (remaining names are just classified)")
    p.add_argument("--max-classify", type=int, default=500,
                   help="cap on uncrawled names to classify at the edge (0 = no cap)")
    p.add_argument("--no-classify-leaves", action="store_true",
                   help="don't check what the names at the outer edge are (faster)")
    p.add_argument("--network-only", action="store_true",
                   help="map the channel network only: record links between channels but keep no "
                        "post text, media or page copies")
    p.add_argument("--no-save-pages", action="store_true",
                   help="don't keep raw copies of fetched pages (kept by default, gzip + sha256, as evidence)")
    p.add_argument("--no-posts", action="store_true",
                   help="don't save post content, only discover channels")
    p.add_argument("--media", choices=["none", "photos", "all"], default="none",
                   help="download media while crawling: photos (+ video thumbnails) or all (+ videos)")
    p.add_argument("--max-video-mb", type=int, default=50, help="skip videos larger than this")
    p.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    p.add_argument("--retries", type=int, default=5,
                   help="times a failed request is retried (with growing waits) before the channel is given up on")
    p.add_argument("-o", "--out", default="tgspider_out",
                   help="output folder (re-use it to resume a run)")
    p.add_argument("--max-runtime", type=int, default=0,
                   help="stop cleanly after this many seconds (0 = no limit); what was collected is kept")
    p.add_argument("--max-requests", type=int, default=0,
                   help="stop cleanly after this many requests to Telegram (0 = no limit)")
    p.add_argument("--resume-from", action="append", default=[], metavar="RUN_DIR",
                   help="continue an earlier run folder (repeatable, oldest first): read-only, only what it "
                        "did not finish is visited, and new captures go to --out")
    p.add_argument("--top", type=int, default=30, help="rows to print in the summary")
    p.add_argument("--report-only", action="store_true",
                   help="just rebuild the reports from the --out folder")
    p.add_argument("--state", help="re-crawl state from the investigator (JSON): per channel, where the "
                                   "re-check window starts and which posts already have media")
    p.add_argument("-q", "--quiet", action="store_true")
    args = p.parse_args()
    args.recrawl = {}
    if args.state:
        with open(args.state, encoding="utf-8") as f:
            args.recrawl = json.load(f)

    raw = list(args.seeds)
    if args.seeds_file:
        with open(args.seeds_file, encoding="utf-8") as f:
            raw.extend(f.read().splitlines())
    seeds, bad = [], []
    for r in raw:
        s = normalize_seed(r)
        if s and s not in seeds:
            seeds.append(s)
        elif not s and r.strip() and not r.strip().startswith("#"):
            bad.append(r.strip())
    for b in bad:
        log(f"Skipping unrecognised seed: {b!r}")
    if not seeds:
        p.error("no valid seeds given")
    args.depth = max(1, args.depth) - 1   # internal: max depth index to crawl
    if args.network_only:
        args.no_posts, args.no_save_pages, args.media = True, True, "none"

    if args.report_only:
        write_reports(State(args.out), seeds, args)
        return
    # The app stops a stuck crawl with SIGINT, then SIGTERM: both must save progress and write
    # the reports, not die with the phase still "crawling".
    def on_sigterm(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_sigterm)
    try:
        crawl(seeds, args)
    except KeyboardInterrupt:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)         # a second signal must not cut the reports short
        log("\nInterrupted — progress saved. Re-run the same command to resume.")
        if PROG:
            PROG.d["phase"] = "cancelled"
            PROG.d["current"] = None
            PROG.event("Cancelled; everything collected so far is kept", "warn")
        write_reports(State(args.out), seeds, args)
    except Exception as e:
        # Anything unexpected: say so in the progress file (the app reads the phase) and keep what
        # was collected, then fail loudly so the exit code says the run did not finish.
        import traceback
        traceback.print_exc()
        if PROG:
            PROG.d["phase"] = "failed"
            PROG.d["current"] = None
            PROG.d["abort"] = {"reason": f"{type(e).__name__}: {e}", "kind": "crash"}
            PROG.event(f"The crawler crashed: {type(e).__name__}: {e}. Everything collected so far is kept.", "error")
        try:
            write_reports(State(args.out), seeds, args)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
