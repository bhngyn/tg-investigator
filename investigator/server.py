"""
Local web server for the investigator UI. Binds to 127.0.0.1 only.

Security for a localhost app:
  - Host header must be localhost/127.0.0.1 (blocks DNS-rebinding attacks)
  - every state-changing request needs the per-launch token embedded in the page
    (blocks other websites open in the same browser from driving the API)
  - /media only serves files inside the store
"""

import csv
import hashlib
import io
import json
import mimetypes
import logging
import os
import secrets
import threading
from types import SimpleNamespace
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import (alerts, analysis, cases, comments, community, db, discovery, entities, incidents, ingest, jobs, lexicon,
               mediatext, places, savedsearch, searchlog, terms, watch)

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
WEB = os.path.join(HERE, "web")          # redesigned UI (built from ui/ by `npm run build`)
TOKEN = secrets.token_urlsafe(24)

DEFAULT_SETTINGS = {
    "investigator": "investigator",
    "blur": "all",            # all / sensitive / none
    "grayscale": "0",
    "autoplay_muted": "1",
    "page_size": "25",
    "collect_phones": "1",    # record commenters' phone numbers where Telegram shows them
    "recheck_days": "7",      # comment polls re-check messages this recent for deletions/edits
    "auto_mediascan": "1",    # hash + read metadata of new media files right after each job
}


log = logging.getLogger("investigator")

def create_app(workspace, port):
    workspace = os.path.abspath(workspace)
    store = os.path.join(workspace, "store")
    con = db.connect(os.path.join(workspace, "investigator.db"))
    comments.backfill_mirrors(con, store)     # one-off repair of rows imported before the collector fix
    lock = con.lock           # the connection's own lock: q()/write() and direct con.execute() share it
    runner = jobs.JobRunner(con, lock, workspace)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.exception_handler(ValueError)
    async def bad_value(request: Request, exc: ValueError):
        # int("abc"), an unreadable near=/entity=/in=, a malformed JSON body: the caller's mistake. One log line
        # (no traceback: bad input is routine), so a ValueError from a genuine bug is still visible in the log.
        log.warning("400 for %s %s: %s", request.method, request.url.path, exc)
        msg = str(exc)
        if msg.startswith("invalid literal for int()"):          # Python's wording is no help to a user
            msg = f"expected a whole number, got {msg.split(':', 1)[-1].strip()}"
        elif msg.startswith("could not convert string to float"):
            msg = f"expected a number, got {msg.split(':', 1)[-1].strip()}"
        return JSONResponse({"detail": f"Invalid parameter: {msg}"}, status_code=400)

    @app.exception_handler(KeyError)
    @app.exception_handler(AttributeError)
    @app.exception_handler(TypeError)
    async def bad_body(request: Request, exc: Exception):
        # A POST body that isn't a JSON object ("[]", "3") or lacks a required field makes handlers fail on
        # b.get()/b["id"]. For writes that's a bad request; for anything else it's a server bug: 500 as before.
        log.error("%s on %s %s", type(exc).__name__, request.method, request.url.path, exc_info=exc)
        if request.method == "POST":
            what = f"missing field {exc}" if isinstance(exc, KeyError) else "the JSON body must be an object with the documented fields"
            return JSONResponse({"detail": f"Invalid request: {what}"}, status_code=400)
        return JSONResponse({"detail": "Internal error (see the server log)"}, status_code=500)
    if os.path.isdir(os.path.join(WEB, "assets")):
        app.mount("/app", StaticFiles(directory=WEB), name="web")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in allowed_hosts:
            return JSONResponse({"error": "bad host"}, status_code=403)
        if request.method not in ("GET", "HEAD") and request.headers.get("x-token") != TOKEN:
            return JSONResponse({"error": "missing or bad token"}, status_code=403)
        for k in ("limit", "offset"):     # one check for every endpoint: SQLite reads LIMIT -1 as "no limit"
            v = request.query_params.get(k)
            if v is not None and v.strip().startswith("-"):
                return JSONResponse({"detail": f"Invalid parameter: {k} must be 0 or more"}, status_code=400)
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'")
        return resp

    # ---- helpers --------------------------------------------------------------

    def q(sql, args=()):
        with lock:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def q1(sql, args=()):
        with lock:
            r = con.execute(sql, args).fetchone()
            return dict(r) if r else None

    def setting(key):
        r = q1("SELECT value FROM settings WHERE key = ?", (key,))
        return r["value"] if r else DEFAULT_SETTINGS.get(key)

    def actor():
        return setting("investigator") or "investigator"

    def write(fn):
        with lock:
            try:
                out = fn()
                con.commit()
                return out
            except Exception:
                con.rollback()
                raise

    # Phase 2 features live in their own modules and get what they need through ctx
    ctx = SimpleNamespace(con=con, lock=lock, q=None, q1=None, write=write, actor=actor, setting=setting,
                          runner=runner, store=store, workspace=workspace)
    ctx.q, ctx.q1 = q, q1
    ctx.expand = lambda query, enabled=True, drop=(), skip=(): lexicon.expand(ctx, query, enabled, drop, skip)

    def query_expansion(params):
        """Dictionary expansion of params["q"] (expand=0, drop=, skip= honoured), computed once per request."""
        if "_exp" not in params:
            params["_exp"] = ctx.expand(params.get("q"), *lexicon.from_params(params))
        return params["_exp"]

    def jl(v, default):
        try:
            return json.loads(v) if v else default
        except (TypeError, ValueError):
            return default

    def card(p, media_rows, tag_rows, note_counts):
        return {
            "id": p["id"], "channel": p["channel"], "channel_title": p.get("channel_title"),
            "msg_id": p["msg_id"], "url": p["url"], "date": p["date"], "views": p["views"],
            "edited": bool(p["edited"]), "text": p["text"],
            "fwd_username": p["fwd_username"], "fwd_name": p["fwd_name"], "fwd_url": p["fwd_url"],
            "reply_to_url": p["reply_to_url"],
            "media_types": jl(p["media_types"], []), "sensitive": bool(p["sensitive"]),
            "video_durations": jl(p["video_durations"], []),
            "media_unavailable": jl(p["media_unavailable"], []),
            "reactions_total": p["reactions_total"], "review_status": p["review_status"],
            "capture_count": p["capture_count"], "first_captured": p["first_captured"],
            "media": media_rows.get(p["id"], []), "tags": tag_rows.get(p["id"], []),
            "notes": note_counts.get(p["id"], 0),
            "last_seen": p.get("last_seen"), "deleted_detected": p.get("deleted_detected"),
        }

    def cards(rows):
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        media_rows, tag_rows, note_counts = {}, {}, {}
        for m in q(f"SELECT id, post_id, kind, idx, path, mime, bytes FROM media "
                   f"WHERE post_id IN ({marks}) ORDER BY post_id, idx, kind", ids):
            media_rows.setdefault(m["post_id"], []).append(m)
        for t in q(f"SELECT post_id, tag_id FROM post_tags WHERE post_id IN ({marks})", ids):
            tag_rows.setdefault(t["post_id"], []).append(t["tag_id"])
        for n in q(f"SELECT post_id, COUNT(*) c FROM notes WHERE post_id IN ({marks}) GROUP BY post_id", ids):
            note_counts[n["post_id"]] = n["c"]
        return [card(r, media_rows, tag_rows, note_counts) for r in rows]

    def ymd(params, key):
        """A YYYY-MM-DD date parameter (a real calendar date), or None; anything else is a 400, never ignored."""
        v = (params.get(key) or "").strip()
        if not v:
            return None
        try:
            if len(v) != 10:
                raise ValueError
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            raise ValueError(f"{key} must be a date written YYYY-MM-DD (e.g. 2026-03-31); got {v!r}") from None

    SOURCES = ("text", "versions", "ocr", "speech", "names")   # names: opt-in, never in the default set

    def post_sources(params):
        """(available, used) text sources for post search: `in=text,versions,ocr,speech` (default: all available)."""
        avail = ["text"]
        if hasattr(db, "versions_match_sql") and entities.has_table(con, "post_versions_fts"):
            avail.append("versions")
        if hasattr(db, "media_text_match_sql") and entities.has_table(con, "media_text_fts"):
            avail += ["ocr", "speech"]
        avail.append("names")                       # channel & forward names (posts_fts.meta): only when asked for
        asked = [x for x in (params.get("in") or "").replace(" ", "").split(",") if x]
        unknown = [x for x in asked if x not in SOURCES]
        if unknown:
            raise ValueError(f"in= accepts {', '.join(SOURCES)}; got {', '.join(unknown)}")
        return avail, ([x for x in avail if x in asked] if asked else [x for x in avail if x != "names"])

    def active_sources(used):
        """`used` minus sources with nothing indexed (no point running their subqueries): with no earlier versions and
        no OCR/speech text, a plain search is exactly the classic text-only query."""
        tables = {"versions": "post_versions_fts", "ocr": "media_text_fts", "speech": "media_text_fts"}
        return [x for x in used if x in ("text", "names") or con.execute(f"SELECT 1 FROM {tables[x]} LIMIT 1").fetchone()]

    def search_sql(params, ranked=True):
        """Build WHERE clause from query params (shared by search, review, export, saved searches, the search log).
        Returns (join, where, args, rank); `args` covers join and where in text order. ranked=False leaves out the
        bm25 join that only ordering needs (counts, facets)."""
        where, args, join, join_args, rank = [], [], "", [], None
        expr = query_expansion(params)["fts"]
        avail, used = params["_src"] = post_sources(params)
        used = active_sources(used)
        if params.get("q") and not expr:
            where.append("0")
        if expr:
            extras = [x for x in used if x not in ("text", "names")]
            has_fts = "text" in used or "names" in used
            fx = db.fts_scoped(expr, "text" in used, "names" in used)   # never the meta column unless in=names
            if not used:
                where.append("0")
            elif not extras:                       # plain text search: exactly the classic query
                join = "JOIN posts_fts f ON f.rowid = p.id"
                where.append("posts_fts MATCH ?")
                args.append(fx)
                rank = "bm25(posts_fts)"
            else:                                  # text OR earlier versions OR OCR / speech text, same expression
                parts, pa = [], []
                if has_fts:
                    parts.append("SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?")
                    pa.append(fx)
                if "versions" in used:
                    parts.append(db.versions_match_sql())
                    pa.append(expr)
                kinds = [k for k in ("ocr", "speech") if k in used]
                if kinds:
                    parts.append(f"SELECT post_id FROM ({db.media_text_match_sql()}) "
                                 f"WHERE kind IN ({','.join('?' * len(kinds))})")
                    pa += [expr, *kinds]
                where.append("p.id IN (" + " UNION ".join(parts) + ")")
                args += pa
                if has_fts and ranked:             # bm25 for text matches; the other sources rank after them
                    join = ("LEFT JOIN (SELECT rowid rid, bm25(posts_fts) rk FROM posts_fts WHERE posts_fts MATCH ?) f "
                            "ON f.rid = p.id")
                    join_args = [fx]
                    rank = "f.rk"
        if params.get("ids"):                        # ids=1,2,3: exactly these posts
            id_list = [int(x) for x in params["ids"].split(",") if x.strip().isdigit()]
            if len(id_list) > 500:
                raise ValueError(f"ids= takes at most 500 ids per request; got {len(id_list)}")
            where.append(f"p.id IN ({','.join('?' * len(id_list))})" if id_list else "0")
            args += id_list
        chans = [c for c in (params.get("channels") or "").split(",") if c]
        if chans:
            where.append(f"p.channel IN ({','.join('?' * len(chans))})")
            args += chans
        case_id = int(params["case"]) if (params.get("case") or "").isdigit() else None
        if case_id:
            where.append("p.channel IN (SELECT channel FROM case_channels WHERE case_id = ?)")
            args.append(case_id)
        if params.get("since"):                      # collected since (e.g. the case's last visit)
            where.append("p.first_captured >= ?")
            args.append(params["since"])
        if params.get("deleted") == "1":
            where.append("p.deleted_detected IS NOT NULL")
        if ymd(params, "date_from"):
            where.append("p.date >= ?")
            args.append(ymd(params, "date_from"))
        if ymd(params, "date_to"):
            where.append("p.date < ?")
            args.append(ymd(params, "date_to") + "T99")   # inclusive of the whole day
        if params.get("fwd"):
            where.append("p.fwd_username = ?")
            args.append(params["fwd"].lstrip("@").lower())
        if params.get("forwarded") == "1":
            where.append("p.fwd_username IS NOT NULL")
        if params.get("forwarded") == "0":
            where.append("p.fwd_username IS NULL")
        for mt in [m for m in (params.get("media") or "").split(",") if m]:
            if mt == "downloaded":
                where.append("EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id)")
            elif mt == "refused":
                where.append("p.media_unavailable NOT IN ('[]','') AND NOT EXISTS "
                             "(SELECT 1 FROM media m WHERE m.post_id = p.id AND m.kind = 'full')")
            elif mt == "none":
                where.append("p.media_types = '[]'")
            else:
                where.append("p.media_types LIKE ?")
                args.append(f'%"{mt}"%')
        if params.get("review"):
            st = params["review"].split(",")
            if case_id:     # inside a case, triage is the case's own
                where.append(f"""COALESCE((SELECT r.status FROM case_reviews r WHERE r.case_id = ? AND
                                 r.item_type = 'post' AND r.item_id = p.id), 'unreviewed') IN ({','.join('?' * len(st))})""")
                args += [case_id, *st]
            else:
                where.append(f"p.review_status IN ({','.join('?' * len(st))})")
                args += st
        if params.get("tag"):
            tags = [int(t) for t in params["tag"].split(",") if t.isdigit()]
            for t in tags:
                if case_id:
                    where.append("EXISTS (SELECT 1 FROM case_tags ct WHERE ct.case_id = ? AND ct.item_type = 'post' "
                                 "AND ct.item_id = p.id AND ct.tag_id = ?)")
                    args += [case_id, t]
                else:
                    where.append("EXISTS (SELECT 1 FROM post_tags pt WHERE pt.post_id = p.id AND pt.tag_id = ?)")
                    args.append(t)
        if params.get("untagged") == "1":
            if case_id:
                where.append("NOT EXISTS (SELECT 1 FROM case_tags ct WHERE ct.case_id = ? AND ct.item_type = 'post' "
                             "AND ct.item_id = p.id)")
                args.append(case_id)
            else:
                where.append("NOT EXISTS (SELECT 1 FROM post_tags pt WHERE pt.post_id = p.id)")
        if params.get("sensitive") == "1":
            where.append("p.sensitive = 1")
        if params.get("min_views") not in (None, ""):
            try:
                mv = int(str(params["min_views"]).strip())
                if mv < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(f"min_views must be a whole number of 0 or more; got {params['min_views']!r}") from None
            where.append("p.views >= ?")
            args.append(mv)
        if params.get("has_notes") == "1":
            where.append("EXISTS (SELECT 1 FROM notes n WHERE n.post_id = p.id)")
        if params.get("edited") == "1":
            where.append("(p.edited = 1 OR EXISTS (SELECT 1 FROM post_versions v WHERE v.post_id = p.id))")
        for mod in (entities, places):             # entity=kind:value (ANDed), place= (ORed), near=lat,lon,km
            cl, ca = mod.filter_sql(con, params, "post", "p.id")
            where += cl
            args += ca
        return join, (" AND ".join(where) or "1"), [*join_args, *args], rank

    def order_by(params, rank):
        sort = params.get("sort") or ("relevance" if rank else "newest")
        return {
            "relevance": (f"{'f.rk IS NULL, ' if rank == 'f.rk' else ''}{rank}, p.date DESC") if rank else "p.date DESC",
            "newest": "p.date DESC",
            "oldest": "p.date ASC",
            "views": "p.views DESC",
            "reactions": "p.reactions_total DESC",
        }.get(sort, "p.date DESC")

    # ---- one search path: results, ids, sub-queries, hits, logging ------------------------------

    def qparams(request):
        """Query parameters as a dict; repeated entity= / place= are joined (they are ANDed / ORed later)."""
        p = dict(request.query_params)
        for k in ("entity", "place"):
            vals = request.query_params.getlist(k)
            if len(vals) > 1:
                p[k] = "\n".join(vals)
        return p

    def export_headers(name, params):
        """Download headers; carries the data attribution when place/dictionary-derived data shaped the export."""
        h = {"Content-Disposition": f'attachment; filename="{name}"'}
        exp = params.get("_exp") or {}
        if params.get("place") or params.get("near") or (exp.get("enabled") and params.get("q")):
            h["X-Data-Attribution"] = places.ATTRIBUTION.replace("(c)", "\u00a9")
        return h

    def public(params):
        return {k: v for k, v in params.items() if not k.startswith("_")}

    ID_CAP = searchlog.ID_CAP

    def thread_query(params):
        query_expansion(params)                    # computed on the caller's dict, so it can be reported
        params = {**params, "mirrors": "1"}
        join, where, args = comment_sql(params)
        order = "last ASC" if params.get("sort") == "oldest" else "last DESC"
        # Mirrors only count as a match through a text search (they are the post, not a comment).
        where += " AND (c.is_mirror = 0 OR ? != '')"
        return join, where, [*args, params.get("q") or ""], order

    def execute(kind, params):
        """Ordered result ids of a search (posts / comments / threads), capped at 50,000; the ONE path the search
        log, saved searches and re-runs use. Threads are identified as "group_id:top"."""
        if kind == "comments":
            join, where, args = comment_sql(params)
            order = {"oldest": "c.date ASC", "newest": "c.date DESC"}.get(params.get("sort"), "c.date DESC")
            ids = [r["id"] for r in q(f"SELECT c.id FROM comments c {join} WHERE {where} ORDER BY {order} LIMIT {ID_CAP}", args)]
        elif kind == "threads":
            join, where, args, order = thread_query(params)
            ids = [f"{r['group_id']}:{r['top']}" for r in q(
                f"""SELECT c.group_id, {THREAD_KEY} top, MAX(c.date) last FROM comments c {join} WHERE {where}
                    GROUP BY c.group_id, top ORDER BY {order} LIMIT {ID_CAP}""", args)]
        else:
            join, where, args, rank = search_sql(params)
            ids = [r["id"] for r in q(f"SELECT p.id FROM posts p {join} WHERE {where} "
                                      f"ORDER BY {order_by(params, rank)} LIMIT {ID_CAP}", args)]
        return {"ids": ids, "total": len(ids), "capped": len(ids) >= ID_CAP, "exp": query_expansion(params)}

    def subquery(params, scope="posts", cols="id", order=False, limit=None, ranked=None):
        """(sql, args) selecting one column of the result set: for facets, maps, terms."""
        if scope == "comments":
            join, where, args = comment_sql(params)
            sql = f"SELECT c.{cols} FROM comments c {join} WHERE {where}"
            by = "c.date DESC"
        else:
            join, where, args, rank = search_sql(params, ranked=order if ranked is None else ranked)
            sql = f"SELECT p.{cols} FROM posts p {join} WHERE {where}"
            by = order_by(params, rank)
        if order:
            sql += f" ORDER BY {by}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return sql, args

    def log_search(kind, params, endpoint=None, ids=None, total=None):
        """Record an executed search in the search log: first page only, and only real searches (a query, entity or
        place filter). Exports pass their own ids/total and are always logged. Never breaks the search itself."""
        try:
            if ids is None:
                if int(params.get("offset") or 0) != 0 or not searchlog.loggable(params):
                    return None
                ids = execute(kind, params)["ids"]
            return searchlog.record(ctx, endpoint or kind, params, query_expansion(params),
                                    len(ids) if total is None else total, ids,
                                    kind=kind if endpoint in ("export", "saved") else None)
        except Exception as e:
            print(f"warning: search log failed: {e}")
            return None

    def post_hits(params, ids):
        """Where each result matched: {post_id: {matched_in: [text|versions|ocr|speech], media_hits: [..], version_hit}}."""
        out = {i: {"matched_in": [], "media_hits": [], "version_hit": False} for i in ids}
        expr = query_expansion(params)["fts"]
        if not ids or not expr:
            return out
        used = active_sources(post_sources(params)[1])
        marks = ",".join("?" * len(ids))
        for src, fx in (("text", db.fts_text(expr)), ("names", db.fts_names(expr))):
            if src in used:
                for r in q(f"SELECT rowid FROM posts_fts WHERE posts_fts MATCH ? AND rowid IN ({marks})", (fx, *ids)):
                    out[r["rowid"]]["matched_in"].append(src)
        if "versions" in used:
            for r in q(f"SELECT post_id FROM ({db.versions_match_sql()}) WHERE post_id IN ({marks})", (expr, *ids)):
                out[r["post_id"]]["version_hit"] = True
                out[r["post_id"]]["matched_in"].append("versions")
        kinds = [k for k in ("ocr", "speech") if k in used]
        if kinds:
            rows = q(f"""SELECT * FROM ({db.media_text_match_sql()}) WHERE post_id IN ({marks})
                         AND kind IN ({','.join('?' * len(kinds))}) ORDER BY post_id, media_text_id""", (expr, *ids, *kinds))
            texts = {}
            if rows:
                mids = list({r["media_text_id"] for r in rows})
                texts = {t["id"]: t["text"] for t in q(
                    f"SELECT id, text FROM media_text WHERE id IN ({','.join('?' * len(mids))})", mids)}
            terms_ = [t.lower() for t in lexicon.highlight_terms(query_expansion(params), params.get("q")) if len(t) >= 3]
            for r in rows:
                o = out[r["post_id"]]
                if r["kind"] not in o["matched_in"]:
                    o["matched_in"].append(r["kind"])
                if len(o["media_hits"]) < 3:
                    t = texts.get(r["media_text_id"]) or ""
                    low, at = t.lower(), -1
                    for w in terms_:
                        at = low.find(w)
                        if at >= 0:
                            break
                    a = max(0, at - 60) if at >= 0 else 0
                    o["media_hits"].append({"kind": r["kind"], "t_start": r["t_start"], "sha256": r["sha256"],
                                            "media_text_id": r["media_text_id"],
                                            "excerpt": ("…" if a else "") + t[a:a + 200].replace("\n", " ")})
        for o in out.values():
            o["matched_in"].sort(key=SOURCES.index)
        return out

    ctx.execute, ctx.subquery, ctx.query_expansion, ctx.qparams = execute, subquery, query_expansion, qparams

    # ---- pages ----------------------------------------------------------------

    def page(folder):
        with open(os.path.join(folder, "index.html"), encoding="utf-8") as f:
            return f.read().replace("__TOKEN__", TOKEN)

    @app.get("/", response_class=HTMLResponse)
    def index():
        # the redesigned UI when it has been built, else the classic one
        return page(WEB if os.path.isfile(os.path.join(WEB, "index.html")) else STATIC)

    FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" '
               'fill="#2f6fed"/><text x="16" y="21.5" font-family="sans-serif" font-size="14" font-weight="700" '
               'fill="white" text-anchor="middle">TI</text></svg>')

    @app.get("/favicon.ico")
    def favicon():
        return Response(FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})

    @app.get("/classic", response_class=HTMLResponse)
    def classic():
        return page(STATIC)

    @app.get("/media/{path:path}")
    def media(path: str):
        full = os.path.realpath(os.path.join(store, path))
        if not full.startswith(os.path.realpath(store) + os.sep) or not os.path.isfile(full):
            raise HTTPException(404)
        headers = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}
        kind = (mimetypes.guess_type(full)[0] or "").split("/")[0]
        if full.lower().endswith((".svg", ".svgz")) or kind not in ("image", "video", "audio"):
            headers["Content-Disposition"] = "attachment"       # never render stored files as pages next to the API
        return FileResponse(full, headers=headers)

    # ---- overview -------------------------------------------------------------

    @app.get("/api/overview")
    def overview():
        counts = q1("""SELECT
            (SELECT COUNT(*) FROM channels WHERE type = 'channel' OR type IS NULL) channels,
            (SELECT COUNT(*) FROM channels WHERE last_crawled IS NOT NULL) crawled,
            (SELECT COUNT(*) FROM channels WHERE status = 'candidate' AND (type = 'channel' OR type IS NULL)) candidates,
            (SELECT COUNT(*) FROM channels WHERE status IN ('watched','seed')) watched,
            (SELECT COUNT(*) FROM posts) posts,
            (SELECT COUNT(*) FROM posts WHERE review_status = 'unreviewed') unreviewed,
            (SELECT COUNT(*) FROM posts WHERE review_status = 'relevant') relevant,
            (SELECT COUNT(*) FROM posts WHERE review_status = 'verify') verify,
            (SELECT COUNT(*) FROM media) media,
            (SELECT COALESCE(SUM(bytes),0) FROM media) media_bytes,
            (SELECT COUNT(*) FROM captures WHERE kind = 'page') pages,
            (SELECT COUNT(*) FROM comments WHERE is_mirror = 0) comments,
            (SELECT COUNT(*) FROM comments WHERE deleted_detected IS NOT NULL AND is_mirror = 0) comments_deleted,
            (SELECT COUNT(*) FROM tg_users) tg_users,
            (SELECT MIN(date) FROM posts) first_post,
            (SELECT MAX(date) FROM posts) last_post""")
        per_day = q("""SELECT substr(date,1,10) d, COUNT(*) n FROM posts
                       WHERE date >= date('now','-120 days') GROUP BY d ORDER BY d""")
        top = q("""SELECT c.username, c.title, c.subscribers, c.status,
                     (SELECT COUNT(DISTINCT source) FROM edges e WHERE e.target = c.username) linked_from,
                     (SELECT COALESCE(SUM(weight),0) FROM edges e WHERE e.target = c.username) signals
                   FROM channels c WHERE (c.type = 'channel' OR c.type IS NULL)
                   ORDER BY linked_from DESC, signals DESC LIMIT 8""")
        activity = q("SELECT ts, actor, action, target FROM audit ORDER BY id DESC LIMIT 12")
        return {"counts": counts, "per_day": per_day, "top": top, "activity": activity,
                "jobs": q("SELECT id, kind, status, created, summary FROM jobs ORDER BY id DESC LIMIT 5")}

    # ---- search ---------------------------------------------------------------

    @app.get("/api/search")
    def search(request: Request):
        params = qparams(request)
        join, where, args, rank = search_sql(params)
        ujoin, uwhere, uargs, _ = search_sql(params, ranked=False)     # counts and facets need no ranking
        limit = min(int(params.get("limit") or 25), 200)
        offset = int(params.get("offset") or 0)
        try:
            total = q1(f"SELECT COUNT(*) n FROM posts p {ujoin} WHERE {uwhere}", uargs)["n"]
            rows = q(f"""SELECT p.*, c.title channel_title FROM posts p {join}
                         LEFT JOIN channels c ON c.username = p.channel
                         WHERE {where} ORDER BY {order_by(params, rank)} LIMIT ? OFFSET ?""",
                     (*args, limit, offset))
        except Exception as e:
            raise HTTPException(400, f"Search error: {e}")
        facets = q(f"""SELECT p.channel, COUNT(*) n FROM posts p {ujoin} WHERE {uwhere}
                       GROUP BY p.channel ORDER BY n DESC LIMIT 15""", uargs)
        timeline = q(f"""SELECT substr(p.date,1,10) d, COUNT(*) n FROM posts p {ujoin} WHERE {uwhere}
                         GROUP BY d ORDER BY d""", uargs)
        results = cases.annotate(ctx, cards(rows), params.get("case"), "post")
        hits = post_hits(params, [r["id"] for r in results])
        for r in results:
            r.update(hits[r["id"]])
        sub, sargs = subquery(params, ranked=False)
        avail, used = params["_src"]
        run_id = log_search("posts", params)
        return {"total": total, "offset": offset, "results": results,
                "facets": facets, "timeline": timeline,
                # patterns found in the text of the results (claims to check) and places they mention
                "entities": entities.facet(ctx, "post", sub, sargs),
                "places": places.facet(ctx, "post", sub, sargs, unambiguous=params.get("unambiguous") == "1"), "places_note": places.LABEL,
                "sources": {"available": avail, "used": used}, "search_run": run_id,
                "terms": lexicon.highlight_terms(params["_exp"], params.get("q")), "expansion": params["_exp"]}

    @app.get("/api/export.csv")
    def export(request: Request):
        params = qparams(request)
        join, where, args, rank = search_sql(params)
        rows = q(f"""SELECT p.*, c.title channel_title,
                       (SELECT group_concat(t.name, '; ') FROM post_tags pt JOIN tags t ON t.id = pt.tag_id
                        WHERE pt.post_id = p.id) tag_names,
                       (SELECT group_concat(m.kind || ':' || m.sha256, ' ') FROM media m WHERE m.post_id = p.id) media_hashes,
                       (SELECT group_concat(n.text, ' | ') FROM notes n WHERE n.post_id = p.id) note_text
                     FROM posts p {join} LEFT JOIN channels c ON c.username = p.channel
                     WHERE {where} ORDER BY {order_by(params, rank)} LIMIT 50000""", args)
        buf = io.StringIO()
        cols = ["url", "channel", "channel_title", "date", "views", "text", "fwd_username", "fwd_url",
                "media_types", "sensitive", "review_status", "tag_names", "note_text", "media_hashes",
                "first_captured", "last_captured", "capture_count"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(db.csv_rows(rows))
        exp = params["_exp"]
        run_id = log_search("posts", params, "export", [r["id"] for r in rows], len(rows))
        params = public(params)
        write(lambda: (db.audit(con, actor(), "export_csv", None, {"params": params, "rows": len(rows)}),
                       db.audit(con, actor(), "search_export", None, {"q": params.get("q"), "fts": exp["fts"],
                                                                      "dict_version": exp["dict_version"],
                                                                      "expand": exp["enabled"], "rows": len(rows),
                                                                      "scope": "posts", "search_run": run_id})))
        name = "export-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".csv"
        return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv",
                                 headers=export_headers(name, {**params, "_exp": exp}))

    # ---- posts ----------------------------------------------------------------

    def _in_case(h, case_id):
        """Audit row is global or belongs to this case (detail.case), not to another case."""
        try:
            d = json.loads(h["detail"]) if h.get("detail") else {}
        except (TypeError, ValueError):
            return True
        return not isinstance(d, dict) or d.get("case") in (None, case_id)

    @app.get("/api/posts/{pid}")
    def post(pid: int, case: str = ""):
        rows = q("SELECT p.*, c.title channel_title FROM posts p LEFT JOIN channels c "
                 "ON c.username = p.channel WHERE p.id = ?", (pid,))
        if not rows:
            raise HTTPException(404)
        p = cards(rows)[0]
        raw = rows[0]
        p.update(
            hashtags=jl(raw["hashtags"], []), links=jl(raw["links"], []),
            link_preview_url=raw["link_preview_url"], reactions=jl(raw["reactions"], {}),
            author_signature=raw["author_signature"], last_captured=raw["last_captured"],
            reviewed_at=raw["reviewed_at"],
            media=q("SELECT * FROM media WHERE post_id = ? ORDER BY idx, kind", (pid,)),
            notes=q("SELECT * FROM notes WHERE post_id = ? ORDER BY id", (pid,)),
            versions=q("SELECT * FROM post_versions WHERE post_id = ? ORDER BY id", (pid,)),
            captures=q("""SELECT c.* FROM captures c WHERE c.kind = 'media' AND c.sha256 IN
                            (SELECT sha256 FROM media WHERE post_id = ?) ORDER BY captured_at""", (pid,)),
            history=q("SELECT ts, actor, action, detail FROM audit WHERE target = ? ORDER BY id",
                      (f"post {pid}",)),
            page_capture=q1("SELECT url, sha256, path, bytes, captured_at, tool FROM captures "
                            "WHERE kind = 'page' AND sha256 = ? ORDER BY captured_at LIMIT 1",
                            (raw["page_sha256"],)) if raw["page_sha256"] else None,
        )
        if case.isdigit():      # ?case=<id>: this case's own review/tags/notes; history and notes of other cases are left out
            cases.annotate(ctx, [p], case, "post")
            p["notes"] = [n for n in p["notes"] if n["case_id"] == int(case)]
            p["history"] = [h for h in p["history"] if _in_case(h, int(case))]
        # the same original post forwarded elsewhere, and identical media elsewhere
        if raw["fwd_url"]:
            p["same_forward"] = q("""SELECT id, channel, date, url FROM posts
                                     WHERE fwd_url = ? AND id != ? ORDER BY date""", (raw["fwd_url"], pid))
            src = raw["fwd_url"].rsplit("/", 2)
            if len(src) == 3 and src[2].isdigit():
                p["original"] = q1("SELECT id, channel, date, url FROM posts WHERE channel = ? AND msg_id = ?",
                                   (src[1].lower(), int(src[2])))
        else:
            p["same_forward"] = q("""SELECT id, channel, date, url FROM posts WHERE fwd_url = ?
                                     ORDER BY date""", (raw["url"],))
        p["same_media"] = q("""SELECT DISTINCT p2.id, p2.channel, p2.date, p2.url FROM media m1
                               JOIN media m2 ON m2.sha256 = m1.sha256 AND m2.post_id != m1.post_id
                               JOIN posts p2 ON p2.id = m2.post_id
                               WHERE m1.post_id = ? ORDER BY p2.date""", (pid,))
        return p

    @app.post("/api/posts/{pid}/review")
    async def review(pid: int, request: Request):
        body = await request.json()
        status = body.get("status")
        if status not in ("unreviewed", "relevant", "irrelevant", "verify"):
            raise HTTPException(400, "bad status")

        def do():
            old = con.execute("SELECT review_status FROM posts WHERE id = ?", (pid,)).fetchone()
            if not old:
                raise HTTPException(404)
            con.execute("UPDATE posts SET review_status = ?, reviewed_at = ? WHERE id = ?",
                        (status, db.now(), pid))
            db.audit(con, actor(), "review", f"post {pid}", {"from": old[0], "to": status})
        write(do)
        return {"ok": True}

    @app.post("/api/posts/{pid}/tags")
    async def tag(pid: int, request: Request):
        body = await request.json()
        try:
            tid, on = int(body["tag_id"]), bool(body.get("on", True))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Need tag_id (a whole number).")

        def do():
            name = con.execute("SELECT name FROM tags WHERE id = ?", (tid,)).fetchone()
            if not name:
                raise HTTPException(404, "no such tag")
            if on:
                con.execute("INSERT OR IGNORE INTO post_tags(post_id, tag_id, added_by, added_at) "
                            "VALUES (?,?,?,?)", (pid, tid, actor(), db.now()))
            else:
                con.execute("DELETE FROM post_tags WHERE post_id = ? AND tag_id = ?", (pid, tid))
            db.audit(con, actor(), "tag_add" if on else "tag_remove", f"post {pid}", {"tag": name[0]})
        write(do)
        return {"ok": True, "tags": [r["tag_id"] for r in q("SELECT tag_id FROM post_tags WHERE post_id = ?", (pid,))]}

    @app.post("/api/posts/{pid}/notes")
    async def add_note(pid: int, request: Request):
        text = ((await request.json()).get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty note")

        def do():
            con.execute("INSERT INTO notes(post_id, text, author, created_at) VALUES (?,?,?,?)",
                        (pid, text, actor(), db.now()))
            db.audit(con, actor(), "note_add", f"post {pid}", {"text": text[:200]})
        write(do)
        return {"ok": True, "notes": q("SELECT * FROM notes WHERE post_id = ? ORDER BY id", (pid,))}

    # ---- channels -------------------------------------------------------------

    CHANNEL_STATS = """
        (SELECT COUNT(*) FROM posts p WHERE p.channel = c.username) posts,
        (SELECT COUNT(*) FROM posts p WHERE p.channel = c.username AND p.review_status = 'relevant') relevant,
        (SELECT COUNT(DISTINCT source) FROM edges e WHERE e.target = c.username) linked_from,
        (SELECT COALESCE(SUM(weight),0) FROM edges e WHERE e.target = c.username) signals_in,
        (SELECT COUNT(DISTINCT target) FROM edges e WHERE e.source = c.username) links_to,
        (SELECT MAX(date) FROM posts p WHERE p.channel = c.username) last_post"""

    @app.get("/api/channels")
    def channels(request: Request):
        params = dict(request.query_params)
        where, args = ["1"], []
        if params.get("status"):
            st = params["status"].split(",")
            where.append(f"c.status IN ({','.join('?' * len(st))})")
            args += st
        if params.get("type"):
            ty = params["type"].split(",")
            where.append(f"COALESCE(c.type, 'unchecked') IN ({','.join('?' * len(ty))})")
            args += ty
        if (params.get("case") or "").isdigit():
            where.append("c.username IN (SELECT channel FROM case_channels WHERE case_id = ?)")
            args.append(int(params["case"]))
        if params.get("q"):
            where.append("(c.username LIKE ? OR c.title LIKE ? OR c.description LIKE ?)")
            args += [f"%{params['q']}%"] * 3
        sort = {"linked": "linked_from DESC, signals_in DESC", "subscribers": "c.subscribers DESC",
                "posts": "posts DESC", "name": "c.username", "recent": "last_post DESC",
                "relevant": "relevant DESC"}.get(params.get("sort"), "linked_from DESC, signals_in DESC")
        return q(f"SELECT c.*, {CHANNEL_STATS} FROM channels c WHERE {' AND '.join(where)} "
                 f"ORDER BY {sort} LIMIT 2000", args)

    @app.get("/api/channels/{name}")
    def channel(name: str):
        c = q1(f"SELECT c.*, {CHANNEL_STATS} FROM channels c WHERE c.username = ?", (name,))
        if not c:
            raise HTTPException(404)
        c["sources"] = q("""SELECT e.source username, e.type, e.weight, e.first_seen, e.last_seen, ch.title
                            FROM edges e LEFT JOIN channels ch ON ch.username = e.source
                            WHERE e.target = ? ORDER BY e.weight DESC""", (name,))
        c["targets"] = q("""SELECT e.target username, e.type, e.weight, e.first_seen, e.last_seen,
                                   ch.title, ch.type ctype, ch.status
                            FROM edges e LEFT JOIN channels ch ON ch.username = e.target
                            WHERE e.source = ? ORDER BY e.weight DESC""", (name,))
        c["per_day"] = q("SELECT substr(date,1,10) d, COUNT(*) n FROM posts WHERE channel = ? "
                         "GROUP BY d ORDER BY d", (name,))
        c["tags"] = q("""SELECT t.id, t.name, t.color, COUNT(*) n FROM post_tags pt
                         JOIN tags t ON t.id = pt.tag_id JOIN posts p ON p.id = pt.post_id
                         WHERE p.channel = ? GROUP BY t.id ORDER BY n DESC""", (name,))
        c["history"] = q("SELECT ts, actor, action, detail FROM audit WHERE target = ? ORDER BY id DESC",
                         (f"channel {name}",))
        c["jobs"] = q("SELECT id, kind, status, created FROM jobs WHERE params LIKE ? ORDER BY id DESC LIMIT 10",
                      (f'%"{name}"%',))
        c["notes"] = q("SELECT * FROM notes WHERE channel = ? ORDER BY id", (name,))
        c["comment_group"] = q1("""SELECT g.*,
                (SELECT COUNT(*) FROM comments WHERE group_id = g.group_id AND is_mirror = 0) comments,
                (SELECT COUNT(*) FROM comments WHERE group_id = g.group_id AND is_mirror = 0
                   AND deleted_detected IS NOT NULL) deleted,
                (SELECT COUNT(DISTINCT sender_id) FROM comments WHERE group_id = g.group_id AND is_mirror = 0
                   AND sender_type = 'user') commenters,
                (SELECT MIN(date) FROM comments WHERE group_id = g.group_id) first_date,
                (SELECT MAX(date) FROM comments WHERE group_id = g.group_id) last_date
              FROM comment_groups g WHERE g.channel = ?""", (name,))
        if c["comment_group"]:
            c["admins"] = q("""SELECT a.*, u.username, u.first_name, u.last_name, u.bot, u.phone, u.deleted,
                                 (SELECT COUNT(*) FROM comments x WHERE x.sender_id = a.user_id
                                    AND x.sender_type = 'user') comments_anywhere,
                                 (SELECT COUNT(DISTINCT x.group_id) FROM comments x WHERE x.sender_id = a.user_id
                                    AND x.sender_type = 'user') groups_anywhere
                               FROM group_admins a LEFT JOIN tg_users u ON u.user_id = a.user_id
                               WHERE a.group_id = ? ORDER BY a.removed_detected IS NOT NULL, a.role = 'creator' DESC,
                                 u.bot, a.since""", (c["comment_group"]["group_id"],))
        c["schedule"] = q1("SELECT * FROM schedules WHERE channel = ?", (name,))
        return c

    @app.post("/api/channels/{name}/status")
    async def channel_status(name: str, request: Request):
        body = await request.json()
        status = body.get("status")
        if status not in ("seed", "watched", "candidate", "dismissed"):
            raise HTTPException(400, "bad status")

        def do():
            con.execute("UPDATE channels SET status = ?, status_reason = ? WHERE username = ?",
                        (status, body.get("reason"), name))
            db.audit(con, actor(), "channel_status", f"channel {name}",
                     {"status": status, "reason": body.get("reason")})
        write(do)
        return {"ok": True}

    @app.post("/api/channels/{name}/notes")
    async def channel_note(name: str, request: Request):
        text = ((await request.json()).get("text") or "").strip()
        if not text:
            raise HTTPException(400, "empty note")

        def do():
            con.execute("INSERT INTO notes(channel, text, author, created_at) VALUES (?,?,?,?)",
                        (name, text, actor(), db.now()))
            db.audit(con, actor(), "note_add", f"channel {name}", {"text": text[:200]})
        write(do)
        return {"ok": True}

    # ---- graph ----------------------------------------------------------------

    @app.get("/api/graph")
    def graph(request: Request):
        params = dict(request.query_params)
        types = [t for t in (params.get("types") or
                             "forward,link,mention,description,comment_forward,comment_sender,similar").split(",") if t]
        min_w = int(params.get("min_weight") or 1)
        marks = ",".join("?" * len(types))
        links = q(f"""SELECT source, target, SUM(weight) weight, group_concat(type) types
                      FROM edges WHERE type IN ({marks}) GROUP BY source, target
                      HAVING SUM(weight) >= ?""", (*types, min_w))
        hide = set()
        if params.get("hide_dismissed", "1") == "1":
            hide |= {r["username"] for r in q("SELECT username FROM channels WHERE status = 'dismissed'")}
        if params.get("channels_only", "1") == "1":
            hide |= {r["username"] for r in q("SELECT username FROM channels WHERE type IN ('user','bot')")}
        links = [l for l in links if l["source"] not in hide and l["target"] not in hide]
        case_names = None
        if (params.get("case") or "").isdigit():
            case_names = {r["channel"] for r in q("SELECT channel FROM case_channels WHERE case_id = ?",
                                                  (int(params["case"]),))}
            links = [l for l in links if l["source"] in case_names or l["target"] in case_names]
        names = {l["source"] for l in links} | {l["target"] for l in links}
        names |= case_names if case_names is not None else \
            {r["username"] for r in q("SELECT username FROM channels WHERE status IN ('seed','watched')")}
        names -= hide
        nodes = q(f"""SELECT c.username, c.title, c.type, c.status, c.subscribers,
                        c.last_crawled IS NOT NULL crawled,
                        (SELECT COUNT(*) FROM posts p WHERE p.channel = c.username) posts,
                        (SELECT COUNT(*) FROM posts p WHERE p.channel = c.username AND p.review_status = 'relevant') relevant
                      FROM channels c WHERE c.username IN ({','.join('?' * len(names))})""",
                  tuple(names)) if names else []
        if case_names is not None:
            for n in nodes:
                n["in_case"] = n["username"] in case_names
        return {"nodes": nodes, "links": links}

    # ---- tags / settings --------------------------------------------------------

    @app.get("/api/tags")
    def tags():
        return q("""SELECT t.*, (SELECT COUNT(*) FROM post_tags pt WHERE pt.tag_id = t.id) uses
                    FROM tags t ORDER BY t.category, t.id""")

    @app.post("/api/tags")
    async def save_tag(request: Request):
        b = await request.json()

        def do():
            if b.get("delete") and b.get("id"):
                con.execute("DELETE FROM tags WHERE id = ?", (b["id"],))
                db.audit(con, actor(), "tag_delete", f"tag {b['id']}")
                return
            vals = (b["name"].strip(), b.get("category") or "Other", b.get("color") or "#64748b",
                    b.get("description") or "", (b.get("key") or "")[:1] or None)
            if b.get("id"):
                con.execute("UPDATE tags SET name=?, category=?, color=?, description=?, key=? WHERE id=?",
                            (*vals, b["id"]))
                db.audit(con, actor(), "tag_edit", f"tag {b['id']}", {"name": vals[0]})
            else:
                con.execute("INSERT INTO tags(name, category, color, description, key) VALUES (?,?,?,?,?)", vals)
                db.audit(con, actor(), "tag_create", None, {"name": vals[0]})
        write(do)
        return tags()

    @app.get("/api/settings")
    def get_settings():
        out = dict(DEFAULT_SETTINGS)
        out.update({r["key"]: r["value"] for r in q("SELECT key, value FROM settings")})
        today = datetime.now().strftime("%Y-%m-%d")
        out["exposure_today"] = int(out.get(f"exposure:{today}", 0) or 0)
        out["workspace"] = workspace
        return {k: v for k, v in out.items() if not k.startswith("exposure:")}

    @app.post("/api/settings")
    async def save_settings(request: Request):
        b = await request.json()

        def do():
            for k, v in b.items():
                if k in DEFAULT_SETTINGS:
                    con.execute("INSERT INTO settings(key, value) VALUES (?,?) "
                                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, str(v)))
            db.audit(con, actor(), "settings", None, b)
        write(do)
        return get_settings()

    @app.post("/api/exposure")
    def exposure():
        key = "exposure:" + datetime.now().strftime("%Y-%m-%d")
        write(lambda: con.execute("INSERT INTO settings(key, value) VALUES (?, '1') ON CONFLICT(key) "
                                  "DO UPDATE SET value = CAST(value AS INTEGER) + 1", (key,)))
        return {"ok": True}

    # ---- jobs -----------------------------------------------------------------

    @app.get("/api/jobs")
    def list_jobs():
        return q("SELECT * FROM jobs ORDER BY id DESC LIMIT 100")

    @app.post("/api/jobs")
    async def new_job(request: Request):
        b = await request.json()
        kind = b.get("kind")
        if b.get("resume_of") is not None:               # continue a finished crawl (same seeds and settings)
            if kind not in (None, "crawl"):
                raise HTTPException(400, "Only crawls can be resumed.")
            try:
                return {"id": runner.resume_crawl(int(b["resume_of"]), actor())}
            except (TypeError, ValueError):
                raise HTTPException(400, "resume_of must be a job id.")
            except jobs.ResumeError as e:
                raise HTTPException(e.code, str(e))
        if kind == "crawl":
            from tgspider import normalize_seed          # same parsing as the CLI
            seeds = []
            for raw in (b.get("seeds") or "").replace(",", "\n").splitlines():
                s = normalize_seed(raw)
                if s and s not in seeds:
                    seeds.append(s)
            if not seeds:
                raise HTTPException(400, "No valid channel names.")
            for j in q("SELECT id, status, params FROM jobs WHERE kind = 'crawl' AND status IN ('queued','running')"):
                if sorted(json.loads(j["params"]).get("seeds", [])) == sorted(seeds):
                    raise HTTPException(409, f"A crawl of {', '.join('@' + x for x in seeds)} is already "
                                             f"{j['status']} (job #{j['id']}). Watch it on the Collect page.")
            network_only = b.get("collect") == "network"
            params = {"seeds": seeds, "depth": max(1, min(int(b.get("depth", 1)), 4)),
                      "network_only": network_only,
                      "pages": max(1, min(int(b.get("pages", 10)), 500)),
                      "max_channels": max(1, min(int(b.get("max_channels", 30)), 1000)),
                      "media": "none" if network_only else
                               (b.get("media") if b.get("media") in ("none", "photos", "all") else "photos")}
            if b.get("mark_seeds", True):
                def mark():
                    for s in seeds:
                        con.execute("INSERT INTO channels(username, status, first_seen) VALUES (?, 'seed', ?) "
                                    "ON CONFLICT(username) DO UPDATE SET status = CASE WHEN status = 'candidate' "
                                    "THEN 'seed' ELSE status END", (s, db.now()))
                write(mark)
        elif kind == "media":
            params = {"channel": b.get("channel"), "post_ids": b.get("post_ids") or [],
                      "what": b.get("what") if b.get("what") in ("videos", "missing", "all") else "videos",
                      "max_mb": float(b.get("max_mb") or 200), "limit": int(b.get("limit") or 50),
                      "only_refused": bool(b.get("only_refused", True))}
            if params["post_ids"]:
                # tgmedia works on (channel, msg_id); resolve from our ids up front
                params["post_ids"] = [int(x) for x in params["post_ids"]]
        elif kind == "comments":
            from tgspider import normalize_seed
            channel = normalize_seed(b.get("channel") or "")
            if not channel:
                raise HTTPException(400, "Enter a channel username.")
            busy = runner.active_comment_job(channel)
            if busy:
                raise HTTPException(409, f"Comments of @{channel} are already being collected (job #{busy}).")
            params = {"channel": channel, "limit": max(1, min(int(b.get("limit") or 200), 100000)),
                      "media": b.get("media") if b.get("media") in ("none", "photos", "all") else "none",
                      "max_mb": float(b.get("max_mb") or 50)}
        else:
            raise HTTPException(400, "unknown job kind")
        return {"id": runner.submit(kind, params, actor())}

    def read_progress(job):
        path = os.path.join(job["run_dir"] or "", "progress.json")
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    @app.get("/api/jobs/active")
    def active_job():
        """The job the worker is on right now (for the sidebar monitor)."""
        running = q("SELECT * FROM jobs WHERE status IN ('running','importing') ORDER BY id")
        # a long crawl is more interesting to show than a few-second comment poll
        j = next((r for r in running if r["kind"] != "comments"), running[0] if running else None)
        queued = q1("SELECT COUNT(*) n FROM jobs WHERE status = 'queued'")["n"]
        last = q1("SELECT id, kind, status, finished, summary FROM jobs WHERE status IN "
                  "('done','failed','cancelled','interrupted') ORDER BY id DESC LIMIT 1")
        stop = bool(j and j["run_dir"] and os.path.exists(os.path.join(j["run_dir"], "STOP")))
        return {"job": j, "progress": read_progress(j) if j else None, "queued": queued, "last": last,
                "stop_requested": stop, "running": len(running),
                "others": [{"id": r["id"], "kind": r["kind"], "status": r["status"],
                            "params": r["params"]} for r in running if j and r["id"] != j["id"]]}

    @app.get("/api/jobs/{jid}/progress")
    def job_progress(jid: int):
        j = q1("SELECT * FROM jobs WHERE id = ?", (jid,))
        if not j:
            raise HTTPException(404)
        return {"job": j, "progress": read_progress(j),
                "stop_requested": bool(j["run_dir"]) and os.path.exists(os.path.join(j["run_dir"], "STOP"))}

    @app.post("/api/jobs/{jid}/stop")
    def stop_job(jid: int):
        return {"ok": runner.stop(jid, actor())}

    @app.post("/api/jobs/{jid}/cancel")
    def cancel_job(jid: int):
        return {"ok": runner.cancel(jid, actor())}

    @app.get("/api/jobs/{jid}/log")
    def job_log(jid: int, offset: int = 0):
        j = q1("SELECT log_path FROM jobs WHERE id = ?", (jid,))
        if not j or not j["log_path"] or not os.path.exists(j["log_path"]):
            return {"text": "", "offset": 0}
        with open(j["log_path"], encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            text = f.read(200_000)
            return {"text": text, "offset": f.tell()}

    @app.get("/api/telegram")
    def telegram(force: int = 0):
        return jobs.telegram_status(bool(force))

    # ---- evidence integrity -----------------------------------------------------

    @app.post("/api/integrity")
    def integrity():
        rows = q("SELECT id, sha256, path FROM media UNION ALL SELECT id, sha256, path FROM comment_media")
        pages = q("SELECT id, sha256, path FROM captures WHERE kind IN ('page', 'api_dump')")
        bad, missing = [], []
        for r in rows:
            f = os.path.join(store, r["path"])
            if not os.path.exists(f):
                missing.append(r["path"])
            elif ingest.sha256_file(f) != r["sha256"]:
                bad.append(r["path"])
        import gzip
        for r in pages:
            f = os.path.join(store, r["path"])
            if not os.path.exists(f):
                missing.append(r["path"])
                continue
            with gzip.open(f, "rb") as g:
                if hashlib.sha256(g.read()).hexdigest() != r["sha256"]:
                    bad.append(r["path"])
        exports = q("SELECT path, sha256 FROM exports")
        for r in exports:                      # evidence packages handed out must stay byte-identical
            if not os.path.exists(r["path"]):
                missing.append(r["path"])
            elif ingest.sha256_file(r["path"]) != r["sha256"]:
                bad.append(r["path"])
        result = {"checked_media": len(rows), "checked_pages": len(pages), "checked_exports": len(exports),
                  "mismatched": bad, "missing": missing, "ok": not bad and not missing,
                  "at": db.now()}
        write(lambda: db.audit(con, actor(), "integrity_check", None,
                               {k: v for k, v in result.items() if k != "at"}))
        return result

    @app.get("/api/audit")
    def audit_log(limit: int = 200):
        return q("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(limit, 2000),))

    @app.post("/api/import")
    async def import_run(request: Request):
        body = await request.json()
        path = os.path.abspath(os.path.expanduser(body.get("path", "")))
        if not os.path.isdir(path) or not (os.path.exists(os.path.join(path, "nodes.jsonl"))
                                           or os.path.exists(os.path.join(path, "group.json"))):
            raise HTTPException(400, "Not a tgspider or tgcomments output folder (no nodes.jsonl or group.json).")
        with lock:
            stats = ingest.ingest_run(con, path, store, actor=actor(), log=lambda m: None,
                                      network_only=bool(body.get("network_only")))
        return stats

    # ---- comments ---------------------------------------------------------------

    COMMENT_SELECT = """
        SELECT c.*, u.username u_username, u.first_name u_first, u.last_name u_last, u.bot u_bot,
               u.phone u_phone, u.deleted u_deleted,
               ch.username s_username, ch.title s_title,
               (SELECT m.channel_post FROM comments m WHERE m.group_id = c.group_id
                  AND m.msg_id = c.thread_top_id AND m.is_mirror = 1) post_msg_id,
               (SELECT a.rank FROM group_admins a WHERE a.group_id = c.group_id AND a.user_id = c.sender_id
                  AND c.sender_type = 'user') admin_rank,
               (SELECT a.role FROM group_admins a WHERE a.group_id = c.group_id AND a.user_id = c.sender_id
                  AND c.sender_type = 'user') admin_role,
               (SELECT COUNT(*) FROM comment_versions v WHERE v.comment_id = c.id) versions
        FROM comments c
        LEFT JOIN tg_users u ON c.sender_type = 'user' AND u.user_id = c.sender_id
        LEFT JOIN tg_chats ch ON c.sender_type IN ('channel', 'group') AND ch.chat_id = c.sender_id"""

    def comment_sources(params):
        """(available, used) text sources of a comment search: `in=text,ocr,speech,names` (default text + OCR + speech; names only when
        asked for). OCR / speech are left out while no comment media has been read, so a plain search stays the
        classic text-only query."""
        asked = {x for x in (params.get("in") or "").replace(" ", "").split(",") if x in ("text", "ocr", "speech", "names")}
        avail = ["text"]
        if hasattr(db, "comment_media_text_match_sql") and entities.has_table(con, "media_text_fts") and \
                con.execute("SELECT 1 FROM media_text t JOIN comment_media m ON m.sha256 = t.sha256 LIMIT 1").fetchone():
            avail += ["ocr", "speech"]
        avail.append("names")
        return avail, ([x for x in avail if x in asked] if asked else [x for x in avail if x != "names"])

    def comment_sql(params):
        where, args, join = [], [], ""
        expr = query_expansion(params)["fts"]
        if params.get("q") and not expr:
            where.append("0")
        if expr:
            used = comment_sources(params)[1]
            extras = [x for x in used if x in ("ocr", "speech")]
            fx = db.fts_scoped(expr, "text" in used, "names" in used)   # text columns only; names only with in=names
            if not used:
                where.append("0")
            elif not extras:                       # plain text search: exactly the classic query
                join = "JOIN comments_fts f ON f.rowid = c.id"
                where.append("comments_fts MATCH ?")
                args.append(fx)
            else:                                  # text OR the OCR / speech text of the comment's media
                parts, pa = [], []
                if "text" in used or "names" in used:
                    parts.append("SELECT rowid FROM comments_fts WHERE comments_fts MATCH ?")
                    pa.append(fx)
                parts.append(f"SELECT comment_id FROM ({db.comment_media_text_match_sql()}) "
                             f"WHERE kind IN ({','.join('?' * len(extras))})")
                pa += [expr, *extras]
                where.append("c.id IN (" + " UNION ".join(parts) + ")")
                args += pa
        if params.get("mirrors") != "1":
            where.append("c.is_mirror = 0")
        chans = [x for x in (params.get("channels") or "").split(",") if x]
        if chans:
            where.append(f"c.channel IN ({','.join('?' * len(chans))})")
            args += chans
        if params.get("ids"):                      # ids=1,2,3: exactly these comments
            id_list = [int(x) for x in params["ids"].split(",") if x.strip().isdigit()]
            if len(id_list) > 500:
                raise ValueError(f"ids= takes at most 500 ids per request; got {len(id_list)}")
            where.append(f"c.id IN ({','.join('?' * len(id_list))})" if id_list else "0")
            args += id_list
        if (params.get("case") or "").isdigit():
            where.append("c.channel IN (SELECT channel FROM case_channels WHERE case_id = ?)")
            args.append(int(params["case"]))
        if params.get("review"):
            # Comments are triaged per case only; outside a case they have no review status (all unreviewed).
            st = [x for x in params["review"].split(",") if x]
            if (params.get("case") or "").isdigit():
                where.append(f"""COALESCE((SELECT r.status FROM case_reviews r WHERE r.case_id = ? AND
                                 r.item_type = 'comment' AND r.item_id = c.id), 'unreviewed') IN ({','.join('?' * len(st))})""")
                args += [int(params["case"]), *st]
            else:
                where.append("1" if "unreviewed" in st else "0")
        if params.get("since"):
            where.append("c.first_captured >= ?")
            args.append(params["since"])
        if params.get("sender"):
            where.append("c.sender_id = ?")
            args.append(int(params["sender"]))
        if params.get("post"):
            where.append("""c.thread_top_id IN (SELECT m.msg_id FROM comments m WHERE m.group_id = c.group_id
                            AND m.is_mirror = 1 AND m.channel_post = ?)""")
            args.append(int(params["post"]))
        if ymd(params, "date_from"):
            where.append("c.date >= ?")
            args.append(ymd(params, "date_from"))
        if ymd(params, "date_to"):
            where.append("c.date < ?")
            args.append(ymd(params, "date_to") + "T99")
        if params.get("deleted") == "1":
            where.append("c.deleted_detected IS NOT NULL")
        if params.get("forwarded") == "1":
            where.append("c.fwd_type IS NOT NULL")
        if params.get("edited") == "1":
            where.append("EXISTS (SELECT 1 FROM comment_versions v WHERE v.comment_id = c.id)")
        if params.get("media") == "1":
            where.append("c.media_types NOT IN ('[]', '[\"service\"]')")
        if params.get("sender_type"):
            where.append("c.sender_type = ?")
            args.append(params["sender_type"])
        if params.get("admins") == "1":
            where.append("""c.sender_type = 'user' AND EXISTS (SELECT 1 FROM group_admins a
                            WHERE a.group_id = c.group_id AND a.user_id = c.sender_id)""")
        if params.get("service") != "1":
            where.append("c.media_types != '[\"service\"]'")
        for mod in (entities, places):
            cl, ca = mod.filter_sql(con, params, "comment", "c.id")
            where += cl
            args += ca
        return join, " AND ".join(where) or "1", args

    def comment_card(r, media):
        name = " ".join(x for x in (r["u_first"], r["u_last"]) if x) if r["sender_type"] == "user" else r["s_title"]
        return {**{k: r[k] for k in ("id", "group_id", "msg_id", "channel", "date", "edit_date", "text",
                                    "sender_type", "sender_id", "post_author", "reply_to_msg_id", "thread_top_id",
                                    "fwd_type", "fwd_id", "fwd_name", "fwd_username", "fwd_post", "fwd_date",
                                    "reactions_total", "first_captured", "last_captured", "last_seen",
                                    "capture_count", "deleted_detected", "post_msg_id", "admin_rank",
                                    "admin_role", "versions", "is_mirror", "channel_post")},
                "media_types": jl(r["media_types"], []),
                "sender_name": name, "sender_username": r["u_username"] or r["s_username"],
                "sender_bot": bool(r["u_bot"]), "sender_phone": r["u_phone"], "sender_deleted": bool(r["u_deleted"]),
                "media": media.get(r["id"], [])}

    def comment_cards(rows):
        media = {}
        if rows:
            ids = [r["id"] for r in rows]
            for m in q(f"""SELECT id, comment_id, CASE kind WHEN 'photo' THEN 'photo' ELSE 'full' END kind,
                             idx, path, mime, bytes, sha256, captured_at FROM comment_media
                           WHERE comment_id IN ({','.join('?' * len(ids))}) ORDER BY comment_id, idx""", ids):
                media.setdefault(m["comment_id"], []).append(m)
        return [comment_card(r, media) for r in rows]

    def comment_hits(params, ids):
        """Where each comment result matched: {comment_id: {matched_in: [text|ocr|speech|names], media_hits: [..]}}
        (the comment twin of post_hits; media_hits carry the file's hash and an excerpt of the OCR / speech line)."""
        out = {i: {"matched_in": [], "media_hits": []} for i in ids}
        expr = query_expansion(params)["fts"]
        if not ids or not expr:
            return out
        used = comment_sources(params)[1]
        marks = ",".join("?" * len(ids))
        for src, fx in (("text", db.fts_text(expr)), ("names", db.fts_names(expr))):
            if src in used:
                for r in q(f"SELECT rowid FROM comments_fts WHERE comments_fts MATCH ? AND rowid IN ({marks})", (fx, *ids)):
                    out[r["rowid"]]["matched_in"].append(src)
        kinds = [k for k in ("ocr", "speech") if k in used]
        if kinds:
            rows = q(f"""SELECT * FROM ({db.comment_media_text_match_sql()}) WHERE comment_id IN ({marks})
                         AND kind IN ({','.join('?' * len(kinds))}) ORDER BY comment_id, media_text_id""", (expr, *ids, *kinds))
            mids = list({r["media_text_id"] for r in rows})
            texts = {t["id"]: t["text"] for t in q(
                f"SELECT id, text FROM media_text WHERE id IN ({','.join('?' * len(mids))})", mids)} if mids else {}
            terms_ = [t.lower() for t in lexicon.highlight_terms(query_expansion(params), params.get("q")) if len(t) >= 3]
            for r in rows:
                o = out[r["comment_id"]]
                if r["kind"] not in o["matched_in"]:
                    o["matched_in"].append(r["kind"])
                if len(o["media_hits"]) < 3:
                    t = texts.get(r["media_text_id"]) or ""
                    low, at = t.lower(), -1
                    for w in terms_:
                        at = low.find(w)
                        if at >= 0:
                            break
                    a = max(0, at - 60) if at >= 0 else 0
                    o["media_hits"].append({"kind": r["kind"], "t_start": r["t_start"], "sha256": r["sha256"],
                                            "media_text_id": r["media_text_id"],
                                            "excerpt": ("…" if a else "") + t[a:a + 200].replace("\n", " ")})
        for o in out.values():
            o["matched_in"].sort(key=SOURCES.index)
        return out

    @app.get("/api/comment_groups")
    def comment_groups():
        return q("""SELECT g.*, (SELECT COUNT(*) FROM comments c WHERE c.group_id = g.group_id AND c.is_mirror = 0) comments
                    FROM comment_groups g ORDER BY g.channel""")

    @app.get("/api/comments")
    def search_comments(request: Request):
        params = qparams(request)
        join, where, args = comment_sql(params)
        limit = min(int(params.get("limit") or 50), 500)
        offset = int(params.get("offset") or 0)
        order = {"oldest": "c.date ASC", "newest": "c.date DESC"}.get(params.get("sort"), "c.date DESC")
        try:
            total = q1(f"SELECT COUNT(*) n FROM comments c {join} WHERE {where}", args)["n"]
            rows = q(f"{COMMENT_SELECT} {join} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                     (*args, limit, offset))
        except Exception as e:
            raise HTTPException(400, f"Search error: {e}")
        facets = q(f"""SELECT c.channel, COUNT(*) n FROM comments c {join} WHERE {where}
                       GROUP BY c.channel ORDER BY n DESC LIMIT 15""", args)
        timeline = q(f"""SELECT substr(c.date,1,10) d, COUNT(*) n FROM comments c {join} WHERE {where}
                         GROUP BY d ORDER BY d""", args)
        sub, sargs = subquery(params, "comments")
        run_id = log_search("comments", params)
        results = cases.annotate(ctx, comment_cards(rows), params.get("case"), "comment")
        hits = comment_hits(params, [r["id"] for r in results])
        for r in results:
            r.update(hits[r["id"]])
        avail, used = comment_sources(params)
        return {"total": total, "offset": offset,
                "results": results, "facets": facets, "sources": {"available": avail, "used": used},
                "timeline": timeline, "entities": entities.facet(ctx, "comment", sub, sargs),
                "places": places.facet(ctx, "comment", sub, sargs, unambiguous=params.get("unambiguous") == "1"), "places_note": places.LABEL, "search_run": run_id,
                "terms": lexicon.highlight_terms(params["_exp"], params.get("q")), "expansion": params["_exp"]}

    # A thread is keyed by the group message it hangs off: the channel post's mirror, or a plain group
    # message that got replies. Replies carry thread_top_id; the root itself has none.
    THREAD_KEY = "COALESCE(c.thread_top_id, c.msg_id)"

    def thread_head(group_id, top):
        root = q(f"{COMMENT_SELECT} WHERE c.group_id = ? AND c.msg_id = ?", (group_id, top))
        root = comment_cards(root)[0] if root else None
        post = None
        if root and root["is_mirror"]:
            post = q1("""SELECT p.id, p.url, p.date, p.text FROM posts p JOIN comments m
                         ON p.channel = m.channel AND p.msg_id = m.channel_post
                         WHERE m.group_id = ? AND m.msg_id = ?""", (group_id, top))
        return root, post

    @app.get("/api/comment_threads")
    def comment_threads(request: Request):
        params = qparams(request)
        join, where, args, order = thread_query(params)
        limit = min(int(params.get("limit") or 20), 100)
        offset = int(params.get("offset") or 0)
        try:
            total = q1(f"""SELECT COUNT(*) n FROM (SELECT 1 FROM comments c {join} WHERE {where}
                           GROUP BY c.group_id, {THREAD_KEY})""", args)["n"]
            rows = q(f"""SELECT c.group_id, {THREAD_KEY} top, MAX(c.channel) channel, COUNT(*) matching,
                                MIN(c.date) first, MAX(c.date) last, GROUP_CONCAT(c.id) ids
                         FROM comments c {join} WHERE {where} GROUP BY c.group_id, top
                         ORDER BY {order} LIMIT ? OFFSET ?""", (*args, limit, offset))
        except Exception as e:
            raise HTTPException(400, f"Search error: {e}")
        threads = []
        for r in rows:
            n = q1(f"""SELECT COUNT(*) n, SUM(c.deleted_detected IS NOT NULL) deleted, MAX(c.date) last
                       FROM comments c WHERE c.group_id = ? AND {THREAD_KEY} = ? AND c.is_mirror = 0
                       AND c.msg_id != ? AND c.media_types != '["service"]'""", (r["group_id"], r["top"], r["top"]))
            root, post = thread_head(r["group_id"], r["top"])
            threads.append({**r, "ids": [int(x) for x in (r["ids"] or "").split(",") if x],
                            "comments": n["n"], "deleted": n["deleted"] or 0, "last_comment": n["last"],
                            "root": root, "post": post})
        return {"total": total, "offset": offset, "threads": threads, "expansion": params["_exp"],
                "search_run": log_search("threads", params)}

    @app.get("/api/comment_threads/{group_id}/{top}")
    def comment_thread(group_id: int, top: int, case: str = ""):
        root, post = thread_head(group_id, top)
        rows = q(f"""{COMMENT_SELECT} WHERE c.group_id = ? AND {THREAD_KEY} = ? AND c.msg_id != ?
                     AND c.media_types != '["service"]' ORDER BY c.date, c.msg_id""", (group_id, top, top))
        cards = comment_cards(rows)
        if root:
            cards.insert(0, root)
        cards = cases.annotate(ctx, cards, case or None, "comment")
        head = cards.pop(0) if root else None
        g = q1("SELECT channel, title FROM comment_groups WHERE group_id = ?", (group_id,))
        return {"group_id": group_id, "top": top, "channel": (g or {}).get("channel") or (head or {}).get("channel"),
                "group_title": (g or {}).get("title"), "root": head, "post": post, "comments": cards}

    @app.get("/api/comments/export.csv")
    def export_comments(request: Request):
        params = qparams(request)
        join, where, args = comment_sql(params)
        rows = comment_cards(q(f"{COMMENT_SELECT} {join} WHERE {where} ORDER BY c.date LIMIT 200000", args))
        buf = io.StringIO()
        cols = ["channel", "post_msg_id", "post_url", "msg_id", "date", "edit_date", "text", "sender_type",
                "sender_id", "sender_username", "sender_name", "sender_phone", "admin_role", "admin_rank",
                "post_author", "reply_to_msg_id", "fwd_type", "fwd_id", "fwd_username", "fwd_name", "fwd_post",
                "media_types", "media_hashes", "deleted_detected", "last_seen", "versions", "first_captured",
                "last_captured", "capture_count"]
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(db.csv_row({**r, "media_types": ",".join(r["media_types"]),
                        "media_hashes": " ".join(m["sha256"] for m in r["media"]),
                        "post_url": f"https://t.me/{r['channel']}/{r['post_msg_id']}" if r["post_msg_id"] else ""}))
        exp = params["_exp"]
        run_id = log_search("comments", params, "export", [r["id"] for r in rows], len(rows))
        params = public(params)
        write(lambda: (db.audit(con, actor(), "export_comments_csv", None, {"params": params, "rows": len(rows)}),
                       db.audit(con, actor(), "search_export", None, {"q": params.get("q"), "fts": exp["fts"],
                                                                      "dict_version": exp["dict_version"],
                                                                      "expand": exp["enabled"], "rows": len(rows),
                                                                      "scope": "comments", "search_run": run_id})))
        name = "comments-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".csv"
        return StreamingResponse(iter(["\ufeff" + buf.getvalue()]), media_type="text/csv",
                                 headers=export_headers(name, {**params, "_exp": exp}))

    @app.get("/api/comments/{cid}")
    def comment(cid: int, case: str = ""):
        rows = q(f"{COMMENT_SELECT} WHERE c.id = ?", (cid,))
        if not rows:
            raise HTTPException(404)
        c = comment_cards(rows)[0]
        raw = rows[0]
        c["versions"] = q("SELECT * FROM comment_versions WHERE comment_id = ? ORDER BY id", (cid,))
        c["media"] = q("SELECT *, CASE kind WHEN 'photo' THEN 'photo' ELSE 'full' END kind FROM comment_media "
                       "WHERE comment_id = ? ORDER BY idx", (cid,))
        c["raw_capture"] = q1("SELECT url, sha256, path, bytes, captured_at, tool, account FROM captures "
                              "WHERE kind = 'api_dump' AND sha256 = ? ORDER BY captured_at LIMIT 1",
                              (raw["raw_sha256"],)) if raw["raw_sha256"] else None
        c["parent"] = None
        if raw["reply_to_msg_id"]:
            p = q(f"{COMMENT_SELECT} WHERE c.group_id = ? AND c.msg_id = ?", (raw["group_id"], raw["reply_to_msg_id"]))
            c["parent"] = comment_cards(p)[0] if p else None
        c["post"] = q1("SELECT id, url, date, text FROM posts WHERE channel = ? AND msg_id = ?",
                       (raw["channel"], c["post_msg_id"])) if c["post_msg_id"] else None
        c["replies"] = comment_cards(q(f"{COMMENT_SELECT} WHERE c.group_id = ? AND c.reply_to_msg_id = ? "
                                       f"ORDER BY c.date LIMIT 50", (raw["group_id"], raw["msg_id"])))
        if case.isdigit():      # ?case=<id>: the case's own review status, tags and note count
            cases.annotate(ctx, [c], case, "comment")
        return c

    @app.get("/api/tg_users/{uid}")
    def tg_user(uid: int):
        u = q1("SELECT * FROM tg_users WHERE user_id = ?", (uid,))
        if not u:
            raise HTTPException(404)
        u["snapshots"] = q("SELECT * FROM tg_user_snapshots WHERE user_id = ? ORDER BY id", (uid,))
        u["roles"] = q("""SELECT a.*, g.channel, g.title FROM group_admins a
                          LEFT JOIN comment_groups g ON g.group_id = a.group_id WHERE a.user_id = ?""", (uid,))
        u["groups"] = q("""SELECT c.channel, COUNT(*) n, MIN(c.date) first, MAX(c.date) last,
                             SUM(c.deleted_detected IS NOT NULL) deleted, SUM(c.fwd_type IS NOT NULL) forwards
                           FROM comments c WHERE c.sender_type = 'user' AND c.sender_id = ?
                           GROUP BY c.channel ORDER BY n DESC""", (uid,))
        return u

    # ---- cross-section overlap and community overview ------------------------------

    @app.get("/api/overlap")
    def overlap(case: str = ""):
        return community.overlap(con, lock, int(case) if case.isdigit() else None)

    @app.get("/api/overlap/export.csv")
    def export_overlap():
        o = community.overlap(con, lock)
        buf = io.StringIO()
        cols = ["user_id", "username", "name", "phone", "phone_country", "bot", "sections", "channel",
                "comments", "deleted", "first", "last", "admin_of"]
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        for a in o["accounts"]:
            admin_of = "; ".join(f"{r['channel']} ({r['role']}{': ' + r['rank'] if r['rank'] else ''})"
                                 for r in a["admin_of"])
            for s in a["sections"]:
                w.writerow(db.csv_row({"user_id": a["user_id"], "username": a["username"] or "", "name": a["name"] or "",
                            "phone": a["phone"] or "", "phone_country": a["phone_country"] or "",
                            "bot": a["bot"] or 0, "sections": len(a["sections"]), "channel": s["channel"],
                            "comments": s["n"], "deleted": s["deleted"], "first": s["first"], "last": s["last"],
                            "admin_of": admin_of}))
        write(lambda: db.audit(con, actor(), "export_overlap_csv", None, {"accounts": len(o["accounts"])}))
        name = "overlap-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".csv"
        return StreamingResponse(iter(["﻿" + buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/community")
    def community_overview(channel: str = "", case: str = ""):
        return community.community(con, channel or None, lock, int(case) if case.isdigit() else None)

    # ---- schedules ----------------------------------------------------------------

    @app.get("/api/schedules")
    def schedules():
        rows = q("""SELECT s.*, j.status last_status, j.summary last_summary FROM schedules s
                    LEFT JOIN jobs j ON j.id = s.last_job ORDER BY s.channel""")
        for r in rows:
            r["scheduler_error"] = runner.scheduler_error
        return rows

    @app.post("/api/schedules")
    async def save_schedule(request: Request):
        b = await request.json()
        from tgspider import normalize_seed

        def do():
            if b.get("id") and b.get("delete"):
                con.execute("DELETE FROM schedules WHERE id = ?", (b["id"],))
                db.audit(con, actor(), "schedule_delete", f"schedule {b['id']}")
                return
            if b.get("id"):
                row = con.execute("SELECT * FROM schedules WHERE id = ?", (b["id"],)).fetchone()
                if not row:
                    raise HTTPException(404)
                interval = max(1, int(b.get("interval_min", row["interval_min"])))
                media = b.get("media", row["media"])
                enabled = int(bool(b.get("enabled", row["enabled"])))
                con.execute("UPDATE schedules SET interval_min = ?, media = ?, enabled = ?, "
                            "next_run = CASE WHEN ? = 1 AND enabled = 0 THEN NULL ELSE next_run END WHERE id = ?",
                            (interval, media if media in ("none", "photos", "all") else "none", enabled,
                             enabled, b["id"]))
                db.audit(con, actor(), "schedule_edit", f"channel {row['channel']}",
                         {"interval_min": interval, "media": media, "enabled": enabled})
                return
            channel = normalize_seed(b.get("channel") or "")
            if not channel:
                raise HTTPException(400, "Enter a channel username.")
            interval = max(1, int(b.get("interval_min") or 10))
            media = b.get("media") if b.get("media") in ("none", "photos", "all") else "none"
            enabled = int(bool(b.get("enabled", True)))
            con.execute("""INSERT INTO schedules(channel, interval_min, media, enabled, created) VALUES (?,?,?,?,?)
                           ON CONFLICT(channel) DO UPDATE SET interval_min = excluded.interval_min,
                             media = excluded.media, enabled = excluded.enabled""",
                        (channel, interval, media, enabled, db.now()))
            db.audit(con, actor(), "schedule_set", f"channel {channel}",
                     {"interval_min": interval, "media": media, "enabled": enabled})
        write(do)
        return schedules()

    @app.post("/api/schedules/{sid}/run")
    def run_schedule(sid: int):
        if not q1("SELECT 1 FROM schedules WHERE id = ?", (sid,)):
            raise HTTPException(404)
        write(lambda: db.audit(con, actor(), "schedule_run_now", f"schedule {sid}"))
        return {"queued": runner.run_due_schedules(only_id=sid)}

    for module in (cases, watch, alerts, incidents, discovery, analysis, lexicon, searchlog, entities, places,
                   terms, savedsearch, mediatext):
        module.register(app, ctx)

    return app
