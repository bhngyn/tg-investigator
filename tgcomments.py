#!/usr/bin/env python3
"""
tgcomments: collect a channel's comment section with a logged-in Telegram account.

A channel's comments live in a separate "discussion" group linked to it. This
reads that group's messages without joining it, plus its admin list and the
accounts that post there, and writes everything to a run folder that the
investigator imports.

    .venv/bin/python tgcomments.py <channel> -o <run folder> [--limit 200] [--media none]

Run folder:
  group.json          the channel, its linked group, the account used
  messages.jsonl      one row per message (comments, automatic post copies, service messages)
  rechecks.jsonl      earlier-captured ids re-checked: still there (with current text) or deleted
  raw/messages.jsonl  every message exactly as the API returned it (evidence copy)
  users.jsonl         accounts seen (senders, admins), with phone where Telegram shows it
  chats.jsonl         channels/groups seen (channel senders, forward sources)
  admins.jsonl        the group's admin list (role, custom title, rights)
  media/              downloaded files (with --media)
  progress.json       live status for the investigator UI

Incremental use: pass --state state.json with {"max_msg_id": N, "recheck_ids": [...]}.
Messages newer than N are fetched oldest-first, so coverage stays contiguous even if
--max-new cuts a run short. The login comes from `tgmedia.py login`; this never prompts.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tgmedia  # noqa: E402  (shared login + flood handling)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(d):
    return d.astimezone(timezone.utc).isoformat(timespec="seconds") if d else None


def log(msg):
    print(msg, file=sys.stderr, flush=True)


class Progress:
    """Writes <out>/progress.json for the investigator UI's live monitor."""

    def __init__(self, out, channel):
        self.path = os.path.join(out, "progress.json")
        self.t0 = time.time()
        self._last = 0.0
        self.d = {"tool": "tgcomments", "phase": "connecting", "started_at": _now(), "pid": os.getpid(),
                  "account": None, "channel": channel, "group": None, "waiting": None,
                  "activity": None,
                  "totals": {"messages": 0, "comments": 0, "mirrors": 0, "users": 0, "admins": 0,
                             "files": 0, "bytes": 0, "rechecked": 0, "deleted": 0, "errors": 0},
                  "events": []}

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
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.d, f, ensure_ascii=False)
        os.replace(tmp, self.path)


class Out:
    """Append-only jsonl writers for the run folder."""

    def __init__(self, out):
        self.out = out
        os.makedirs(os.path.join(out, "raw"), exist_ok=True)
        self.files = {}

    def write(self, name, rec):
        f = self.files.get(name)
        if f is None:
            f = self.files[name] = open(os.path.join(self.out, name), "a", encoding="utf-8")
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def flush(self):
        for f in self.files.values():
            f.flush()

    def close(self):
        for f in self.files.values():
            f.close()


# --------------------------------------------------------------------------- #
# Record builders (pure; no network)
# --------------------------------------------------------------------------- #

ADMIN_RIGHTS = ("change_info", "post_messages", "edit_messages", "delete_messages", "ban_users",
                "invite_users", "pin_messages", "add_admins", "anonymous", "manage_call", "other",
                "manage_topics", "post_stories", "edit_stories", "delete_stories")


def user_record(u, phones=True):
    from telethon import types
    if not isinstance(u, types.User):
        return None
    username = u.username
    if not username and getattr(u, "usernames", None):
        active = [x.username for x in u.usernames if getattr(x, "active", False)]
        username = active[0] if active else None
    return {"id": u.id, "username": username, "first_name": u.first_name, "last_name": u.last_name,
            "phone": (u.phone if phones else None), "bot": bool(u.bot), "premium": bool(u.premium),
            "deleted": bool(u.deleted), "scam": bool(u.scam), "fake": bool(u.fake),
            "verified": bool(u.verified)}


def chat_record(c):
    from telethon import types
    if not isinstance(c, (types.Channel, types.Chat)):
        return None
    return {"id": c.id, "username": getattr(c, "username", None), "title": c.title,
            "broadcast": bool(getattr(c, "broadcast", False))}


def media_types(m):
    from telethon import types
    if isinstance(m, types.MessageService):
        return ["service"]
    if not m.media:
        return []
    if m.photo:
        return ["photo"]
    if m.video_note:
        return ["roundvideo"]
    if m.gif:
        return ["gif"]
    if m.video:
        return ["video"]
    if m.voice:
        return ["voice"]
    if m.audio:
        return ["audio"]
    if m.sticker:
        return ["sticker"]
    name = type(m.media).__name__.replace("MessageMedia", "").lower()
    return [name or "media"]


def message_record(m, channel_id, group_id):
    """Normalise one discussion-group message."""
    from telethon import types
    service = isinstance(m, types.MessageService)
    frm = m.from_id
    if isinstance(frm, types.PeerUser):
        sender_type, sender_id = "user", frm.user_id
    elif isinstance(frm, types.PeerChannel):
        sender_type = "group" if frm.channel_id == group_id else "channel"
        sender_id = frm.channel_id
    elif isinstance(frm, types.PeerChat):
        sender_type, sender_id = "channel", frm.chat_id
    else:
        sender_type, sender_id = "none", None

    rec = {"id": m.id, "date": _iso(m.date), "edit_date": _iso(getattr(m, "edit_date", None)),
           "text": (f"[{type(m.action).__name__.replace('MessageAction', '')}]" if service
                    else (m.message or "")),
           "sender_type": sender_type, "sender_id": sender_id,
           "post_author": getattr(m, "post_author", None),
           "reply_to_msg_id": None, "thread_top_id": None, "is_mirror": False, "channel_post": None,
           "fwd_type": None, "fwd_id": None, "fwd_name": None, "fwd_username": None,
           "fwd_post": None, "fwd_date": None,
           "via_bot_id": getattr(m, "via_bot_id", None), "grouped_id": getattr(m, "grouped_id", None),
           "media_types": media_types(m), "reactions_total": None, "files": []}
    rt = m.reply_to
    if isinstance(rt, types.MessageReplyHeader):
        rec["reply_to_msg_id"] = rt.reply_to_msg_id
        rec["thread_top_id"] = rt.reply_to_top_id or rt.reply_to_msg_id
    f = getattr(m, "fwd_from", None)
    if f:
        if isinstance(f.from_id, types.PeerChannel):
            rec["fwd_type"], rec["fwd_id"] = "channel", f.from_id.channel_id
        elif isinstance(f.from_id, types.PeerUser):
            rec["fwd_type"], rec["fwd_id"] = "user", f.from_id.user_id
        else:
            rec["fwd_type"] = "hidden"
        rec["fwd_name"] = f.from_name or f.post_author
        rec["fwd_post"] = f.channel_post
        rec["fwd_date"] = _iso(f.date)
        # The automatic copy of a channel post that its comments hang off. Telegram marks it with
        # saved_from_peer/saved_from_msg_id = the linked channel and its post. When the post is itself
        # a repost, from_id/channel_post keep pointing at the original source, so those can't decide.
        saved = isinstance(f.saved_from_peer, types.PeerChannel) and f.saved_from_peer.channel_id == channel_id
        from_chan = isinstance(frm, types.PeerChannel) and frm.channel_id == channel_id
        if saved and from_chan and f.saved_from_msg_id:
            rec["is_mirror"], rec["channel_post"] = True, f.saved_from_msg_id
        elif rec["fwd_id"] == channel_id and f.channel_post and (saved or from_chan):
            rec["is_mirror"], rec["channel_post"] = True, f.channel_post
    try:
        fc = m.forward.chat if f else None          # entity for the forward source, when known
        if fc is not None:
            rec["fwd_username"] = getattr(fc, "username", None)
            rec["fwd_name"] = rec["fwd_name"] or getattr(fc, "title", None)
    except Exception:
        pass
    r = getattr(m, "reactions", None)
    if r and getattr(r, "results", None):
        rec["reactions_total"] = sum(x.count for x in r.results)
    return rec


# --------------------------------------------------------------------------- #
# Collection
# --------------------------------------------------------------------------- #

async def collect(args):
    from telethon import errors, functions, types
    state = {}
    if args.state and os.path.exists(args.state):
        with open(args.state, encoding="utf-8") as f:
            state = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    prog = tgmedia.PROG = Progress(args.out, args.channel)   # tgmedia.with_flood reports waits here
    out = Out(args.out)
    users, chats = {}, {}

    def see_user(u):
        rec = user_record(u, phones=not args.no_phones)
        if rec and rec["id"] not in users:
            users[rec["id"]] = rec
            out.write("users.jsonl", {**rec, "seen_at": _now()})
            prog.d["totals"]["users"] = len(users)

    def see_chat(c):
        rec = chat_record(c)
        if rec and rec["id"] not in chats:
            chats[rec["id"]] = rec
            out.write("chats.jsonl", {**rec, "seen_at": _now()})

    def stop_requested():
        return os.path.exists(os.path.join(args.out, "STOP"))

    prog.event(f"Connecting to Telegram for @{args.channel}'s comments")
    client = await tgmedia.connect_authorized()
    async with client:
        me = await client.get_me()
        account = f"{me.first_name or ''} (@{me.username or '-'}, id {me.id})"
        prog.d.update(account=account, phase="resolving")
        prog.event(f"Logged in as {account}")

        ch = await tgmedia.with_flood(lambda: client.get_entity(args.channel), errors)
        if not isinstance(ch, types.Channel) or not ch.broadcast:
            raise SystemExit(f"@{args.channel} is not a channel.")
        # GetFullChannel also delivers the linked group's entity (it's often private
        # and couldn't be looked up on its own).
        full = await tgmedia.with_flood(lambda: client(functions.channels.GetFullChannelRequest(ch)), errors)
        linked = full.full_chat.linked_chat_id
        info = {"channel": {"id": ch.id, "username": ch.username, "title": ch.title,
                            "participants": full.full_chat.participants_count},
                "group": None, "account": account, "collected_at": _now(),
                "method": "api", "tool": "tgcomments"}
        if not linked:
            prog.event(f"@{args.channel} has no comment section", "warn")
            with open(os.path.join(args.out, "group.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=1)
            prog.d["phase"] = "done"
            prog.write(True)
            return
        g = next((c for c in full.chats if c.id == linked), None) \
            or await client.get_entity(types.PeerChannel(linked))
        gfull = await tgmedia.with_flood(lambda: client(functions.channels.GetFullChannelRequest(g)), errors)
        info["group"] = {"id": g.id, "username": g.username, "title": g.title,
                         "participants": gfull.full_chat.participants_count,
                         "hidden_members": bool(getattr(gfull.full_chat, "participants_hidden", False)),
                         "joined": not g.left}
        with open(os.path.join(args.out, "group.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1)
        prog.d["group"] = {"id": g.id, "title": g.title, "participants": info["group"]["participants"]}
        prog.event(f"Comment group: {g.title} ({info['group']['participants']} members"
                   f"{', member list hidden' if info['group']['hidden_members'] else ''})")

        # ---- admins --------------------------------------------------------
        if not args.no_admins:
            prog.d.update(phase="admins", activity="reading the admin list")
            try:
                r = await tgmedia.with_flood(lambda: client(functions.channels.GetParticipantsRequest(
                    g, types.ChannelParticipantsAdmins(), 0, 200, 0)), errors)
                for u in r.users:
                    see_user(u)
                for p in r.participants:
                    rights = getattr(p, "admin_rights", None)
                    out.write("admins.jsonl", {
                        "user_id": p.user_id,
                        "role": "creator" if isinstance(p, types.ChannelParticipantCreator) else "admin",
                        "rank": getattr(p, "rank", None),
                        "rights": [k for k in ADMIN_RIGHTS if rights and getattr(rights, k, False)],
                        "promoted_by": getattr(p, "promoted_by", None),
                        "since": _iso(getattr(p, "date", None)), "seen_at": _now()})
                prog.d["totals"]["admins"] = len(r.participants)
                prog.event(f"{len(r.participants)} admin(s) listed")
            except errors.RPCError as e:
                out.write("admins_error.json", {"error": type(e).__name__, "at": _now()})
                prog.event(f"Admin list not readable ({type(e).__name__})", "warn")

        # Message ids are per group: if the channel now links a different group,
        # the saved position and re-check list don't apply to it.
        if state.get("group_id") and state["group_id"] != g.id:
            prog.event(f"The channel's comment group changed (was {state['group_id']}); starting fresh", "warn")
            state = {}

        # ---- messages ------------------------------------------------------
        folder = os.path.join(args.out, "media")
        max_known = int(state.get("max_msg_id") or 0)
        if max_known:
            it = client.iter_messages(g, min_id=max_known, reverse=True, limit=args.max_new or None)
            prog.event(f"Fetching messages newer than #{max_known} (oldest first)")
        else:
            it = client.iter_messages(g, limit=args.limit or None)
            prog.event(f"First collection: the newest {args.limit or 'all'} message(s)")
        prog.d.update(phase="messages", activity="reading messages")
        n = 0
        async for m in it:
            rec = message_record(m, ch.id, g.id)
            see_user(m.sender) if isinstance(m.sender, types.User) else see_chat(m.sender)
            if m.fwd_from:
                try:
                    see_chat(m.forward.chat) if m.forward.chat else None
                    see_user(m.forward.sender) if m.forward.sender else None
                except Exception:
                    pass
            if (args.media != "none" and m.media and not isinstance(m, types.MessageService)
                    and not (args.max_files and prog.d["totals"]["files"] >= args.max_files)):
                rec["files"] = await download(client, m, folder, args, prog, errors)
            out.write("raw/messages.jsonl", json.loads(m.to_json()))
            out.write("messages.jsonl", {**rec, "captured_at": _now()})
            n += 1
            t = prog.d["totals"]
            t["messages"] += 1
            if rec["is_mirror"]:
                t["mirrors"] += 1
            elif rec["media_types"] != ["service"]:
                t["comments"] += 1
            prog.d["activity"] = f"message #{m.id} ({rec['date'][:16].replace('T', ' ')})"
            if n % 50 == 0:
                out.flush()
                prog.write()
            if stop_requested():
                prog.event("Stopped on request", "warn")
                break
        out.flush()
        prog.event(f"{n} new message(s)", "good" if n else "info")

        # ---- recheck earlier captures (deletions and edits) ---------------------
        ids = [int(i) for i in state.get("recheck_ids") or []]
        if ids and not stop_requested():
            prog.d.update(phase="recheck", activity=f"re-checking {len(ids)} earlier message(s)")
            prog.event(f"Re-checking {len(ids)} earlier message(s) for deletions and edits")
            deleted = 0
            for i in range(0, len(ids), 100):
                chunk = ids[i:i + 100]
                got = await tgmedia.with_flood(lambda: client.get_messages(g, ids=chunk), errors)
                for mid, m in zip(chunk, got):
                    if m is None or isinstance(m, types.MessageEmpty):
                        out.write("rechecks.jsonl", {"id": mid, "status": "deleted", "checked_at": _now()})
                        deleted += 1
                    else:
                        out.write("raw/messages.jsonl", json.loads(m.to_json()))
                        out.write("rechecks.jsonl", {"id": mid, "status": "present", "checked_at": _now(),
                                                     "text": message_record(m, ch.id, g.id)["text"],
                                                     "edit_date": _iso(getattr(m, "edit_date", None))})
                prog.d["totals"]["rechecked"] += len(chunk)
                prog.d["totals"]["deleted"] = deleted
                prog.write()
            prog.event(f"{deleted} of {len(ids)} earlier message(s) are gone", "warn" if deleted else "good")

        prog.d.update(phase="stopped" if stop_requested() else "done", activity=None)
        prog.event("Done", "good")
    out.close()


async def download(client, m, folder, args, prog, errors):
    kind = "photo" if m.photo else "video" if (m.video or m.gif or m.video_note) else "file"
    if args.media == "photos" and kind != "photo":
        return []
    size = (m.file.size if m.file else 0) or 0
    if args.max_mb and size > args.max_mb * 2**20:
        prog.event(f"#{m.id}: {kind} {size / 2**20:.0f} MB is over the size limit; skipped", "warn")
        return []
    os.makedirs(folder, exist_ok=True)
    rel = os.path.join("media", f"{m.id}_0_{kind}{(m.file.ext if m.file else '') or ''}")
    dest = os.path.join(args.out, rel)
    try:
        prog.d["activity"] = f"downloading {kind} from #{m.id}"
        prog.write()
        await tgmedia.with_flood(lambda: client.download_media(m, file=dest + ".part"), errors)
        os.replace(dest + ".part", dest)
    except Exception as e:
        prog.d["totals"]["errors"] += 1
        prog.event(f"#{m.id}: download failed ({type(e).__name__})", "error")
        return []
    prog.d["totals"]["files"] += 1
    prog.d["totals"]["bytes"] += os.path.getsize(dest)
    return [rel]


def main():
    p = argparse.ArgumentParser(description="Collect a channel's comment section (logged-in).",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("channel", help="channel username (the channel, not its comment group)")
    p.add_argument("-o", "--out", required=True, help="run folder")
    p.add_argument("--state", help="state.json from the investigator (max_msg_id, recheck_ids)")
    p.add_argument("--limit", type=int, default=200, help="first collection: newest N messages (0 = all)")
    p.add_argument("--max-new", type=int, default=5000, help="later collections: at most N new messages")
    p.add_argument("--media", choices=["none", "photos", "all"], default="none")
    p.add_argument("--max-mb", type=float, default=50, help="skip files bigger than this")
    p.add_argument("--max-files", type=int, default=0, help="stop downloading after N files (0 = no cap)")
    p.add_argument("--no-phones", action="store_true", help="don't record phone numbers")
    p.add_argument("--no-admins", action="store_true", help="skip the admin list")
    args = p.parse_args()
    args.channel = args.channel.lstrip("@").split("/")[-1]
    try:
        asyncio.run(collect(args))
    except KeyboardInterrupt:
        log("Interrupted; what was collected is saved.")
        sys.exit(130)


if __name__ == "__main__":
    main()
