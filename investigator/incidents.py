"""
Incidents and evidence packages.

An incident is one event (an attack, a killing, a detention) with its date, place and the
posts, comments, media, channels and accounts that document it.

An evidence package is a zip written to <workspace>/exports/ holding, for a case, an incident
or a hand-picked selection:
  items.json / items.csv   every post and comment with where, when and how it was collected
  media/                   the collected files, named by their SHA-256 (byte-identical copies)
  pages/                   the raw preview pages posts were read from (gzip, named by SHA-256)
  api/                     the raw API responses comments were read from (gzip, named by SHA-256)
  capture_log.csv          the capture record of every file above
  audit.csv                every investigator action on these items
  incident(s).json         incident details, if any
  manifest.json            what's in the package, who made it and when
  SHA256SUMS               a hash of every file, checkable with `shasum -a 256 -c SHA256SUMS`
The zip's own SHA-256 is recorded in the workspace (exports table + audit log).
"""

import csv
import hashlib
import io
import json
import os
import zipfile
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse

from . import db, entities, places, searchlog

ITEM_TYPES = ("post", "comment", "media", "comment_media", "channel", "account")
STATUSES = ("open", "verified", "disputed", "closed")
TOOL = "telegram-investigator"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def register(app, ctx):
    q, q1, write, con, actor = ctx.q, ctx.q1, ctx.write, ctx.con, ctx.actor
    exports_dir = os.path.join(ctx.workspace, "exports")

    def get_incident(iid):
        i = q1("SELECT i.*, k.name case_name FROM incidents i LEFT JOIN cases k ON k.id = i.case_id WHERE i.id = ?", (iid,))
        if not i:
            raise HTTPException(404, "No such incident")
        return i

    def incident_items(iid):
        items = q("SELECT * FROM incident_items WHERE incident_id = ? ORDER BY id", (iid,))
        for it in items:
            t, i = it["item_type"], it["item_id"]
            if t == "post":
                it["item"] = q1("SELECT id, channel, msg_id, url, date, substr(text, 1, 400) text, media_types, "
                                "deleted_detected FROM posts WHERE id = ?", (int(i),))
            elif t == "comment":
                it["item"] = q1("SELECT id, channel, msg_id, date, sender_type, sender_id, substr(text, 1, 400) text, "
                                "deleted_detected FROM comments WHERE id = ?", (int(i),))
            elif t == "media":
                it["item"] = q1("SELECT m.*, p.channel, p.url FROM media m JOIN posts p ON p.id = m.post_id WHERE m.id = ?",
                                (int(i),))
            elif t == "comment_media":
                it["item"] = q1("SELECT m.*, c.channel FROM comment_media m JOIN comments c ON c.id = m.comment_id "
                                "WHERE m.id = ?", (int(i),))
            elif t == "channel":
                it["item"] = q1("SELECT username, title, type, subscribers, tg_id FROM channels WHERE username = ?", (i,))
            elif t == "account":
                it["item"] = q1("SELECT user_id, username, first_name, last_name FROM tg_users WHERE user_id = ?", (int(i),))
        return items

    @app.get("/api/incidents")
    def list_incidents(case: str = "", status: str = ""):
        where, args = ["1"], []
        if case.isdigit():
            where.append("i.case_id = ?"); args.append(int(case))
        if status:
            where.append("i.status = ?"); args.append(status)
        return q(f"""SELECT i.*, k.name case_name,
                       (SELECT COUNT(*) FROM incident_items x WHERE x.incident_id = i.id) items
                     FROM incidents i LEFT JOIN cases k ON k.id = i.case_id WHERE {' AND '.join(where)}
                     ORDER BY COALESCE(i.date_start, i.created) DESC""", args)

    @app.get("/api/incidents/{iid}")
    def incident(iid: int):
        i = get_incident(iid)
        i["items"] = incident_items(iid)
        i["history"] = q("SELECT ts, actor, action, detail FROM audit WHERE target = ? ORDER BY id", (f"incident {iid}",))
        i["exports"] = q("SELECT * FROM exports WHERE scope = 'incident' AND scope_id = ? ORDER BY id DESC", (iid,))
        return i

    FIELDS = ("case_id", "title", "description", "date_start", "date_end", "date_note", "location", "lat", "lon",
              "location_note", "status")

    @app.post("/api/incidents")
    async def save_incident(request: Request):
        """Create/edit: {"id"?, "title", "description", "date_start", "date_end", "date_note", "location",
        "lat", "lon", "location_note", "status", "case_id"}. Incidents are closed, not deleted."""
        b = await request.json()

        def do():
            old = get_incident(int(b["id"])) if b.get("id") else None
            f = {k: b[k] for k in FIELDS if k in b}
            if "status" in f and f["status"] not in STATUSES:
                raise HTTPException(400, f"status must be one of {STATUSES}")
            for k in ("lat", "lon"):
                if k not in f:
                    continue
                if f[k] in ("", None):
                    f[k] = None
                else:
                    f[k] = float(f[k])
                    if (k == "lat" and not -90 <= f[k] <= 90) or (k == "lon" and not -180 <= f[k] <= 180):
                        raise HTTPException(400, f"{k} out of range")
            if old:
                if f:
                    con.execute(f"UPDATE incidents SET {', '.join(k + ' = ?' for k in f)}, updated = ? WHERE id = ?",
                                (*f.values(), db.now(), old["id"]))
                    db.audit(con, actor(), "incident_edit", f"incident {old['id']}",
                             {k: {"from": old[k], "to": v} for k, v in f.items() if old[k] != v})
                return old["id"]
            if not (f.get("title") or "").strip():
                raise HTTPException(400, "An incident needs a title.")
            cols = list(f)
            cur = con.execute(f"INSERT INTO incidents({', '.join(cols)}, created, created_by, updated) "
                              f"VALUES ({', '.join('?' * len(cols))}, ?, ?, ?)", (*f.values(), db.now(), actor(), db.now()))
            db.audit(con, actor(), "incident_create", f"incident {cur.lastrowid}", f)
            return cur.lastrowid
        return incident(write(do))

    @app.post("/api/incidents/{iid}/items")
    async def incident_add(iid: int, request: Request):
        """{"add": [{"item_type": "post", "item_id": 12, "relation": "primary footage", "note": ".."}]}
        {"remove": [{"item_type": "post", "item_id": 12}]}"""
        b = await request.json()
        get_incident(iid)

        def do():
            added = removed = 0
            for it in b.get("add") or []:
                if it.get("item_type") not in ITEM_TYPES:
                    raise HTTPException(400, f"item_type must be one of {ITEM_TYPES}")
                n = con.execute("""INSERT INTO incident_items(incident_id, item_type, item_id, relation, note, added_by, added_at)
                                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(incident_id, item_type, item_id) DO UPDATE SET
                                   relation = COALESCE(excluded.relation, relation), note = COALESCE(excluded.note, note)""",
                                (iid, it["item_type"], str(it["item_id"]), it.get("relation"), it.get("note"),
                                 actor(), db.now())).rowcount
                added += n
                db.audit(con, actor(), "incident_item_add", f"incident {iid}",
                         {"item": f"{it['item_type']} {it['item_id']}", "relation": it.get("relation")})
            for it in b.get("remove") or []:
                n = con.execute("DELETE FROM incident_items WHERE incident_id = ? AND item_type = ? AND item_id = ?",
                                (iid, it["item_type"], str(it["item_id"]))).rowcount
                removed += n
                if n:
                    db.audit(con, actor(), "incident_item_remove", f"incident {iid}",
                             {"item": f"{it['item_type']} {it['item_id']}"})
            return {"added": added, "removed": removed}
        return write(do)

    @app.get("/api/items/{item_type}/{item_id}/incidents")
    def item_incidents(item_type: str, item_id: str):
        return q("""SELECT i.id, i.title, i.status, x.relation FROM incident_items x JOIN incidents i ON i.id = x.incident_id
                    WHERE x.item_type = ? AND x.item_id = ?""", (item_type, item_id))

    # ---- evidence packages --------------------------------------------------------

    def collect(scope, scope_id, b):
        """-> (posts, comments, incidents, extra_media_ids) for an export."""
        post_ids, comment_ids, media_ids, cmedia_ids, incs = set(), set(), set(), set(), []
        if scope == "incident":
            incs = [scope_id]
        elif scope == "case":
            statuses = b.get("statuses") or ["relevant"]
            marks = ",".join("?" * len(statuses))
            for r in q(f"SELECT item_type, item_id FROM case_reviews WHERE case_id = ? AND status IN ({marks})",
                       (scope_id, *statuses)):
                (post_ids if r["item_type"] == "post" else comment_ids).add(r["item_id"])
            if "unreviewed" in statuses:     # never-triaged items have no case_reviews row: they are unreviewed too
                done = ("NOT EXISTS (SELECT 1 FROM case_reviews r WHERE r.case_id = ? AND r.item_type = '{t}' "
                        "AND r.item_id = {a}.id AND r.status != 'unreviewed')")
                post_ids |= {r["id"] for r in q(
                    "SELECT p.id FROM posts p JOIN case_channels cc ON cc.channel = p.channel AND cc.case_id = ? "
                    "WHERE " + done.format(t="post", a="p"), (scope_id, scope_id))}
                comment_ids |= {r["id"] for r in q(
                    "SELECT c.id FROM comments c JOIN case_channels cc ON cc.channel = c.channel AND cc.case_id = ? "
                    "WHERE c.is_mirror = 0 AND c.media_types != '[\"service\"]' AND " + done.format(t="comment", a="c"),
                    (scope_id, scope_id))}
            if b.get("include_incidents", True):
                incs = [r["id"] for r in q("SELECT id FROM incidents WHERE case_id = ?", (scope_id,))]
        else:   # selection
            post_ids |= {int(i) for i in b.get("posts") or []}
            comment_ids |= {int(i) for i in b.get("comments") or []}
        for iid in incs:
            for it in q("SELECT item_type, item_id FROM incident_items WHERE incident_id = ?", (iid,)):
                t, i = it["item_type"], it["item_id"]
                if t == "post": post_ids.add(int(i))
                elif t == "comment": comment_ids.add(int(i))
                elif t == "media": media_ids.add(int(i))
                elif t == "comment_media": cmedia_ids.add(int(i))
        for m in q(f"SELECT post_id FROM media WHERE id IN ({','.join('?' * len(media_ids))})", list(media_ids)) if media_ids else []:
            post_ids.add(m["post_id"])
        for m in q(f"SELECT comment_id FROM comment_media WHERE id IN ({','.join('?' * len(cmedia_ids))})",
                   list(cmedia_ids)) if cmedia_ids else []:
            comment_ids.add(m["comment_id"])
        return sorted(post_ids), sorted(comment_ids), incs

    def build_package(scope, scope_id, b):
        posts_ids, comment_ids, inc_ids = collect(scope, scope_id, b)
        if not (posts_ids or comment_ids or inc_ids):
            raise HTTPException(400, "Nothing to export: no items match (in a case, only items reviewed as "
                                     "relevant are exported unless you pass other statuses).")
        include_media = b.get("media", True)
        case_id = scope_id if scope == "case" else (b.get("case") and int(b["case"]))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base = f"evidence-{scope}{scope_id or ''}-{stamp}"
        os.makedirs(exports_dir, exist_ok=True)
        files = {}           # arcname -> sha256
        store = ctx.store

        def mark(ids):
            return ",".join("?" * len(ids))

        posts = q(f"""SELECT p.*, c.title channel_title, c.tg_id channel_tg_id FROM posts p
                      LEFT JOIN channels c ON c.username = p.channel WHERE p.id IN ({mark(posts_ids)}) ORDER BY p.date""",
                  posts_ids) if posts_ids else []
        comments = q(f"""SELECT c.*, u.username sender_username, u.first_name sender_first, u.last_name sender_last,
                           u.phone sender_phone FROM comments c LEFT JOIN tg_users u ON c.sender_type = 'user'
                           AND u.user_id = c.sender_id WHERE c.id IN ({mark(comment_ids)}) ORDER BY c.date""",
                     comment_ids) if comment_ids else []
        media = q(f"SELECT * FROM media WHERE post_id IN ({mark(posts_ids)}) ORDER BY post_id, idx",
                  posts_ids) if posts_ids else []
        cmedia = q(f"SELECT * FROM comment_media WHERE comment_id IN ({mark(comment_ids)}) ORDER BY comment_id, idx",
                   comment_ids) if comment_ids else []
        page_shas = sorted({p["page_sha256"] for p in posts if p["page_sha256"]})
        api_shas = sorted({c["raw_sha256"] for c in comments if c["raw_sha256"]})
        all_shas = [m["sha256"] for m in media + cmedia] + page_shas + api_shas
        captures = q(f"SELECT * FROM captures WHERE sha256 IN ({mark(all_shas)}) ORDER BY captured_at",
                     all_shas) if all_shas else []
        targets = [f"post {i}" for i in posts_ids] + [f"comment {i}" for i in comment_ids] + \
                  [f"incident {i}" for i in inc_ids] + ([f"case {case_id}"] if case_id else [])
        audit = q(f"SELECT * FROM audit WHERE target IN ({mark(targets)}) ORDER BY id", targets) if targets else []
        versions = {}
        for v in q(f"SELECT * FROM post_versions WHERE post_id IN ({mark(posts_ids)}) ORDER BY id", posts_ids) if posts_ids else []:
            versions.setdefault(("post", v["post_id"]), []).append(v)
        for v in q(f"SELECT * FROM comment_versions WHERE comment_id IN ({mark(comment_ids)}) ORDER BY id",
                   comment_ids) if comment_ids else []:
            versions.setdefault(("comment", v["comment_id"]), []).append(v)
        reviews, tags, notes = {}, {}, {}
        if case_id:
            for r in q("SELECT * FROM case_reviews WHERE case_id = ?", (case_id,)):
                reviews[(r["item_type"], r["item_id"])] = r["status"]
            for r in q("SELECT ct.item_type, ct.item_id, t.name FROM case_tags ct JOIN tags t ON t.id = ct.tag_id "
                       "WHERE ct.case_id = ?", (case_id,)):
                tags.setdefault((r["item_type"], r["item_id"]), []).append(r["name"])
            for r in q("SELECT * FROM notes WHERE case_id = ?", (case_id,)):
                key = ("post", r["post_id"]) if r["post_id"] else ("comment", r["comment_id"])
                notes.setdefault(key, []).append({"text": r["text"], "author": r["author"], "at": r["created_at"]})
        else:
            for p in posts:
                reviews[("post", p["id"])] = p["review_status"]
            for r in q(f"SELECT pt.post_id, t.name FROM post_tags pt JOIN tags t ON t.id = pt.tag_id "
                       f"WHERE pt.post_id IN ({mark(posts_ids)})", posts_ids) if posts_ids else []:
                tags.setdefault(("post", r["post_id"]), []).append(r["name"])
            for r in q(f"SELECT * FROM notes WHERE post_id IN ({mark(posts_ids)}) AND case_id IS NULL",
                       posts_ids) if posts_ids else []:
                notes.setdefault(("post", r["post_id"]), []).append({"text": r["text"], "author": r["author"],
                                                                     "at": r["created_at"]})
        incidents = []
        for iid in inc_ids:
            i = dict(get_incident(iid))
            i["items"] = q("SELECT item_type, item_id, relation, note, added_by, added_at FROM incident_items "
                           "WHERE incident_id = ?", (iid,))
            incidents.append(i)
        cap_by_sha = {}
        for c in captures:
            cap_by_sha.setdefault(c["sha256"], []).append(c)

        items = []
        for p in posts:
            key = ("post", p["id"])
            pm = [m for m in media if m["post_id"] == p["id"]]
            items.append({
                "type": "post", "id": p["id"], "url": p["url"], "channel": p["channel"],
                "channel_title": p["channel_title"], "channel_tg_id": p["channel_tg_id"], "msg_id": p["msg_id"],
                "posted_utc": p["date"], "text": p["text"], "views": p["views"], "edited_on_telegram": bool(p["edited"]),
                "forwarded_from": p["fwd_username"] or p["fwd_name"], "forwarded_from_url": p["fwd_url"],
                "author_signature": p["author_signature"],
                "first_captured_utc": p["first_captured"], "last_captured_utc": p["last_captured"],
                "capture_count": p["capture_count"], "last_seen_utc": p["last_seen"],
                "deleted_detected_utc": p["deleted_detected"],
                "capture_method": "public web preview (t.me/s), no login",
                "source_page_sha256": p["page_sha256"],
                "media": [{"kind": m["kind"], "sha256": m["sha256"], "bytes": m["bytes"], "mime": m["mime"],
                           "method": m["method"], "captured_utc": m["captured_at"], "source_url": m["source_url"],
                           "file": f"media/{m['sha256']}{os.path.splitext(m['path'])[1]}" if include_media else None}
                          for m in pm],
                "earlier_versions": versions.get(key, []),
                "review_status": reviews.get(key, "unreviewed"), "tags": tags.get(key, []), "notes": notes.get(key, []),
            })
        for c in comments:
            key = ("comment", c["id"])
            cm = [m for m in cmedia if m["comment_id"] == c["id"]]
            raw_cap = (cap_by_sha.get(c["raw_sha256"]) or [{}])[0]
            items.append({
                "type": "comment", "id": c["id"], "channel": c["channel"], "comment_group_id": c["group_id"],
                "msg_id": c["msg_id"], "posted_utc": c["date"], "edited_utc": c["edit_date"], "text": c["text"],
                "sender_type": c["sender_type"], "sender_id": c["sender_id"], "sender_username": c["sender_username"],
                "sender_name": " ".join(x for x in (c["sender_first"], c["sender_last"]) if x) or None,
                "sender_phone": c["sender_phone"], "post_author_signature": c["post_author"],
                "reply_to_msg_id": c["reply_to_msg_id"], "thread_top_id": c["thread_top_id"],
                "forwarded_from": c["fwd_username"] or c["fwd_name"], "forwarded_from_id": c["fwd_id"],
                "first_captured_utc": c["first_captured"], "last_captured_utc": c["last_captured"],
                "last_seen_utc": c["last_seen"], "deleted_detected_utc": c["deleted_detected"],
                "capture_method": "Telegram API, logged-in account", "capture_account": raw_cap.get("account"),
                "source_api_dump_sha256": c["raw_sha256"],
                "media": [{"kind": m["kind"], "sha256": m["sha256"], "bytes": m["bytes"], "mime": m["mime"],
                           "captured_utc": m["captured_at"],
                           "file": f"media/{m['sha256']}{os.path.splitext(m['path'])[1]}" if include_media else None}
                          for m in cm],
                "earlier_versions": versions.get(key, []),
                "review_status": reviews.get(key, "unreviewed"), "tags": tags.get(key, []), "notes": notes.get(key, []),
            })

        missing = []
        log_case = case_id or ((q1("SELECT case_id FROM incidents WHERE id = ?", (scope_id,)) or {}).get("case_id")
                               if scope == "incident" else None)
        searches = searchlog.for_export(ctx, log_case)
        # Claim the name atomically: two exports in the same second must never share a file (the second would
        # overwrite the first, leaving the first export's record pointing at the wrong package and hash).
        for n in range(1, 1000):
            path = os.path.join(exports_dir, base + (f"-{n}" if n > 1 else "") + ".zip")
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644))
                break
            except FileExistsError:
                continue
        else:
            raise HTTPException(500, "Could not find a free export file name.")
        base = os.path.basename(path)[:-len(".zip")]      # the package's inner folder matches its file name
        try:
            write_zip(path, base, files, missing, media, cmedia, page_shas, api_shas, cap_by_sha, include_media, store,
                      items, captures, audit, incidents, scope, scope_id, case_id, b, posts, comments, searches)
        except BaseException:
            if os.path.exists(path + ".part"):     # never leave a half-written package behind
                os.remove(path + ".part")
            if os.path.exists(path) and os.path.getsize(path) == 0:
                os.remove(path)                     # the empty placeholder that reserved the name
            raise
        os.replace(path + ".part", path)
        return finish_package(path, scope, scope_id, items, files, missing)

    def uses_dictionary(items, searches):
        """True if place/dictionary-derived data is in this package: an exported item mentions a place, or a
        recorded search was expanded with the dictionary."""
        if any(s.get("dict_version") and s.get("query") for s in searches):
            return True
        for t in ("post", "comment"):
            ids = [i["id"] for i in items if i["type"] == t]
            for k in range(0, len(ids), 500):
                part = ids[k:k + 500]
                if q1(f"SELECT 1 FROM item_places WHERE item_type = ? AND item_id IN ({','.join('?' * len(part))}) LIMIT 1",
                      (t, *part)):
                    return True
        return False

    def write_zip(path, base, files, missing, media, cmedia, page_shas, api_shas, cap_by_sha, include_media, store,
                  items, captures, audit, incidents, scope, scope_id, case_id, b, posts, comments, searches=()):
        with zipfile.ZipFile(path + ".part", "w", zipfile.ZIP_DEFLATED) as z:
            def put_bytes(arc, data):
                z.writestr(f"{base}/{arc}", data)
                files[arc] = _sha(data)

            def put_file(arc, src, expect_sha, gz_content_sha=None):
                if arc in files:
                    return
                if not os.path.exists(src):
                    missing.append(arc)
                    return
                if gz_content_sha:        # raw captures: the recorded hash is of the content before gzip
                    import gzip
                    hc = hashlib.sha256()
                    with gzip.open(src, "rb") as g:
                        for chunk in iter(lambda: g.read(1 << 20), b""):
                            hc.update(chunk)
                    if hc.hexdigest() != gz_content_sha:
                        raise HTTPException(500, f"Integrity check failed for {arc}: the stored capture no longer "
                                                 f"matches its recorded hash. Run Settings → Verify all files.")
                h = hashlib.sha256()
                with open(src, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 20), b""):
                        h.update(chunk)
                if expect_sha and h.hexdigest() != expect_sha:
                    raise HTTPException(500, f"Integrity check failed for {arc}: the stored file no longer matches "
                                             f"its recorded hash. Run Settings → Verify all files.")
                z.write(src, f"{base}/{arc}", compress_type=zipfile.ZIP_STORED)
                files[arc] = h.hexdigest()

            if include_media:
                for m in media + cmedia:
                    put_file(f"media/{m['sha256']}{os.path.splitext(m['path'])[1]}", os.path.join(store, m["path"]), m["sha256"])
            # machine-read text of the media (OCR / speech records written by mediatext.py): small, and the basis of
            # any "found in OCR/speech" match, so included even without the media files; verified like everything else
            if entities.has_table(ctx.con, "media_text_runs"):
                for sha in sorted({m["sha256"] for m in media + cmedia if m.get("sha256")}):
                    seen = set()
                    for r in q("""SELECT kind, output_path, output_sha256 FROM media_text_runs WHERE sha256 = ?
                                  AND status = 'ok' AND output_path IS NOT NULL ORDER BY id DESC""", (sha,)):
                        if r["kind"] in seen:
                            continue                      # newest run per kind; older outputs were superseded
                        seen.add(r["kind"])
                        put_file(f"derived/{sha}/{os.path.basename(r['output_path'])}",
                                 os.path.join(ctx.workspace, r["output_path"]), r["output_sha256"])
            for sha in page_shas + api_shas:
                cap = (cap_by_sha.get(sha) or [None])[0]
                if cap and cap["path"]:
                    folder = "pages" if cap["kind"] == "page" else "api"
                    # raw pages/dumps are stored gzip-compressed; the recorded hash is of the content as received
                    put_file(f"{folder}/{os.path.basename(cap['path'])}", os.path.join(store, cap["path"]), None, sha)
            put_bytes("items.json", json.dumps(items, ensure_ascii=False, indent=1, default=str).encode())
            buf = io.StringIO()
            cols = ["type", "id", "url", "channel", "msg_id", "posted_utc", "text", "sender_id", "sender_username",
                    "forwarded_from", "first_captured_utc", "last_seen_utc", "deleted_detected_utc", "capture_method",
                    "review_status", "tags", "media_sha256"]
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for it in items:
                w.writerow(db.csv_row({**it, "tags": "; ".join(it["tags"]),
                                       "media_sha256": " ".join(m["sha256"] for m in it["media"])}))
            put_bytes("items.csv", ("﻿" + buf.getvalue()).encode())
            for name, rows in (("capture_log.csv", captures), ("audit.csv", audit)):
                buf = io.StringIO()
                if rows:
                    w = csv.DictWriter(buf, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(db.csv_rows(rows))
                put_bytes(name, ("﻿" + buf.getvalue()).encode())
            buf = io.StringIO()                     # how the items were found (also summarised in README.txt)
            cols = ["id", "time_utc", "last_repeat_utc", "repeats", "by", "endpoint", "query", "fts", "dict_version",
                    "key_version", "total", "result_sha256", "first_id", "saved_search_id", "params"]
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(db.csv_rows(searches))
            put_bytes("search_log.csv", ("\ufeff" + buf.getvalue()).encode())
            if incidents:
                put_bytes("incidents.json", json.dumps(incidents, ensure_ascii=False, indent=1, default=str).encode())
            attribution = places.ATTRIBUTION if uses_dictionary(items, searches) else None
            manifest = {
                "tool": TOOL, "created_utc": db.now(), "created_by": actor(), "scope": scope, "scope_id": scope_id,
                "case": (q1("SELECT id, name FROM cases WHERE id = ?", (case_id,)) if case_id else None),
                "options": {k: v for k, v in b.items() if k in ("statuses", "media", "include_incidents")},
                "counts": {"posts": len(posts), "comments": len(comments), "incidents": len(incidents),
                           "media_files": len([a for a in files if a.startswith("media/")]),
                           "raw_pages": len([a for a in files if a.startswith("pages/")]),
                           "raw_api_dumps": len([a for a in files if a.startswith("api/")]),
                           "search_runs": len(searches)},
                "missing_files": missing,
                "data_attribution": attribution,
                "notes": ["items.csv, capture_log.csv, audit.csv and search_log.csv start any text cell that begins with "
                          "= + - or @ with an apostrophe so spreadsheets cannot run it as a formula; items.json and "
                          "the other files hold the original text.",
                          "All times are UTC.",
                          "media/ files are byte-identical to what was collected; their names are their SHA-256.",
                          "pages/ and api/ hold gzip-compressed raw captures; their names are the SHA-256 of the "
                          "uncompressed content as received (check with: gunzip -c FILE | shasum -a 256).",
                          "deleted_detected_utc: the item was found missing on that check; last_seen_utc is the last "
                          "check it was still there. It was deleted between the two."],
            }
            put_bytes("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1, default=str).encode())
            put_bytes("README.txt", (README + searchlog.readme_section(searches)
                                     + (f"\nData attribution\n  {attribution}\n" if attribution else "")).encode())
            sums = "".join(f"{sha}  {arc}\n" for arc, sha in sorted(files.items()))
            z.writestr(f"{base}/SHA256SUMS", sums)

    def finish_package(path, scope, scope_id, items, files, missing):
        os.chmod(path, 0o444)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        zsha, size = h.hexdigest(), os.path.getsize(path)

        def rec():
            cur = con.execute("INSERT INTO exports(scope, scope_id, path, sha256, bytes, items, files, created, created_by) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (scope, scope_id, path, zsha, size, len(items), len(files) + 1,
                                                             db.now(), actor()))
            db.audit(con, actor(), "evidence_export", f"{scope} {scope_id}" if scope_id else "selection",
                     {"export_id": cur.lastrowid, "path": path, "sha256": zsha, "items": len(items), "missing": missing})
            return cur.lastrowid
        eid = write(rec)
        return {"id": eid, "path": path, "sha256": zsha, "bytes": size, "items": len(items), "files": len(files) + 1,
                "missing_files": missing, "download": f"/api/exports/{eid}/download"}

    @app.post("/api/exports")
    async def export_package(request: Request):
        """{"scope": "case", "id": 3, "statuses": ["relevant", "verify"], "media": true}
           {"scope": "incident", "id": 7}
           {"scope": "selection", "posts": [..], "comments": [..], "case": 3}"""
        b = await request.json()
        scope = b.get("scope")
        if scope not in ("case", "incident", "selection"):
            raise HTTPException(400, "scope must be case, incident or selection")
        sid = int(b["id"]) if scope != "selection" else None
        if scope == "case" and not q1("SELECT 1 FROM cases WHERE id = ?", (sid,)):
            raise HTTPException(404, "No such case")
        if scope == "incident":
            get_incident(sid)
        return build_package(scope, sid, b)

    @app.get("/api/exports")
    def list_exports():
        rows = q("SELECT * FROM exports ORDER BY id DESC")
        for r in rows:
            r["exists"] = os.path.exists(r["path"])
        return rows

    @app.get("/api/exports/{eid}/download")
    def download_export(eid: int):
        r = q1("SELECT * FROM exports WHERE id = ?", (eid,))
        if not r or not os.path.exists(r["path"]):
            raise HTTPException(404)
        write(lambda: db.audit(con, actor(), "evidence_export_download", f"export {eid}"))
        return FileResponse(r["path"], media_type="application/zip", filename=os.path.basename(r["path"]))


README = """Evidence package — Telegram Investigator
=========================================

This folder was exported from a local Telegram Investigator workspace.

Files
  items.json        Every exported post and comment with its collection record (what, when,
                    how, by which account for logged-in collection) and investigator review.
  items.csv         The same, flattened for spreadsheets.
  media/            Collected photos/videos. Each file is named by its SHA-256 hash and is
                    byte-identical to the file as downloaded.
  pages/            Raw public preview pages the posts were read from (gzip). The name is the
                    SHA-256 of the page as received:  gunzip -c pages/X.html.gz | shasum -a 256
  api/              Raw Telegram API responses comments were read from (gzip, same naming).
  capture_log.csv   The capture record of every file: URL, UTC time, method, tool, account, job.
  audit.csv         Every investigator action on these items (reviews, tags, notes, incidents).
  search_log.csv    The searches run in this case: who, when, the query, the exact search expression that ran
                    (fts), the dictionary version, result count and a SHA-256 over the ordered result ids.
  derived/          Machine-read text of the media, one folder per media SHA-256: ocr.json (text read from
                    images and video frames, with frame times) and whisper.json (speech transcript, with times).
                    Produced on this laptop by Apple Vision and Whisper; both make mistakes, and Whisper can
                    invent text over music or silence. Check any quote against the media itself.
  incidents.json    Incident details (date, place, how established) and linked items, if any.
  manifest.json     Scope, counts, who exported it and when.
  SHA256SUMS        SHA-256 of every file here.

Verify the package
  cd into this folder and run:   shasum -a 256 -c SHA256SUMS
  Every line should end in "OK".

Deletions
  deleted_detected_utc means the item was captured earlier and found missing on that check;
  last_seen_utc is the last check where it still existed. It was deleted in between. The
  copy here is the one captured before deletion.
"""
