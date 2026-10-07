"""
Places mentioned in text: gazetteer names found in posts, comments and OCR/speech text.

Everything here is "places mentioned in text (not verified locations)": a name appearing in a post says the
post talks about it, not that anything happened there. Names shared by several places (Kirkuk, Nyala...) are
kept with all their candidates and marked ambiguous.

Tagging is dictionary-driven only (lexicon.find_places, the same gazetteer as multilingual search). Without
dictionary.db nothing is tagged and the endpoints say so. Table `item_places` is derived data: written by
db.INDEX_HOOKS for new items and by POST /api/places/backfill for existing ones.

Search integration (server.py): place=<entry_id>[,<entry_id>...] (ORed), near=lat,lon,km (or entry_id,km),
unambiguous=1; the search response lists `places` (top places among the results).
"""

import math

from fastapi import HTTPException, Request

from . import db, entities, lexicon

LABEL = "Places mentioned in text (not verified locations)"
# Carried with exports/packages that used place or dictionary-derived data (the dictionary is built from these).
ATTRIBUTION = ("Place names and multilingual search terms come from the Investigator dictionary, built from "
               "OpenStreetMap data (c) OpenStreetMap contributors, licensed ODbL 1.0 "
               "(https://www.openstreetmap.org/copyright), and GeoNames, CC BY 4.0 (https://www.geonames.org). "
               "The dictionary database is itself available under ODbL 1.0.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_places (
    id         INTEGER PRIMARY KEY,
    item_type  TEXT NOT NULL,        -- post / comment / media_text
    item_id    INTEGER NOT NULL,
    entry_id   TEXT NOT NULL,        -- dictionary entry id
    name       TEXT,                 -- the words as written (normalised)
    start_tok  INTEGER,
    candidates INTEGER DEFAULT 1,    -- >1: the same name belongs to several places (ambiguous)
    created    TEXT
);
CREATE INDEX IF NOT EXISTS item_places_entry ON item_places(entry_id);
CREATE INDEX IF NOT EXISTS item_places_item ON item_places(item_type, item_id);
"""

_cache = {}          # entry_id -> place dict | None (cleared when the dictionary or edits change)
_cache_sig = [None]


def available():
    return hasattr(lexicon, "find_places") and lexicon.get_dict() is not None


def place(entry_id):
    """lexicon.place() with a small cache; None when unknown."""
    sig = (getattr(lexicon.get_dict(), "path", None), id(lexicon.get_dict()))
    if _cache_sig[0] != sig:
        _cache.clear()
        _cache_sig[0] = sig
    if entry_id not in _cache:
        try:
            _cache[entry_id] = lexicon.place(entry_id) if hasattr(lexicon, "place") else None
        except Exception:
            _cache[entry_id] = None
        if len(_cache) > 50000:
            _cache.clear()
    return _cache[entry_id]


def find(text, con=None):
    """[{"entry_id", "name", "start_tok", "end_tok", "candidates"}] for raw text; [] without the gazetteer."""
    if not text or not hasattr(lexicon, "find_places"):
        return []
    return lexicon.find_places(db.normalize(text), con)


def store(con, item_type, item_id, text):
    """(Re)write one item's place tags. Idempotent. Caller commits."""
    con.execute("DELETE FROM item_places WHERE item_type = ? AND item_id = ?", (item_type, item_id))
    n = 0
    seen = set()
    for f in find(text, con):
        key = (f["entry_id"], f["start_tok"])
        if key in seen:
            continue
        seen.add(key)
        con.execute("INSERT INTO item_places(item_type, item_id, entry_id, name, start_tok, candidates, created) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (item_type, item_id, f["entry_id"], f["name"], f["start_tok"], f.get("candidates") or 1, db.now()))
        n += 1
    return n


def hook(con, item_type, item_id, text):
    entities.ensure(con, SCHEMA)
    store(con, item_type, item_id, text)


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _place_km(entry_id, lat, lon):
    p = place(entry_id)
    if not p or p.get("lat") is None or p.get("lon") is None:
        return None
    return haversine(lat, lon, p["lat"], p["lon"])


def parse_near(params):
    """near=lat,lon,km or near=entry_id,km -> (lat, lon, km) or None."""
    parts = [x.strip() for x in str(params.get("near") or "").split(",") if x.strip()]
    usage = "near= takes lat,lon,km or <place id>,km"
    try:
        if len(parts) == 3:
            lat, lon, km = (float(x) for x in parts)
        elif len(parts) == 2:
            p = place(parts[0])
            if not p or p.get("lat") is None:
                raise ValueError(f"{parts[0]!r} is not a place with coordinates")
            lat, lon, km = p["lat"], p["lon"], float(parts[1])
        else:
            raise ValueError(usage)
    except ValueError as e:
        raise ValueError(str(e) if "place" in str(e) or str(e) == usage else usage) from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < km <= 20000):
        raise ValueError("near=: latitude -90..90, longitude -180..180, radius 0..20000 km")
    return lat, lon, km


def parse_places(params):
    return [x.strip() for x in str(params.get("place") or "").replace("\n", ",").split(",") if x.strip()]


def filter_sql(con, params, item_type, idcol):
    """(clauses, args) for place= / near= / unambiguous=1 on posts (idcol p.id) or comments (c.id)."""
    clauses, args = [], []
    amb = " AND candidates = 1" if params.get("unambiguous") == "1" else ""
    src = (f"SELECT post_id, entry_id FROM ({entities.post_source(con, 'item_places', 'entry_id, candidates')})"
           f" WHERE 1{amb}") if item_type == "post" else \
          f"SELECT comment_id post_id, entry_id FROM ({entities.comment_source(con, 'item_places', 'entry_id, candidates')})" \
          f" WHERE 1{amb}"
    ids = parse_places(params)
    if ids:
        clauses.append(f"{idcol} IN (SELECT post_id FROM ({src}) WHERE entry_id IN ({','.join('?' * len(ids))}))")
        args += ids
    if params.get("near"):
        near = parse_near(params)
        if not near:
            clauses.append("0")                       # unreadable near= matches nothing, rather than everything
        else:
            clauses.append(f"{idcol} IN (SELECT post_id FROM ({src}) WHERE place_km(entry_id, ?, ?) <= ?)")
            args += list(near)
    return clauses, args


def _decorate(rows):
    if not available():
        return []                                     # tags stay in the table, but there is nothing to label them with
    out = []
    for r in rows:
        p = place(r["entry_id"]) or {}
        out.append({"entry_id": r["entry_id"], "label": p.get("label") or r.get("name"),
                    "country": p.get("country"), "category": p.get("category"), "admin1": p.get("admin1"),
                    "lat": p.get("lat"), "lon": p.get("lon"), "n": r["n"], "ambiguous_n": r["ambiguous_n"]})
    return out


def facet(ctx, item_type, sub_sql, sub_args, limit=15, unambiguous=False):
    """Top places mentioned among the results. n = results mentioning it; ambiguous_n = of those, results where the
    name could also be another place."""
    if item_type == "post":
        src = f"SELECT post_id item, entry_id, candidates FROM ({entities.post_source(ctx.con, 'item_places', 'entry_id, candidates')})"
    else:
        src = f"SELECT comment_id item, entry_id, candidates FROM ({entities.comment_source(ctx.con, 'item_places', 'entry_id, candidates')})"
    rows = ctx.q(f"""SELECT entry_id, COUNT(DISTINCT item) n,
                       COUNT(DISTINCT CASE WHEN candidates > 1 THEN item END) ambiguous_n
                     FROM ({src}) WHERE item IN ({sub_sql}) {"AND candidates = 1" if unambiguous else ""}
                     GROUP BY entry_id ORDER BY n DESC, entry_id LIMIT ?""",
                 (*sub_args, limit))
    return _decorate(rows)


def register(app, ctx):
    q, q1, write, con, actor = ctx.q, ctx.q1, ctx.write, ctx.con, ctx.actor
    con.executescript(SCHEMA)
    con.create_function("place_km", 3, _place_km, deterministic=True)
    if hasattr(db, "INDEX_HOOKS") and hook not in db.INDEX_HOOKS:
        db.INDEX_HOOKS.append(hook)
    state = {"running": False}

    def unavailable():
        return None if available() else ("No place dictionary is loaded (data/gazetteer/dictionary.db missing, or "
                                         "lexicon.find_places not available): no places are tagged.")

    @app.get("/api/places/status")
    def status():
        return {"label": LABEL, "available": available(), "message": unavailable(), "running": state["running"],
                "tagged_items": q1("SELECT COUNT(DISTINCT item_type || ':' || item_id) n FROM item_places")["n"],
                "mentions": q1("SELECT COUNT(*) n FROM item_places")["n"],
                "backfill": entities._setting(q1, "places_backfill"),
                "hook_registered": hook in getattr(db, "INDEX_HOOKS", [])}

    @app.post("/api/places/backfill")
    def backfill():
        """Tag every post, comment (not mirrors) and OCR/speech text from the dictionary. Idempotent (each item's
        tags are replaced). Synchronous, in batches."""
        import json
        if not available():
            return {"available": False, "message": unavailable(), "tagged": 0}
        if state["running"]:
            raise HTTPException(409, "A backfill is already running.")
        state["running"] = True
        try:
            sources = [("post", "SELECT id, text FROM posts WHERE text IS NOT NULL AND text != ''", "posts", "posts"),
                       ("comment", "SELECT id, text FROM comments WHERE is_mirror = 0 AND text IS NOT NULL AND text != ''",
                        "comments", "comments")]
            if entities.has_table(con, "media_text"):
                sources.append(("media_text", "SELECT id, text FROM media_text WHERE text IS NOT NULL AND text != ''",
                                "media_text", "media_text"))
            scanned, mentions, items = {}, {}, {}
            for item_type, sql, label, tbl in sources:
                scanned[label], mentions[label], items[label] = 0, 0, 0
                after = 0
                while True:
                    rows = q(sql + " AND id > ? ORDER BY id LIMIT 500", (after,))
                    if not rows:
                        break
                    after = rows[-1]["id"]

                    def one(rows=rows, item_type=item_type):
                        return [store(con, item_type, r["id"], r["text"]) for r in rows]
                    got = write(one)
                    scanned[label] += len(rows)
                    mentions[label] += sum(got)
                    items[label] += sum(1 for n in got if n)
                write(lambda t=item_type, tb=tbl: con.execute(
                    f"DELETE FROM item_places WHERE item_type = ? AND item_id NOT IN (SELECT id FROM {tb})", (t,)))
            summary = {"available": True, "items_scanned": scanned, "items_with_places": items,
                       "place_mentions": mentions, "at": db.now()}
            write(lambda: (db.audit(con, actor(), "places_backfill", None, summary),
                           con.execute("INSERT INTO settings(key, value) VALUES ('places_backfill', ?) "
                                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(summary),))))
            return summary
        finally:
            state["running"] = False

    @app.get("/api/places")
    def list_places(case: str = "", limit: int = 100, offset: int = 0):
        """Places mentioned across the workspace (or a case's channels), with counts."""
        cc = ""
        args = []
        if case.isdigit():
            cc = ("WHERE COALESCE(p.channel, c.channel) IN (SELECT channel FROM case_channels WHERE case_id = ?)")
            args = [int(case)]
        rows = q(f"""SELECT ip.entry_id, COUNT(DISTINCT ip.item_type || ':' || ip.item_id) n,
                       COUNT(DISTINCT CASE WHEN ip.candidates > 1 THEN ip.item_type || ':' || ip.item_id END) ambiguous_n
                     FROM item_places ip LEFT JOIN posts p ON ip.item_type = 'post' AND p.id = ip.item_id
                     LEFT JOIN comments c ON ip.item_type = 'comment' AND c.id = ip.item_id {cc}
                     GROUP BY ip.entry_id ORDER BY n DESC, ip.entry_id LIMIT ? OFFSET ?""", (*args, min(limit, 500), offset))
        return {"label": LABEL, "available": available(), "message": unavailable(), "places": _decorate(rows)}

    @app.get("/api/places/map")
    def places_map(request: Request):
        """GeoJSON points for places mentioned in the search results (same params as /api/search, plus scope=comments).
        properties: entry_id, label, country, category, admin1, n (results mentioning it), ambiguous_n."""
        params = ctx.qparams(request)
        scope = "comments" if params.get("scope") == "comments" else "posts"
        sub, args = ctx.subquery(params, scope)
        feats = []
        for p in facet(ctx, "comment" if scope == "comments" else "post", sub, args, limit=min(int(params.get("top") or 300), 1000),
                          unambiguous=params.get("unambiguous") == "1"):
            if p["lat"] is None or p["lon"] is None:
                continue
            feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                          "properties": {k: p[k] for k in ("entry_id", "label", "country", "category", "admin1", "n", "ambiguous_n")}})
        return {"type": "FeatureCollection", "features": feats, "label": LABEL, "available": available(),
                "message": unavailable()}
