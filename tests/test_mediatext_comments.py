"""OCR / speech of comment media: which files get read (posts + comment sections, one entry per file), how the read
text is tied back to comments (search, entities, places). Plain asserts, temp DB.
Run from the repo root: .venv/bin/python tests/test_mediatext_comments.py"""
import os, shutil, sys, tempfile
from types import SimpleNamespace
HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.dirname(HERE))
os.environ["INVESTIGATOR_DICT"] = os.path.join(FIX, "dictionary_fixture.db")
from investigator import db, entities, mediatext

results = []
def check(name, cond, info=""):
    results.append((name, bool(cond)))
    assert cond, f"{name} {info}"

tmp = tempfile.mkdtemp()
try:
    con = db.connect(os.path.join(tmp, "t.db"))
    con.executescript(entities.SCHEMA)
    mediatext.ensure_schema(con)
    q = lambda sql, a=(): [dict(r) for r in con.execute(sql, a).fetchall()]
    now = db.now()

    def post(ch, mid):
        return con.execute("INSERT INTO posts(channel, msg_id, text, first_captured, last_captured) VALUES (?,?,?,?,?)",
                           (ch, mid, "post", now, now)).lastrowid
    def pmedia(pid, kind, sha, mime="image/jpeg", idx=0):
        con.execute("INSERT INTO media(post_id, kind, idx, sha256, path, mime) VALUES (?,?,?,?,?,?)",
                    (pid, kind, idx, sha, f"{sha}.bin", mime))
    def comment(ch, mid, text="c"):
        return con.execute("INSERT INTO comments(group_id, msg_id, channel, date, text, is_mirror, media_types, first_captured, last_captured) "
                           "VALUES (1,?,?,?,?,0,'[]',?,?)", (mid, ch, "2026-09-01T00:00:00", text, now, now)).lastrowid
    def cmedia(cid, kind, sha, mime, idx=0):
        con.execute("INSERT INTO comment_media(comment_id, kind, idx, sha256, path, mime) VALUES (?,?,?,?,?,?)",
                    (cid, kind, idx, sha, f"{sha}.bin", mime))

    # posts: alpha has a photo; beta has a small web video; delta has only a thumb (read)
    pa, pb, pd = post("alpha", 1), post("beta", 2), post("delta", 3)
    pmedia(pa, "photo", "a" * 64)
    pmedia(pa, "thumb", "b" * 64)                     # skipped: the post has a bigger photo
    pmedia(pd, "thumb", "c" * 64)
    pmedia(pb, "video", "d" * 64, "video/mp4", 1)
    pmedia(pa, "photo", "s" * 64, idx=2)              # the file a comment reposts (same sha256)
    # comments: photo, video, pdf, sticker, the reposted post photo, and one in another channel
    c1, c2, c3, c4, c5, c6 = (comment("alpha", i) for i in range(1, 7))
    cmedia(c1, "photo", "1" * 64, "image/png")
    cmedia(c2, "video", "2" * 64, "video/mp4")
    cmedia(c3, "file", "3" * 64, "application/pdf")
    cmedia(c4, "file", "4" * 64, "application/x-tgsticker")
    cmedia(c5, "photo", "s" * 64, "image/jpeg")
    cmedia(comment("gamma", 9), "photo", "9" * 64, "image/jpeg")
    con.execute("INSERT INTO cases(id, name, created) VALUES (1, 'k', ?)", (now,))
    con.execute("INSERT INTO case_channels(case_id, channel) VALUES (1, 'alpha')")
    con.commit()

    T = lambda scope, kinds=("ocr", "speech"): {f["sha256"][0]: f for f in mediatext.targets(q, scope, kinds)}

    # ---------------------------------------------------------------- what gets read
    t = T({"all_missing": True})
    check("all: posts and comment sections both read", set(t) == set("acd12s9"), sorted(t))
    check("all: pdf and sticker skipped", "3" not in t and "4" not in t)
    check("all: thumb skipped when the post has a bigger photo", "b" not in t and "c" in t)
    check("all: comment video gets speech, comment photo does not", t["2"]["speech"] and not t["1"]["speech"])
    check("shared file: one entry, both sides recorded", t["s"]["post_ids"] == [pa] and t["s"]["comment_ids"] == [c5], t["s"])
    check("comment-only file has no post ids", t["1"]["post_ids"] == [] and t["1"]["comment_ids"] == [c1])
    check("channels merged from posts and comments", t["s"]["channels"] == ["alpha"] and t["9"]["channels"] == ["gamma"])

    check("only=comments", set(T({"all_missing": True, "only": "comments"})) == set("12s9"))
    check("only=posts", set(T({"all_missing": True, "only": "posts"})) == set("acds"))
    check("post_ids narrows to posts", set(T({"post_ids": [pa]})) == set("as"))
    check("comment_ids narrows to comments", set(T({"comment_ids": [c1, c2]})) == set("12"))
    check("post_ids + comment_ids: exactly those", set(T({"post_ids": [pa], "comment_ids": [c1]})) == set("as1"))
    check("channel scope covers both", set(T({"channel": "alpha"})) == set("a12s") and set(T({"channel": "@gamma"})) == {"9"})
    check("case scope covers both", set(T({"case": 1})) == set("a12s"))
    check("kinds=ocr: no speech job", not any(f["speech"] for f in mediatext.targets(q, {"all_missing": True}, ("ocr",))))

    # ---------------------------------------------------------------- what is still missing
    con.execute("INSERT INTO media_text_runs(sha256, kind, status, created) VALUES (?,?,?,?)", ("1" * 64, "ocr", "ok", now))
    con.execute("INSERT INTO media_text_runs(sha256, kind, status, created) VALUES (?,?,?,?)", ("2" * 64, "speech", "no_audio", now))
    con.execute("INSERT INTO media_text_runs(sha256, kind, status, created) VALUES (?,?,?,?)", ("9" * 64, "ocr", "error", now))
    miss = {f["sha256"][0]: f for f in mediatext.missing(q, {"all_missing": True}, ("ocr", "speech"))}
    check("missing: done files drop out, errors are retried", "1" not in miss and miss["9"]["ocr"])
    check("missing: video keeps OCR, loses speech", miss["2"]["ocr"] and not miss["2"]["speech"])
    check("missing: redo brings everything back", "1" in {f["sha256"][0] for f in mediatext.missing(q, {"all_missing": True}, ("ocr",), redo=True)})

    # ---------------------------------------------------------------- read text -> comments
    def read(sha, kind, text):
        cur = con.execute("INSERT INTO media_text(sha256, kind, seq, text) VALUES (?,?,0,?)", (sha, kind, text))
        db.index_media_text(con, cur.lastrowid, text)
        return cur.lastrowid
    m1 = read("1" * 64, "ocr", "Drone strike on the market")
    m2 = read("2" * 64, "speech", "the convoy was attacked")
    ms = read("s" * 64, "ocr", "shared photo text mentions a bridge")
    expr = db.fts_query("convoy")
    rows = q(f"SELECT * FROM ({db.comment_media_text_match_sql()})", (expr,))
    check("comment match: speech tied to its comment", [(r["comment_id"], r["kind"]) for r in rows] == [(c2, "speech")], rows)
    rows = q(f"SELECT * FROM ({db.comment_media_text_match_sql()})", (db.fts_query("bridge"),))
    check("comment match: a reposted file matches its comment", [r["comment_id"] for r in rows] == [c5], rows)
    rows = q(f"SELECT * FROM ({db.media_text_match_sql()})", (db.fts_query("bridge"),))
    check("post match: the same file still matches its post", [r["post_id"] for r in rows] == [pa], rows)
    rows = q(f"SELECT * FROM ({db.comment_media_text_match_sql()})", (db.fts_query("bridge market"),))
    check("comment match: terms are ANDed inside one segment", rows == [])

    # entities / places rows tagged on media_text reach the comment (and only via comment_media)
    con.execute("INSERT INTO entities(item_type, item_id, kind, value, raw, start, \"end\") VALUES ('media_text', ?, 'phone', '+249111', '+249111', 0, 7)", (m2,))
    con.execute("INSERT INTO entities(item_type, item_id, kind, value, raw, start, \"end\") VALUES ('comment', ?, 'phone', '+249222', '+249222', 0, 7)", (c1,))
    con.commit()
    rows = q(f"SELECT comment_id, kind, value FROM ({entities.comment_source(con, 'entities', 'kind, value')}) ORDER BY comment_id")
    check("comment_source: own rows plus those of its media text", rows == [
        {"comment_id": c1, "kind": "phone", "value": "+249222"}, {"comment_id": c2, "kind": "phone", "value": "+249111"}], rows)
    clauses, args = entities.filter_sql(con, {"entity": "phone:+249111"}, "comment", "c.id")
    got = [r["id"] for r in q(f"SELECT c.id FROM comments c WHERE {' AND '.join(clauses)}", args)]
    check("entity filter finds a comment through its media text", got == [c2], got)
    src = entities.post_source(con, "entities", "kind, value")
    check("post side untouched by comment media", q(f"SELECT post_id FROM ({src}) WHERE value = '+249111'") == [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("PASS", sum(ok for _, ok in results), "/", len(results))
