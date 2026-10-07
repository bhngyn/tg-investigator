"""
Cases: named sets of channels over one shared archive.

A channel is collected once and can belong to several cases. Triage (review
status), tags and notes made inside a case belong to that case; the original
global review status, tags and notes (used when no case is selected) are untouched.
Cases are archived, never deleted, so case work can't be lost by accident.
"""

import json

from fastapi import HTTPException, Request

from . import db

REVIEW = ("unreviewed", "relevant", "irrelevant", "verify")
ROLES = ("seed", "watched", "context")
ITEM_TYPES = ("post", "comment")


def annotate(ctx, items, case, item_type):
    """Add the case's own review status, tags and note count to post/comment cards."""
    if not items or not (str(case or "")).isdigit():
        return items
    case_id, ids = int(case), [i["id"] for i in items]
    marks = ",".join("?" * len(ids))
    reviews = {r["item_id"]: r["status"] for r in ctx.q(
        f"SELECT item_id, status FROM case_reviews WHERE case_id = ? AND item_type = ? AND item_id IN ({marks})",
        (case_id, item_type, *ids))}
    tags = {}
    for r in ctx.q(f"SELECT item_id, tag_id FROM case_tags WHERE case_id = ? AND item_type = ? AND item_id IN ({marks})",
                   (case_id, item_type, *ids)):
        tags.setdefault(r["item_id"], []).append(r["tag_id"])
    col = "post_id" if item_type == "post" else "comment_id"
    notes = {r["i"]: r["n"] for r in ctx.q(
        f"SELECT {col} i, COUNT(*) n FROM notes WHERE case_id = ? AND {col} IN ({marks}) GROUP BY {col}",
        (case_id, *ids))}
    for i in items:
        i["case_review"] = reviews.get(i["id"], "unreviewed")
        i["case_tags"] = tags.get(i["id"], [])
        i["case_notes"] = notes.get(i["id"], 0)
    return items


def case_channels_sql(case_id):
    return f"(SELECT channel FROM case_channels WHERE case_id = {int(case_id)})"


def active_jobs_by_channel(ctx):
    """channel -> list of queued/running jobs touching it."""
    out = {}
    for j in ctx.q("SELECT id, kind, status, params FROM jobs WHERE status IN ('queued','running','importing')"):
        p = json.loads(j["params"] or "{}")
        for ch in [p.get("channel"), *(p.get("seeds") or [])]:
            if ch:
                out.setdefault(ch, []).append({"id": j["id"], "kind": j["kind"], "status": j["status"]})
    return out


def case_summary(ctx, case_id=None):
    where = "WHERE k.id = ?" if case_id else ""
    rows = ctx.q(f"""
        SELECT k.*,
          (SELECT COUNT(*) FROM case_channels cc WHERE cc.case_id = k.id) channels,
          (SELECT COUNT(*) FROM posts p JOIN case_channels cc ON cc.channel = p.channel AND cc.case_id = k.id) posts,
          (SELECT COUNT(*) FROM posts p JOIN case_channels cc ON cc.channel = p.channel AND cc.case_id = k.id
             WHERE p.first_captured > COALESCE(k.last_opened, '')) new_posts,
          (SELECT COUNT(*) FROM posts p JOIN case_channels cc ON cc.channel = p.channel AND cc.case_id = k.id
             WHERE p.deleted_detected IS NOT NULL) deleted_posts,
          -- Reviews only count for items that exist in the case's channels (what the case's Search shows).
          -- Posts and comments are counted separately; relevant / verify / reviewed are the POST counts (old keys).
          (SELECT COUNT(*) FROM case_reviews r JOIN posts p ON p.id = r.item_id JOIN case_channels cc
             ON cc.channel = p.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'post' AND r.status != 'unreviewed') post_reviewed,
          (SELECT COUNT(*) FROM case_reviews r JOIN posts p ON p.id = r.item_id JOIN case_channels cc
             ON cc.channel = p.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'post' AND r.status = 'relevant') post_relevant,
          (SELECT COUNT(*) FROM case_reviews r JOIN posts p ON p.id = r.item_id JOIN case_channels cc
             ON cc.channel = p.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'post' AND r.status = 'verify') post_verify,
          (SELECT COUNT(*) FROM case_reviews r JOIN posts p ON p.id = r.item_id JOIN case_channels cc
             ON cc.channel = p.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'post' AND r.status = 'irrelevant') post_irrelevant,
          (SELECT COUNT(*) FROM case_reviews r JOIN comments c ON c.id = r.item_id JOIN case_channels cc
             ON cc.channel = c.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'comment' AND r.status != 'unreviewed'
               AND c.is_mirror = 0 AND c.media_types != '["service"]') comment_reviewed,
          (SELECT COUNT(*) FROM case_reviews r JOIN comments c ON c.id = r.item_id JOIN case_channels cc
             ON cc.channel = c.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'comment' AND r.status = 'relevant'
               AND c.is_mirror = 0 AND c.media_types != '["service"]') comment_relevant,
          (SELECT COUNT(*) FROM case_reviews r JOIN comments c ON c.id = r.item_id JOIN case_channels cc
             ON cc.channel = c.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'comment' AND r.status = 'verify'
               AND c.is_mirror = 0 AND c.media_types != '["service"]') comment_verify,
          (SELECT COUNT(*) FROM case_reviews r JOIN comments c ON c.id = r.item_id JOIN case_channels cc
             ON cc.channel = c.channel AND cc.case_id = k.id
             WHERE r.case_id = k.id AND r.item_type = 'comment' AND r.status = 'irrelevant'
               AND c.is_mirror = 0 AND c.media_types != '["service"]') comment_irrelevant,
          -- comments as the case's comment Search lists them (no mirrors of channel posts, no service messages)
          (SELECT COUNT(*) FROM comments c JOIN case_channels cc ON cc.channel = c.channel AND cc.case_id = k.id
             WHERE c.is_mirror = 0 AND c.media_types != '["service"]') comments,
          (SELECT COUNT(*) FROM comments c JOIN case_channels cc ON cc.channel = c.channel AND cc.case_id = k.id
             WHERE c.is_mirror = 0 AND c.media_types != '["service"]'
               AND c.first_captured > COALESCE(k.last_opened, '')) new_comments,
          (SELECT COUNT(*) FROM alerts a WHERE a.case_id = k.id AND a.read_at IS NULL) unread_alerts,
          (SELECT COUNT(*) FROM incidents i WHERE i.case_id = k.id) incidents,
          (SELECT MAX(p.first_captured) FROM posts p JOIN case_channels cc ON cc.channel = p.channel
             AND cc.case_id = k.id) last_collected
        FROM cases k {where} ORDER BY k.status = 'archived', COALESCE(k.last_opened, k.created) DESC""",
                 (case_id,) if case_id else ())
    running = active_jobs_by_channel(ctx)
    members = {}
    for r in ctx.q("SELECT case_id, channel FROM case_channels"):
        members.setdefault(r["case_id"], []).append(r["channel"])
    for r in rows:
        # explicit post / comment keys; the old keys (reviewed, unreviewed, relevant, verify) are the post numbers
        r["reviewed"], r["relevant"], r["verify"] = r["post_reviewed"], r["post_relevant"], r["post_verify"]
        r["post_unreviewed"] = r["unreviewed"] = max(0, r["posts"] - r["post_reviewed"])
        r["comment_unreviewed"] = max(0, r["comments"] - r["comment_reviewed"])
        r["active_jobs"] = sorted({j["id"] for ch in members.get(r["id"], []) for j in running.get(ch, [])})
    return rows


def register(app, ctx):
    q, q1, write, con, actor = ctx.q, ctx.q1, ctx.write, ctx.con, ctx.actor

    def get_case(cid):
        c = q1("SELECT * FROM cases WHERE id = ?", (cid,))
        if not c:
            raise HTTPException(404, "No such case")
        return c

    @app.get("/api/cases")
    def list_cases(archived: str = ""):
        rows = case_summary(ctx)
        return rows if archived == "1" else [r for r in rows if r["status"] != "archived"]

    @app.post("/api/cases")
    async def save_case(request: Request):
        b = await request.json()

        def do():
            if b.get("id"):
                old = get_case(int(b["id"]))
                fields = {k: b[k] for k in ("name", "description", "color", "status") if k in b}
                if "status" in fields and fields["status"] not in ("active", "archived"):
                    raise HTTPException(400, "status must be active or archived")
                if fields.get("name") is not None and not fields["name"].strip():
                    raise HTTPException(400, "A case needs a name.")
                if fields.get("name") is not None:
                    fields["name"] = fields["name"].strip()
                    if con.execute("SELECT 1 FROM cases WHERE name = ? AND id != ?", (fields["name"], old["id"])).fetchone():
                        raise HTTPException(409, "A case with that name already exists.")
                if fields:
                    con.execute(f"UPDATE cases SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                                (*fields.values(), old["id"]))
                    db.audit(con, actor(), "case_edit", f"case {old['id']}",
                             {k: {"from": old[k], "to": v} for k, v in fields.items() if old[k] != v})
                return old["id"]
            name = (b.get("name") or "").strip()
            if not name:
                raise HTTPException(400, "A case needs a name.")
            if con.execute("SELECT 1 FROM cases WHERE name = ?", (name,)).fetchone():
                raise HTTPException(409, "A case with that name already exists.")
            cur = con.execute("INSERT INTO cases(name, description, color, created, created_by) VALUES (?,?,?,?,?)",
                              (name, b.get("description"), b.get("color"), db.now(), actor()))
            db.audit(con, actor(), "case_create", f"case {cur.lastrowid}", {"name": name})
            return cur.lastrowid
        cid = write(do)
        return case_summary(ctx, cid)[0]

    @app.get("/api/cases/{cid}")
    def case_detail(cid: int):
        c = case_summary(ctx, cid)
        if not c:
            raise HTTPException(404, "No such case")
        c = c[0]
        since = c["last_opened"] or ""
        running = active_jobs_by_channel(ctx)
        c["channels_detail"] = q("""
            SELECT cc.channel, cc.role, cc.note, cc.added, cc.added_by,
                   ch.title, ch.type, ch.subscribers, ch.status channel_status, ch.preview, ch.tg_id,
                   ch.last_crawled, ch.description,
              (SELECT COUNT(*) FROM posts p WHERE p.channel = cc.channel) posts,
              (SELECT COUNT(*) FROM posts p WHERE p.channel = cc.channel AND p.first_captured > ?) new_posts,
              (SELECT COUNT(*) FROM posts p WHERE p.channel = cc.channel AND p.deleted_detected IS NOT NULL) deleted_posts,
              (SELECT MAX(date) FROM posts p WHERE p.channel = cc.channel) last_post,
              (SELECT MIN(date) FROM posts p WHERE p.channel = cc.channel) first_post,
              (SELECT COUNT(*) FROM posts p WHERE p.channel = cc.channel AND NOT EXISTS
                 (SELECT 1 FROM case_reviews r WHERE r.case_id = cc.case_id AND r.item_type = 'post'
                    AND r.item_id = p.id AND r.status != 'unreviewed')) unreviewed,
              (SELECT COUNT(*) FROM comments c WHERE c.channel = cc.channel AND c.is_mirror = 0
                 AND c.media_types != '["service"]') comments,
              (SELECT COUNT(*) FROM comments c WHERE c.channel = cc.channel AND c.is_mirror = 0
                 AND c.media_types != '["service"]' AND c.first_captured > ?) new_comments,
              (SELECT MAX(last_checked) FROM comment_groups g WHERE g.channel = cc.channel) comments_checked,
              (SELECT COUNT(DISTINCT source) FROM edges e WHERE e.target = cc.channel) linked_from,
              (SELECT COUNT(DISTINCT target) FROM edges e WHERE e.source = cc.channel) links_to
            FROM case_channels cc LEFT JOIN channels ch ON ch.username = cc.channel
            WHERE cc.case_id = ? ORDER BY cc.role = 'seed' DESC, cc.channel""", (since, since, cid))
        watches = {w["channel"]: w for w in q("SELECT * FROM watches")}
        scheds = {s["channel"]: s for s in q("SELECT * FROM schedules")}
        events = {}
        for e in q(f"SELECT * FROM channel_events WHERE channel IN {case_channels_sql(cid)} ORDER BY id DESC"):
            events.setdefault(e["channel"], []).append(e)
        for ch in c["channels_detail"]:
            ch["watch"] = watches.get(ch["channel"])
            ch["comment_schedule"] = scheds.get(ch["channel"])
            ch["active_jobs"] = running.get(ch["channel"], [])
            ch["events"] = events.get(ch["channel"], [])[:5]
            ch["other_cases"] = [r["name"] for r in q(
                "SELECT k.name FROM case_channels cc JOIN cases k ON k.id = cc.case_id "
                "WHERE cc.channel = ? AND cc.case_id != ?", (ch["channel"], cid))]
        c["alerts"] = q("SELECT * FROM alerts WHERE case_id = ? ORDER BY id DESC LIMIT 20", (cid,))
        c["incidents_list"] = q("SELECT id, title, date_start, location, status FROM incidents WHERE case_id = ? "
                                "ORDER BY COALESCE(date_start, created) DESC", (cid,))
        c["history"] = q("SELECT ts, actor, action, target, detail FROM audit WHERE target = ? ORDER BY id DESC LIMIT 50",
                         (f"case {cid}",))
        return c

    @app.post("/api/cases/{cid}/open")
    def open_case(cid: int):
        """Mark the case as visited now. Returns the previous visit time, so the UI can
        still show "new since last visit" for this session (pass it as `since`)."""
        c = get_case(cid)
        write(lambda: con.execute("UPDATE cases SET last_opened = ? WHERE id = ?", (db.now(), cid)))
        return {"previous": c["last_opened"]}

    @app.post("/api/cases/{cid}/channels")
    async def case_channels(cid: int, request: Request):
        """Add channels: {"add": ["@name", "t.me/x", ...], "role": "seed"}; remove: {"remove": [...]};
        change: {"update": {"channel": ..., "role": ..., "note": ...}}."""
        from tgspider import normalize_seed
        b = await request.json()
        get_case(cid)
        out = {"added": [], "already": [], "invalid": [], "removed": []}

        def do():
            role = b.get("role") if b.get("role") in ROLES else "watched"
            for raw in b.get("add") or []:
                name = normalize_seed(str(raw))
                if not name:
                    out["invalid"].append(raw)
                    continue
                if not con.execute("SELECT 1 FROM channels WHERE username = ?", (name,)).fetchone():
                    con.execute("INSERT INTO channels(username, status, first_seen) VALUES (?, 'watched', ?)",
                                (name, db.now()))
                cur = con.execute("INSERT OR IGNORE INTO case_channels(case_id, channel, role, added, added_by, note) "
                                  "VALUES (?,?,?,?,?,?)", (cid, name, role, db.now(), actor(), b.get("note")))
                if cur.rowcount:
                    out["added"].append(name)
                    # a channel someone put in a case is no longer just a candidate
                    con.execute("UPDATE channels SET status = 'watched' WHERE username = ? AND status IN "
                                "('candidate', 'dismissed')", (name,))
                else:
                    out["already"].append(name)
            for raw in b.get("remove") or []:
                name = normalize_seed(str(raw)) or str(raw)
                if con.execute("DELETE FROM case_channels WHERE case_id = ? AND channel = ?", (cid, name)).rowcount:
                    out["removed"].append(name)
            u = b.get("update")
            if u and u.get("channel"):
                sets = {k: u[k] for k in ("role", "note") if k in u}
                if sets.get("role") and sets["role"] not in ROLES:
                    raise HTTPException(400, f"role must be one of {ROLES}")
                if sets:
                    con.execute(f"UPDATE case_channels SET {', '.join(k + ' = ?' for k in sets)} "
                                "WHERE case_id = ? AND channel = ?", (*sets.values(), cid, u["channel"]))
                    db.audit(con, actor(), "case_channel_edit", f"case {cid}", {"channel": u["channel"], **sets})
            if out["added"]:
                db.audit(con, actor(), "case_channels_add", f"case {cid}", {"channels": out["added"], "role": role})
            if out["removed"]:
                db.audit(con, actor(), "case_channels_remove", f"case {cid}", {"channels": out["removed"]})
        write(do)
        return out

    @app.get("/api/channels/{name}/cases")
    def channel_cases(name: str):
        return q("""SELECT k.id, k.name, k.color, k.status, cc.role, cc.note FROM case_channels cc
                    JOIN cases k ON k.id = cc.case_id WHERE cc.channel = ? ORDER BY k.name""", (name,))

    def check_items(cid, item_type, ids):
        """None if every id is a real post/comment of one of the case's channels, else (status, message)."""
        table, chan = ("posts", "channel") if item_type == "post" else ("comments", "channel")
        found = {}
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            for r in q(f"SELECT id, {chan} ch FROM {table} WHERE id IN ({','.join('?' * len(part))})", part):
                found[r["id"]] = r["ch"]
        missing = [i for i in ids if i not in found]
        if missing:
            return 404, f"No such {item_type}: {', '.join(map(str, missing[:10]))}{' ...' if len(missing) > 10 else ''}"
        members = {r["channel"] for r in q("SELECT channel FROM case_channels WHERE case_id = ?", (cid,))}
        outside = [i for i in ids if found[i] not in members]
        if outside:
            return 400, (f"{item_type.capitalize()} {', '.join(map(str, outside[:10]))} is not in a channel of this "
                         f"case. Add the channel to the case first.")
        return None

    @app.get("/api/cases/{cid}/reviews")
    def case_reviews(cid: int, type: str = "post", ids: str = ""):
        """Case triage state for exactly these items: ?type=post|comment&ids=1,2,3 (max 1000).
        -> {"reviews": {id: status}, "tags": {id: [tag_id]}, "notes": {id: n}} with every requested id present
        (status 'unreviewed' when never triaged)."""
        get_case(cid)
        if type not in ITEM_TYPES:
            raise HTTPException(400, "type must be post or comment")
        want = list(dict.fromkeys(int(x) for x in ids.split(",") if x.strip().isdigit()))
        if len(want) > 1000:          # never answer for only some of the ids: the caller would show the rest wrongly
            raise ValueError(f"ids= takes at most 1000 ids per request; got {len(want)}")
        rows = annotate(ctx, [{"id": i} for i in want], cid, type)
        return {"case": cid, "type": type,
                "reviews": {str(r["id"]): r["case_review"] for r in rows},
                "tags": {str(r["id"]): r["case_tags"] for r in rows},
                "notes": {str(r["id"]): r["case_notes"] for r in rows}}

    @app.post("/api/cases/{cid}/review")
    async def case_review(cid: int, request: Request):
        """{"item_type": "post", "ids": [..], "status": "relevant"} — bulk triage inside a case."""
        b = await request.json()
        get_case(cid)
        item_type = b.get("item_type", "post")
        status = b.get("status")
        try:
            ids = list(dict.fromkeys(int(i) for i in (b.get("ids") or ([b["id"]] if b.get("id") else []))))
        except (TypeError, ValueError):
            raise HTTPException(400, "ids must be whole numbers.")
        if item_type not in ITEM_TYPES or status not in REVIEW or not ids:
            raise HTTPException(400, "Need item_type (post/comment), status and ids.")
        problem = check_items(cid, item_type, ids)
        if problem:
            raise HTTPException(*problem)

        def do():
            for i in ids:
                old = con.execute("SELECT status FROM case_reviews WHERE case_id = ? AND item_type = ? AND item_id = ?",
                                  (cid, item_type, i)).fetchone()
                con.execute("""INSERT INTO case_reviews(case_id, item_type, item_id, status, reviewed_at, reviewer)
                               VALUES (?,?,?,?,?,?) ON CONFLICT(case_id, item_type, item_id) DO UPDATE SET
                               status = excluded.status, reviewed_at = excluded.reviewed_at, reviewer = excluded.reviewer""",
                            (cid, item_type, i, status, db.now(), actor()))
                db.audit(con, actor(), "case_review", f"{item_type} {i}",
                         {"case": cid, "from": old[0] if old else "unreviewed", "to": status})
        write(do)
        return {"ok": True, "count": len(ids)}

    @app.post("/api/cases/{cid}/tags")
    async def case_tag(cid: int, request: Request):
        """{"item_type": "post", "id": 12, "tag_id": 3, "on": true}"""
        b = await request.json()
        get_case(cid)
        item_type, on = b.get("item_type", "post"), bool(b.get("on", True))
        try:
            iid, tid = int(b["id"]), int(b["tag_id"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Need id and tag_id (whole numbers).")
        if item_type not in ITEM_TYPES:
            raise HTTPException(400, "item_type must be post or comment")
        problem = check_items(cid, item_type, [iid]) if on else None
        if problem:
            raise HTTPException(*problem)

        def do():
            tag = con.execute("SELECT name FROM tags WHERE id = ?", (tid,)).fetchone()
            if not tag:
                raise HTTPException(404, "No such tag")
            if on:
                con.execute("INSERT OR IGNORE INTO case_tags(case_id, item_type, item_id, tag_id, added_by, added_at) "
                            "VALUES (?,?,?,?,?,?)", (cid, item_type, iid, tid, actor(), db.now()))
            else:
                con.execute("DELETE FROM case_tags WHERE case_id = ? AND item_type = ? AND item_id = ? AND tag_id = ?",
                            (cid, item_type, iid, tid))
            db.audit(con, actor(), "case_tag_add" if on else "case_tag_remove", f"{item_type} {iid}",
                     {"case": cid, "tag": tag[0]})
        write(do)
        return {"tags": [r["tag_id"] for r in q(
            "SELECT tag_id FROM case_tags WHERE case_id = ? AND item_type = ? AND item_id = ?", (cid, item_type, iid))]}

    @app.get("/api/cases/{cid}/notes")
    def case_notes(cid: int, item_type: str = "", id: int = 0, channel: str = ""):
        where, args = ["case_id = ?"], [cid]
        if item_type == "post" and id:
            where.append("post_id = ?"); args.append(id)
        if item_type == "comment" and id:
            where.append("comment_id = ?"); args.append(id)
        if channel:
            where.append("channel = ?"); args.append(channel)
        return q(f"SELECT * FROM notes WHERE {' AND '.join(where)} ORDER BY id", args)

    @app.post("/api/cases/{cid}/notes")
    async def add_case_note(cid: int, request: Request):
        """{"text": "...", "item_type": "post"|"comment"|"channel"|"case", "id": .., "channel": ..}"""
        b = await request.json()
        get_case(cid)
        text = (b.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "Empty note")
        it = b.get("item_type", "case")
        try:
            post_id = int(b["id"]) if it == "post" else None
            comment_id = int(b["id"]) if it == "comment" else None
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Need the id of the post or comment.")
        if it in ("post", "comment"):
            problem = check_items(cid, it, [post_id or comment_id])
            if problem:
                raise HTTPException(*problem)
        channel = b.get("channel") if it == "channel" else None
        target = {"post": f"post {post_id}", "comment": f"comment {comment_id}",
                  "channel": f"channel {channel}"}.get(it, f"case {cid}")

        def do():
            cur = con.execute("INSERT INTO notes(post_id, comment_id, channel, text, author, created_at, case_id) "
                              "VALUES (?,?,?,?,?,?,?)", (post_id, comment_id, channel, text, actor(), db.now(), cid))
            db.audit(con, actor(), "case_note_add", target, {"case": cid, "text": text[:200]})
            return cur.lastrowid
        return {"id": write(do)}

    @app.post("/api/resolve")
    async def resolve(request: Request):
        """Turn pasted handles/links into usernames and say what's already known about each.
        {"handles": [...], "live": true} also checks each on t.me (public page, no login) — that
        request is visible to Telegram like any page visit."""
        import tgspider
        b = await request.json()
        handles = b.get("handles") or []
        if isinstance(handles, str):
            handles = handles.replace(",", "\n").split()
        live = bool(b.get("live"))
        if live and len(handles) > 25:
            raise HTTPException(400, "Check at most 25 handles live at once.")
        fetcher = tgspider.Fetcher(delay=1.0, retries=1, verbose=False) if live else None
        told_to_wait = None          # Telegram said to come back later: don't ask again for the rest
        out = []
        for raw in handles:
            name = tgspider.normalize_seed(str(raw))
            r = {"input": raw, "username": name}
            if not name:
                r["error"] = "Not a public username or t.me link (invite links can't be read without joining)."
                out.append(r)
                continue
            r["known"] = q1("""SELECT c.username, c.title, c.type, c.subscribers, c.status, c.last_crawled, c.preview,
                                 (SELECT COUNT(*) FROM posts p WHERE p.channel = c.username) posts,
                                 (SELECT COUNT(*) FROM comment_groups g WHERE g.channel = c.username) has_comments
                               FROM channels c WHERE c.username = ?""", (name,))
            r["cases"] = [x["name"] for x in q("SELECT k.name FROM case_channels cc JOIN cases k ON k.id = cc.case_id "
                                               "WHERE cc.channel = ?", (name,))]
            if live and told_to_wait:
                r["live"] = {"error": f"not checked: {told_to_wait}"}
            elif live:
                try:
                    final, page = fetcher.get(f"https://t.me/s/{name}")
                    if "/s/" in final and "tgme_channel_info" in page:
                        header, msgs, _ = tgspider.parse_channel_page(page, name)
                        r["live"] = {"type": "channel", "preview": True, "title": header.get("title"),
                                     "subscribers": header.get("subscribers"), "tg_id": header.get("tg_id"),
                                     "latest_post": max((m["date"] for m in msgs if m["date"]), default=None)}
                    else:
                        _, page = fetcher.get(f"https://t.me/{name}")
                        info = tgspider.classify_profile(page)
                        r["live"] = {"type": info["type"], "preview": False, "title": info.get("title"),
                                     "subscribers": info.get("subscribers")}
                except Exception as e:
                    r["live"] = {"error": f"{type(e).__name__}: {e}"}
                    if isinstance(e, tgspider.RunAbort):
                        told_to_wait = str(e)
            out.append(r)
        return out
