"""
Analysis over collected data (no network, no models):

  coordination   the same text, the same file (SHA-256) or the same forwarded original
                 showing up in several channels, and which channel pairs repeatedly post
                 the same thing within a short time of each other
  spread         everywhere one post's content went: identical files, the same forwarded
                 original, identical text, in posts and comments, in time order
  coordinates    latitude/longitude written in posts and comments (decimal, degrees-minutes-
                 seconds, map links), plus incidents with a location, as GeoJSON

Text matching is exact after normalising (case, diacritics, Arabic letter variants, links,
emoji and punctuation removed), so light edits of the same message still match. It won't
catch paraphrases. Files match only when byte-identical: a re-encoded copy of a video has a
different hash.
"""

import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from . import db

URL_RE = re.compile(r"https?://\S+|t\.me/\S+|@\w+")


def text_key(text, min_len=40):
    """Stable key for 'the same message', or None if too short to mean anything."""
    norm = db.normalize(URL_RE.sub(" ", text or ""))
    norm = re.sub(r"[^\w\s]", " ", norm)
    norm = " ".join(norm.split())
    if len(norm) < min_len:
        return None
    return hashlib.sha1(norm.encode()).hexdigest()[:20]


def _dt(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _scope(case_id, col):
    return f" AND {col} IN (SELECT channel FROM case_channels WHERE case_id = {int(case_id)})" if case_id else ""


def coordination(ctx, case_id=None, days=90, window_min=60, min_channels=2, limit=200):
    """Groups of identical content across channels, and channel pairs that post the same things close together."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    q = ctx.q
    groups = []

    # 1. identical text (original posts, not forwards: forwards are covered by 3)
    by_text = defaultdict(list)
    for p in q(f"""SELECT id, channel, date, url, text FROM posts p WHERE date >= ? AND fwd_username IS NULL
                   AND fwd_name IS NULL AND length(text) >= 40 {_scope(case_id, 'p.channel')}""", (since,)):
        k = text_key(p["text"])
        if k:
            by_text[k].append(p)
    for k, items in by_text.items():
        if len({i["channel"] for i in items}) >= min_channels:
            groups.append({"kind": "same_text", "key": k, "sample": items[0]["text"][:300], "items": items})

    # 2. byte-identical files
    by_sha = defaultdict(list)
    for m in q(f"""SELECT m.sha256, m.kind, p.id, p.channel, p.date, p.url FROM media m JOIN posts p ON p.id = m.post_id
                   WHERE p.date >= ? {_scope(case_id, 'p.channel')}""", (since,)):
        by_sha[m["sha256"]].append(m)
    for sha, items in by_sha.items():
        uniq = {i["id"]: i for i in items}
        if len({i["channel"] for i in uniq.values()}) >= min_channels:
            groups.append({"kind": "same_file", "key": sha, "sample": f"{items[0]['kind']} {sha[:16]}…",
                           "items": list(uniq.values())})

    # 2b. the same picture/video re-encoded (perceptual hash), where hashes exist
    sha_posts = defaultdict(list)
    for m in q(f"""SELECT m.sha256, p.id, p.channel, p.date, p.url FROM media m JOIN posts p ON p.id = m.post_id
                   WHERE p.date >= ? {_scope(case_id, 'p.channel')}""", (since,)):
        sha_posts[m["sha256"]].append(m)
    pairs_ph = similar_pairs(ctx, list(sha_posts))
    done = set()
    for sha, others in pairs_ph.items():
        if sha in done:
            continue
        cluster = {sha} | {o for o in others if o in sha_posts}
        done |= cluster
        if len(cluster) < 2:
            continue
        items = {i["id"]: i for s in cluster for i in sha_posts[s]}
        if len({i["channel"] for i in items.values()}) >= min_channels:
            groups.append({"kind": "similar_file", "key": sha, "sample": f"{len(cluster)} versions of one file",
                           "items": list(items.values())})

    # 3. the same forwarded original
    by_fwd = defaultdict(list)
    for p in q(f"""SELECT id, channel, date, url, fwd_url, fwd_username FROM posts p WHERE date >= ? AND fwd_url IS NOT NULL
                   {_scope(case_id, 'p.channel')}""", (since,)):
        by_fwd[p["fwd_url"]].append(p)
    for u, items in by_fwd.items():
        if len({i["channel"] for i in items}) >= min_channels:
            groups.append({"kind": "same_forward", "key": u, "sample": f"forwarded from {u}", "items": items})

    # timing: order each group, note who was first and how fast it spread
    pairs = defaultdict(lambda: {"count": 0, "kinds": defaultdict(int), "examples": []})
    for g in groups:
        g["items"].sort(key=lambda i: i["date"] or "")
        g["items"] = [{k: i[k] for k in ("id", "channel", "date", "url")} for i in g["items"]]
        first = g["items"][0]
        g["first"] = first
        g["channels"] = sorted({i["channel"] for i in g["items"]})
        t0 = _dt(first["date"])
        tl = _dt(g["items"][-1]["date"])
        g["spread_minutes"] = int((tl - t0).total_seconds() / 60) if t0 and tl else None
        # channel pairs posting it within the window of each other
        seen_pairs = set()
        for i, a in enumerate(g["items"]):
            ta = _dt(a["date"])
            for b in g["items"][i + 1:]:
                tb = _dt(b["date"])
                if not (ta and tb) or a["channel"] == b["channel"]:
                    continue
                if abs((tb - ta).total_seconds()) <= window_min * 60:
                    pair = tuple(sorted((a["channel"], b["channel"])))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    pr = pairs[pair]
                    pr["count"] += 1
                    pr["kinds"][g["kind"]] += 1
                    if len(pr["examples"]) < 5:
                        pr["examples"].append({"kind": g["kind"], "a": a, "b": b,
                                               "gap_minutes": int(abs((tb - ta).total_seconds()) / 60)})
    groups.sort(key=lambda g: (-len(g["channels"]), -len(g["items"])))
    pair_list = sorted(({"channels": list(k), "count": v["count"], "kinds": dict(v["kinds"]), "examples": v["examples"]}
                        for k, v in pairs.items()), key=lambda p: -p["count"])
    return {"params": {"days": days, "window_min": window_min, "min_channels": min_channels, "case": case_id},
            "counts": {k: sum(1 for g in groups if g["kind"] == k)
                       for k in ("same_text", "same_file", "similar_file", "same_forward")},
            "groups": groups[:limit], "pairs": pair_list[:limit]}


def spread(ctx, post_id):
    """Everywhere this post's content appears, oldest first."""
    q, q1 = ctx.q, ctx.q1
    p = q1("SELECT * FROM posts WHERE id = ?", (post_id,))
    if not p:
        raise HTTPException(404, "No such post")
    hits = {}

    def add(rows, how, item_type="post"):
        for r in rows:
            key = (item_type, r["id"])
            h = hits.setdefault(key, {**r, "item_type": item_type, "how": set()})
            h["how"].add(how)

    shas = [m["sha256"] for m in q("SELECT sha256 FROM media WHERE post_id = ?", (post_id,))]
    if shas:
        marks = ",".join("?" * len(shas))
        add(q(f"""SELECT DISTINCT p.id, p.channel, p.date, p.url, substr(p.text, 1, 200) text FROM media m
                  JOIN posts p ON p.id = m.post_id WHERE m.sha256 IN ({marks})""", shas), "same file")
        add(q(f"""SELECT DISTINCT c.id, c.channel, c.date, NULL url, substr(c.text, 1, 200) text FROM comment_media m
                  JOIN comments c ON c.id = m.comment_id WHERE m.sha256 IN ({marks})""", shas), "same file", "comment")
    if shas:
        near = {o for s, others in similar_pairs(ctx, shas).items() for o in others} - set(shas)
        if near:
            marks = ",".join("?" * len(near))
            add(q(f"""SELECT DISTINCT p.id, p.channel, p.date, p.url, substr(p.text, 1, 200) text FROM media m
                      JOIN posts p ON p.id = m.post_id WHERE m.sha256 IN ({marks})""", list(near)), "similar file")
            add(q(f"""SELECT DISTINCT c.id, c.channel, c.date, NULL url, substr(c.text, 1, 200) text FROM comment_media m
                      JOIN comments c ON c.id = m.comment_id WHERE m.sha256 IN ({marks})""", list(near)),
                "similar file", "comment")
    origin = p["fwd_url"] or p["url"]
    add(q("SELECT id, channel, date, url, substr(text, 1, 200) text FROM posts WHERE fwd_url = ? OR url = ?",
          (origin, origin)), "same forwarded original")
    k = text_key(p["text"])
    if k:
        words = [w for w in db.normalize(p["text"]).split() if len(w) > 3][:6]
        expr = db.fts_text(db.fts_query(" ".join(f'"{w}"' for w in words))) if words else None
        cands = q("SELECT p.id, p.channel, p.date, p.url, p.text FROM posts p JOIN posts_fts f ON f.rowid = p.id "
                  "WHERE posts_fts MATCH ? LIMIT 2000", (expr,)) if expr else []
        add([{**c, "text": c["text"][:200]} for c in cands if text_key(c["text"]) == k], "same text")
        ccands = q("SELECT c.id, c.channel, c.date, NULL url, c.text FROM comments c JOIN comments_fts f ON f.rowid = c.id "
                   "WHERE comments_fts MATCH ? LIMIT 2000", (expr,)) if expr else []
        add([{**c, "text": (c["text"] or "")[:200]} for c in ccands if text_key(c["text"]) == k], "same text", "comment")
    items = sorted(hits.values(), key=lambda h: h["date"] or "")
    for h in items:
        h["is_this"] = h["item_type"] == "post" and h["id"] == post_id
        h["how"] = ["this post"] if h["is_this"] else sorted(h["how"])
    return {"post": {"id": p["id"], "channel": p["channel"], "date": p["date"], "url": p["url"]},
            "channels": sorted({h["channel"] for h in items}), "items": items,
            "first": items[0] if items else None}


# ---- coordinates in text --------------------------------------------------------

DEC = r"([-+]?\d{1,2}\.\d{3,})"
DEC_LON = r"([-+]?\d{1,3}\.\d{3,})"
PATTERNS = [
    # map links: google (@lat,lon / q=lat,lon / ll=), apple, osm (mlat/mlon or #map=z/lat/lon), yandex (ll=lon,lat)
    ("map_link", re.compile(r"(?:@|[?&](?:q|ll|query|daddr|center)=)" + DEC + r"\s*,\s*" + DEC_LON)),
    ("map_link", re.compile(r"mlat=" + DEC + r"&mlon=" + DEC_LON)),
    ("map_link", re.compile(r"#map=\d+/" + DEC + "/" + DEC_LON)),
    ("yandex", re.compile(r"yandex\.\w+/maps\S*?[?&]ll=" + DEC_LON + r"(?:%2C|,)" + DEC)),
    # 12.3456, 34.5678   or   12.3456N 34.5678E
    ("decimal", re.compile(r"(?<![\d.])" + DEC + r"\s*°?\s*([NS])?\s*[,;/ ]\s*" + DEC_LON + r"\s*°?\s*([EW])?(?![\d.])")),
    # 12°34'56"N 34°56'12"E  (also with ′ ″ and optional seconds)
    ("dms", re.compile(r"(\d{1,2})\s*°\s*(\d{1,2})\s*['′]\s*(?:(\d{1,2}(?:\.\d+)?)\s*[\"″]\s*)?([NS])[\s,]*"
                       r"(\d{1,3})\s*°\s*(\d{1,2})\s*['′]\s*(?:(\d{1,2}(?:\.\d+)?)\s*[\"″]\s*)?([EW])", re.I)),
    # MGRS grid reference (military posts often use these), recorded but not converted
    ("mgrs", re.compile(r"\b(\d{1,2}[C-HJ-NP-X])\s?([A-HJ-NP-Z]{2})\s?(\d{4,5})\s?(\d{4,5})\b")),
]


def find_coordinates(text):
    out = []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text or ""):
            g = m.groups()
            try:
                if kind == "yandex":
                    lat, lon = float(g[1]), float(g[0])
                elif kind in ("map_link",):
                    lat, lon = float(g[0]), float(g[1])
                elif kind == "decimal":
                    lat, lon = float(g[0]), float(g[2])
                    if (g[1] or "").upper() == "S":
                        lat = -abs(lat)
                    if (g[3] or "").upper() == "W":
                        lon = -abs(lon)
                elif kind == "dms":
                    lat = int(g[0]) + int(g[1]) / 60 + float(g[2] or 0) / 3600
                    lon = int(g[4]) + int(g[5]) / 60 + float(g[6] or 0) / 3600
                    lat = -lat if g[3].upper() == "S" else lat
                    lon = -lon if g[7].upper() == "W" else lon
                else:
                    out.append({"kind": kind, "text": m.group(0), "lat": None, "lon": None})
                    continue
            except (TypeError, ValueError):
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0):
                out.append({"kind": kind, "text": m.group(0), "lat": round(lat, 6), "lon": round(lon, 6)})
    # the same spot matched by two patterns (e.g. a link and its decimal) counts once
    uniq = {}
    for c in out:
        uniq.setdefault((c["lat"], c["lon"], c["text"] if c["lat"] is None else None), c)
    return list(uniq.values())


def coordinates(ctx, case_id=None, limit=2000):
    feats = []
    q = ctx.q
    like = "(text LIKE '%°%' OR text LIKE '%maps%' OR text LIKE '%map=%' OR text GLOB '*[0-9].[0-9][0-9][0-9]*')"
    for p in q(f"SELECT id, channel, date, url, text FROM posts p WHERE {like} {_scope(case_id, 'p.channel')} "
               f"ORDER BY date DESC LIMIT 20000"):
        for c in find_coordinates(p["text"]):
            feats.append(("post", p, c))
    for cm in q(f"SELECT id, channel, date, text FROM comments c WHERE is_mirror = 0 AND {like} "
                f"{_scope(case_id, 'c.channel')} ORDER BY date DESC LIMIT 20000"):
        for c in find_coordinates(cm["text"]):
            feats.append(("comment", {**cm, "url": None}, c))
    geo, unconverted = [], []
    for item_type, r, c in feats[:limit]:
        props = {"item_type": item_type, "id": r["id"], "channel": r["channel"], "date": r["date"], "url": r["url"],
                 "match": c["text"], "kind": c["kind"], "excerpt": (r["text"] or "")[:200]}
        if c["lat"] is None:
            unconverted.append(props)
        else:
            geo.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [c["lon"], c["lat"]]},
                        "properties": props})
    inc_where = f"AND case_id = {int(case_id)}" if case_id else ""
    for i in q(f"SELECT * FROM incidents WHERE lat IS NOT NULL AND lon IS NOT NULL {inc_where}"):
        geo.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [i["lon"], i["lat"]]},
                    "properties": {"item_type": "incident", "id": i["id"], "title": i["title"], "date": i["date_start"],
                                   "location": i["location"], "location_note": i["location_note"], "status": i["status"]}})
    return {"type": "FeatureCollection", "features": geo, "unconverted": unconverted,
            "note": "Coordinates as written in the text: a claim to check, not a verified location."}


# ---- perceptual hashes (mediascan) ------------------------------------------------

PHASH_MAX = 7          # bits out of 64; the band index below finds every pair up to 7 apart


def ingest_mediascan(con, run_dir, actor="system", job_id=None, log=print):
    stats = dict(scanned=0, with_phash=0, with_embedded=0, with_gps=0, errors=0)
    path = os.path.join(run_dir, "mediascan.jsonl")
    for line in open(path, encoding="utf-8") if os.path.exists(path) else []:
        if not line.strip():
            continue
        r = json.loads(line)
        emb = r.get("embedded") or {}
        gps = int(any(k.startswith("GPS") for k in emb) or "location" in json.dumps(emb.get("container", {})).lower())
        con.execute("""INSERT INTO media_scan(sha256, phashes, meta, embedded, has_gps, error, scanned_at, job_id)
                       VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(sha256) DO UPDATE SET phashes = excluded.phashes,
                       meta = excluded.meta, embedded = excluded.embedded, has_gps = excluded.has_gps,
                       error = excluded.error, scanned_at = excluded.scanned_at, job_id = excluded.job_id""",
                    (r["sha256"], json.dumps(r.get("phashes") or []), json.dumps(r.get("meta") or {}),
                     json.dumps(emb, ensure_ascii=False), gps, r.get("error"), r.get("scanned_at"), job_id))
        stats["scanned"] += 1
        stats["with_phash"] += bool(r.get("phashes"))
        stats["with_embedded"] += bool(emb)
        stats["with_gps"] += gps
        stats["errors"] += bool(r.get("error"))
    db.audit(con, actor, "import_mediascan", run_dir, stats)
    con.commit()
    log(f"Imported media scan {run_dir}: {stats}")
    return stats


def _bands(h):
    v = int(h, 16)
    return [(i, (v >> (8 * i)) & 0xFF) for i in range(8)]


def similar_pairs(ctx, shas=None, max_distance=PHASH_MAX):
    """{sha: {other_sha: distance}} for files whose perceptual hashes are within max_distance.
    shas limits which files to look for (their matches can be anywhere)."""
    rows = ctx.q("SELECT sha256, phashes FROM media_scan WHERE phashes IS NOT NULL AND phashes != '[]'")
    hashes = {r["sha256"]: json.loads(r["phashes"]) for r in rows}
    index = defaultdict(set)
    for sha, hs in hashes.items():
        for h in hs:
            for b in _bands(h):
                index[b].add(sha)
    out = defaultdict(dict)
    wanted = set(shas) if shas is not None else set(hashes)
    for sha in wanted & set(hashes):
        cands = set()
        for h in hashes[sha]:
            for b in _bands(h):
                cands |= index[b]
        cands.discard(sha)
        for o in cands:
            d = min(bin(int(a, 16) ^ int(b, 16)).count("1") for a in hashes[sha] for b in hashes[o])
            if d <= max_distance:
                out[sha][o] = d
    return out


def similar_media(ctx, sha, max_distance=PHASH_MAX):
    """Posts and comments carrying a file that looks the same as `sha` (re-encoded, resized...)."""
    q = ctx.q
    scan = ctx.q1("SELECT * FROM media_scan WHERE sha256 = ?", (sha,))
    if not scan:
        return {"sha256": sha, "scanned": False, "matches": []}
    pairs = similar_pairs(ctx, [sha], max_distance).get(sha, {})
    dist = {sha: 0, **pairs}
    shas = list(dist)
    marks = ",".join("?" * len(shas))
    items = q(f"""SELECT 'post' item_type, p.id, p.channel, p.date, p.url, m.sha256, m.kind FROM media m
                  JOIN posts p ON p.id = m.post_id WHERE m.sha256 IN ({marks})
                  UNION ALL
                  SELECT 'comment', c.id, c.channel, c.date, NULL, m.sha256, m.kind FROM comment_media m
                  JOIN comments c ON c.id = m.comment_id WHERE m.sha256 IN ({marks})""", (*shas, *shas))
    for i in items:
        i["distance"] = dist[i["sha256"]]
        i["identical"] = i["sha256"] == sha
    items.sort(key=lambda i: i["date"] or "")
    return {"sha256": sha, "scanned": True, "meta": json.loads(scan["meta"] or "{}"),
            "embedded": json.loads(scan["embedded"] or "{}"), "matches": items,
            "note": "distance = differing bits of 64 in the perceptual hash; 0-3 is almost certainly the same "
                    "picture/video, 4-7 very likely, to be confirmed by eye."}


def register(app, ctx):
    @app.get("/api/analysis/coordination")
    def api_coordination(case: str = "", days: int = 90, window_min: int = 60, min_channels: int = 2, limit: int = 200):
        return coordination(ctx, int(case) if case.isdigit() else None, max(1, min(days, 3650)),
                            max(1, window_min), max(2, min_channels), min(limit, 2000))

    @app.get("/api/analysis/spread/{post_id}")
    def api_spread(post_id: int):
        return spread(ctx, post_id)

    @app.get("/api/analysis/coordinates")
    def api_coordinates(case: str = ""):
        return coordinates(ctx, int(case) if case.isdigit() else None)

    @app.post("/api/analysis/mediascan")
    async def api_mediascan(request: Request):
        """Queue a local scan (perceptual hashes + embedded metadata) of files not scanned yet:
        {"channel"?: .., "case"?: .., "limit"?: 5000}."""
        b = await request.json()
        params = {k: b[k] for k in ("channel", "case", "limit") if b.get(k)}
        return {"id": ctx.runner.submit("mediascan", params, ctx.actor())}

    @app.get("/api/analysis/similar_media/{sha}")
    def api_similar_media(sha: str, max_distance: int = PHASH_MAX):
        return similar_media(ctx, sha.lower(), max(0, min(max_distance, PHASH_MAX)))

    @app.get("/api/analysis/embedded")
    def api_embedded(case: str = "", gps: str = ""):
        """Files that still carry camera/GPS/date metadata: often the most revealing ones."""
        where = "s.embedded NOT IN ('{}', '') " + ("AND s.has_gps = 1 " if gps == "1" else "")
        rows = ctx.q(f"""SELECT s.sha256, s.embedded, s.meta, s.has_gps,
                           (SELECT group_concat(DISTINCT p.channel) FROM media m JOIN posts p ON p.id = m.post_id
                              WHERE m.sha256 = s.sha256) channels,
                           (SELECT MIN(p.id) FROM media m JOIN posts p ON p.id = m.post_id WHERE m.sha256 = s.sha256) post_id
                         FROM media_scan s WHERE {where}""")
        if case.isdigit():
            members = {r["channel"] for r in ctx.q("SELECT channel FROM case_channels WHERE case_id = ?", (int(case),))}
            rows = [r for r in rows if set((r["channels"] or "").split(",")) & members]
        for r in rows:
            r["embedded"], r["meta"] = json.loads(r["embedded"]), json.loads(r["meta"] or "{}")
        return rows

    def auto_scan(job, stats):
        """Scan new files right after a job downloads them (Settings: auto_mediascan)."""
        if job["kind"] == "mediascan" or not (stats.get("media_new") or 0):
            return
        if (ctx.q1("SELECT value FROM settings WHERE key = 'auto_mediascan'") or {}).get("value", "1") == "0":
            return
        if ctx.q1("SELECT 1 FROM jobs WHERE kind = 'mediascan' AND status IN ('queued', 'running')"):
            return
        ctx.runner.submit("mediascan", {}, "system")

    ctx.runner.hooks["after_job"].append(auto_scan)
