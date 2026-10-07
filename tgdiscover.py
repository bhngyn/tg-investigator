#!/usr/bin/env python3
"""
tgdiscover: find channels with a logged-in Telegram account.

  similar          Telegram's own "similar channels" for each given channel
  search_channels  public channels/groups whose name or username matches a keyword
  search_posts     public channel posts matching a keyword (Telegram's global search)

    .venv/bin/python tgdiscover.py --spec spec.json -o <run folder>
    spec: {"method": "similar", "channels": ["name", ...]}
          {"method": "search_channels", "queries": ["keyword", ...], "limit": 50}
          {"method": "search_posts", "queries": ["keyword", ...], "limit": 50}

Run folder:
  discover.json          the spec, the account used and when
  discoveries.jsonl      one row per channel found (and how)
  raw/responses.jsonl    every API response as returned (evidence copy)
  progress.json          live status for the investigator UI

Read-only: nothing is joined, followed or sent. The login comes from `tgmedia.py login`.
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
from tgcomments import Out, Progress, _iso, _now, log  # noqa: E402

METHODS = ("similar", "search_channels", "search_posts")


def chat_rec(c):
    from telethon import types
    if not isinstance(c, types.Channel):
        return None
    username = c.username
    if not username and getattr(c, "usernames", None):
        active = [x.username for x in c.usernames if getattr(x, "active", False)]
        username = active[0] if active else None
    return {"tg_id": c.id, "username": (username or "").lower() or None, "title": c.title,
            "type": "channel" if c.broadcast else "group",
            "subscribers": getattr(c, "participants_count", None),
            "verified": bool(getattr(c, "verified", False)), "scam": bool(getattr(c, "scam", False)),
            "fake": bool(getattr(c, "fake", False))}


async def discover(args):
    from telethon import errors, functions, types
    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    method = spec.get("method")
    if method not in METHODS:
        raise SystemExit(f"method must be one of {METHODS}")
    targets = spec.get("channels") if method == "similar" else spec.get("queries")
    targets = [t.strip().lstrip("@") for t in targets or [] if t and t.strip()]
    if not targets:
        raise SystemExit("nothing to look up")
    limit = max(1, min(100, int(spec.get("limit", 50))))
    os.makedirs(args.out, exist_ok=True)
    prog = tgmedia.PROG = Progress(args.out, ", ".join(targets)[:80])
    prog.d.update(tool="tgdiscover", totals={"lookups": 0, "found": 0, "errors": 0})
    out = Out(args.out)
    seen = set()

    prog.event(f"Connecting to Telegram for {method.replace('_', ' ')}")
    client = await tgmedia.connect_authorized()
    async with client:
        me = await client.get_me()
        account = f"{me.first_name or ''} (@{me.username or '-'}, id {me.id})"
        prog.d.update(account=account, phase="searching")
        prog.event(f"Logged in as {account}")
        with open(os.path.join(args.out, "discover.json"), "w", encoding="utf-8") as f:
            json.dump({**spec, "account": account, "started_at": _now(), "tool": "tgdiscover"}, f, ensure_ascii=False)

        for t in targets:
            if os.path.exists(os.path.join(args.out, "STOP")):
                prog.event("Stopped on request", "warn")
                break
            prog.d["activity"] = f"{method.replace('_', ' ')}: {t}"
            try:
                if method == "similar":
                    ent = await tgmedia.with_flood(lambda: client.get_entity(t), errors)
                    res = await tgmedia.with_flood(
                        lambda: client(functions.channels.GetChannelRecommendationsRequest(channel=ent)), errors)
                    found = [(c, None) for c in res.chats]
                elif method == "search_channels":
                    res = await tgmedia.with_flood(lambda: client(functions.contacts.SearchRequest(q=t, limit=limit)), errors)
                    found = [(c, None) for c in res.chats]
                else:
                    res = await tgmedia.with_flood(lambda: client(functions.messages.SearchGlobalRequest(
                        q=t, filter=types.InputMessagesFilterEmpty(), min_date=None, max_date=None, offset_rate=0,
                        offset_peer=types.InputPeerEmpty(), offset_id=0, limit=limit, broadcasts_only=True)), errors)
                    chats = {c.id: c for c in res.chats}
                    found = []
                    for m in res.messages:
                        cid = getattr(m.peer_id, "channel_id", None)
                        if cid in chats:
                            found.append((chats[cid], {"msg_id": m.id, "date": _iso(m.date),
                                                       "text": (m.message or "")[:500]}))
                out.write("raw/responses.jsonl", {"method": method, "query": t, "received_at": _now(),
                                                  "response": res.to_dict()})
                prog.d["totals"]["lookups"] += 1
                rank = 0
                for c, post in found:
                    rec = chat_rec(c)
                    if not rec:
                        continue
                    rank += 1
                    key = (t, rec["tg_id"], post and post["msg_id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    out.write("discoveries.jsonl", {**rec, "method": method, "query": t.lower(), "rank": rank,
                                                    "post": post, "found_at": _now()})
                    prog.d["totals"]["found"] += 1
                prog.event(f"{t}: {rank} result(s)")
            except (errors.RPCError, ValueError) as e:
                prog.d["totals"]["errors"] += 1
                prog.event(f"{t}: {type(e).__name__}: {e}", "warn")
            out.flush()
            prog.write()
            time.sleep(1.5)                   # be gentle: these are interactive-style lookups
    out.close()
    prog.d.update(phase="done", activity=None)
    prog.event("Finished")


def main():
    p = argparse.ArgumentParser(description="Find channels with a logged-in account (read-only).")
    p.add_argument("--spec", required=True, help="JSON spec (see module docstring)")
    p.add_argument("-o", "--out", required=True, help="run folder")
    args = p.parse_args()
    try:
        asyncio.run(discover(args))
    except KeyboardInterrupt:
        log("Interrupted; what was found is saved.")
        sys.exit(130)


if __name__ == "__main__":
    main()
