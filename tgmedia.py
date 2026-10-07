#!/usr/bin/env python3
"""
tgmedia — download full-size media for posts found by tgspider, using a
logged-in Telegram account (MTProto via Telethon).

The public web preview refuses anything but small videos ("Media is too big").
This script reads <out>/posts.jsonl from a tgspider run, picks the posts whose
media couldn't be saved, and downloads the originals through the Telegram API.

  Setup (once, by the account owner, in a terminal):
    python3 -m venv .venv && .venv/bin/pip install telethon cryptg
    get an API ID + hash at https://my.telegram.org  ->  API development tools
    .venv/bin/python tgmedia.py login

  `login` asks for the api_id / api_hash (hidden input), saves them, then asks for your phone number, the code Telegram
  sends you, and your 2FA password. The login is kept in
  ~/.config/tgmedia/ (tgmedia.session + credentials.json), owner-only.

  Afterwards nothing ever prompts, so it can be run unattended:
    .venv/bin/python tgmedia.py status                       # who is logged in
    .venv/bin/python tgmedia.py -o tgspider_out --dry-run     # what would be fetched
    .venv/bin/python tgmedia.py -o tgspider_out               # download
    .venv/bin/python tgmedia.py logout                        # end the session

  Credentials can also come from TG_API_ID / TG_API_HASH environment variables.

Results go to <out>/media/<channel>/<id>_<n>_full.<ext> and are logged in
<out>/media_full.jsonl; `tgspider.py --report-only` merges them into posts.csv.
Resumable: posts already in media_full.jsonl are skipped.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# Login state lives outside the project so it never lands in a synced folder or repo
CONFIG_DIR = os.environ.get("TGMEDIA_HOME", os.path.expanduser("~/.config/tgmedia"))
SESSION = os.path.join(CONFIG_DIR, "tgmedia")    # -> tgmedia.session
CREDS = os.path.join(CONFIG_DIR, "credentials.json")


# --------------------------------------------------------------------------- #
# Credentials and session
# --------------------------------------------------------------------------- #

def load_creds():
    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if (not api_id or not api_hash) and os.path.exists(CREDS):
        with open(CREDS, encoding="utf-8") as f:
            c = json.load(f)
        api_id, api_hash = c.get("api_id"), c.get("api_hash")
    return (int(api_id), api_hash) if api_id and api_hash else (None, None)


def make_client(api_id, api_hash):
    try:
        from telethon import TelegramClient
    except ImportError:
        sys.exit("Telethon isn't installed. Run:  .venv/bin/pip install telethon cryptg\n"
                 "and start this script with .venv/bin/python")
    os.umask(0o077)                          # session/creds files: owner-only
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    client = TelegramClient(SESSION, api_id, api_hash)
    client.flood_sleep_threshold = 120      # Telethon waits out short flood limits itself
    return client


def lock_down():
    for path in (CREDS, SESSION + ".session", SESSION + ".session-journal"):
        if os.path.exists(path):
            os.chmod(path, 0o600)


async def connect_authorized():
    """Connect with the saved login. Never prompts: exits if not logged in."""
    api_id, api_hash = load_creds()
    if not api_id:
        sys.exit("No API credentials. The account owner needs to run, in a terminal:\n"
                 "  .venv/bin/python tgmedia.py login")
    client = make_client(api_id, api_hash)
    await client.connect()
    lock_down()
    if not await client.is_user_authorized():
        await client.disconnect()
        sys.exit("Not logged in (or the session was revoked). The account owner needs to run:\n"
                 "  .venv/bin/python tgmedia.py login")
    return client


async def cmd_login():
    import getpass
    if not sys.stdin.isatty():
        sys.exit("`login` is interactive: run it yourself in a terminal.")
    api_id, api_hash = load_creds()
    if not api_id:
        print("API credentials from https://my.telegram.org -> API development tools")
        api_id = input("api_id: ").strip()
        api_hash = getpass.getpass("api_hash (hidden): ").strip()
        if not api_id.isdigit() or len(api_hash) != 32:
            sys.exit("That doesn't look right: api_id is a number, api_hash is 32 characters.")
        old = os.umask(0o077)
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        try:
            with open(CREDS, "w", encoding="utf-8") as f:
                json.dump({"api_id": int(api_id), "api_hash": api_hash}, f)
        finally:
            os.umask(old)
        api_id = int(api_id)
    client = make_client(api_id, api_hash)
    await client.start()                    # phone, code, 2FA password
    lock_down()
    me = await client.get_me()
    print(f"Logged in as {me.first_name or ''} (@{me.username or '-'}). "
          f"Session saved to {SESSION}.session; tgmedia can now run unattended.")
    await client.disconnect()


async def cmd_status():
    client = await connect_authorized()
    me = await client.get_me()
    print(f"Logged in as {me.first_name or ''} {me.last_name or ''}".rstrip()
          + f" (@{me.username or '-'}, id {me.id}).")
    await client.disconnect()


async def cmd_logout():
    api_id, api_hash = load_creds()
    if api_id and os.path.exists(SESSION + ".session"):
        client = make_client(api_id, api_hash)
        await client.connect()
        if await client.is_user_authorized():
            await client.log_out()          # ends the session on Telegram's side too
        await client.disconnect()
    for path in (SESSION + ".session", SESSION + ".session-journal"):
        if os.path.exists(path):
            os.remove(path)
    print("Logged out and session deleted. API credentials were kept "
          f"(delete {CREDS} too if you want them gone).")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Selection (pure; no network)
# --------------------------------------------------------------------------- #

def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def needs_full_media(post, mode):
    """Should this post's media be fetched through the API?"""
    types = post.get("media_types") or []
    files = post.get("media_files") or []
    if mode == "all":
        return any(t in types for t in ("photo", "video", "roundvideo", "voice",
                                        "audio", "document"))
    if post.get("media_unavailable"):
        return True
    return "video" in types and not any("_video" in f for f in files)


def select(posts, done, args):
    chans = {c.lower().lstrip("@") for c in args.channels} if args.channels else None
    out = []
    for p in posts:
        if (p["channel"], p["id"]) in done:
            continue
        if chans and p["channel"] not in chans:
            continue
        if args.ids and p["id"] not in args.ids:
            continue
        if needs_full_media(p, args.what):
            out.append(p)
    out.sort(key=lambda p: (p["channel"], -p["id"]))
    return out[:args.limit] if args.limit else out


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #

class Progress:
    """Writes <out>/progress.json for the investigator UI's live monitor."""

    def __init__(self, out, total):
        self.path = os.path.join(out, "progress.json")
        self.t0 = time.time()
        self._last = 0.0
        self.post_times = []
        self.d = {"tool": "tgmedia", "phase": "connecting", "started_at": _now(), "pid": os.getpid(),
                  "account": None, "current": None, "waiting": None, "eta_seconds": None,
                  "totals": {"posts_total": total, "posts_done": 0, "files": 0, "bytes": 0,
                             "skipped": 0, "errors": 0, "deleted": 0},
                  "posts": [], "events": []}

    def event(self, msg, level="info"):
        self.d["events"].append({"t": _now(), "level": level, "msg": msg})
        del self.d["events"][:-200]
        self.write(True)

    def write(self, force=False):
        now = time.time()
        if not force and now - self._last < 0.4:
            return
        self._last = now
        self.d["updated_at"] = _now()
        self.d["elapsed_seconds"] = int(now - self.t0)
        t = self.d["totals"]
        c = self.d["current"] or {}
        if self.post_times:
            avg = sum(self.post_times) / len(self.post_times)
            left = max(0, t["posts_total"] - t["posts_done"] - (1 if c else 0))
            here = 0
            if c.get("speed") and c.get("file_bytes_total"):
                here = (c["file_bytes_total"] - c.get("file_bytes_done", 0)) / c["speed"]
            self.d["eta_seconds"] = int(left * avg + here)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False)
        os.replace(tmp, self.path)


PROG = None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def run(targets, args):
    global PROG
    PROG = Progress(args.out, len(targets))
    PROG.event(f"Connecting to Telegram to download {len(targets)} post(s)")
    client = await connect_authorized()
    from telethon import errors
    log_path = os.path.join(args.out, "media_full.jsonl")
    by_channel = defaultdict(list)
    for p in targets:
        by_channel[p["channel"]].append(p)

    async with client:
        me = await client.get_me()
        global ACCOUNT
        ACCOUNT = f"{me.first_name or ''} (@{me.username or '-'}, id {me.id})"
        log(f"Logged in as {ACCOUNT}.")
        PROG.d.update(account=ACCOUNT, phase="downloading")
        PROG.event(f"Logged in as {ACCOUNT}")

        total_bytes, t0 = 0, time.time()
        for channel, posts in by_channel.items():
            try:
                entity = await with_flood(lambda: client.get_entity(channel), errors)
            except (errors.ChannelPrivateError, errors.UsernameNotOccupiedError,
                    errors.UsernameInvalidError, ValueError) as e:
                log(f"@{channel}: can't open ({type(e).__name__}), skipping {len(posts)} posts")
                PROG.d["totals"]["errors"] += len(posts)
                PROG.d["totals"]["posts_done"] += len(posts)
                PROG.event(f"@{channel}: can't open ({type(e).__name__}); skipping {len(posts)} posts", "error")
                for p in posts:
                    record(log_path, p, [], "error", f"{type(e).__name__}")
                continue
            log(f"@{channel}: {len(posts)} posts")
            PROG.event(f"@{channel}: {len(posts)} post(s) to fetch")

            for p in posts:
                if os.path.exists(os.path.join(args.out, "STOP")):
                    PROG.event("Stopped on request after the current post", "warn")
                    PROG.d["phase"] = "stopped"
                    break
                t_post = time.time()
                PROG.d["current"] = {"channel": channel, "post": p["id"], "url": p.get("url"),
                                     "durations": p.get("video_durations") or [], "started_at": _now(),
                                     "activity": "looking up message", "file": None,
                                     "file_bytes_done": 0, "file_bytes_total": 0, "speed": None,
                                     "index": PROG.d["totals"]["posts_done"] + 1}
                PROG.write(True)
                try:
                    files, size, note = await fetch_post(client, entity, p, args, errors)
                    total_bytes += size
                    status = "ok" if files else ("skipped" if note else "no_media")
                    record(log_path, p, files, status, note)
                    mb = f"{size / 2**20:.1f} MB" if size else ""
                    log(f"  {p['id']}: {status} {len(files)} file(s) {mb} {note or ''}".rstrip())
                    PROG.d["totals"]["skipped"] += status == "skipped"
                    PROG.d["totals"]["deleted"] += note == "message deleted"
                    PROG.event(f"@{channel}/{p['id']}: {status}, {len(files)} file(s) {mb} {note or ''}".rstrip(),
                               "good" if files else "warn")
                except Exception as e:
                    record(log_path, p, [], "error", f"{type(e).__name__}: {e}")
                    log(f"  {p['id']}: error {type(e).__name__}: {e}")
                    status, files, size, note = "error", [], 0, f"{type(e).__name__}: {e}"
                    PROG.d["totals"]["errors"] += 1
                    PROG.event(f"@{channel}/{p['id']}: {note}", "error")
                secs = time.time() - t_post
                PROG.post_times.append(secs)
                PROG.d["totals"]["posts_done"] += 1
                PROG.d["posts"].append({"channel": channel, "post": p["id"], "status": status,
                                        "files": len(files), "bytes": size, "note": note,
                                        "seconds": round(secs, 1),
                                        "speed": round(size / secs) if size and secs else None})
                PROG.d["current"] = None
                PROG.write(True)

        if PROG.d["phase"] != "stopped":
            PROG.d["phase"] = "done"
        PROG.d["eta_seconds"] = 0
        PROG.event(f"Done: {total_bytes / 2**20:.0f} MB in {time.time() - t0:.0f}s", "good")
        log(f"Done: {total_bytes / 2**20:.0f} MB in {time.time() - t0:.0f}s. "
            f"Run `python3 tgspider.py <seeds> -o {args.out} --report-only` to update posts.csv.")


async def with_flood(fn, errors):
    """Run a Telethon call, sleeping through long flood waits."""
    while True:
        try:
            return await fn()
        except errors.FloodWaitError as e:
            log(f"  flood wait: sleeping {e.seconds}s (Telegram rate limit)")
            if PROG:
                PROG.d["waiting"] = {"reason": "Telegram rate limit (flood wait)", "seconds": e.seconds,
                                     "since": _now()}
                PROG.event(f"Telegram rate limit: waiting {e.seconds}s", "warn")
            await asyncio.sleep(e.seconds + 1)
            if PROG:
                PROG.d["waiting"] = None


async def fetch_post(client, entity, post, args, errors):
    """Download all media for one preview post (including every album item).
    Returns (relative paths, bytes, note)."""
    pid = post["id"]
    # The preview shows an album (up to 10 items) under one id; fetch the
    # neighbours on both sides in the same request and keep its siblings.
    ids = list(range(max(1, pid - 9), pid + 10))
    msgs = await with_flood(lambda: client.get_messages(entity, ids=ids), errors)
    head = msgs[ids.index(pid)]
    if head is None:
        return [], 0, "message deleted"
    group = [head]
    if head.grouped_id:
        group = sorted((m for m in msgs if m is not None and m.grouped_id == head.grouped_id),
                       key=lambda m: m.id)

    folder = os.path.join(args.out, "media", post["channel"])
    os.makedirs(folder, exist_ok=True)
    files, size, skipped = [], 0, []
    for n, m in enumerate(group):
        if not m.media or not m.file:
            continue
        kind = "video" if m.video or m.gif or m.video_note else \
               "photo" if m.photo else "file"
        if args.what == "videos" and kind != "video":
            continue
        fsize = m.file.size or 0
        if args.max_mb and fsize > args.max_mb * 2**20:
            skipped.append(f"{m.id}: {fsize / 2**20:.0f} MB > --max-mb")
            continue
        rel = os.path.join("media", post["channel"], f"{pid}_{n}_full{m.file.ext or ''}")
        if PROG and PROG.d["current"]:
            PROG.d["current"].update(activity=f"downloading {kind} {n + 1}/{len(group)}",
                                     file=f"{kind} {n + 1}/{len(group)}", file_bytes_done=0,
                                     file_bytes_total=fsize, speed=None, file_started=time.time())
            PROG.write(True)
        dest = os.path.join(args.out, rel)
        if not (os.path.exists(dest) and os.path.getsize(dest) == fsize):
            part = dest + ".part"
            await with_flood(lambda: client.download_media(
                m, file=part, progress_callback=progress(f"{pid}_{n}", fsize)), errors)
            os.replace(part, dest)
            size += fsize
            if PROG:
                PROG.d["totals"]["files"] += 1
                PROG.d["totals"]["bytes"] += fsize
        files.append(rel)
    return files, size, "; ".join(skipped) or None


def progress(label, total):
    """Print a line every ~10% (and at most every 5s) for big downloads."""
    state = {"t": 0.0, "pct": -10}
    def cb(done, size):
        size = size or total
        c = PROG and PROG.d["current"]
        if c:
            el = time.time() - c.get("file_started", time.time())
            c.update(file_bytes_done=done, file_bytes_total=size, speed=done / el if el > 1 else None)
            PROG.write()
        if not size or size < 20 * 2**20:
            return
        pct = int(done * 100 / size)
        if pct >= state["pct"] + 10 and time.time() - state["t"] > 5 or done == size:
            state.update(t=time.time(), pct=pct)
            log(f"    {label}: {done / 2**20:.0f}/{size / 2**20:.0f} MB ({pct}%)")
    return cb


ACCOUNT = None      # "Name (@username, id N)" of the logged-in account, for the capture log


def record(path, post, files, status, note):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "channel": post["channel"], "id": post["id"], "files": files,
            "status": status, "note": note, "account": ACCOUNT,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------- #

def main():
    p = argparse.ArgumentParser(
        description="Download full-size media for tgspider posts using a logged-in Telegram account.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("command", nargs="?", default="download",
                   choices=["download", "login", "status", "logout"],
                   help="login/logout are for the account owner; download is the default")
    p.add_argument("-o", "--out", default="tgspider_out", help="tgspider output folder")
    p.add_argument("--what", choices=["videos", "missing", "all"], default="videos",
                   help="videos = full videos the preview refused; missing = all media the "
                        "preview refused; all = full-res originals of every post's media")
    p.add_argument("-c", "--channels", nargs="*", help="only these channels")
    p.add_argument("--max-mb", type=float, default=500, help="skip files bigger than this (0 = no cap)")
    p.add_argument("--ids", type=int, nargs="*", help="only these post ids")
    p.add_argument("-n", "--limit", type=int, default=0, help="stop after this many posts (0 = all)")
    p.add_argument("--retry-errors", action="store_true", help="retry posts that failed before")
    p.add_argument("--dry-run", action="store_true", help="list what would be downloaded; no login")
    args = p.parse_args()

    if args.command != "download":
        asyncio.run({"login": cmd_login, "status": cmd_status, "logout": cmd_logout}[args.command]())
        return

    posts = load_jsonl(os.path.join(args.out, "posts.jsonl"))
    if not posts:
        sys.exit(f"No posts in {args.out}/posts.jsonl — run tgspider.py first.")
    done = {(r["channel"], r["id"]) for r in load_jsonl(os.path.join(args.out, "media_full.jsonl"))
            if not (args.retry_errors and r["status"] == "error")}
    targets = select(posts, done, args)

    counts = defaultdict(int)
    for t in targets:
        counts[t["channel"]] += 1
    print(f"{len(targets)} posts to fetch" +
          (f" ({len(done)} already done)" if done else "") + ":")
    for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  @{c}: {n}")
    if args.dry_run:
        for t in targets:
            why = "; ".join(t.get("media_unavailable") or []) or ",".join(t.get("media_types") or [])
            print(f"  {t['url']}  {' '.join(t.get('video_durations') or [])}  [{why}]")
        return
    if targets:
        asyncio.run(run(targets, args))


if __name__ == "__main__":
    main()
