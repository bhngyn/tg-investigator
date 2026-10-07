"""
Discovery: finding channels that belong in a case.

Without login: candidates are ranked from what's already collected — channels the case's
channels forward, link to or mention (and comment-section links), weighted by how many
different case channels point at them and how recently.
With login (tgdiscover jobs): Telegram's "similar channels" for a channel, and keyword
search over public channel names and public posts. Results land in `discoveries`, new
channels become candidates, and "similar" results become `similar` links in the network.
"""

import gzip
import hashlib
import json
import math
import os
import shutil

from fastapi import HTTPException, Request

from . import db

WEIGHTS = {"forward": 3.0, "mention": 2.0, "link": 2.0, "description": 3.0, "comment_forward": 1.5,
           "comment_sender": 2.0, "similar": 2.5}


def is_discover_run(run_dir):
    return os.path.exists(os.path.join(run_dir, "discover.json"))


def ingest_discover(con, run_dir, store_dir, actor="system", job_id=None, log=print):
    stats = dict(found=0, discoveries_new=0, channels_new=0, edges_new=0)
    with open(os.path.join(run_dir, "discover.json"), encoding="utf-8") as f:
        spec = json.load(f)
    account, method = spec.get("account"), spec.get("method")
    raw = os.path.join(run_dir, "raw", "responses.jsonl")
    if os.path.exists(raw) and os.path.getsize(raw):
        h = hashlib.sha256()
        with open(raw, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        sha = h.hexdigest()
        rel = os.path.join("api", sha[:2], sha + ".jsonl.gz")
        dest = os.path.join(store_dir, rel)
        if not os.path.exists(dest):
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(raw, "rb") as src, gzip.open(dest + ".part", "wb") as out:
                shutil.copyfileobj(src, out)
            os.replace(dest + ".part", dest)
            os.chmod(dest, 0o444)
        con.execute("""INSERT OR IGNORE INTO captures(kind, url, sha256, path, bytes, captured_at, method, tool,
                           account, job_id, run_dir, note) VALUES ('api_dump',?,?,?,?,?,'api','tgdiscover',?,?,?,?)""",
                    (f"tg://discover/{method}", sha, rel, os.path.getsize(raw), spec.get("started_at") or db.now(),
                     account, job_id, run_dir, "sha256 of the raw API responses (JSON lines) as received; gzip-compressed"))
    sources = set()
    path = os.path.join(run_dir, "discoveries.jsonl")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()] if os.path.exists(path) else []
    for r in rows:
        stats["found"] += 1
        detail = {"rank": r.get("rank"), "post": r.get("post"), "verified": r.get("verified"),
                  "scam": r.get("scam"), "fake": r.get("fake")}
        # search_posts can hit the same channel many times: key those by post
        key_q = r["query"] if not r.get("post") else f"{r['query']}#{r['tg_id']}:{r['post']['msg_id']}"
        stats["discoveries_new"] += con.execute(
            """INSERT OR IGNORE INTO discoveries(job_id, method, query, username, tg_id, title, subscribers, type,
                 detail, found_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (job_id, r["method"], key_q, r.get("username"), r["tg_id"], r.get("title"), r.get("subscribers"),
             r.get("type"), json.dumps(detail, ensure_ascii=False), r.get("found_at"))).rowcount
        name = r.get("username")
        if not name:
            continue                            # private: recorded in discoveries only
        cur = con.execute("""INSERT INTO channels(username, tg_id, title, type, subscribers, status, first_seen)
                             VALUES (?,?,?,?,?, 'candidate', ?) ON CONFLICT(username) DO UPDATE SET
                             tg_id = COALESCE(tg_id, excluded.tg_id), title = COALESCE(title, excluded.title),
                             type = COALESCE(type, excluded.type), subscribers = COALESCE(excluded.subscribers, subscribers)""",
                          (name, r["tg_id"], r.get("title"), r.get("type"), r.get("subscribers"), r.get("found_at")))
        if r["method"] == "similar":
            stats["edges_new"] += con.execute(
                "INSERT OR IGNORE INTO edge_hits(source, target, type, msg_id, date) VALUES (?,?,'similar',-1,?)",
                (r["query"], name, r.get("found_at"))).rowcount
            sources.add(r["query"])
    stats["channels_new"] = len({r.get("username") for r in rows if r.get("username")})
    if sources:
        db.rebuild_edges(con, sorted(sources))
    db.audit(con, actor, "import_discovery", run_dir, {"method": method, **stats})
    con.commit()
    log(f"Imported discovery {run_dir}: {stats}")
    return stats


def candidates(ctx, case_id=None, limit=100, include_classified_users=False):
    """Channels not (yet) in scope, ranked by how strongly the scope points at them."""
    if case_id:
        scope = f"(SELECT channel FROM case_channels WHERE case_id = {int(case_id)})"
    else:
        scope = "(SELECT username FROM channels WHERE status IN ('seed', 'watched'))"
    rows = ctx.q(f"""
        SELECT e.target username, e.type, COUNT(DISTINCT e.source) sources, SUM(e.weight) weight,
               MAX(e.last_seen) last_seen, group_concat(DISTINCT e.source) via
        FROM edges e WHERE e.source IN {scope} AND e.target NOT IN {scope}
        GROUP BY e.target, e.type""")
    agg = {}
    for r in rows:
        a = agg.setdefault(r["username"], {"username": r["username"], "by_type": {}, "sources": set(),
                                           "last_seen": None})
        a["by_type"][r["type"]] = r["weight"]
        a["sources"] |= set((r["via"] or "").split(","))
        a["last_seen"] = max(filter(None, [a["last_seen"], r["last_seen"]]), default=None)
    if not agg:
        return []
    names = list(agg)
    info = {}
    for i in range(0, len(names), 900):
        chunk = names[i:i + 900]
        for c in ctx.q(f"""SELECT c.username, c.title, c.type, c.subscribers, c.status, c.last_crawled, c.preview,
                              (SELECT COUNT(*) FROM discoveries d WHERE d.username = c.username
                                 AND d.method IN ('search_channels', 'search_posts')) search_hits,
                              (SELECT COUNT(*) FROM posts p WHERE p.channel = c.username) posts
                            FROM channels c WHERE c.username IN ({','.join('?' * len(chunk))})""", chunk):
            info[c["username"]] = c
    from datetime import datetime, timedelta, timezone
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    out = []
    for name, a in agg.items():
        c = info.get(name, {})
        if c.get("status") == "dismissed":
            continue
        if c.get("type") in ("user", "bot") and not include_classified_users:
            continue
        n_src = len(a["sources"] - {""})
        signal = sum(WEIGHTS.get(t, 1.0) * math.log1p(w or 0) for t, w in a["by_type"].items())
        score = 3.0 * n_src + signal + 1.0 * (c.get("search_hits") or 0) + (1.0 if (a["last_seen"] or "") >= recent else 0)
        reasons = [f"linked from {n_src} channel{'s' if n_src != 1 else ''}"]
        reasons += [f"{w} {t.replace('_', ' ')}{'s' if w != 1 else ''}" for t, w in
                    sorted(a["by_type"].items(), key=lambda x: -x[1])]
        if c.get("search_hits"):
            reasons.append(f"{c['search_hits']} keyword search hit(s)")
        out.append({**{k: c.get(k) for k in ("title", "type", "subscribers", "status", "last_crawled", "preview", "posts")},
                    "username": name, "score": round(score, 2), "sources": sorted(a["sources"] - {""}),
                    "by_type": a["by_type"], "last_seen": a["last_seen"], "reasons": reasons})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def register(app, ctx):
    q, q1, write, con, actor, runner = ctx.q, ctx.q1, ctx.write, ctx.con, ctx.actor, ctx.runner

    @app.get("/api/discovery/candidates")
    def list_candidates(case: str = "", limit: int = 100, users: str = ""):
        return candidates(ctx, int(case) if case.isdigit() else None, min(limit, 1000), users == "1")

    @app.post("/api/discovery")
    async def start_discovery(request: Request):
        """Queue a logged-in lookup: {"method": "similar", "channels": [..]} or
        {"method": "search_channels"|"search_posts", "queries": [..], "limit": 50}. Read-only on Telegram."""
        b = await request.json()
        method = b.get("method")
        if method not in ("similar", "search_channels", "search_posts"):
            raise HTTPException(400, "method must be similar, search_channels or search_posts")
        key = "channels" if method == "similar" else "queries"
        vals = [str(v).strip() for v in b.get(key) or [] if str(v).strip()]
        if not vals:
            raise HTTPException(400, f"Give at least one entry in {key}.")
        if len(vals) > 50:
            raise HTTPException(400, "At most 50 lookups per job.")
        if method == "similar":
            from tgspider import normalize_seed
            vals = [normalize_seed(v) or v for v in vals]
        params = {"method": method, key: vals, "limit": max(1, min(100, int(b.get("limit", 50))))}
        if b.get("case"):
            params["case"] = int(b["case"])
        return {"id": runner.submit("discover", params, actor())}

    @app.get("/api/discoveries")
    def list_discoveries(method: str = "", query: str = "", case: str = "", limit: int = 500):
        where, args = ["1"], []
        if method:
            where.append("d.method = ?"); args.append(method)
        if query:
            where.append("(d.query = ? OR d.query LIKE ?)"); args += [query.lower(), query.lower() + "#%"]
        if case.isdigit():
            where.append("d.job_id IN (SELECT id FROM jobs WHERE params LIKE ?)"); args.append(f'%"case": {int(case)}%')
        rows = q(f"""SELECT d.*, c.status, c.last_crawled,
                       (SELECT group_concat(k.name, ', ') FROM case_channels cc JOIN cases k ON k.id = cc.case_id
                        WHERE cc.channel = d.username) cases
                     FROM discoveries d LEFT JOIN channels c ON c.username = d.username
                     WHERE {' AND '.join(where)} ORDER BY d.id DESC LIMIT ?""", (*args, min(limit, 5000)))
        for r in rows:
            r["detail"] = json.loads(r["detail"] or "{}")
            r["query"] = r["query"].split("#", 1)[0]
        return rows
