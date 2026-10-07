"""
Import a tgcomments run folder (a channel's comment section, collected with the API).

Same evidence rules as posts: the raw API dump and every media file are hashed and
copied read-only into the store, nothing is overwritten (changed text goes to
comment_versions), and deletions are only recorded when a message captured earlier
is re-checked and found gone.
"""

import gzip
import hashlib
import json
import mimetypes
import os
import re
import shutil

from . import db

FILE_RE = re.compile(r"(\d+)_(\d+)_(photo|video|file)(\.[A-Za-z0-9]+)?$")
USER_FIELDS = ("username", "first_name", "last_name", "phone", "bot", "premium", "deleted",
               "scam", "fake", "verified")
MSG_FIELDS = ("date", "edit_date", "text", "sender_type", "sender_id", "post_author", "reply_to_msg_id",
              "thread_top_id", "is_mirror", "channel_post", "fwd_type", "fwd_id", "fwd_name",
              "fwd_username", "fwd_post", "fwd_date", "via_bot_id", "grouped_id", "media_types",
              "reactions_total")


def is_comments_run(run_dir):
    return os.path.exists(os.path.join(run_dir, "group.json"))


def _jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _b(v):
    return None if v is None else int(bool(v))


def ingest_comments(con, run_dir, store_dir, actor="system", job_id=None, log=print):
    from .ingest import store_file
    run_dir = os.path.abspath(run_dir)
    stats = dict(comments_new=0, comments_updated=0, comments_changed=0, deleted_detected=0,
                 users_new=0, user_changes=0, admins=0, admins_removed=0, media_new=0, edges_new=0)
    with open(os.path.join(run_dir, "group.json"), encoding="utf-8") as f:
        info = json.load(f)
    account, collected_at = info.get("account"), info.get("collected_at") or db.now()
    ch = info["channel"]
    channel = (ch.get("username") or f"id{ch['id']}").lower()
    stats["channel"] = channel

    # the channel itself (it may not have been crawled yet)
    con.execute("""INSERT INTO channels(username, tg_id, title, type, subscribers, status, first_seen)
                   VALUES (?,?,?,'channel',?, 'candidate', ?)
                   ON CONFLICT(username) DO UPDATE SET tg_id = COALESCE(excluded.tg_id, tg_id),
                     title = COALESCE(excluded.title, title), type = COALESCE(type, 'channel'),
                     subscribers = COALESCE(excluded.subscribers, subscribers)""",
                (channel, ch["id"], ch.get("title"), ch.get("participants"), collected_at))

    g = info.get("group")
    if not g:
        stats["note"] = "channel has no comment section"
        _finish(con, actor, job_id, run_dir, stats, log)
        return stats
    gid = g["id"]
    con.execute("""INSERT INTO comment_groups(group_id, channel, channel_id, title, username, participants,
                       hidden_members, first_seen, last_checked) VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(group_id) DO UPDATE SET channel = excluded.channel,
                     channel_id = excluded.channel_id, title = excluded.title, username = excluded.username,
                     participants = excluded.participants, hidden_members = excluded.hidden_members,
                     last_checked = MAX(COALESCE(last_checked, ''), excluded.last_checked)""",
                (gid, channel, ch["id"], g.get("title"), g.get("username"), g.get("participants"),
                 _b(g.get("hidden_members")), collected_at, collected_at))

    # ---- raw API dump: the evidence copy every comment row points back to -------
    raw_sha = None
    raw = os.path.join(run_dir, "raw", "messages.jsonl")
    if os.path.exists(raw) and os.path.getsize(raw):
        h = hashlib.sha256()
        with open(raw, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        raw_sha = h.hexdigest()
        rel = os.path.join("api", raw_sha[:2], raw_sha + ".jsonl.gz")
        dest = os.path.join(store_dir, rel)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(raw, "rb") as src, gzip.open(dest + ".part", "wb") as out:
                shutil.copyfileobj(src, out)
            os.replace(dest + ".part", dest)
            os.chmod(dest, 0o444)
        con.execute("""INSERT OR IGNORE INTO captures(kind, url, sha256, path, bytes, captured_at, method,
                           tool, account, job_id, run_dir, note)
                       VALUES ('api_dump',?,?,?,?,?,'api','tgcomments',?,?,?,?)""",
                    (f"tg://comments/{channel}/{gid}", raw_sha, rel, os.path.getsize(raw), collected_at,
                     account, job_id, run_dir,
                     "sha256 of the raw API messages (JSON lines) as received; stored gzip-compressed"))

    # ---- channels/groups referenced by id ----------------------------------------
    for c in _jsonl(os.path.join(run_dir, "chats.jsonl")):
        con.execute("""INSERT INTO tg_chats(chat_id, username, title, broadcast, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(chat_id) DO UPDATE SET username = COALESCE(excluded.username, username),
                         title = COALESCE(excluded.title, title), last_seen = excluded.last_seen""",
                    (c["id"], (c.get("username") or "").lower() or None, c.get("title"),
                     _b(c.get("broadcast")), c.get("seen_at"), c.get("seen_at")))

    # ---- accounts: current state + a snapshot whenever something changed ----------
    for u in _jsonl(os.path.join(run_dir, "users.jsonl")):
        at = u.get("seen_at") or collected_at
        vals = {k: (_b(u.get(k)) if k not in ("username", "first_name", "last_name", "phone") else u.get(k))
                for k in USER_FIELDS}
        if vals["phone"] is None:        # not collected this time: keep what we had
            prev = con.execute("SELECT phone FROM tg_users WHERE user_id = ?", (u["id"],)).fetchone()
            vals["phone"] = prev[0] if prev else None
        last = con.execute(f"SELECT {','.join(USER_FIELDS)} FROM tg_user_snapshots WHERE user_id = ? "
                           f"ORDER BY id DESC LIMIT 1", (u["id"],)).fetchone()
        if last is None or tuple(last) != tuple(vals.values()):
            con.execute(f"INSERT INTO tg_user_snapshots(user_id, captured_at, {','.join(USER_FIELDS)}) "
                        f"VALUES (?,?,{','.join('?' * len(USER_FIELDS))})", (u["id"], at, *vals.values()))
            stats["users_new" if last is None else "user_changes"] += 1
        sets = ", ".join(f"{k} = excluded.{k}" for k in USER_FIELDS)
        con.execute(f"""INSERT INTO tg_users(user_id, {','.join(USER_FIELDS)}, first_seen, last_seen)
                        VALUES (?,{','.join('?' * len(USER_FIELDS))},?,?)
                        ON CONFLICT(user_id) DO UPDATE SET {sets},
                          last_seen = MAX(COALESCE(last_seen, ''), excluded.last_seen)""",
                    (u["id"], *vals.values(), at, at))

    # ---- admins ---------------------------------------------------------------------
    admins = _jsonl(os.path.join(run_dir, "admins.jsonl"))
    for a in admins:
        con.execute("""INSERT INTO group_admins(group_id, user_id, role, rank, rights, promoted_by, since,
                           first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(group_id, user_id) DO UPDATE SET role = excluded.role,
                         rank = excluded.rank, rights = excluded.rights, promoted_by = excluded.promoted_by,
                         since = excluded.since, last_seen = excluded.last_seen, removed_detected = NULL""",
                    (gid, a["user_id"], a.get("role"), a.get("rank"), json.dumps(a.get("rights") or []),
                     a.get("promoted_by"), a.get("since"), a["seen_at"], a["seen_at"]))
    stats["admins"] = len(admins)
    if admins:     # the list was read: anyone listed before but not now has been removed
        seen = min(a["seen_at"] for a in admins)
        cur = con.execute("""UPDATE group_admins SET removed_detected = ? WHERE group_id = ?
                             AND last_seen < ? AND removed_detected IS NULL""", (seen, gid, seen))
        stats["admins_removed"] = cur.rowcount

    # ---- messages -------------------------------------------------------------------
    max_id = 0
    edge_sources = set()
    for m in _jsonl(os.path.join(run_dir, "messages.jsonl")):
        at = m.get("captured_at") or collected_at
        max_id = max(max_id, m["id"])
        vals = {k: m.get(k) for k in MSG_FIELDS}
        vals["is_mirror"] = _b(vals["is_mirror"])
        vals["media_types"] = json.dumps(vals["media_types"] or [])
        vals["fwd_username"] = (vals["fwd_username"] or "").lower() or None
        row = con.execute("SELECT id, text, edit_date, last_captured FROM comments WHERE group_id = ? "
                          "AND msg_id = ?", (gid, m["id"])).fetchone()
        if row is None:
            cols = ["group_id", "msg_id", "channel", *MSG_FIELDS, "first_captured", "last_captured",
                    "last_seen", "raw_sha256"]
            cur = con.execute(f"INSERT INTO comments({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              (gid, m["id"], channel, *vals.values(), at, at, at, raw_sha))
            cid = cur.lastrowid
            stats["comments_new"] += 1
            _index(con, cid, vals, channel)
        else:
            cid = row["id"]
            if at > (row["last_captured"] or ""):
                if (row["text"] or "") != (vals["text"] or ""):
                    _version(con, cid, row)
                    stats["comments_changed"] += 1
                    _index(con, cid, vals, channel)
                sets = ", ".join(f"{k} = ?" for k in MSG_FIELDS)
                con.execute(f"UPDATE comments SET {sets}, last_captured = ?, last_seen = ?, raw_sha256 = ?, "
                            f"capture_count = capture_count + 1 WHERE id = ?",
                            (*vals.values(), at, at, raw_sha, cid))
                stats["comments_updated"] += 1
        for rel in m.get("files") or []:
            stats["media_new"] += _add_media(con, run_dir, store_dir, cid, rel, at, account, job_id, store_file)

        # network edges from the comment section (source = the channel whose comments these are)
        if not m.get("is_mirror"):
            if m.get("fwd_type") == "channel" and vals["fwd_username"] and vals["fwd_username"] != channel:
                stats["edges_new"] += _edge(con, channel, vals["fwd_username"], "comment_forward", m)
                edge_sources.add(channel)
            if m.get("sender_type") == "channel" and m.get("sender_id") != ch["id"]:
                s = con.execute("SELECT username FROM tg_chats WHERE chat_id = ?", (m["sender_id"],)).fetchone()
                if s and s[0] and s[0] != channel:
                    stats["edges_new"] += _edge(con, channel, s[0], "comment_sender", m)
                    edge_sources.add(channel)
    if max_id:
        con.execute("UPDATE comment_groups SET max_msg_id = MAX(COALESCE(max_msg_id, 0), ?) WHERE group_id = ?",
                    (max_id, gid))
    if edge_sources:
        db.rebuild_edges(con, sorted(edge_sources))

    # ---- rechecks: still there (maybe edited) or gone -------------------------------
    for r in _jsonl(os.path.join(run_dir, "rechecks.jsonl")):
        row = con.execute("SELECT * FROM comments WHERE group_id = ? AND msg_id = ?", (gid, r["id"])).fetchone()
        if not row:
            continue
        if r["status"] == "deleted":
            if not row["deleted_detected"]:
                con.execute("UPDATE comments SET deleted_detected = ? WHERE id = ?", (r["checked_at"], row["id"]))
                stats["deleted_detected"] += 1
        elif r["checked_at"] > (row["last_seen"] or ""):
            if (r.get("text") or "") != (row["text"] or "") and r.get("text") is not None:
                _version(con, row["id"], row)
                con.execute("UPDATE comments SET text = ?, edit_date = ?, last_captured = ?, raw_sha256 = ?, "
                            "capture_count = capture_count + 1 WHERE id = ?",
                            (r["text"], r.get("edit_date"), r["checked_at"], raw_sha, row["id"]))
                _index(con, row["id"], {**dict(row), "text": r["text"]}, channel)
                stats["comments_changed"] += 1
            con.execute("UPDATE comments SET last_seen = ? WHERE id = ?", (r["checked_at"], row["id"]))

    _finish(con, actor, job_id, run_dir, stats, log)
    backfill_mirrors(con, store_dir, log=log)
    return stats


def backfill_mirrors(con, store_dir, actor="system", log=print):
    """Flag post copies that older collectors missed: reposted channel posts.

    The group's copy of a channel post that is itself a repost carries the original's forward header,
    and tgcomments used to require the forward to come from the linked channel. The stored raw API dump
    each row was read from settles it: saved_from_peer/saved_from_msg_id name the linked channel and its
    post. Only rows the dump confirms are changed. Idempotent; returns the stats.
    """
    cands = con.execute("""SELECT c.id, c.group_id, c.msg_id, c.channel, c.raw_sha256, g.channel_id
                           FROM comments c JOIN comment_groups g ON g.group_id = c.group_id
                           WHERE c.is_mirror = 0 AND c.sender_type = 'channel' AND c.sender_id = g.channel_id
                             AND c.fwd_type IS NOT NULL""").fetchall()
    stats = dict(candidates=len(cands), flipped=0, unconfirmed=0, no_dump=0, edge_hits_removed=0, flipped_ids=[])
    if not cands:
        return stats
    by_dump = {}
    for r in cands:
        by_dump.setdefault(r["raw_sha256"], []).append(r)
    touched = set()
    for sha, rows in by_dump.items():
        cap = sha and con.execute("SELECT path FROM captures WHERE kind = 'api_dump' AND sha256 = ?",
                                  (sha,)).fetchone()
        path = cap and os.path.join(store_dir, cap["path"])
        if not path or not os.path.exists(path):
            stats["no_dump"] += len(rows)
            continue
        want = {r["msg_id"]: r for r in rows}
        found = {}
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                m = json.loads(line)
                if m.get("id") in want:
                    found[m["id"]] = m
        for mid, r in want.items():
            f = (found.get(mid) or {}).get("fwd_from") or {}
            peer = f.get("saved_from_peer") or {}
            frm = (found.get(mid) or {}).get("from_id") or {}
            if (peer.get("channel_id") != r["channel_id"] or frm.get("channel_id") != r["channel_id"]
                    or not f.get("saved_from_msg_id")):
                stats["unconfirmed"] += 1
                continue
            con.execute("UPDATE comments SET is_mirror = 1, channel_post = ? WHERE id = ?",
                        (f["saved_from_msg_id"], r["id"]))
            # it was counted as the comment section forwarding the original source
            cur = con.execute("DELETE FROM edge_hits WHERE source = ? AND type = 'comment_forward' AND msg_id = ?",
                              (r["channel"], mid))
            stats["edge_hits_removed"] += cur.rowcount
            stats["flipped"] += 1
            stats["flipped_ids"].append(r["id"])
            touched.add(r["channel"])
    if touched:
        db.rebuild_edges(con, sorted(touched))
    if stats["flipped"]:
        db.audit(con, actor, "backfill_mirrors", None, stats)
        log(f"Marked reposted channel posts as post copies: {stats}")
    con.commit()
    return stats


def _finish(con, actor, job_id, run_dir, stats, log):
    con.execute("""INSERT OR IGNORE INTO captures(kind, captured_at, method, tool, job_id, run_dir, note)
                   VALUES ('import', ?, 'import', 'investigator', ?, ?, ?)""",
                (db.now(), job_id, run_dir, json.dumps(stats)))
    db.audit(con, actor, "import_comments", run_dir, stats)
    con.commit()
    log(f"Imported comments {run_dir}: {stats}")


def _version(con, cid, row):
    con.execute("INSERT INTO comment_versions(comment_id, captured_at, text, edit_date) VALUES (?,?,?,?)",
                (cid, row["last_captured"], row["text"], row["edit_date"]))


def _index(con, cid, vals, channel):
    db.index_comment(con, cid, vals.get("text"), [channel, vals.get("fwd_username"), vals.get("fwd_name"),
                                                   vals.get("post_author")])


def _edge(con, source, target, kind, m):
    cur = con.execute("INSERT OR IGNORE INTO edge_hits(source, target, type, msg_id, date) VALUES (?,?,?,?,?)",
                      (source, target, kind, m["id"], m.get("date")))
    if not con.execute("SELECT 1 FROM channels WHERE username = ?", (target,)).fetchone():
        con.execute("INSERT INTO channels(username, status, first_seen) VALUES (?, 'candidate', ?)",
                    (target, m.get("date") or db.now()))
    return cur.rowcount


def _add_media(con, run_dir, store_dir, cid, rel, at, account, job_id, store_file):
    src = os.path.join(run_dir, rel)
    mm = FILE_RE.search(os.path.basename(rel))
    if not os.path.exists(src) or not mm:
        return 0
    idx, kind = int(mm.group(2)), mm.group(3)
    sha, store_rel, size = store_file(src, store_dir)
    cur = con.execute("""INSERT OR IGNORE INTO comment_media(comment_id, kind, idx, sha256, path, bytes, mime,
                             captured_at) VALUES (?,?,?,?,?,?,?,?)""",
                      (cid, kind, idx, sha, store_rel, size, mimetypes.guess_type(store_rel)[0], at))
    if cur.rowcount:
        con.execute("""INSERT OR IGNORE INTO captures(kind, url, sha256, path, bytes, captured_at, method,
                           tool, account, job_id, run_dir, note)
                       VALUES ('media',NULL,?,?,?,?,'api','tgcomments',?,?,?,?)""",
                    (sha, store_rel, size, at, account, job_id, run_dir, f"comment {cid}, {kind} #{idx}"))
    return cur.rowcount
