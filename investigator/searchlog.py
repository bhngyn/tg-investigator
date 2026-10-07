"""
Search log: a record of every search that was actually run, so "how did you find this?" has an answer.

One row per executed search (first page of /api/search, /api/comments, /api/comment_threads, the CSV exports
and saved-search runs): who, when, the query, the FINAL FTS expression that ran (after dictionary expansion),
the dictionary version, the parameters, how many results and a SHA-256 over the ordered result ids. Repeating
the identical search with the identical result within 10 minutes only bumps `repeat`.

A logged search can be re-run later against the frozen `fts` (POST /api/search_log/{id}/rerun): the response
says whether the result set is the same and lists added / removed items, which is how "the search found more
after the next collection" is shown rather than asserted.

The ordered id list is kept (zlib-compressed, capped at 50,000) so re-runs can say exactly what changed.
Evidence exports (incidents.py) include the case's searches as search_log.csv plus a "How items were found"
section in README.txt.
"""

import hashlib
import json
import zlib
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from . import db, lexicon

ENDPOINTS = ("posts", "comments", "threads", "export", "saved")
DEDUPE_MIN = 10
ID_CAP = 50000

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_runs (
    id              INTEGER PRIMARY KEY,
    ts              TEXT NOT NULL,
    actor           TEXT,
    endpoint        TEXT NOT NULL,        -- posts / comments / threads / export / saved
    case_id         INTEGER,
    q               TEXT,
    params          TEXT,                 -- JSON (without paging); "_kind" = posts|comments|threads for export/saved
    fts             TEXT,                 -- the FTS expression that ran (frozen for re-runs)
    dict_version    TEXT,
    key_version     TEXT,
    total           INTEGER,
    result_sha256   TEXT,
    first_id        TEXT,
    repeat          INTEGER NOT NULL DEFAULT 1,
    saved_search_id INTEGER,
    last_ts         TEXT,                 -- most recent repeat
    rerun_of        INTEGER,
    ids             BLOB                  -- zlib(JSON list of result ids), see ID_CAP
);
CREATE INDEX IF NOT EXISTS search_runs_case ON search_runs(case_id, id);
CREATE INDEX IF NOT EXISTS search_runs_ts ON search_runs(ts);
"""

_SKIP = ("_exp", "limit", "offset", "log", "token")


def sha(ids):
    h = hashlib.sha256()
    for i in ids[:ID_CAP]:
        h.update(f"{i}\n".encode())
    return h.hexdigest()


def clean_params(params, kind=None):
    p = {k: v for k, v in params.items() if k not in _SKIP and not k.startswith("_") and v not in (None, "")}
    if kind:
        p["_kind"] = kind
    return p


def loggable(params):
    """Plain browsing (no query, no entity/place filter) is not a search worth recording."""
    return params.get("log") != "0" and any(params.get(k) for k in ("q", "entity", "place", "near"))


def record(ctx, endpoint, params, exp, total, ids, kind=None, saved_id=None, rerun_of=None, actor=None):
    """Write (or repeat-bump) one search_runs row. `ids` = ordered result ids (any type). Returns the row id."""
    ids = [str(i) if not isinstance(i, (int, str)) else i for i in ids][:ID_CAP]
    p = clean_params(params, kind)
    pj = json.dumps(p, ensure_ascii=False, sort_keys=True)
    digest = sha(ids)
    fts = (exp or {}).get("fts")
    now = db.now()
    case = params.get("case")
    who = actor or ctx.actor()
    from . import translit

    def do():
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=DEDUPE_MIN)).isoformat(timespec="seconds")
        same = ctx.con.execute(
            """SELECT id FROM search_runs WHERE endpoint = ? AND params = ? AND COALESCE(fts, '') = ? AND total = ?
               AND result_sha256 = ? AND COALESCE(last_ts, ts) >= ? AND COALESCE(saved_search_id, 0) = ?
               ORDER BY id DESC LIMIT 1""", (endpoint, pj, fts or "", total, digest, cutoff, saved_id or 0)).fetchone()
        if same:
            ctx.con.execute("UPDATE search_runs SET repeat = repeat + 1, last_ts = ? WHERE id = ?", (now, same[0]))
            return same[0]
        cur = ctx.con.execute(
            "INSERT INTO search_runs(ts, actor, endpoint, case_id, q, params, fts, dict_version, key_version, total, "
            "result_sha256, first_id, repeat, saved_search_id, last_ts, rerun_of, ids) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?)",
            (now, who, endpoint, int(case) if (case or "").isdigit() else None, params.get("q"), pj, fts,
             (exp or {}).get("dict_version"), str(translit.KEY_VERSION), total, digest,
             str(ids[0]) if ids else None, saved_id, now, rerun_of,
             zlib.compress(json.dumps(ids).encode(), 6)))
        return cur.lastrowid
    return ctx.write(do)


def frozen_expansion(run):
    """The expansion object that reproduces a logged run: the stored fts, nothing re-expanded."""
    return {"enabled": True, "dict_version": run["dict_version"], "fts": run["fts"], "groups": [], "frozen": True,
            "dictionary": lexicon.dict_status()}


def _row(r, with_ids=False):
    r = dict(r)
    r["params"] = json.loads(r["params"] or "{}")
    ids = r.pop("ids", None)
    if with_ids:
        r["ids"] = json.loads(zlib.decompress(ids)) if ids else []
    return r


def for_export(ctx, case_id):
    """Searches recorded in a case, oldest first, as flat dicts for search_log.csv and the README."""
    if not case_id:
        return []
    rows = ctx.q("SELECT * FROM search_runs WHERE case_id = ? ORDER BY id", (case_id,))
    out = []
    for r in rows:
        r = _row(r)
        out.append({"id": r["id"], "time_utc": r["ts"], "last_repeat_utc": r["last_ts"], "repeats": r["repeat"],
                    "by": r["actor"], "endpoint": r["endpoint"], "query": r["q"], "fts": r["fts"],
                    "dict_version": r["dict_version"], "key_version": r["key_version"], "total": r["total"],
                    "result_sha256": r["result_sha256"], "first_id": r["first_id"], "saved_search_id": r["saved_search_id"],
                    "params": json.dumps(r["params"], ensure_ascii=False, sort_keys=True)})
    return out


def readme_section(rows):
    """'How items were found' text for the evidence package README."""
    lines = ["", "How items were found", "  Searches recorded in this case (search_log.csv has the same, with parameters).",
             "  fts is the exact search expression that ran after dictionary expansion; dict_version identifies the",
             "  dictionary it was expanded with; hash = SHA-256 over the ordered result ids."]
    if not rows:
        lines.append("  (no searches were recorded for this case)")
    for r in rows:
        lines.append(f"  #{r['id']} {r['time_utc']} by {r['by'] or '?'} [{r['endpoint']}]  q={r['query'] or ''!r}")
        lines.append(f"      fts={r['fts'] or '-'}  dict={r['dict_version'] or '-'}  total={r['total']}  hash={r['result_sha256'][:16]}…"
                     + (f"  repeated x{r['repeats']}" if r["repeats"] > 1 else ""))
    return "\n".join(lines) + "\n"


def register(app, ctx):
    q, q1, write, con = ctx.q, ctx.q1, ctx.write, ctx.con
    con.executescript(SCHEMA)

    @app.get("/api/search_log")
    def list_runs(case: str = "", q: str = "", endpoint: str = "", saved: str = "", limit: int = 50, offset: int = 0):
        where, args = ["1"], []
        if case.isdigit():
            where.append("case_id = ?"); args.append(int(case))
        if q:
            where.append("q LIKE ?"); args.append(f"%{q}%")
        if endpoint in ENDPOINTS:
            where.append("endpoint = ?"); args.append(endpoint)
        if saved.isdigit():
            where.append("saved_search_id = ?"); args.append(int(saved))
        w = " AND ".join(where)
        total = ctx.q1(f"SELECT COUNT(*) n FROM search_runs WHERE {w}", args)["n"]
        rows = ctx.q(f"""SELECT id, ts, actor, endpoint, case_id, q, params, fts, dict_version, key_version, total,
                           result_sha256, first_id, repeat, saved_search_id, last_ts, rerun_of
                         FROM search_runs WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?""",
                     (*args, min(limit, 500), offset))
        return {"total": total, "runs": [_row(r) for r in rows]}

    @app.get("/api/search_log/{rid}")
    def get_run(rid: int, ids: str = ""):
        r = q1("SELECT * FROM search_runs WHERE id = ?", (rid,))
        if not r:
            raise HTTPException(404)
        out = _row(r, with_ids=ids == "1")
        out["reruns"] = q("SELECT id, ts, total, result_sha256 FROM search_runs WHERE rerun_of = ? ORDER BY id", (rid,))
        return out

    @app.post("/api/search_log/{rid}/rerun")
    def rerun(rid: int):
        """Run the logged search again with its frozen FTS expression and stored parameters, and compare.
        Returns {same, total_then, total_now, added_ids, removed_ids (<=200 each), dict_version_then/now, run_id}."""
        r = q1("SELECT * FROM search_runs WHERE id = ?", (rid,))
        if not r:
            raise HTTPException(404)
        run = _row(r, with_ids=True)
        params = dict(run["params"])
        kind = params.pop("_kind", None) or {"posts": "posts", "comments": "comments", "threads": "threads"}.get(run["endpoint"], "posts")
        if params.get("scope") == "comments" and kind == "posts":
            kind = "comments"
        params["_exp"] = frozen_expansion(run)
        now_ids = ctx.execute(kind, params)["ids"]
        then, now = run["ids"], [str(i) for i in now_ids]
        then_s, now_s = set(map(str, then)), set(now)
        same = sha(now) == run["result_sha256"]
        added = [i for i in now if i not in then_s]
        removed = [i for i in map(str, then) if i not in now_s]
        conv = (lambda x: int(x) if str(x).isdigit() else x)
        new_id = record(ctx, run["endpoint"], params, params["_exp"], len(now), now_ids, kind=None if run["endpoint"] in ("posts", "comments", "threads") else kind,
                        saved_id=run["saved_search_id"], rerun_of=rid)
        cur_dict = ctx.expand("", True)["dict_version"]
        write(lambda: db.audit(con, ctx.actor(), "search_rerun", f"search_run {rid}",
                               {"same": same, "total_then": run["total"], "total_now": len(now), "new_run": new_id}))
        return {"same": same, "total_then": run["total"], "total_now": len(now),
                "added": len(added), "removed": len(removed),
                "added_ids": [conv(i) for i in added[:200]], "removed_ids": [conv(i) for i in removed[:200]],
                "dict_version_then": run["dict_version"], "dict_version_now": cur_dict,
                "fts": run["fts"], "run_id": new_id}
