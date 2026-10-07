"""
Import a tgspider / tgmedia output folder into the workspace database.

- Media and raw pages are copied (never moved) into a content-addressed store
  (<store>/<sha[:2]>/<sha>.<ext>), hashed with SHA-256, and logged in `captures`.
- Re-importing is safe: nothing is duplicated, and review status, tags and notes
  are never touched. Changed post text is kept in `post_versions`.
"""

import hashlib
import json
import mimetypes
import os
import re
import shutil

from . import comments, db, discovery

FILE_RE = re.compile(r"(\d+)_(\d+)_(photo|thumb|video|full)(\.[A-Za-z0-9]+)?$")


def _jsonl(path, torn=None):
    """Records of a JSONL file. A line that doesn't parse is a write cut off by a crash or power loss:
    it is skipped (the crawler skips it too) and counted in `torn` (a one-item list) when given."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                if torn is not None:
                    torn[0] += 1
    return out


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_file(src, store_dir, ext=None):
    """Copy a file into the content-addressed store. Returns (sha, rel_path, bytes)."""
    sha = sha256_file(src)
    ext = ext if ext is not None else os.path.splitext(src)[1].lower()
    rel = os.path.join(sha[:2], sha + ext)
    dest = os.path.join(store_dir, rel)
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(src, dest + ".part")
        os.replace(dest + ".part", dest)
        os.chmod(dest, 0o444)                 # evidence copies are read-only
    return sha, rel, os.path.getsize(dest)


def _j(v):
    return json.dumps(v, ensure_ascii=False) if v is not None else None


def ingest_run(con, run_dir, store_dir, actor="system", job_id=None, log=print, only_media=False,
               network_only=False):
    """only_media: import just the logged-in downloads (media jobs write a stub
    posts.jsonl for tgmedia that must not overwrite real posts).
    network_only: import channels and the links between them, but no post
    content, media or page copies."""
    run_dir = os.path.abspath(run_dir)
    if comments.is_comments_run(run_dir):
        return comments.ingest_comments(con, run_dir, store_dir, actor=actor, job_id=job_id, log=log)
    if os.path.exists(os.path.join(run_dir, "mediatext_run.json")):
        from . import mediatext
        return mediatext.ingest_run(con, run_dir, store_dir, actor=actor, job_id=job_id, log=log)
    if os.path.exists(os.path.join(run_dir, "scan_list.json")):
        from . import analysis
        return analysis.ingest_mediascan(con, run_dir, actor=actor, job_id=job_id, log=log)
    if discovery.is_discover_run(run_dir):
        return discovery.ingest_discover(con, run_dir, store_dir, actor=actor, job_id=job_id, log=log)
    stats = dict(channels_new=0, channels_updated=0, posts_new=0, posts_updated=0,
                 posts_changed=0, posts_deleted=0, posts_reappeared=0, channel_events=0,
                 media_new=0, pages_new=0, edges_new=0, renames=0)
    torn = [0]                  # unreadable lines skipped in this run's files

    # ---- channels ---------------------------------------------------------
    nodes = {}
    for rec in [] if only_media else _jsonl(os.path.join(run_dir, "nodes.jsonl"), torn):
        nodes.setdefault(rec["username"], {}).update(rec)
    failed = warned = 0
    for name, n in nodes.items():
        row = con.execute("SELECT * FROM channels WHERE username = ?", (name,)).fetchone()
        if n.get("status") == "error":
            # A visit that failed says nothing about the channel. It must not rewrite what is known
            # (type "error" and preview 0 would later read as a preview being disabled, then restored).
            failed += 1
            if row is None:
                con.execute("INSERT INTO channels(username, status, first_seen) VALUES (?,?,?)",
                            (name, "seed" if n.get("depth") == 0 else "candidate", n.get("checked_at") or db.now()))
            elif n.get("depth") == 0:
                con.execute("UPDATE channels SET status = 'seed' WHERE username = ? AND status = 'candidate'", (name,))
            _audit_once(con, actor, "channel_crawl_failed", name,
                        {"error": n.get("error"), "pages_read": n.get("pages_read"),
                         "posts_saved": n.get("posts_saved"), "run": os.path.basename(run_dir)})
            continue
        if n.get("parse_warnings"):
            warned += len(n["parse_warnings"])
            _audit_once(con, actor, "crawl_parser_warning", name,
                        {"warnings": [{k: w.get(k) for k in ("code", "message", "page", "page_sha256")}
                                      for w in n["parse_warnings"]], "run": os.path.basename(run_dir)})
        tg_id = n.get("tg_id")
        other = None
        if tg_id:
            other = con.execute("SELECT username FROM channels WHERE tg_id = ? AND username != ?",
                                (tg_id, name)).fetchone()
            if other:
                stats["renames"] += 1
                db.audit(con, actor, "channel_rename_detected", name,
                         {"tg_id": tg_id, "previous_username": other["username"]})
        crawled_at = n.get("checked_at") if n.get("status") == "crawled" else None
        if row is not None and not only_media:
            stats["channel_events"] += _channel_events(con, row, n, other["username"] if tg_id and other else None,
                                                      job_id)
        if row is None:
            con.execute("""INSERT INTO channels(username, tg_id, title, type, subscribers, description,
                               preview, status, first_seen, last_crawled, posts_scanned)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (name, tg_id, n.get("title"), n.get("type"), n.get("subscribers"),
                         n.get("description"), int(bool(n.get("preview"))),
                         "seed" if n.get("depth") == 0 else "candidate",
                         n.get("checked_at") or db.now(), crawled_at, n.get("posts_scanned") or 0))
            stats["channels_new"] += 1
        else:
            con.execute("""UPDATE channels SET
                    tg_id = COALESCE(?, tg_id), title = COALESCE(?, title),
                    type = COALESCE(?, type), subscribers = COALESCE(?, subscribers),
                    description = COALESCE(?, description), preview = COALESCE(?, preview),
                    last_crawled = MAX(COALESCE(last_crawled, ''), COALESCE(?, '')),
                    posts_scanned = MAX(posts_scanned, ?),
                    status = CASE WHEN ? = 0 AND status = 'candidate' THEN 'seed' ELSE status END
                    WHERE username = ?""",
                        (tg_id, n.get("title"), n.get("type"), n.get("subscribers"),
                         n.get("description"), int(bool(n.get("preview"))), crawled_at,
                         n.get("posts_scanned") or 0, n.get("depth"), name))
            stats["channels_updated"] += 1

    # ---- posts -------------------------------------------------------------
    post_ids = {}
    media_failed = 0
    for p in [] if (only_media or network_only) else _jsonl(os.path.join(run_dir, "posts.jsonl"), torn):
        if "scraped_at" not in p:
            continue            # placeholder rows written for tgmedia by a download job
        key = (p["channel"], p["id"])
        at = p.get("scraped_at") or db.now()
        row = con.execute("SELECT id, text, views, edited, media_types, last_captured, deleted_detected FROM posts "
                          "WHERE channel = ? AND msg_id = ?", key).fetchone()
        fields = dict(
            url=p.get("url"), date=p.get("date"), edited=int(bool(p.get("edited"))),
            views=p.get("views"), text=p.get("text") or "",
            fwd_username=p.get("forwarded_from_username"), fwd_name=p.get("forwarded_from_name"),
            fwd_url=p.get("forwarded_from_url"), reply_to_url=p.get("reply_to_url"),
            author_signature=p.get("author_signature"), hashtags=_j(p.get("hashtags") or []),
            links=_j(p.get("links") or []), link_preview_url=p.get("link_preview_url"),
            media_types=_j(p.get("media_types") or []), sensitive=int(bool(p.get("sensitive"))),
            video_durations=_j(p.get("video_durations") or []),
            media_unavailable=_j(p.get("media_unavailable") or []),
            reactions=_j(p.get("reactions") or {}),
            reactions_total=sum(v or 0 for v in (p.get("reactions") or {}).values()),
            page_sha256=p.get("page_sha256"),
        )
        if row is None:
            cols = ["channel", "msg_id", *fields, "first_captured", "last_captured", "last_seen"]
            cur = con.execute(f"INSERT INTO posts({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              (p["channel"], p["id"], *fields.values(), at, at, at))
            pid = cur.lastrowid
            stats["posts_new"] += 1
            db.index_post(con, pid, fields["text"], p["channel"], fields["fwd_username"],
                          fields["fwd_name"], p.get("hashtags"))
        else:
            pid = row["id"]
            newer = at > (row["last_captured"] or "")
            con.execute("UPDATE posts SET last_seen = MAX(COALESCE(last_seen, ''), ?) WHERE id = ?", (at, pid))
            if row["deleted_detected"] and at > row["deleted_detected"]:
                # it was missing on an earlier check but is back: say so rather than hide it
                con.execute("UPDATE posts SET deleted_detected = NULL WHERE id = ?", (pid,))
                db.audit(con, actor, "post_reappeared", f"post {pid}",
                         {"was_marked_deleted": row["deleted_detected"], "seen_again": at})
                stats["posts_reappeared"] += 1
            if newer:
                if (row["text"] or "") != fields["text"] or (row["media_types"] or "[]") != fields["media_types"]:
                    vcur = con.execute("INSERT INTO post_versions(post_id, captured_at, text, views, edited, media_types) "
                                       "VALUES (?,?,?,?,?,?)",
                                       (pid, row["last_captured"], row["text"], row["views"], row["edited"],
                                        row["media_types"]))
                    db.index_version(con, vcur.lastrowid, row["text"])      # edited-away text stays searchable
                    stats["posts_changed"] += 1
                    db.index_post(con, pid, fields["text"], p["channel"], fields["fwd_username"],
                                  fields["fwd_name"], p.get("hashtags"))
                # a page whose date or view count could not be read must not blank an earlier good capture
                sets = ", ".join(f"{k} = COALESCE(?, {k})" if k in ("date", "views") else f"{k} = ?" for k in fields)
                con.execute(f"UPDATE posts SET {sets}, last_captured = ?, "
                            f"capture_count = capture_count + 1 WHERE id = ?",
                            (*fields.values(), at, pid))
                stats["posts_updated"] += 1
        post_ids[key] = pid
        for mf in p.get("media_failed") or []:
            media_failed += 1
            _audit_once(con, actor, "media_download_failed", f"post {pid}",
                        {"kind": mf.get("kind"), "index": mf.get("index"), "url": mf.get("url"),
                         "error": mf.get("error"), "run": os.path.basename(run_dir)})

        # web-preview media downloaded during the crawl
        urls = (p.get("photos") or []) + (p.get("video_thumbs") or []) + (p.get("videos") or [])
        for rel in p.get("media_files") or []:
            stats["media_new"] += _add_media(con, run_dir, store_dir, pid, rel, "web", at,
                                             urls, job_id)

    # ---- deletions: captured earlier, inside the range just read, and now gone ---
    if not (only_media or network_only):
        seen = {}
        for (ch, mid) in post_ids:
            seen.setdefault(ch, set()).add(mid)
        for name, n in nodes.items():
            cov = n.get("covered")
            if not cov or n.get("status") != "crawled" or n.get("parse_warnings"):
                continue            # older runs don't record what they covered, and a page that did not read
                                    # as expected proves nothing about what is missing: never guess
            d, r = _detect_deletions(con, name, cov, seen.get(name, set()), n.get("checked_at") or db.now(),
                                     actor, job_id)
            stats["posts_deleted"] += d

    # ---- logged-in downloads (tgmedia) ------------------------------------
    for r in [] if network_only else _jsonl(os.path.join(run_dir, "media_full.jsonl"), torn):
        pid = post_ids.get((r["channel"], r["id"])) or _post_id(con, r["channel"], r["id"])
        if not pid:
            continue
        for rel in r.get("files") or []:
            stats["media_new"] += _add_media(con, run_dir, store_dir, pid, rel, "api",
                                             r.get("downloaded_at"), [], job_id,
                                             account=r.get("account"))

    # ---- raw pages ---------------------------------------------------------
    for pg in [] if network_only else _jsonl(os.path.join(run_dir, "pages.jsonl"), torn):
        src = os.path.join(run_dir, pg["path"])
        if not os.path.exists(src):
            continue
        dest_rel = os.path.join("pages", pg["sha256"][:2], pg["sha256"] + ".html.gz")
        dest = os.path.join(store_dir, dest_rel)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(src, dest)
            os.chmod(dest, 0o444)
        cur = con.execute("""INSERT OR IGNORE INTO captures(kind, url, sha256, path, bytes, captured_at,
                                 method, tool, job_id, run_dir, note)
                             VALUES ('page',?,?,?,?,?,?,?,?,?,?)""",
                          (pg["url"], pg["sha256"], dest_rel, pg.get("bytes"), pg["fetched_at"],
                           "web", pg.get("tool"), job_id, run_dir,
                           "sha256 of the raw HTML as received; stored gzip-compressed"))
        stats["pages_new"] += cur.rowcount

    # ---- edges -------------------------------------------------------------
    sources = set()
    for e in _jsonl(os.path.join(run_dir, "edges.jsonl"), torn):
        cur = con.execute("INSERT OR IGNORE INTO edge_hits(source, target, type, msg_id, date) "
                          "VALUES (?,?,?,?,?)",
                          (e["source"], e["target"], e["type"],
                           e.get("post_id") if e.get("post_id") is not None else -1, e.get("date")))
        stats["edges_new"] += cur.rowcount
        sources.add(e["source"])
        if not con.execute("SELECT 1 FROM channels WHERE username = ?", (e["target"],)).fetchone():
            con.execute("INSERT INTO channels(username, status, first_seen) VALUES (?, 'candidate', ?)",
                        (e["target"], e.get("date") or db.now()))
    if sources:
        db.rebuild_edges(con, sorted(sources))

    if network_only:
        stats["network_only"] = True
    # only when there is something to report, so a clean import keeps the same stats as before
    for key, val in (("channels_failed", failed), ("parse_warnings", warned), ("media_failed", media_failed),
                     ("torn_lines", torn[0])):
        if val:
            stats[key] = val
    if any(v for k, v in stats.items() if k != "channels_updated"):
        con.execute("""INSERT OR IGNORE INTO captures(kind, captured_at, method, tool, job_id, run_dir, note)
                       VALUES ('import', ?, 'import', 'investigator', ?, ?, ?)""",
                    (db.now(), job_id, run_dir, json.dumps(stats)))
        db.audit(con, actor, "import_run", run_dir, stats)
    con.commit()
    log(f"Imported {run_dir}: {stats}")
    return stats


def _audit_once(con, actor, action, target, detail):
    """Audit an event once: importing the same run again must not repeat it."""
    dj = json.dumps(detail, ensure_ascii=False)
    if not con.execute("SELECT 1 FROM audit WHERE action = ? AND target = ? AND detail = ?",
                       (action, target, dj)).fetchone():
        db.audit(con, actor, action, target, detail)


def _detect_deletions(con, channel, cov, seen_ids, at, actor, job_id):
    """Mark posts deleted when they were captured before `at`, their id lies inside the range
    of message ids this crawl read, and they weren't on those pages. The lowest id read is
    the boundary: anything at or below it wasn't checked. The first page is always the
    channel's newest, so ids above the highest one read were checked too."""
    lo, hi = int(cov["min_id"]), int(cov["max_id"])
    rows = con.execute("SELECT id, msg_id FROM posts WHERE channel = ? AND msg_id > ? AND (msg_id <= ? OR ?) "
                       "AND deleted_detected IS NULL AND first_captured < ?",
                       (channel, lo, hi, 1 if cov.get("to_latest") else 0, at)).fetchall()
    gone = [r for r in rows if r["msg_id"] not in seen_ids]
    for r in gone:
        con.execute("UPDATE posts SET deleted_detected = ? WHERE id = ?", (at, r["id"]))
        db.audit(con, actor, "post_deleted_detected", f"post {r['id']}",
                 {"channel": channel, "msg_id": r["msg_id"], "checked_range": [lo, hi], "job": job_id})
    return len(gone), len(rows)


EVENT_FIELDS = (("title", "title_changed"), ("description", "description_changed"))


def _channel_events(con, row, n, previous_username, job_id):
    """Record what changed about a channel since the last crawl."""
    at = n.get("checked_at") or db.now()
    events = []
    if previous_username:
        events.append(("renamed", previous_username, row["username"]))
    for field, kind in EVENT_FIELDS:
        old, new = row[field], n.get(field)
        if old and new and old.strip() != new.strip():
            events.append((kind, old, new))
    if row["type"] == "channel":
        if row["preview"] and n.get("preview") is False and n.get("type") == "channel":
            events.append(("preview_disabled", "public preview", "no preview"))
        elif n.get("type") == "unknown":
            events.append(("unavailable", "channel", "not found / private"))
    if row["preview"] == 0 and n.get("preview") is True and row["last_crawled"]:
        events.append(("preview_restored", "no preview", "public preview"))
    old_s, new_s = row["subscribers"], n.get("subscribers")
    if old_s and new_s and abs(new_s - old_s) >= max(500, 0.25 * old_s):
        events.append(("subscribers_jump", str(old_s), str(new_s)))
    for kind, old, new in events:
        con.execute("INSERT INTO channel_events(channel, kind, detected_at, old, new, job_id) VALUES (?,?,?,?,?,?)",
                    (row["username"], kind, at, old, new, job_id))
    return len(events)


def _post_id(con, channel, msg_id):
    r = con.execute("SELECT id FROM posts WHERE channel = ? AND msg_id = ?", (channel, msg_id)).fetchone()
    return r["id"] if r else None


def _add_media(con, run_dir, store_dir, post_id, rel, method, captured_at, urls, job_id, account=None):
    src = os.path.join(run_dir, rel)
    m = FILE_RE.search(os.path.basename(rel))
    if not os.path.exists(src) or not m:
        return 0
    idx, kind = int(m.group(2)), m.group(3)
    sha, store_rel, size = store_file(src, store_dir)
    mime = mimetypes.guess_type(store_rel)[0]
    source_url = urls[idx] if idx < len(urls) else None
    cur = con.execute("""INSERT OR IGNORE INTO media(post_id, kind, idx, sha256, path, bytes, mime,
                             captured_at, method, source_url)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""",
                      (post_id, kind, idx, sha, store_rel, size, mime, captured_at, method, source_url))
    if cur.rowcount:
        con.execute("""INSERT OR IGNORE INTO captures(kind, url, sha256, path, bytes, captured_at, method,
                           tool, account, job_id, run_dir, note)
                       VALUES ('media',?,?,?,?,?,?,?,?,?,?,?)""",
                    (source_url, sha, store_rel, size, captured_at, method,
                     "tgmedia" if method == "api" else "tgspider", account, job_id, run_dir,
                     f"post {post_id}, {kind} #{idx}"))
    return cur.rowcount
