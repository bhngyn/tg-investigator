"""
Watchlist: re-crawl channels' posts on a schedule, catching new posts, edits and deletions.

Each run is an ordinary crawl job of one channel that reads from the newest post back
past the re-check window (recheck_days), then stops. Posts in that window that were
captured before and are no longer there are marked deleted (see ingest._detect_deletions);
changed text or media is kept as a new version. Comment sections have their own
schedules (`schedules` table); this module also offers one call that sets up any mix
of steps for a channel.
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from . import db

MEDIA = ("none", "photos", "all")


def register(app, ctx):
    q, q1, write, con, actor, runner = ctx.q, ctx.q1, ctx.write, ctx.con, ctx.actor, ctx.runner

    def active_crawl(channel):
        for j in q("SELECT id, params FROM jobs WHERE kind = 'crawl' AND status IN ('queued','running','importing')"):
            if channel in (json.loads(j["params"] or "{}").get("seeds") or []):
                return j["id"]
        return None

    def queue_watch(w, who="watchlist"):
        busy = active_crawl(w["channel"])
        if busy:
            return busy, False
        jid = runner.submit("crawl", {"seeds": [w["channel"]], "depth": 1, "pages": w["pages"], "max_channels": 1,
                                      "media": w["media"], "recheck_days": w["recheck_days"], "watch_id": w["id"]}, who)
        return jid, True

    def run_due_watches(only_id=None):
        now = datetime.now(timezone.utc)
        if only_id:
            due = q("SELECT * FROM watches WHERE id = ?", (only_id,))
        else:
            due = q("SELECT * FROM watches WHERE enabled = 1 AND (next_run IS NULL OR next_run <= ?)", (db.now(),))
        queued = []
        for w in due:
            jid, new = queue_watch(w)
            nxt = (now + timedelta(minutes=max(15, w["interval_min"]))).isoformat(timespec="seconds")
            write(lambda: con.execute("UPDATE watches SET last_run = ?, next_run = ?, last_job = ? WHERE id = ?",
                                      (db.now(), nxt, jid, w["id"])))
            if new:
                queued.append(jid)
        return queued

    runner.hooks["tick"].append(run_due_watches)

    def watch_rows(where="1", args=()):
        return q(f"""SELECT w.*, j.status last_status, j.summary last_summary,
                       c.title, c.last_crawled,
                       (SELECT COUNT(*) FROM posts p WHERE p.channel = w.channel) posts,
                       (SELECT COUNT(*) FROM posts p WHERE p.channel = w.channel AND p.deleted_detected IS NOT NULL) deleted,
                       (SELECT MAX(date) FROM posts p WHERE p.channel = w.channel) last_post
                     FROM watches w LEFT JOIN jobs j ON j.id = w.last_job
                     LEFT JOIN channels c ON c.username = w.channel WHERE {where} ORDER BY w.channel""", args)

    @app.get("/api/watches")
    def watches(case: str = ""):
        if case.isdigit():
            return watch_rows("w.channel IN (SELECT channel FROM case_channels WHERE case_id = ?)", (int(case),))
        return watch_rows()

    @app.post("/api/watches")
    async def save_watch(request: Request):
        """Create/update: {"channel", "interval_min", "pages", "recheck_days", "media", "enabled"};
        by id: {"id", ...fields}; delete: {"id", "delete": true}."""
        from tgspider import normalize_seed
        b = await request.json()

        def clean(src, row=None):
            row = row or {}
            out = {
                "interval_min": max(15, int(src.get("interval_min", row.get("interval_min", 360)))),
                "pages": min(100, max(1, int(src.get("pages", row.get("pages", 30))))),
                "recheck_days": min(365, max(1, int(src.get("recheck_days", row.get("recheck_days", 7))))),
                "media": src.get("media", row.get("media", "photos")),
                "enabled": int(bool(src.get("enabled", row.get("enabled", 1)))),
            }
            if out["media"] not in MEDIA:
                raise HTTPException(400, f"media must be one of {MEDIA}")
            return out

        def do():
            if b.get("id"):
                row = q1("SELECT * FROM watches WHERE id = ?", (b["id"],))
                if not row:
                    raise HTTPException(404)
                if b.get("delete"):
                    con.execute("DELETE FROM watches WHERE id = ?", (b["id"],))
                    db.audit(con, actor(), "watch_delete", f"channel {row['channel']}")
                    return row["id"]
                f = clean(b, row)
                con.execute("UPDATE watches SET interval_min = ?, pages = ?, recheck_days = ?, media = ?, enabled = ?, "
                            "next_run = CASE WHEN ? = 1 AND enabled = 0 THEN NULL ELSE next_run END WHERE id = ?",
                            (*f.values(), f["enabled"], b["id"]))
                db.audit(con, actor(), "watch_edit", f"channel {row['channel']}", f)
                return row["id"]
            channel = normalize_seed(b.get("channel") or "")
            if not channel:
                raise HTTPException(400, "Enter a channel username.")
            f = clean(b)
            con.execute("""INSERT INTO watches(channel, interval_min, pages, recheck_days, media, enabled, created, created_by)
                           VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(channel) DO UPDATE SET
                             interval_min = excluded.interval_min, pages = excluded.pages,
                             recheck_days = excluded.recheck_days, media = excluded.media, enabled = excluded.enabled""",
                        (channel, *f.values(), db.now(), actor()))
            if not con.execute("SELECT 1 FROM channels WHERE username = ?", (channel,)).fetchone():
                con.execute("INSERT INTO channels(username, status, first_seen) VALUES (?, 'watched', ?)",
                            (channel, db.now()))
            db.audit(con, actor(), "watch_set", f"channel {channel}", f)
            return con.execute("SELECT id FROM watches WHERE channel = ?", (channel,)).fetchone()[0]
        wid = write(do)
        return watch_rows("w.id = ?", (wid,))[0] if not b.get("delete") else {"deleted": wid}

    @app.post("/api/watches/{wid}/run")
    def run_watch(wid: int):
        if not q1("SELECT 1 FROM watches WHERE id = ?", (wid,)):
            raise HTTPException(404)
        write(lambda: db.audit(con, actor(), "watch_run_now", f"watch {wid}"))
        return {"queued": run_due_watches(only_id=wid)}

    @app.get("/api/channels/{name}/events")
    def channel_events(name: str):
        return q("SELECT * FROM channel_events WHERE channel = ? ORDER BY id DESC", (name,))

    @app.get("/api/changes")
    def changes(case: str = "", since: str = "", limit: int = 200):
        """Everything that changed on Telegram since `since`: deleted and edited posts, deleted
        comments, channel changes. The raw material for a "what happened" feed."""
        scope_p = scope_c = scope_e = ""
        args_case = ()
        if case.isdigit():
            sub = "(SELECT channel FROM case_channels WHERE case_id = ?)"
            scope_p, scope_c, scope_e = f" AND p.channel IN {sub}", f" AND c.channel IN {sub}", f" AND e.channel IN {sub}"
            args_case = (int(case),)
        since = since or "0"
        deleted_posts = q(f"""SELECT 'post_deleted' kind, p.id, p.channel, p.msg_id, p.url, p.date, p.last_seen,
                                p.deleted_detected at, substr(p.text, 1, 200) text FROM posts p
                              WHERE p.deleted_detected >= ? {scope_p} ORDER BY p.deleted_detected DESC LIMIT ?""",
                          (since, *args_case, limit))
        edited_posts = q(f"""SELECT 'post_edited' kind, p.id, p.channel, p.msg_id, p.url, p.date, v.id version_id,
                               p.last_captured at, substr(p.text, 1, 200) text, substr(v.text, 1, 200) previous_text
                             FROM (SELECT post_id, MAX(id) vid FROM post_versions GROUP BY post_id) lv
                             JOIN post_versions v ON v.id = lv.vid JOIN posts p ON p.id = lv.post_id
                             WHERE p.last_captured >= ? {scope_p} ORDER BY p.last_captured DESC LIMIT ?""",
                         (since, *args_case, limit))
        deleted_comments = q(f"""SELECT 'comment_deleted' kind, c.id, c.channel, c.msg_id, c.date, c.last_seen,
                                   c.deleted_detected at, c.sender_id, substr(c.text, 1, 200) text FROM comments c
                                 WHERE c.deleted_detected >= ? AND c.is_mirror = 0 {scope_c}
                                 ORDER BY c.deleted_detected DESC LIMIT ?""", (since, *args_case, limit))
        events = q(f"""SELECT 'channel_' || e.kind kind, e.id, e.channel, e.old, e.new, e.detected_at at
                       FROM channel_events e WHERE e.detected_at >= ? {scope_e} ORDER BY e.id DESC LIMIT ?""",
                   (since, *args_case, limit))
        items = sorted(deleted_posts + edited_posts + deleted_comments + events, key=lambda r: r["at"] or "", reverse=True)
        return {"items": items[:limit], "counts": {"post_deleted": len(deleted_posts), "post_edited": len(edited_posts),
                                                   "comment_deleted": len(deleted_comments), "channel": len(events)}}

    @app.post("/api/channels/{name}/collect")
    async def collect_channel(name: str, request: Request):
        """Set up any mix of steps for one channel in one call (the "paste a handle, choose
        the steps" flow). Every key is optional:
          {"posts": {"pages": 10, "media": "photos", "depth": 1},
           "comments": {"limit": 200, "media": "none"},
           "similar": true,                                 # logged-in: Telegram's similar channels
           "watch": {"interval_min": 360, "recheck_days": 7, "media": "photos"},
           "comment_schedule": {"interval_min": 10, "media": "none"},
           "case": 3, "role": "seed"}                        # also add it to a case"""
        from tgspider import normalize_seed
        b = await request.json()
        ch = normalize_seed(name)
        if not ch:
            raise HTTPException(400, "Not a public username")
        out = {"channel": ch, "jobs": {}}

        def ensure_channel():
            if not con.execute("SELECT 1 FROM channels WHERE username = ?", (ch,)).fetchone():
                con.execute("INSERT INTO channels(username, status, first_seen) VALUES (?, 'watched', ?)", (ch, db.now()))
        write(ensure_channel)
        if b.get("case"):
            cid = int(b["case"])
            if not q1("SELECT 1 FROM cases WHERE id = ?", (cid,)):
                raise HTTPException(404, "No such case")
            role = b.get("role") if b.get("role") in ("seed", "watched", "context") else "watched"

            def add():
                if con.execute("INSERT OR IGNORE INTO case_channels(case_id, channel, role, added, added_by) VALUES (?,?,?,?,?)",
                               (cid, ch, role, db.now(), actor())).rowcount:
                    db.audit(con, actor(), "case_channels_add", f"case {cid}", {"channels": [ch], "role": role})
            write(add)
            out["case"] = cid
        if b.get("posts") is not None:
            p = b["posts"] or {}
            busy = active_crawl(ch)
            out["jobs"]["posts"] = busy or runner.submit("crawl", {
                "seeds": [ch], "depth": max(1, int(p.get("depth", 1))), "pages": min(500, max(1, int(p.get("pages", 10)))),
                "max_channels": int(p.get("max_channels", 50)), "media": p.get("media", "photos") if p.get("media", "photos") in MEDIA else "photos",
                "network_only": bool(p.get("network_only"))}, actor())
        if b.get("comments") is not None:
            c = b["comments"] or {}
            busy = runner.active_comment_job(ch)
            out["jobs"]["comments"] = busy or runner.submit("comments", {
                "channel": ch, "limit": max(1, int(c.get("limit", 200))),
                "media": c.get("media", "none") if c.get("media", "none") in MEDIA else "none",
                "max_mb": int(c.get("max_mb", 50))}, actor())
        if b.get("similar"):
            out["jobs"]["similar"] = runner.submit("discover", {"method": "similar", "channels": [ch]}, actor())
        if b.get("watch") is not None:
            w = b["watch"] or {}

            def do_watch():
                con.execute("""INSERT INTO watches(channel, interval_min, pages, recheck_days, media, enabled, created, created_by)
                               VALUES (?,?,?,?,?,1,?,?) ON CONFLICT(channel) DO UPDATE SET interval_min = excluded.interval_min,
                               recheck_days = excluded.recheck_days, media = excluded.media, enabled = 1""",
                            (ch, max(15, int(w.get("interval_min", 360))), min(100, int(w.get("pages", 30))),
                             max(1, int(w.get("recheck_days", 7))),
                             w.get("media", "photos") if w.get("media", "photos") in MEDIA else "photos", db.now(), actor()))
                db.audit(con, actor(), "watch_set", f"channel {ch}", w)
            write(do_watch)
            out["watch"] = watch_rows("w.channel = ?", (ch,))[0]
        if b.get("comment_schedule") is not None:
            s = b["comment_schedule"] or {}

            def do_sched():
                con.execute("""INSERT INTO schedules(channel, interval_min, media, enabled, created) VALUES (?,?,?,1,?)
                               ON CONFLICT(channel) DO UPDATE SET interval_min = excluded.interval_min,
                               media = excluded.media, enabled = 1""",
                            (ch, max(1, int(s.get("interval_min", 10))),
                             s.get("media", "none") if s.get("media", "none") in MEDIA else "none", db.now()))
                db.audit(con, actor(), "schedule_set", f"channel {ch}", s)
            write(do_sched)
            out["comment_schedule"] = q1("SELECT * FROM schedules WHERE channel = ?", (ch,))
        return out
