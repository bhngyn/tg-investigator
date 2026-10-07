"""
Media text: OCR (Apple Vision) and speech-to-text (mlx-whisper) of collected photos and videos.

The heavy lifting is done by the top-level worker mediatext.py, run with its own venv
(tools/models/.venv) as a local-lane job, exactly like mediascan.py. This module:
  - decides which files still need reading and writes the worker's input list,
  - imports the worker's results into media_text / media_text_runs when the job ends,
  - serves the API (queue a run, status, settings, text of a file or a post's files).

The derived layer is keyed by the media file's SHA-256 (never a post id) and lives next to the store:
    <workspace>/derived/<sha[:2]>/<sha>/ocr.json | whisper.json | whisper.srt
The JSON files are the record; the database rows can be rebuilt from them (reindex()).
Nothing runs automatically unless the setting `auto_after_media_download` is turned on (default off).
"""

import glob
import hashlib
import json
import os
import re

from fastapi import HTTPException, Request

from . import db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER = os.path.join(ROOT, "mediatext.py")
WORKER_PY = os.path.join(ROOT, "tools", "models", ".venv", "bin", "python")
MODEL = "mlx-community/whisper-large-v3-turbo"
SETTINGS_KEY = "mediatext"
DONE = ("ok", "no_text", "no_audio")
VISION_LANGS = {"en-US", "fr-FR", "it-IT", "de-DE", "es-ES", "pt-BR", "zh-Hans", "zh-Hant", "yue-Hans", "yue-Hant",
                "ko-KR", "ja-JP", "ru-RU", "uk-UA", "th-TH", "vi-VT", "ar-SA", "ars-SA", "tr-TR", "id-ID", "cs-CZ",
                "da-DK", "nl-NL", "no-NO", "nn-NO", "nb-NO", "ms-MY", "pl-PL", "ro-RO", "sv-SE", "auto"}
DEFAULTS = {
    # Vision's automatic language detection reads Arabic, Cyrillic and Latin text in the same picture; with an
    # explicit list only the FIRST language really drives recognition (see tools/models/README.md).
    "languages": ["auto"],
    "interval_s": 5.0,                 # video OCR: at most one keyframe per N seconds
    "channel_ocr_languages": {},       # {"channel": ["ru-RU", "uk-UA", "en-US"]}
    "whisper_hints": {},               # {"channel": "ar"}; empty / missing = auto-detect
    "auto_after_media_download": False,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS media_text (
    id      INTEGER PRIMARY KEY,
    sha256  TEXT NOT NULL,
    kind    TEXT NOT NULL,           -- ocr / speech
    seq     INTEGER,
    t_start REAL,
    t_end   REAL,
    text    TEXT,
    lang    TEXT,
    conf    REAL,
    bbox    TEXT
);
CREATE INDEX IF NOT EXISTS media_text_sha ON media_text(sha256);

CREATE TABLE IF NOT EXISTS media_text_runs (
    id            INTEGER PRIMARY KEY,
    sha256        TEXT NOT NULL,
    kind          TEXT NOT NULL,       -- ocr / speech
    status        TEXT NOT NULL,       -- ok / no_audio / no_text / error
    tool          TEXT,
    model         TEXT,
    languages     TEXT,                -- JSON list (OCR languages) or the Whisper language hint
    params        TEXT,                -- JSON
    output_path   TEXT,                -- relative to the workspace
    output_sha256 TEXT,                -- of the derived JSON
    error         TEXT,
    job_id        INTEGER,
    created       TEXT
);
CREATE INDEX IF NOT EXISTS media_text_runs_sha ON media_text_runs(sha256, kind);
"""


def ensure_schema(con):
    con.executescript(SCHEMA)


# ---- settings ------------------------------------------------------------------------------------

def get_settings(q1):
    row = q1("SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,))
    try:
        cur = json.loads(row["value"]) if row else {}
    except ValueError:
        cur = {}
    return {**DEFAULTS, **{k: v for k, v in cur.items() if k in DEFAULTS}}


def clean_languages(v):
    if not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
        raise HTTPException(400, "languages must be a non-empty list of Apple Vision codes, e.g. [\"ar-SA\", \"en-US\"] or [\"auto\"].")
    bad = [x for x in v if x not in VISION_LANGS]
    if bad:
        raise HTTPException(400, f"Unsupported OCR language(s): {', '.join(bad)}. Supported: {', '.join(sorted(VISION_LANGS))}.")
    return ["auto"] if "auto" in v else list(dict.fromkeys(v))


def clean_hint(v):
    v = (v or "").strip().lower()
    if v and not re.fullmatch(r"[a-z]{2,3}", v):
        raise HTTPException(400, "A Whisper language hint is a short language code such as ar, ru or uk (empty = auto).")
    return v


def clean_settings(b, cur):
    out = dict(cur)
    if "languages" in b:
        out["languages"] = clean_languages(b["languages"])
    if "interval_s" in b:
        try:
            out["interval_s"] = min(60.0, max(1.0, float(b["interval_s"])))
        except (TypeError, ValueError):
            raise HTTPException(400, "interval_s must be a number of seconds (1-60).")
    if "channel_ocr_languages" in b:
        m = b["channel_ocr_languages"] or {}
        if not isinstance(m, dict):
            raise HTTPException(400, "channel_ocr_languages must be an object {channel: [codes]}.")
        out["channel_ocr_languages"] = {str(k).lower().lstrip("@"): clean_languages(v) for k, v in m.items() if v}
    if "whisper_hints" in b:
        m = b["whisper_hints"] or {}
        if not isinstance(m, dict):
            raise HTTPException(400, "whisper_hints must be an object {channel: code}.")
        out["whisper_hints"] = {str(k).lower().lstrip("@"): clean_hint(v) for k, v in m.items() if clean_hint(v)}
    if "auto_after_media_download" in b:
        out["auto_after_media_download"] = bool(b["auto_after_media_download"])
    return out


# ---- what needs reading --------------------------------------------------------------------------

def _is_video(mime, path):
    return (mime or "").startswith("video") or (not mime and path.lower().endswith((".mp4", ".mov", ".webm", ".mkv")))


def _is_image(mime, path):
    return (mime or "").startswith("image") or (not mime and path.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".heic")))


def _scope_sources(scope):
    """(read posts, read comments) for a scope. Explicit ids narrow to that side; `only` picks one side; a channel,
    case or all-missing scope reads both."""
    only = scope.get("only")
    want_posts = bool(scope.get("post_ids")) or not scope.get("comment_ids")
    want_comments = bool(scope.get("comment_ids")) or not scope.get("post_ids")
    return want_posts and only != "comments", want_comments and only != "posts"


def targets(q, scope, kinds=("ocr", "speech")):
    """[{sha256, path (relative to the store), mime, channels, post_ids, comment_ids, ocr, speech}] for the files in
    scope: the media of posts and of comment sections (one entry per file, whichever mix of them holds it).

    Photos: every full-size image (photo / full) is read once; a thumbnail only when its post has nothing
    bigger. Videos: keyframes are read and the audio transcribed; the low-resolution web-preview video is
    skipped when the post also has a logged-in original. Comment media are always full files (logged-in API)."""
    files = {}

    def add(m, channel, video, image, key, item_id):
        f = files.setdefault(m["sha256"], {"sha256": m["sha256"], "path": m["path"], "mime": m["mime"],
                                          "channels": [], "post_ids": [], "comment_ids": [], "video": video,
                                          "ocr": "ocr" in kinds, "speech": video and "speech" in kinds})
        if channel and channel not in f["channels"]:
            f["channels"].append(channel)
        f[key].append(item_id)

    want_posts, want_comments = _scope_sources(scope)
    if want_posts:
        where, args = ["1"], []
        if scope.get("post_ids"):
            ids = [int(i) for i in scope["post_ids"]]
            where.append(f"p.id IN ({','.join('?' * len(ids))})")
            args += ids
        if scope.get("channel"):
            where.append("p.channel = ?")
            args.append(str(scope["channel"]).lower().lstrip("@"))
        if scope.get("case"):
            where.append("p.channel IN (SELECT channel FROM case_channels WHERE case_id = ?)")
            args.append(int(scope["case"]))
        rows = q(f"""SELECT m.id, m.post_id, m.kind, m.sha256, m.path, m.mime, p.channel FROM media m
                     JOIN posts p ON p.id = m.post_id WHERE {' AND '.join(where)} ORDER BY m.post_id, m.id""", tuple(args))
        by_post = {}
        for r in rows:
            by_post.setdefault(r["post_id"], []).append(r)
        for post_id, ms in by_post.items():
            has_full_video = any(m["kind"] == "full" and _is_video(m["mime"], m["path"]) for m in ms)
            has_big = any(m["kind"] != "thumb" for m in ms)
            for m in ms:
                video, image = _is_video(m["mime"], m["path"]), _is_image(m["mime"], m["path"])
                if m["kind"] == "thumb" and has_big:
                    continue
                if video and m["kind"] == "video" and has_full_video:
                    continue
                if not (video or image):
                    continue
                add(m, m["channel"], video, image, "post_ids", post_id)
    if want_comments:
        where, args = ["1"], []
        if scope.get("comment_ids"):
            ids = [int(i) for i in scope["comment_ids"]]
            where.append(f"c.id IN ({','.join('?' * len(ids))})")
            args += ids
        if scope.get("channel"):
            where.append("c.channel = ?")
            args.append(str(scope["channel"]).lower().lstrip("@"))
        if scope.get("case"):
            where.append("c.channel IN (SELECT channel FROM case_channels WHERE case_id = ?)")
            args.append(int(scope["case"]))
        for m in q(f"""SELECT m.id, m.comment_id, m.kind, m.sha256, m.path, m.mime, c.channel FROM comment_media m
                       JOIN comments c ON c.id = m.comment_id WHERE {' AND '.join(where)}
                       ORDER BY m.comment_id, m.id""", tuple(args)):
            video, image = _is_video(m["mime"], m["path"]), _is_image(m["mime"], m["path"])
            if video or image:                       # documents, stickers and the like have nothing to read
                add(m, m["channel"], video, image, "comment_ids", m["comment_id"])
    return list(files.values())


def latest_runs(q):
    """{(sha, kind): status} of the newest run per file and kind."""
    rows = q("SELECT sha256, kind, status FROM media_text_runs WHERE id IN "
             "(SELECT MAX(id) FROM media_text_runs GROUP BY sha256, kind)")
    return {(r["sha256"], r["kind"]): r["status"] for r in rows}


def missing(q, scope, kinds, redo=False):
    done = {} if redo else latest_runs(q)
    out = []
    for f in targets(q, scope, kinds):
        f["ocr"] = f["ocr"] and done.get((f["sha256"], "ocr")) not in DONE
        f["speech"] = f["speech"] and done.get((f["sha256"], "speech")) not in DONE
        if f["ocr"] or f["speech"]:
            out.append(f)
    return out


def model_available():
    """Is the Whisper model already in the local Hugging Face cache? (never downloaded at runtime)"""
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")
    snap = os.path.join(hub, "models--" + MODEL.replace("/", "--"), "snapshots", "*")
    return any(glob.glob(os.path.join(d, "weights.*")) or glob.glob(os.path.join(d, "model.*")) for d in glob.glob(snap))


def worker_ready():
    return os.path.exists(WORKER_PY) and os.path.exists(WORKER)


# ---- job: build the worker's command -------------------------------------------------------------

def _kinds(params):
    k = params.get("kind", "both")
    return ("ocr", "speech") if k == "both" else (k,)


def job_command(runner, run_dir, params):
    """Called by jobs.JobRunner for kind "mediatext". Returns the worker's argv, None when there is nothing to
    read, or a str (an error message: the job fails with it)."""
    if not worker_ready():
        return "The media-text worker is not installed: create tools/models/.venv (see tools/models/README.md)."
    kinds = _kinds(params)
    if "speech" in kinds and not model_available():
        return f"The Whisper model {MODEL} is not in the local Hugging Face cache and is never downloaded automatically."

    def q(sql, args=()):
        with runner.lock:
            return [dict(r) for r in runner.con.execute(sql, args).fetchall()]

    def q1(sql, args=()):
        rows = q(sql, args)
        return rows[0] if rows else None

    st = get_settings(q1)
    scope = params.get("scope") or {}
    todo = missing(q, scope, kinds, redo=bool(params.get("redo")))
    if params.get("limit"):
        todo = todo[: int(params["limit"])]
    if not todo:
        return None
    hint_all = clean_hint(params.get("language_hint")) or None
    langs_all = clean_languages(params["languages"]) if params.get("languages") else None
    files = []
    for f in todo:
        langs = langs_all or next((st["channel_ocr_languages"][c] for c in f["channels"] if c in st["channel_ocr_languages"]),
                                  st["languages"])
        hint = hint_all or next((st["whisper_hints"][c] for c in f["channels"] if st["whisper_hints"].get(c)), None)
        files.append({"sha256": f["sha256"], "path": os.path.join(runner.store, f["path"]), "mime": f["mime"],
                      "ocr": f["ocr"], "speech": f["speech"], "ocr_languages": langs, "whisper_lang": hint,
                      "channels": f["channels"], "post_ids": f["post_ids"][:5], "comment_ids": f["comment_ids"][:5]})
    # images first (fast, early coverage), then videos
    files.sort(key=lambda x: (x["mime"] or "").startswith("video"))
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "files.json"), "w", encoding="utf-8") as fh:
        json.dump(files, fh, ensure_ascii=False)
    with open(os.path.join(run_dir, "mediatext_run.json"), "w", encoding="utf-8") as fh:
        json.dump({"params": params, "settings": st, "derived": os.path.join(runner.ws, "derived"),
                   "files": len(files)}, fh, ensure_ascii=False)
    cmd = [WORKER_PY, WORKER, "--list", os.path.join(run_dir, "files.json"), "-o", run_dir,
           "--derived", os.path.join(runner.ws, "derived"), "--interval", str(st["interval_s"]), "--model", MODEL]
    if params.get("redo"):
        cmd.append("--force")
    return cmd


def is_run(run_dir):
    return os.path.exists(os.path.join(run_dir, "mediatext_run.json"))


# ---- import --------------------------------------------------------------------------------------

def _script(text):
    ar = len(re.findall(r"[؀-ۿ]", text))
    cy = len(re.findall(r"[Ѐ-ӿ]", text))
    la = len(re.findall(r"[A-Za-z]", text))
    if not (ar or cy or la):
        return None
    return max((ar, "ar"), (cy, "cyrillic"), (la, "latin"))[1]


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _segments(kind, doc):
    """(seq, t_start, t_end, text, lang, conf, bbox) rows from a derived JSON."""
    out = []
    if kind == "speech":
        for i, s in enumerate(doc.get("segments") or []):
            out.append((i, s["start"], s["end"], s["text"], doc.get("detected_language") or doc.get("language"), None, None))
    elif doc.get("media_kind") == "video":
        frames, dur, step = doc.get("frames") or [], doc.get("duration"), float((doc.get("params") or {}).get("interval_s") or 5)
        i = 0
        for k, fr in enumerate(frames):
            end = frames[k + 1]["t"] if k + 1 < len(frames) else min(fr["t"] + step, dur) if dur else fr["t"] + step
            for l in fr["lines"]:
                out.append((i, fr["t"], end, l["text"], _script(l["text"]), l.get("conf"), json.dumps(l.get("bbox"))))
                i += 1
    else:
        for i, l in enumerate(doc.get("lines") or []):
            out.append((i, None, None, l["text"], _script(l["text"]), l.get("conf"), json.dumps(l.get("bbox"))))
    return out


def _unindex(con, ids):
    fn = getattr(db, "unindex_media_text", None)       # provided by the search integration, if applied
    if fn and ids:
        fn(con, ids)


def _index(con, rowid, text):
    fn = getattr(db, "index_media_text", None)
    if fn:
        fn(con, rowid, text)


def store_result(con, workspace, row, job_id=None):
    """Replace the database rows of one (file, kind) with what its derived JSON says. Returns the run status."""
    sha, kind = row["sha256"], row["kind"]
    rel = None
    status, err, out_sha = row["status"], row.get("error"), row.get("output_sha256")
    doc = None
    if row.get("output_path"):
        path = row["output_path"]
        rel = os.path.relpath(path, workspace) if os.path.isabs(path) else path
        full = os.path.join(workspace, rel)
        try:
            out_sha = _sha_file(full)
            doc = json.load(open(full, encoding="utf-8"))
            if row.get("output_sha256") and row["output_sha256"] != out_sha:
                raise ValueError("derived file changed after the worker wrote it")
        except (OSError, ValueError) as e:
            status, err, doc = "error", f"cannot read derived output: {e}", None
    prev = con.execute("SELECT id, status FROM media_text_runs WHERE sha256 = ? AND kind = ? AND output_sha256 IS ? "
                       "AND status = ?", (sha, kind, out_sha, status)).fetchone()
    if prev and status in DONE:
        have = con.execute("SELECT 1 FROM media_text WHERE sha256 = ? AND kind = ? LIMIT 1", (sha, kind)).fetchone()
        if have or status != "ok":
            return status, 0                          # already imported (a skipped, unchanged output)
    if doc is not None:
        old = [r[0] for r in con.execute("SELECT id FROM media_text WHERE sha256 = ? AND kind = ?", (sha, kind))]
        _unindex(con, old)
        con.execute("DELETE FROM media_text WHERE sha256 = ? AND kind = ?", (sha, kind))
        n = 0
        for seq, t0, t1, text, lang, conf, bbox in _segments(kind, doc):
            cur = con.execute("INSERT INTO media_text(sha256, kind, seq, t_start, t_end, text, lang, conf, bbox) "
                              "VALUES (?,?,?,?,?,?,?,?,?)", (sha, kind, seq, t0, t1, text, lang, conf, bbox))
            _index(con, cur.lastrowid, text)
            n += 1
    else:
        n = 0
    langs = row.get("languages")
    if kind == "speech":
        langs = (row.get("params") or {}).get("language_hint")
    con.execute("""INSERT INTO media_text_runs(sha256, kind, status, tool, model, languages, params, output_path,
                       output_sha256, error, job_id, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sha, kind, status, row.get("tool"), row.get("model"), json.dumps(langs, ensure_ascii=False),
                 json.dumps(row.get("params"), ensure_ascii=False), rel, out_sha, err, job_id,
                 row.get("created") or db.now()))
    return status, n


def ingest_run(con, run_dir, store_dir, actor="system", job_id=None, log=print):
    """Import a finished worker run (called through ingest.ingest_run for run folders holding mediatext_run.json)."""
    ensure_schema(con)
    workspace = os.path.dirname(os.path.abspath(store_dir))
    path = os.path.join(run_dir, "mediatext.jsonl")
    last = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                last[(r["sha256"], r["kind"])] = r          # a restarted worker repeats finished files: keep the last
    stats = {"files": len({k[0] for k in last}), "segments": 0, "errors": 0}
    for row in last.values():
        status, n = store_result(con, workspace, row, job_id)
        stats["segments"] += n
        stats[f"{row['kind']}_{status}"] = stats.get(f"{row['kind']}_{status}", 0) + 1
        stats["errors"] += status == "error"
    db.audit(con, actor, "import_mediatext", run_dir, stats)
    con.commit()
    log(f"Imported media text {run_dir}: {stats}")
    return stats


def reindex(con, workspace):
    """Rebuild media_text / media_text_runs' segments from the derived JSON files (the record of truth)."""
    ensure_schema(con)
    n = 0
    for kind, name in (("ocr", "ocr.json"), ("speech", "whisper.json")):
        for full in glob.glob(os.path.join(workspace, "derived", "??", "*", name)):
            doc = json.load(open(full, encoding="utf-8"))
            row = {"sha256": doc["input_sha256"], "kind": kind, "status": doc.get("status", "ok"), "tool": doc.get("tool"),
                   "model": doc.get("model"), "languages": doc.get("languages"), "params": doc.get("params"),
                   "output_path": os.path.relpath(full, workspace), "created": doc.get("created")}
            n += store_result(con, workspace, row)[1]
    con.commit()
    return n


# ---- API -----------------------------------------------------------------------------------------

def register(app, ctx):
    ensure_schema(ctx.con)
    ctx.con.commit()
    q, q1 = ctx.q, ctx.q1
    workspace = os.path.abspath(getattr(ctx, "workspace", None) or os.path.dirname(ctx.store))

    def job_supported():
        from . import jobs
        return any("mediatext" in kinds for kinds in jobs.LANES.values())

    def runs_of(sha):
        rows = q("SELECT * FROM media_text_runs WHERE sha256 = ? ORDER BY id DESC", (sha,))
        for r in rows:
            for k in ("languages", "params"):
                try:
                    r[k] = json.loads(r[k]) if r[k] else None
                except ValueError:
                    pass
        return rows

    def segments_of(sha):
        rows = q("SELECT id, kind, seq, t_start, t_end, text, lang, conf, bbox FROM media_text WHERE sha256 = ? "
                 "ORDER BY kind, seq", (sha,))
        out = {"ocr": [], "speech": []}
        for r in rows:
            r["bbox"] = json.loads(r["bbox"]) if r["bbox"] else None
            out[r["kind"]].append(r)
        return out

    @app.get("/api/mediatext/status")
    def api_status():
        out = {"worker_ready": worker_ready(), "model": MODEL, "model_available": model_available(),
               "job_supported": job_supported(), "settings": get_settings(q1)}
        for kind in ("ocr", "speech"):
            all_ = [f for f in targets(q, {}, (kind,)) if f[kind]]
            miss = missing(q, {}, (kind,))
            out[kind] = {"eligible": len(all_), "missing": len(miss), "done": len(all_) - len(miss)}
            # the share that lives in comment sections (a file held by both posts and comments counts in each)
            c_all = [f for f in all_ if f["comment_ids"]]
            c_miss = [f for f in miss if f["comment_ids"]]
            out[kind]["comments"] = {"eligible": len(c_all), "missing": len(c_miss), "done": len(c_all) - len(c_miss)}
        out["errors"] = (q1("SELECT COUNT(*) n FROM media_text_runs WHERE id IN (SELECT MAX(id) FROM media_text_runs "
                            "GROUP BY sha256, kind) AND status = 'error'") or {}).get("n", 0)
        job = q1("SELECT id, status, params, started, run_dir, created FROM jobs WHERE kind = 'mediatext' "
                 "AND status IN ('queued', 'running', 'importing') ORDER BY id DESC LIMIT 1")
        if job:
            prog = {}
            try:
                prog = json.load(open(os.path.join(job["run_dir"], "progress.json"), encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                pass
            job["params"] = json.loads(job["params"] or "{}")
            job["progress"] = {k: prog.get(k) for k in ("phase", "detail", "totals", "peak_rss_gb", "updated_at")}
        out["running"] = job
        return out

    @app.post("/api/mediatext/run")
    async def api_run(request: Request):
        """{"kind": "ocr"|"speech"|"both", "scope": {"post_ids"?, "comment_ids"?, "channel"?, "case"?, "all_missing"?: true,
                                                    "only"?: "posts"|"comments"},
            "languages"?: ["ar-SA", ...], "language_hint"?: "ar", "redo"?: false, "limit"?: N} -> queues a local job."""
        b = await request.json()
        kind = b.get("kind", "both")
        if kind not in ("ocr", "speech", "both"):
            raise HTTPException(400, "kind must be ocr, speech or both.")
        scope = b.get("scope") or {}
        if not isinstance(scope, dict) or not any(scope.get(k) for k in ("post_ids", "comment_ids", "channel", "case", "all_missing")):
            raise HTTPException(400, "Say what to read: scope = {post_ids | comment_ids | channel | case | all_missing: true}. "
                                     "Nothing runs automatically.")
        if scope.get("only") not in (None, "", "posts", "comments"):
            raise HTTPException(400, "scope.only is \"posts\" or \"comments\" (leave it out to read both).")
        for k in ("post_ids", "comment_ids"):
            if scope.get(k) and not (isinstance(scope[k], list) and all(isinstance(i, int) and not isinstance(i, bool) for i in scope[k])):
                raise HTTPException(400, f"scope.{k} must be a list of ids.")
        params = {"kind": kind, "scope": {k: scope[k] for k in ("post_ids", "comment_ids", "channel", "case", "all_missing", "only")
                                          if scope.get(k)}}
        if b.get("languages"):
            params["languages"] = clean_languages(b["languages"])
        if clean_hint(b.get("language_hint")):
            params["language_hint"] = clean_hint(b["language_hint"])
        if b.get("redo"):
            params["redo"] = True
        if b.get("limit"):
            params["limit"] = max(1, int(b["limit"]))
        if not job_supported():
            raise HTTPException(503, "The job runner does not know the mediatext job yet (jobs.py hook missing).")
        if not worker_ready():
            raise HTTPException(503, "The media-text worker is not installed: create tools/models/.venv "
                                     "(see tools/models/README.md).")
        if kind in ("speech", "both") and not model_available():
            raise HTTPException(503, f"The Whisper model {MODEL} is not in the local Hugging Face cache; it is never "
                                     "downloaded automatically.")
        todo = missing(q, params["scope"], _kinds(params), redo=bool(params.get("redo")))
        counts = {"ocr": sum(f["ocr"] for f in todo), "speech": sum(f["speech"] for f in todo)}
        if not todo:
            return {"id": None, "queued": counts, "note": "Nothing to read: every file in scope already has a result "
                                                          "(use redo to read again)."}
        return {"id": ctx.runner.submit("mediatext", params, ctx.actor()), "queued": counts}

    @app.get("/api/mediatext/settings")
    def api_get_settings():
        return get_settings(q1)

    @app.post("/api/mediatext/settings")
    async def api_set_settings(request: Request):
        b = await request.json()
        cur = get_settings(q1)

        def do():
            new = clean_settings(b, cur)
            ctx.con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                            (SETTINGS_KEY, json.dumps(new, ensure_ascii=False)))
            db.audit(ctx.con, ctx.actor(), "mediatext_settings", None, new)
            return new
        return ctx.write(do)

    @app.get("/api/mediatext/{sha256}")
    def api_file(sha256: str):
        sha = sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise HTTPException(404)
        media = q("SELECT m.id, m.post_id, m.kind, m.path, m.mime, p.channel FROM media m JOIN posts p ON p.id = m.post_id "
                  "WHERE m.sha256 = ?", (sha,))
        cmedia = q("SELECT m.id, m.comment_id, m.kind, m.path, m.mime, c.channel FROM comment_media m "
                   "JOIN comments c ON c.id = m.comment_id WHERE m.sha256 = ?", (sha,))
        if not media and not cmedia:
            raise HTTPException(404, "No collected file has this hash.")
        seg = segments_of(sha)
        d = os.path.join("derived", sha[:2], sha)
        return {"sha256": sha, "media": media, "comment_media": cmedia, "runs": runs_of(sha), "segments": seg, "derived_dir": d,
                "srt": os.path.join(d, "whisper.srt") if os.path.exists(os.path.join(workspace, d, "whisper.srt")) else None}

    def files_text(rows):
        out = []
        for m in rows:
            seg = segments_of(m["sha256"])
            runs = runs_of(m["sha256"])
            out.append({"media_id": m["id"], "kind": m["kind"], "idx": m["idx"], "sha256": m["sha256"], "mime": m["mime"],
                        "runs": [{k: r[k] for k in ("kind", "status", "tool", "model", "languages", "created", "error")}
                                 for r in runs[:4]],
                        "ocr_text": "\n".join(s["text"] for s in seg["ocr"]),
                        "speech_text": " ".join(s["text"] for s in seg["speech"]),
                        "segments": seg})
        return out

    @app.get("/api/post/{post_id}/mediatext")
    def api_post(post_id: int):
        if not q1("SELECT 1 FROM posts WHERE id = ?", (post_id,)):
            raise HTTPException(404)
        return {"post_id": post_id, "media": files_text(
            q("SELECT id, kind, idx, sha256, path, mime FROM media WHERE post_id = ? ORDER BY idx, id", (post_id,)))}

    @app.get("/api/comments/{comment_id}/mediatext")
    def api_comment(comment_id: int):
        """OCR / speech text of the media attached to one comment (the twin of /api/post/{id}/mediatext)."""
        if not q1("SELECT 1 FROM comments WHERE id = ?", (comment_id,)):
            raise HTTPException(404)
        return {"comment_id": comment_id, "media": files_text(
            q("SELECT id, kind, idx, sha256, path, mime FROM comment_media WHERE comment_id = ? ORDER BY idx, id",
              (comment_id,)))}

    def auto_run(job, stats):
        """After a job downloads new files: read them, if the setting is on (default off)."""
        if job["kind"] == "mediatext" or not (stats.get("media_new") or 0):
            return
        if not get_settings(q1)["auto_after_media_download"] or not job_supported() or not worker_ready():
            return
        if q1("SELECT 1 FROM jobs WHERE kind = 'mediatext' AND status IN ('queued', 'running')"):
            return
        ctx.runner.submit("mediatext", {"kind": "both" if model_available() else "ocr", "scope": {"all_missing": True}},
                          "system")

    ctx.runner.hooks["after_job"].append(auto_run)
