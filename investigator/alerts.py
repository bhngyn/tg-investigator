"""
Alerts: rules checked after every collection job.

Rule kinds
  keyword          new posts and/or comments matching a query (same syntax as Search)
  post_deleted     a captured post was found deleted
  post_edited      a captured post's text or media changed
  comment_deleted  a captured comment was found deleted
  channel_change   renamed, title/description changed, preview disabled, gone, subscriber jump
  admin_change     someone added to / removed from a comment group's admin list
  new_candidate    a channel not in the case is now linked from at least N of the case's channels
  media_reused     a newly collected file (same SHA-256) was already seen in another channel

A rule can be limited to a case and/or a list of channels. Each alert is raised once per
rule and item. Rules only fire on things collected after they were created.
"""

import json
import subprocess
import sys

from fastapi import HTTPException, Request

from . import db

KINDS = ("keyword", "post_deleted", "post_edited", "comment_deleted", "channel_change",
         "admin_change", "new_candidate", "media_reused")


def _scope(rule, col):
    """SQL fragment + args limiting `col` (a channel column) to the rule's case/channels."""
    sql, args = "", []
    if rule["case_id"]:
        sql += f" AND {col} IN (SELECT channel FROM case_channels WHERE case_id = ?)"
        args.append(rule["case_id"])
    chans = json.loads(rule["channels"] or "[]")
    if chans:
        sql += f" AND {col} IN ({','.join('?' * len(chans))})"
        args += chans
    return sql, args


def matches(ctx, rule, since, cursors, limit=500):
    """What this rule would raise for things collected since `since` / past the cursors.
    Returns [{"item_type", "item_id", "channel", "title", "detail"}]."""
    q = ctx.q
    kind, out = rule["kind"], []
    if kind == "keyword":
        expr = rule.get("query_fts") or db.fts_query(rule["query"])   # frozen expansion; legacy rules stay literal
        if not expr:
            return []
        expr = db.fts_text(expr)                   # text columns only, wrapped now so frozen strings stay as saved
        if rule["scope"] in ("posts", "both"):
            sc, a = _scope(rule, "p.channel")
            for r in q(f"""SELECT p.id, p.channel, p.url, p.date, substr(p.text, 1, 300) text FROM posts p
                           JOIN posts_fts f ON f.rowid = p.id WHERE posts_fts MATCH ? AND p.first_captured >= ? {sc}
                           ORDER BY p.date DESC LIMIT ?""", (expr, since, *a, limit)):
                out.append({"item_type": "post", "item_id": r["id"], "channel": r["channel"],
                            "title": f"“{rule['query']}” in @{r['channel']}", "detail": r})
        if rule["scope"] in ("comments", "both"):
            sc, a = _scope(rule, "c.channel")
            for r in q(f"""SELECT c.id, c.channel, c.msg_id, c.date, c.sender_id, substr(c.text, 1, 300) text FROM comments c
                           JOIN comments_fts f ON f.rowid = c.id WHERE comments_fts MATCH ? AND c.first_captured >= ?
                           AND c.is_mirror = 0 {sc} ORDER BY c.date DESC LIMIT ?""", (expr, since, *a, limit)):
                out.append({"item_type": "comment", "item_id": r["id"], "channel": r["channel"],
                            "title": f"“{rule['query']}” in comments of @{r['channel']}", "detail": r})
    elif kind == "post_deleted":
        sc, a = _scope(rule, "p.channel")
        for r in q(f"""SELECT p.id, p.channel, p.url, p.date, p.last_seen, p.deleted_detected, substr(p.text, 1, 300) text
                       FROM posts p WHERE p.deleted_detected >= ? {sc} LIMIT ?""", (since, *a, limit)):
            out.append({"item_type": "post", "item_id": r["id"], "channel": r["channel"],
                        "title": f"Post deleted in @{r['channel']}", "detail": r})
    elif kind == "comment_deleted":
        sc, a = _scope(rule, "c.channel")
        rows = q(f"""SELECT c.id, c.channel, c.msg_id, c.date, c.sender_id, c.last_seen, c.deleted_detected,
                       substr(c.text, 1, 300) text FROM comments c
                     WHERE c.deleted_detected >= ? AND c.is_mirror = 0 {sc} LIMIT ?""", (since, *a, limit))
        for r in rows:
            out.append({"item_type": "comment", "item_id": r["id"], "channel": r["channel"],
                        "title": f"Comment deleted in @{r['channel']}", "detail": r})
    elif kind == "post_edited":
        sc, a = _scope(rule, "p.channel")
        for r in q(f"""SELECT v.id version_id, p.id, p.channel, p.url, substr(v.text, 1, 300) previous_text,
                         substr(p.text, 1, 300) text FROM post_versions v JOIN posts p ON p.id = v.post_id
                       WHERE v.id > ? {sc} LIMIT ?""", (cursors.get("post_versions", 0), *a, limit)):
            out.append({"item_type": "post", "item_id": f"{r['id']}:v{r['version_id']}", "channel": r["channel"],
                        "title": f"Post edited in @{r['channel']}", "detail": r})
    elif kind == "channel_change":
        sc, a = _scope(rule, "e.channel")
        for r in q(f"SELECT * FROM channel_events e WHERE e.id > ? {sc} LIMIT ?",
                   (cursors.get("channel_events", 0), *a, limit)):
            label = r["kind"].replace("_", " ")
            out.append({"item_type": "channel", "item_id": f"{r['channel']}:event{r['id']}", "channel": r["channel"],
                        "title": f"@{r['channel']}: {label}", "detail": r})
    elif kind == "admin_change":
        sc, a = _scope(rule, "g.channel")
        for r in q(f"""SELECT a.*, g.channel, u.username, u.first_name, u.last_name FROM group_admins a
                       JOIN comment_groups g ON g.group_id = a.group_id LEFT JOIN tg_users u ON u.user_id = a.user_id
                       WHERE (a.first_seen >= ? AND a.first_seen > g.first_seen) OR a.removed_detected >= ? {sc} LIMIT ?""",
                   (since, since, *a, limit)):
            who = " ".join(x for x in (r["first_name"], r["last_name"]) if x) or r["username"] or r["user_id"]
            removed = bool(r["removed_detected"] and r["removed_detected"] >= since)
            out.append({"item_type": "account", "item_id": f"{r['group_id']}:{r['user_id']}:{'removed' if removed else 'added'}",
                        "channel": r["channel"],
                        "title": f"@{r['channel']} comments: admin {'removed' if removed else 'added'}: {who}", "detail": r})
    elif kind == "new_candidate":
        if not rule["case_id"]:
            return []
        n = max(1, rule["min_links"] or 2)
        for r in q("""SELECT e.target channel, COUNT(DISTINCT e.source) links, SUM(e.weight) signals, ch.title, ch.type
                      FROM edges e LEFT JOIN channels ch ON ch.username = e.target
                      WHERE e.source IN (SELECT channel FROM case_channels WHERE case_id = ?)
                        AND e.target NOT IN (SELECT channel FROM case_channels WHERE case_id = ?)
                        AND COALESCE(ch.status, '') != 'dismissed' AND COALESCE(ch.type, 'channel') IN ('channel', 'group', 'unknown')
                      GROUP BY e.target HAVING links >= ? ORDER BY links DESC LIMIT ?""",
                   (rule["case_id"], rule["case_id"], n, limit)):
            out.append({"item_type": "channel", "item_id": r["channel"], "channel": r["channel"],
                        "title": f"@{r['channel']} is linked from {r['links']} channels in the case", "detail": r})
    elif kind == "media_reused":
        sc, a = _scope(rule, "p.channel")
        for r in q(f"""SELECT m.id media_id, m.sha256, p.id, p.channel, p.url,
                         (SELECT group_concat(DISTINCT p2.channel) FROM media m2 JOIN posts p2 ON p2.id = m2.post_id
                          WHERE m2.sha256 = m.sha256 AND p2.channel != p.channel) elsewhere
                       FROM media m JOIN posts p ON p.id = m.post_id WHERE m.id > ? {sc} LIMIT ?""",
                   (cursors.get("media", 0), *a, limit * 4)):
            if r["elsewhere"]:
                out.append({"item_type": "post", "item_id": f"{r['id']}:{r['sha256'][:16]}", "channel": r["channel"],
                            "title": f"File in @{r['channel']} also posted in {', '.join('@' + x for x in r['elsewhere'].split(','))}",
                            "detail": r})
    return out[:limit]


def current_cursors(ctx):
    one = lambda sql: (ctx.q1(sql) or {}).get("n") or 0
    return {"post_versions": one("SELECT MAX(id) n FROM post_versions"),
            "channel_events": one("SELECT MAX(id) n FROM channel_events"),
            "media": one("SELECT MAX(id) n FROM media")}


def _notify(title, body):
    """macOS notification (local only; nothing leaves the laptop)."""
    if sys.platform != "darwin":
        return
    esc = lambda s: str(s).replace("\\", "\\\\").replace('"', '\\"')[:200]
    try:
        subprocess.run(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'],
                       timeout=5, capture_output=True)
    except Exception:
        pass


def evaluate(ctx, since, job_id=None):
    """Check every enabled rule. Returns the number of new alerts."""
    get = lambda k: (ctx.q1("SELECT value FROM settings WHERE key = ?", (k,)) or {}).get("value")
    cursors = json.loads(get("alert_cursors") or "{}")
    now_cursors = current_cursors(ctx)
    if not cursors:                     # first ever run: start from now, don't replay history
        cursors = now_cursors
    new_total, notes = 0, []
    for rule in ctx.q("SELECT * FROM alert_rules WHERE enabled = 1"):
        since_rule = max(since, rule["created"] or "")
        rc = {k: v for k, v in cursors.items()}
        found = matches(ctx, rule, since_rule, rc)

        def save():
            n = 0
            for m in found:
                n += ctx.con.execute(
                    "INSERT OR IGNORE INTO alerts(rule_id, case_id, kind, item_type, item_id, channel, title, detail, created, job_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rule["id"], rule["case_id"], rule["kind"], m["item_type"], str(m["item_id"]), m["channel"],
                     m["title"], json.dumps(m["detail"], ensure_ascii=False, default=str), db.now(), job_id)).rowcount
            return n
        n = ctx.write(save)
        new_total += n
        if n and rule["notify"]:
            notes.append(f"{rule['name']}: {n} new")
    ctx.write(lambda: ctx.con.execute("INSERT INTO settings(key, value) VALUES ('alert_cursors', ?) "
                                      "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(now_cursors),)))
    if notes:
        _notify("Telegram Investigator", "; ".join(notes))
    return new_total


def insert_rule(ctx, f, audit_action="alert_rule_create"):
    """Insert one alert rule from a validated field dict (as `validate` returns). Caller commits (ctx.write).
    Returns the new id. Used by the API and by saved searches (to_alert)."""
    cur = ctx.con.execute(f"INSERT INTO alert_rules({', '.join(f)}, created, created_by) VALUES "
                          f"({', '.join('?' * len(f))}, ?, ?)", (*f.values(), db.now(), ctx.actor()))
    db.audit(ctx.con, ctx.actor(), audit_action, f"alert_rule {cur.lastrowid}", f)
    return cur.lastrowid


def register(app, ctx):
    q, q1, write, con, actor = ctx.q, ctx.q1, ctx.write, ctx.con, ctx.actor

    def after_job(job, stats):
        j = q1("SELECT started, created FROM jobs WHERE id = ?", (job["id"],)) or {}
        evaluate(ctx, j.get("started") or j.get("created") or db.now(), job["id"])

    ctx.runner.hooks["after_job"].append(after_job)
    # set the cursors now if this is the first run with alerts, so rules made later start clean
    if not q1("SELECT 1 FROM settings WHERE key = 'alert_cursors'"):
        write(lambda: con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('alert_cursors', ?)",
                                  (json.dumps(current_cursors(ctx)),)))

    def rule_rows(where="1", args=()):
        rows = _rule_rows(where, args)
        for r in rows:
            r["expansion"] = json.loads(r["expansion"]) if r.get("expansion") else None
            r["truncated"] = bool((r["expansion"] or {}).get("truncated"))     # frozen while cut short
            r["truncated_count"] = (r["expansion"] or {}).get("truncated_count") or 0
        return rows

    def _rule_rows(where, args):
        return q(f"""SELECT r.*, k.name case_name,
                       (SELECT COUNT(*) FROM alerts a WHERE a.rule_id = r.id) alerts,
                       (SELECT COUNT(*) FROM alerts a WHERE a.rule_id = r.id AND a.read_at IS NULL) unread
                     FROM alert_rules r LEFT JOIN cases k ON k.id = r.case_id WHERE {where} ORDER BY r.id""", args)

    def freeze(f, b, row):
        """Keyword rules expand ONCE: on create, when the query changes, or on {"reexpand": true}. The result
        (final FTS expression + the expansion behind it) is stored, so later dictionary edits never change the rule.
        Body: expand (default true), drop / skip (lists or comma strings). Rules made before this feature have
        no query_fts and keep matching literally until re-expanded."""
        if f["kind"] != "keyword":
            f.update(query_fts=None, expansion=None, dict_version=None)
        elif not row or b.get("reexpand") or f["query"] != row.get("query") or row.get("kind") != "keyword":
            exp = ctx.expand(f["query"], b.get("expand", True) not in (False, 0, "0", "false", "off"),
                             b.get("drop") or (), b.get("skip") or ())
            f.update(query_fts=exp["fts"], expansion=json.dumps(exp, ensure_ascii=False), dict_version=exp["dict_version"])
        else:
            f.update({k: row.get(k) for k in ("query_fts", "expansion", "dict_version")})
        return f

    def validate(b, row=None):
        row = dict(row or {})
        f = {k: b.get(k, row.get(k)) for k in ("name", "case_id", "kind", "query", "scope", "channels",
                                                 "min_links", "notify", "enabled")}
        if f["kind"] not in KINDS:
            raise HTTPException(400, f"kind must be one of {KINDS}")
        if not (f["name"] or "").strip():
            f["name"] = {"keyword": f"“{f.get('query') or ''}”"}.get(f["kind"], f["kind"].replace("_", " "))
        if f["kind"] == "keyword":
            if not db.fts_query(f["query"]):
                raise HTTPException(400, "Keyword rules need a query (same syntax as Search).")
            f["scope"] = f["scope"] if f["scope"] in ("posts", "comments", "both") else "posts"
        else:
            f["scope"] = f["scope"] or "posts"
        if f["kind"] == "new_candidate":
            if not f["case_id"]:
                raise HTTPException(400, "New-candidate rules belong to a case.")
            f["min_links"] = max(1, int(f["min_links"] or 2))   # 1 fires on every link: noisy
        if isinstance(f["channels"], list):
            f["channels"] = json.dumps(f["channels"]) if f["channels"] else None
        f["notify"] = int(bool(f["notify"]))
        f["enabled"] = int(True if f["enabled"] is None else bool(f["enabled"]))
        f["case_id"] = int(f["case_id"]) if f["case_id"] else None
        return freeze(f, b, row)

    @app.get("/api/alert_rules")
    def rules(case: str = ""):
        return rule_rows("r.case_id = ?", (int(case),)) if case.isdigit() else rule_rows()

    @app.post("/api/alert_rules")
    async def save_rule(request: Request):
        """Create {"kind", "name", "query", "scope", "case_id", "channels": [..], "min_links", "notify"};
        edit {"id", ...}; delete {"id", "delete": true} (its alerts go too).
        Keyword rules also take expand / drop / skip / reexpand (see `freeze`)."""
        b = await request.json()

        def do():
            if b.get("id"):
                row = q1("SELECT * FROM alert_rules WHERE id = ?", (b["id"],))
                if not row:
                    raise HTTPException(404)
                if b.get("delete"):
                    con.execute("DELETE FROM alerts WHERE rule_id = ?", (row["id"],))
                    con.execute("DELETE FROM alert_rules WHERE id = ?", (row["id"],))
                    db.audit(con, actor(), "alert_rule_delete", f"alert_rule {row['id']}", {"name": row["name"]})
                    return None
                f = validate(b, row)
                con.execute(f"UPDATE alert_rules SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), row["id"]))
                db.audit(con, actor(), "alert_rule_edit", f"alert_rule {row['id']}", f)
                return row["id"]
            return insert_rule(ctx, validate(b))
        rid = write(do)
        return rule_rows("r.id = ?", (rid,))[0] if rid else {"deleted": True}

    @app.post("/api/alert_rules/preview")
    async def preview_rule(request: Request):
        """Dry run: what a rule would have matched over the last `days` days. Saves nothing.
        (Edit, channel-change and new-media rules are checked against all history.)"""
        b = await request.json()
        # previewing an existing rule ({"id": ..}): unless the query changed or "reexpand" is set, use the rule's
        # frozen expression and expansion, exactly what it will match, not a fresh dictionary expansion
        row = q1("SELECT * FROM alert_rules WHERE id = ?", (int(b["id"]),)) if str(b.get("id") or "").isdigit() else None
        if b.get("id") and not row:
            raise HTTPException(404)
        f = validate(b, row)
        days = min(365, max(1, int(b.get("days", 7))))
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        found = matches(ctx, {**f, "id": 0, "created": ""}, since, {}, limit=200)
        return {"since": since, "count": len(found), "matches": found,
                "expansion": json.loads(f["expansion"]) if f["expansion"] else None}

    @app.get("/api/alerts")
    def list_alerts(case: str = "", unread: str = "", rule: str = "", limit: int = 100, offset: int = 0):
        where, args = ["1"], []
        if case.isdigit():
            where.append("a.case_id = ?"); args.append(int(case))
        if unread == "1":
            where.append("a.read_at IS NULL")
        if rule.isdigit():
            where.append("a.rule_id = ?"); args.append(int(rule))
        w = " AND ".join(where)
        rows = q(f"""SELECT a.*, r.name rule_name FROM alerts a LEFT JOIN alert_rules r ON r.id = a.rule_id
                     WHERE {w} ORDER BY a.id DESC LIMIT ? OFFSET ?""", (*args, min(limit, 500), offset))
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "null")
        total = q1(f"SELECT COUNT(*) n, SUM(a.read_at IS NULL) unread FROM alerts a WHERE {w}", args)
        return {"total": total["n"], "unread": total["unread"] or 0, "alerts": rows}

    @app.post("/api/alerts/read")
    async def mark_read(request: Request):
        """{"ids": [..]} or {"all": true, "case": 3}; {"unread": true} marks them unread again."""
        b = await request.json()
        val = None if b.get("unread") else db.now()

        # only flip what changes, so the first time an alert was read is kept
        only = "read_at IS NULL" if val else "read_at IS NOT NULL"

        def do():
            if b.get("all"):
                if b.get("case"):
                    return con.execute(f"UPDATE alerts SET read_at = ? WHERE case_id = ? AND {only}",
                                       (val, int(b["case"]))).rowcount
                return con.execute(f"UPDATE alerts SET read_at = ? WHERE {only}", (val,)).rowcount
            ids = [int(i) for i in b.get("ids") or []]
            if not ids:
                return 0
            return con.execute(f"UPDATE alerts SET read_at = ? WHERE {only} AND id IN ({','.join('?' * len(ids))})",
                               (val, *ids)).rowcount
        return {"updated": write(do)}

    @app.post("/api/alerts/check")
    def check_now():
        """Run all rules now over the last day (normally they run after every job)."""
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        return {"new": evaluate(ctx, since)}
