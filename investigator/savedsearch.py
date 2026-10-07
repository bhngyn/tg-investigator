"""
Saved searches: a query + filters kept under a name (optionally in a case), re-runnable, with "what is new
since I last ran it" and one click to turn it into an alert rule.

  freeze = 1  the FTS expression (and its dictionary expansion) is stored at save time and every run uses it,
              like an alert rule: later dictionary edits do not change what the search means
  freeze = 0  the query is expanded live on each run

`params` holds the search parameters exactly as /api/search takes them (channels, date_from, entity, place,
near, in, expand, drop, skip, ...); `scope` inside it = posts (default) or comments. Every run is written to the
search log (endpoint "saved") and audited.
"""

import json
import zlib

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from . import alerts, db, lexicon, searchlog

SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_searches (
    id                 INTEGER PRIMARY KEY,
    case_id            INTEGER,
    name               TEXT NOT NULL,
    q                  TEXT,
    params             TEXT,
    freeze             INTEGER NOT NULL DEFAULT 0,
    fts                TEXT,
    expansion          TEXT,
    dict_version       TEXT,
    notes              TEXT,
    created            TEXT,
    created_by         TEXT,
    last_run_at        TEXT,
    last_total         INTEGER,
    last_result_sha256 TEXT
);
CREATE INDEX IF NOT EXISTS saved_searches_case ON saved_searches(case_id);
"""

# saved-search params an alert rule can carry (query text, scope, channels, the frozen expansion) or that only
# shape the display; every other filter is dropped by to_alert (409 unless {"drop_filters": true})
_ALERT_KEEPS = ("scope", "channels", "case", "expand", "drop", "skip")
_ALERT_HARMLESS = ("sort", "near_label")

# parameters that are not part of the search itself
_DROP = ("limit", "offset", "log", "token", "case", "q", "sort_dir")


def register(app, ctx):
    q, q1, write, con, actor = ctx.q, ctx.q1, ctx.write, ctx.con, ctx.actor
    con.executescript(SCHEMA)

    def view(r):
        r = dict(r)
        r["params"] = json.loads(r["params"] or "{}")
        r["expansion"] = json.loads(r["expansion"]) if r.get("expansion") else None
        # a frozen expansion that was cut short (too many spellings) says so for as long as the search lives
        r["truncated"] = bool((r["expansion"] or {}).get("truncated"))
        r["truncated_count"] = (r["expansion"] or {}).get("truncated_count") or 0
        return r

    def rows(where="1", args=()):
        return [view(r) for r in q(f"""SELECT s.*, k.name case_name FROM saved_searches s
                                       LEFT JOIN cases k ON k.id = s.case_id WHERE {where} ORDER BY s.id""", args)]

    def expand_now(qtext, params):
        on, drop, skip = lexicon.from_params(params)
        return ctx.expand(qtext, on, drop, skip)

    def clean(params):
        out = {}
        for k, v in (params or {}).items():
            if k in _DROP or k.startswith("_") or v in (None, "", []):
                continue
            if isinstance(v, (list, tuple)):        # entity= / place= lists, channels=, in=
                v = "\n".join(map(str, v)) if k == "entity" else ",".join(map(str, v))
            out[k] = v
        return out

    def fields(b, row=None):
        row = row or {}
        params = clean(b["params"] if "params" in b else (row.get("params") or {}))
        qtext = b["q"] if "q" in b else row.get("q")
        if qtext is None and "q" in (b.get("params") or {}):
            qtext = b["params"]["q"]
        name = (b.get("name") if "name" in b else row.get("name")) or (qtext or "Saved search")
        freeze = int(bool(b["freeze"] if "freeze" in b else row.get("freeze", 0)))
        case = b["case_id"] if "case_id" in b else row.get("case_id")
        case = int(case) if case else None
        if params.get("scope") not in (None, "posts", "comments"):
            raise HTTPException(400, "scope must be posts or comments")
        f = {"case_id": case, "name": str(name).strip()[:200], "q": qtext, "params": json.dumps(params, ensure_ascii=False, sort_keys=True),
             "freeze": freeze, "notes": b["notes"] if "notes" in b else row.get("notes")}
        if not (qtext or "").strip() and not any(params.get(k) for k in ("entity", "place", "near")):
            raise HTTPException(400, "A saved search needs a query, an entity or a place filter.")
        # frozen: expand once (on create, when the query/params change, or on {"reexpand": true})
        if freeze and (not row or b.get("reexpand") or not row.get("freeze") or qtext != row.get("q")
                       or f["params"] != json.dumps(row.get("params") or {}, ensure_ascii=False, sort_keys=True)):
            exp = expand_now(qtext, params)
            f.update(fts=exp["fts"], expansion=json.dumps(exp, ensure_ascii=False), dict_version=exp["dict_version"])
        elif freeze:
            f.update(fts=row.get("fts"), expansion=json.dumps(row.get("expansion"), ensure_ascii=False)
                     if row.get("expansion") else None, dict_version=row.get("dict_version"))
        else:
            f.update(fts=None, expansion=None, dict_version=None)
        return f

    @app.get("/api/saved_searches")
    def list_saved(case: str = ""):
        return rows("s.case_id = ?", (int(case),)) if case.isdigit() else rows()

    @app.post("/api/saved_searches")
    async def save(request: Request):
        """Create {"name", "q", "params": {...}, "case_id", "freeze", "notes"}; edit {"id", ...same fields};
        delete {"id", "delete": true}. Frozen searches take {"reexpand": true} to expand again."""
        b = await request.json()

        def do():
            if b.get("id"):
                row = q1("SELECT * FROM saved_searches WHERE id = ?", (b["id"],))
                if not row:
                    raise HTTPException(404)
                if b.get("delete"):
                    con.execute("DELETE FROM saved_searches WHERE id = ?", (row["id"],))
                    con.execute("UPDATE search_runs SET saved_search_id = NULL WHERE saved_search_id = ?", (row["id"],))
                    db.audit(con, actor(), "saved_search_delete", f"saved_search {row['id']}", {"name": row["name"]})
                    return None
                f = fields(b, view(row))
                con.execute(f"UPDATE saved_searches SET {', '.join(k + ' = ?' for k in f)} WHERE id = ?", (*f.values(), row["id"]))
                db.audit(con, actor(), "saved_search_edit", f"saved_search {row['id']}",
                         {k: v for k, v in f.items() if k != "expansion"})
                return row["id"]
            f = fields(b)
            cur = con.execute(f"INSERT INTO saved_searches({', '.join(f)}, created, created_by) VALUES "
                              f"({', '.join('?' * len(f))}, ?, ?)", (*f.values(), db.now(), actor()))
            db.audit(con, actor(), "saved_search_create", f"saved_search {cur.lastrowid}",
                     {k: v for k, v in f.items() if k != "expansion"})
            return cur.lastrowid
        sid = write(do)
        return rows("s.id = ?", (sid,))[0] if sid else {"deleted": True}

    def run_one(row):
        """Execute a saved search through the shared search path; log, audit, update the row. Returns a summary."""
        s = view(row)
        params = dict(s["params"])
        params["q"] = s["q"] or ""
        if s["case_id"]:
            params["case"] = str(s["case_id"])
        kind = "comments" if params.get("scope") == "comments" else "posts"
        if s["freeze"] and s["expansion"]:
            params["_exp"] = {**s["expansion"], "frozen": True, "dictionary": lexicon.dict_status()}
        elif s["freeze"]:
            params["_exp"] = {"enabled": True, "dict_version": s["dict_version"], "fts": s["fts"], "groups": [], "frozen": True,
                              "dictionary": lexicon.dict_status()}
        prev = q1("SELECT id, ids FROM search_runs WHERE saved_search_id = ? ORDER BY id DESC LIMIT 1", (s["id"],))
        res = ctx.execute(kind, params)
        ids, total, exp = res["ids"], len(res["ids"]), params["_exp"] if "_exp" in params else ctx.query_expansion(params)
        digest = searchlog.sha(ids)
        first = s["last_run_at"] is None
        new_ids = []
        if not first and ids:
            table = "comments" if kind == "comments" else "posts"
            for i in range(0, len(ids), 900):
                chunk = ids[i:i + 900]
                new_ids += [r["id"] for r in q(f"SELECT id FROM {table} WHERE id IN ({','.join('?' * len(chunk))}) "
                                               "AND first_captured > ?", (*chunk, s["last_run_at"]))]
        changed = None if first else digest != s["last_result_sha256"]
        added = removed = None                     # membership change vs the previous run's stored id list
        if prev and prev["ids"]:
            before = [str(x) for x in json.loads(zlib.decompress(prev["ids"]))]
            now_ids = [str(x) for x in ids]
            bset, nset = set(before), set(now_ids)
            added = [x for x in now_ids if x not in bset]
            removed = [x for x in before if x not in nset]
        run_id = searchlog.record(ctx, "saved", params, exp, total, ids, kind=kind, saved_id=s["id"])
        now = db.now()

        def upd():
            con.execute("UPDATE saved_searches SET last_run_at = ?, last_total = ?, last_result_sha256 = ? WHERE id = ?",
                        (now, total, digest, s["id"]))
            db.audit(con, actor(), "saved_search_run", f"saved_search {s['id']}",
                     {"total": total, "new": len(new_ids), "changed": changed, "search_run": run_id,
                      "added": None if added is None else len(added), "removed": None if removed is None else len(removed)})
        write(upd)
        return {"id": s["id"], "name": s["name"], "case_id": s["case_id"], "scope": kind, "total": total,
                "total_before": s["last_total"], "first_run": first,
                "new_since_last": None if first else len(new_ids), "new_ids": new_ids[:200],
                "added": None if added is None else len(added), "removed": None if removed is None else len(removed),
                "added_ids": (added or [])[:200], "removed_ids": (removed or [])[:200],
                "previous_run_id": prev["id"] if prev else None,
                "truncated": bool(exp.get("truncated")), "truncated_count": exp.get("truncated_count") or 0,
                "changed": changed, "result_sha256": digest, "run_id": run_id,
                "fts": exp.get("fts"), "dict_version": exp.get("dict_version"), "frozen": bool(s["freeze"]),
                "last_run_at_before": s["last_run_at"], "run_at": now}

    @app.post("/api/saved_searches/run_all")
    def run_all(case: str = ""):
        rs = q("SELECT * FROM saved_searches WHERE case_id = ? ORDER BY id", (int(case),)) if case.isdigit() \
            else q("SELECT * FROM saved_searches ORDER BY id")
        out = []
        for r in rs:
            try:
                out.append(run_one(r))
            except HTTPException as e:
                out.append({"id": r["id"], "name": r["name"], "error": e.detail})
        return {"ran": len(out), "with_new": sum(1 for o in out if o.get("new_since_last")),
                "changed": sum(1 for o in out if o.get("changed")), "searches": out}

    @app.post("/api/saved_searches/{sid}/run")
    def run(sid: int):
        r = q1("SELECT * FROM saved_searches WHERE id = ?", (sid,))
        if not r:
            raise HTTPException(404)
        return run_one(r)

    @app.post("/api/saved_searches/{sid}/to_alert")
    async def to_alert(sid: int, request: Request):
        """Create a keyword alert rule from this search: same query, the frozen expansion (or the current one for a
        live search, frozen from now on), scope posts/comments, same case and channels. Alert rules match on the
        query text only: entity/place/date filters of the saved search are not carried over.
        Body (optional): {"name", "notify"}."""
        try:
            b = await request.json()
        except ValueError:
            b = {}
        r = q1("SELECT * FROM saved_searches WHERE id = ?", (sid,))
        if not r:
            raise HTTPException(404)
        s = view(r)
        if not (s["q"] or "").strip():
            raise HTTPException(400, "This search has no query text: alert rules need one.")
        dropped = [k for k, v in s["params"].items() if k not in _ALERT_KEEPS and k not in _ALERT_HARMLESS
                   and v not in (None, "", [])]
        if s["params"].get("case"):
            dropped = [k for k in dropped if k != "case"]
        if dropped and not b.get("drop_filters"):
            return JSONResponse({
                "detail": ("An alert rule matches on the query text only. This saved search also filters by: "
                           + ", ".join(sorted(dropped)) + ". Those filters would be ignored, so the alert would fire "
                           "on more posts than this search shows. Send {\"drop_filters\": true} to create the alert "
                           "anyway."),
                "dropped_filters": sorted(dropped)}, status_code=409)
        exp = s["expansion"] if s["freeze"] and s["expansion"] else expand_now(s["q"], s["params"])
        chans = [c for c in (s["params"].get("channels") or "").split(",") if c]
        f = {"name": (b.get("name") or f"Saved search: {s['name']}")[:200], "case_id": s["case_id"], "kind": "keyword",
             "query": s["q"], "scope": s["params"].get("scope") or "posts",
             "channels": json.dumps(chans) if chans else None, "min_links": None, "notify": int(bool(b.get("notify"))),
             "enabled": 1, "query_fts": exp["fts"], "expansion": json.dumps(exp, ensure_ascii=False),
             "dict_version": exp["dict_version"]}
        if not f["query_fts"]:
            raise HTTPException(400, "The query has no searchable words.")

        def do():
            rid = alerts.insert_rule(ctx, f)
            db.audit(con, actor(), "saved_search_to_alert", f"saved_search {sid}", {"alert_rule": rid})
            return rid
        rid = write(do)
        return {"alert_rule_id": rid, "query_fts": f["query_fts"], "dict_version": f["dict_version"],
                "dropped_filters": sorted(dropped), "truncated": bool(exp.get("truncated")),
                "truncated_count": exp.get("truncated_count") or 0,
                "note": "Alert rules match the query text only (not entity/place/date filters)."}
