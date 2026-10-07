"""Crawler hardening: markup-drift warnings, request failures and backoff, media downloads, resume, and
what ingest does with a run that went wrong. Runs tgspider against a local fake t.me (tests/crawlfix.py);
nothing here touches the network or the real workspace.
Run from the repo root: .venv/bin/python tests/test_crawl.py"""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("INVESTIGATOR_DICT", os.path.join(HERE, "fixtures", "dictionary_fixture.db"))
import tgspider
from crawlfix import JPEG, Site, serve
from investigator import db, ingest

fails = []
def check(name, cond, info=""):
    if not cond:
        fails.append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   {info}"))

TMP = tempfile.mkdtemp(prefix="crawltest-")
site = Site()
srv = serve(site)
tgspider.BASE_URL = site.base

# No real waiting: record what the crawler would have slept, and honour STOP like the real nap does.
class Slept(list):
    """Every wait the crawler asked for. `.retries` are the waits before a retry (the ones that
    also watch for STOP); the rest are pacing between requests and media retries."""
    @property
    def retries(self):
        return [s for s, aware in self if aware]

slept = Slept()
def fake_nap(seconds, stop_aware=False):
    slept.append((seconds, stop_aware))
    return bool(stop_aware and tgspider.PROG and os.path.exists(tgspider.PROG.stop_file))
tgspider.nap = fake_nap


def make_args(out, **kw):
    d = dict(depth=0, pages=3, max_channels=50, max_classify=500, no_classify_leaves=False, network_only=False,
             no_save_pages=False, no_posts=False, media="none", max_video_mb=1, delay=0.0, retries=1, out=out,
             max_runtime=0, max_requests=0, resume_from=[], top=30, quiet=True, recrawl={}, state=None)
    d.update(kw)
    return SimpleNamespace(**d)


def run(seeds, name, **kw):
    """One in-process crawl into a fresh folder. Returns (out dir, progress dict, nodes by name)."""
    out = os.path.join(TMP, name)
    with contextlib.redirect_stdout(io.StringIO()):        # write_reports prints a summary table
        tgspider.crawl(seeds, make_args(out, **kw))
    return out, prog(out), nodes(out)


def prog(out):
    with open(os.path.join(out, "progress.json"), encoding="utf-8") as f:
        return json.load(f)


def nodes(out):
    n = {}
    for r in tgspider.read_jsonl(os.path.join(out, "nodes.jsonl")):
        n.setdefault(r["username"], {}).update(r)
    return n


def posts(out):
    return tgspider.read_jsonl(os.path.join(out, "posts.jsonl"))


def codes(p):
    return sorted({w["code"] for w in p["warnings"]})


# ============================================================ 1. the markup-drift canary
site.add_channel("alpha", posts=12)
good = site.channel_page("alpha")
hdr, msgs, oldest = tgspider.parse_channel_page(good, "alpha")
check("canary: an intact page raises nothing", tgspider.page_issues(good, hdr, msgs) == [], tgspider.page_issues(good, hdr, msgs))
check("canary: parser read the fixture", len(msgs) == 5 and oldest == 8 and hdr["subscribers"] == 1200 and hdr["tg_id"] == 1000)

def issue_codes(html):
    h, m, _ = tgspider.parse_channel_page(html, "alpha")
    return {c for c, _ in tgspider.page_issues(html, h, m)}

check("canary: renamed wrapper class", "markup_changed" in issue_codes(site.channel_page("alpha", mode="drift_wrapper")))
check("canary: renamed data-post", "no_posts_parsed" in issue_codes(site.channel_page("alpha", mode="drift_datapost")))
check("canary: posts without a date", "date_missing" in issue_codes(site.channel_page("alpha", mode="nodate")))
check("canary: header markup gone", {"header_missing", "subscribers_missing"} <= issue_codes(good.replace("tgme_channel_info_header_title", "x").replace("counter_type", "y")))
check("canary: permanent id token gone", "id_missing" in issue_codes(good.replace('data-view="', 'data-vw="')))
check("canary: some posts unreadable", "posts_dropped" in issue_codes(good.replace('data-post="alpha/8"', 'data-pst="').replace('data-post="alpha/9"', 'data-pst="')))
check("canary: empty body", [c for c, _ in tgspider.profile_issues("", "https://t.me/x")] == ["empty_response"])
check("canary: block page", [c for c, _ in tgspider.profile_issues("<html>Attention Required</html>", "https://t.me/x")] == ["unrecognised_layout"])
check("canary: a plain profile is fine", tgspider.profile_issues(site.profile_page("nobody"), "https://t.me/nobody") == [])

# ============================================================ 2. torn files
d = os.path.join(TMP, "torn")
os.makedirs(d)
with open(os.path.join(d, "nodes.jsonl"), "w") as f:
    f.write(json.dumps({"username": "a", "status": "crawled"}) + "\n" + '{"username": "b", "sta')     # cut off mid-write
bad = []
check("read_jsonl skips a torn last line", [r["username"] for r in tgspider.read_jsonl(os.path.join(d, "nodes.jsonl"), bad)] == ["a"] and len(bad) == 1)
st = tgspider.State(d)
st.save_node({"username": "c", "status": "crawled"})
check("State repairs a missing newline, so the next record is not fused to the fragment",
      [r["username"] for r in tgspider.read_jsonl(os.path.join(d, "nodes.jsonl"))] == ["a", "c"] and len(st.bad_lines) == 1)

# ============================================================ 3. Fetcher
def fetcher(**kw):
    kw.setdefault("delay", 0.0)
    kw.setdefault("retries", 3)
    kw.setdefault("verbose", False)
    return tgspider.Fetcher(**kw)

site.add_channel("flaky", posts=6)
site.rule("flaky", "500", times=2)
slept.clear()
f = fetcher()
url, html = f.get(f"{site.base}/s/flaky")
check("Fetcher: retries 5xx and then succeeds", "post 6 of flaky" in html and f.requests == 3 and f.fail_streak == 0, f.requests)
check("Fetcher: waits grow between retries", len(slept.retries) == 2 and slept.retries[1] > slept.retries[0] * 1.3, slept)
check("Fetcher: slows down after a 5xx", f.delay > 0)

site.add_channel("limited", posts=6)
site.rule("limited", "429", times=1, retry_after=7)
slept.clear()
f = fetcher()
f.get(f"{site.base}/s/limited")
check("Fetcher: honours a short Retry-After", slept.retries and 8 <= slept.retries[0] <= 10.1, slept)

site.rule("limited", "429", retry_after=99999)
f = fetcher()
try:
    f.get(f"{site.base}/s/limited")
    check("Fetcher: a long Retry-After stops the run", False)
except tgspider.RunAbort as e:
    check("Fetcher: a long Retry-After stops the run", e.kind == "rate_limit" and f.requests == 1, (e.kind, f.requests))
f = fetcher(retries=0)
try:
    f.get(f"{site.base}/s/limited")
    check("Fetcher: ...even when it was the last try", False)
except tgspider.RunAbort as e:
    check("Fetcher: ...even when it was the last try", e.kind == "rate_limit", e.kind)
del site.rules["limited"]

site.add_channel("dead", posts=3)
site.rule("dead", "500")
f = fetcher(retries=2)
try:
    f.get(f"{site.base}/s/dead")
except urllib.error.HTTPError:
    pass
check("Fetcher: gives up after its retries and counts the failure", f.requests == 3 and f.fail_streak == 1, (f.requests, f.fail_streak))
del site.rules["dead"]
f.get(f"{site.base}/s/alpha")
check("Fetcher: a good request resets the failure streak", f.fail_streak == 0)

f = fetcher(max_requests=2)
f.get(f"{site.base}/s/alpha"); f.get(f"{site.base}/s/alpha")
try:
    f.get(f"{site.base}/s/alpha")
    check("Fetcher: --max-requests", False)
except tgspider.RunAbort as e:
    check("Fetcher: --max-requests", e.kind == "limit" and f.requests == 2, (e.kind, f.requests))

site.add_channel("cut", posts=6)
site.rule("cut", "truncate")
site.hits.clear()
f = fetcher()
_, html = f.get(f"{site.base}/s/cut")
check("Fetcher: a page cut off before </html> is retried once, then flagged", f.flags == ["truncated"] and site.hits["/s/cut"] == 2, (f.flags, site.hits["/s/cut"]))
check("Fetcher: ...and the flag becomes a warning", [c for c, _ in tgspider.fetch_issues(f)] == ["truncated"])

site.rule("cut", "short_body", times=1)
site.hits.clear()
f = fetcher()
_, html = f.get(f"{site.base}/s/cut")
check("Fetcher: a body shorter than Content-Length is retried", site.hits["/s/cut"] == 2 and html.rstrip().endswith("</html>"), site.hits["/s/cut"])
del site.rules["cut"]

f = fetcher()
final, html = f.get(f"{site.base}/s/nobody")
check("Fetcher: follows a redirect to the profile", final.endswith("/nobody") and "tgme_page" in html, final)

# ============================================================ 4. media downloads
def media_post(*names, kind="photo"):
    return {"channel": "m", "id": 1, "photos": [f"{site.base}/media/{n}" for n in names] if kind == "photo" else [],
            "video_thumbs": [], "videos": [f"{site.base}/media/{n}" for n in names] if kind == "video" else []}

def dl(post, media="photos"):
    out = os.path.join(TMP, "media-" + str(len(os.listdir(TMP))))
    os.makedirs(out)
    files = tgspider.download_media(post, make_args(out, media=media))
    left = [p for r, _, fs in os.walk(out) for p in fs if p.endswith(".part")]
    return out, files, left

out, files, left = dl(media_post("ok.jpg"))
check("media: a good file is stored", len(files) == 1 and open(os.path.join(out, files[0]), "rb").read() == JPEG)
site.hits.clear()
p = media_post("short.jpg")
out, files, left = dl(p)
check("media: a truncated file is never kept", files == [] and left == [] and p["media_failed"][0]["error"].startswith("ConnectionError"),
      (files, left, p.get("media_failed")))
check("media: ...it was retried", site.hits["/media/short.jpg"] == tgspider.MEDIA_TRIES, site.hits["/media/short.jpg"])
p = media_post("html.jpg")
out, files, left = dl(p)
check("media: an error page is not stored as a photo", files == [] and left == [] and "ValueError" in p["media_failed"][0]["error"], p.get("media_failed"))
site.hits.clear()
p = media_post("gone.jpg")
out, files, left = dl(p)
check("media: an expired link is recorded once, not retried",
      files == [] and site.hits["/media/gone.jpg"] == 1 and "expired" in p["media_failed"][0]["error"], (site.hits, p.get("media_failed")))
site.rule("media/flaky.jpg", "500", times=2)
slept.clear()
p = media_post("flaky.jpg")
out, files, left = dl(p)
check("media: a CDN 5xx is retried with backoff and then works", len(files) == 1 and "media_failed" not in p and len(slept) == 2 and slept[1][0] > slept[0][0], (files, slept))
p = media_post("big.mp4", kind="video")
out, files, left = dl(p, media="all")
check("media: a video over the cap is skipped, not failed", files == [] and "media_failed" not in p and left == [])
p = media_post("bigstream.mp4", kind="video")
out, files, left = dl(p, media="all")
check("media: ...also when the size is only known while streaming", files == [] and "media_failed" not in p and left == [])
p = media_post("ok.jpg", "gone.jpg", "ok.jpg")
p["photos"][2] = f"{site.base}/media/ok2.jpg"
out, files, left = dl(p)
check("media: one bad file does not stop the others", len(files) == 2 and len(p["media_failed"]) == 1 and p["media_failed"][0]["index"] == 1, p.get("media_failed"))


# ============================================================ 5. whole crawls
site.add_channel("seed1", posts=12, links=["chan2", "grp1", "bot1", "ghost"], bio=["chan3"],
                 media={12: ["ok.jpg"], 11: ["ok.jpg", "gone.jpg"]}, videos={10: "clip.mp4"})
site.add_channel("chan2", posts=8, links=["seed1"])
site.add_channel("chan3", posts=6)
site.add_profile("grp1", "group")
site.add_profile("bot1", "bot")
out, p, n = run(["seed1"], "happy", depth=1, media="photos")
check("crawl: seeds and what they link to are crawled", [n[x]["status"] for x in ("seed1", "chan2", "chan3")] == ["crawled"] * 3, {k: v["status"] for k, v in n.items()})
check("crawl: other names are classified", (n["grp1"]["type"], n["bot1"]["type"], n["ghost"]["type"]) == ("group", "bot", "unknown"))
check("crawl: every post is saved once", sorted(x["id"] for x in posts(out) if x["channel"] == "seed1") == list(range(1, 13)) and len(posts(out)) == 12 + 8 + 6)
check("crawl: the covered range is claimed for a clean crawl", n["seed1"]["covered"] == {"min_id": 1, "max_id": 12, "to_latest": True}, n["seed1"]["covered"])
check("crawl: phase is done and only the failed media is warned about", p["phase"] == "done" and codes(p) == ["media_failed"], (p["phase"], codes(p)))
p11 = next(x for x in posts(out) if x["channel"] == "seed1" and x["id"] == 11)
check("crawl: a media failure is recorded on the post", len(p11["media_failed"]) == 1 and len(p11["media_files"]) == 1 and "expired" in p11["media_failed"][0]["error"], p11.get("media_failed"))
pages_kept = tgspider.read_jsonl(os.path.join(out, "pages.jsonl"))
check("crawl: raw pages are kept and posts point at them", pages_kept and all(x["page_sha256"] in {r["sha256"] for r in pages_kept} for x in posts(out)))
check("crawl: reports were written", os.path.exists(os.path.join(out, "channels.csv")))

# --- a channel that fails does not stall the run, and is not retried inside it
site.add_channel("bad1", posts=6); site.rule("bad1", "500")
site.add_channel("good1", posts=6)
site.hits.clear()
out, p, n = run(["bad1", "good1"], "isolate")
check("failure: the channel is recorded as an error", n["bad1"]["status"] == "error" and n["good1"]["status"] == "crawled")
check("failure: it was requested once plus its retries, not over and over", site.hits["/s/bad1"] == 2, site.hits["/s/bad1"])
check("failure: the run still finishes", p["phase"] == "done" and p["totals"]["errors"] == 1 and "abort" not in p)
del site.rules["bad1"]

# --- circuit breaker: Telegram refusing everything
for x in "abcde":
    site.add_channel(f"brk_{x}", posts=4); site.rule(f"brk_{x}", "500")
site.hits.clear()
out, p, n = run([f"brk_{x}" for x in "abcde"], "breaker")
check("breaker: the run stops after repeated failures", p["phase"] == "aborted" and p["abort"]["kind"] == "breaker", p.get("abort"))
check("breaker: the rest is not requested", site.hits["/s/brk_e"] == 0 and sum(1 for v in n.values() if v["status"] == "error") == tgspider.BREAKER_LIMIT, (dict(site.hits), list(n)))
for x in "abcde":
    del site.rules[f"brk_{x}"]

# --- rate limiting: Telegram says to come back much later
site.add_channel("rl_a", posts=4); site.add_channel("rl_b", posts=4); site.add_channel("rl_c", posts=4)
site.rule("rl_b", "429", retry_after=99999)
out, p, n = run(["rl_a", "rl_b", "rl_c"], "ratelimit")
check("rate limit: a long Retry-After ends the run cleanly", p["phase"] == "aborted" and p["abort"]["kind"] == "rate_limit" and n["rl_a"]["status"] == "crawled", p.get("abort"))
check("rate limit: nothing after it was requested", "rl_c" not in n and "rl_b" not in n)
rw = tgspider.remaining_work([out], ["rl_a", "rl_b", "rl_c"], 1, 50)
check("rate limit: remaining_work lists what a resume would visit", rw["crawl"] == ["rl_b", "rl_c"] and rw["crawled"] == 1 and rw["failed"] == [], rw)
del site.rules["rl_b"]

# --- limits
for x in "xyz":
    site.add_channel(f"lim_{x}", posts=12)
out, p, n = run(["lim_x", "lim_y", "lim_z"], "maxreq", max_requests=5)
check("--max-requests ends the run cleanly", p["phase"] == "aborted" and p["abort"]["kind"] == "limit" and p["totals"]["requests"] == 5, (p.get("abort"), p["totals"]["requests"]))
out, p, n = run(["lim_x"], "maxtime", max_runtime=1e-9)
check("--max-runtime ends the run before it starts a channel", p["phase"] == "aborted" and p["abort"]["kind"] == "limit" and p["totals"]["requests"] == 0)

# --- STOP
out = os.path.join(TMP, "stopped"); os.makedirs(out); open(os.path.join(out, "STOP"), "w").close()
with contextlib.redirect_stdout(io.StringIO()):
    tgspider.crawl(["lim_x"], make_args(out))
check("STOP before a channel: stopped, nothing requested", prog(out)["phase"] == "stopped" and prog(out)["totals"]["requests"] == 0)
site.add_channel("stopme", posts=4); site.rule("stopme", "500")
_nap = tgspider.nap
def nap_then_stop(seconds, stop_aware=False):
    if stop_aware:
        open(tgspider.PROG.stop_file, "w").close()
    return _nap(seconds, stop_aware)
tgspider.nap = nap_then_stop
out, p, n = run(["stopme"], "stopwait", retries=3)
tgspider.nap = _nap
check("STOP during a retry wait: stopped at once, not after all the retries", p["phase"] == "stopped" and p["totals"]["requests"] == 1, (p["phase"], p["totals"]["requests"]))
del site.rules["stopme"]

# --- markup drift
site.add_channel("drift1", posts=12); site.rule("drift1", "drift_datapost")
site.add_channel("drift2", posts=12); site.rule("drift2", "drift_datapost")
site.add_channel("drift3", posts=12); site.rule("drift3", "drift_datapost")
out, p, n = run(["drift1", "drift2", "drift3"], "drift")
check("drift: unreadable posts become warnings, not a quiet empty crawl", "no_posts_parsed" in codes(p) and p["totals"]["parser_warnings"] >= 3, codes(p))
check("drift: the channel carries its warnings and claims no covered range", n["drift1"]["parse_warnings"] and n["drift1"]["covered"] is None and n["drift1"]["parse_warnings"][0]["page_sha256"])
check("drift: the whole run is flagged when no channel gave any post", "no_posts_run" in codes(p))
for x in ("drift1", "drift2", "drift3"):
    del site.rules[x]

site.add_channel("nodate1", posts=12); site.rule("nodate1", "nodate", times=1)     # the first preview page only
out, p, n = run(["nodate1"], "held")
kept = [x["id"] for x in posts(out)]
check("held back: posts from a page that did not read properly are not saved", 12 not in kept and 8 not in kept and len(kept) < 12 and len(kept) > 0, kept)
check("held back: ...and it is counted, the channel has no covered range", p["totals"]["posts_held_back"] == 5 and n["nodate1"]["covered"] is None, (p["totals"].get("posts_held_back"), n["nodate1"]["covered"]))
check("held back: the raw page is still kept", any(True for _ in tgspider.read_jsonl(os.path.join(out, "pages.jsonl"))))
del site.rules["nodate1"]

site.add_channel("stuck1", posts=30); site.rule("stuck1", "stuck")
out, p, n = run(["stuck1"], "stuck", pages=5)
check("paging that goes nowhere is flagged and stops", "pagination_stuck" in codes(p) and n["stuck1"]["pages"] == 1, (codes(p), n["stuck1"].get("pages")))
del site.rules["stuck1"]

# ============================================================ 6. resume
site.add_channel("rsm_a", posts=6, links=["rsm_e"])
site.add_channel("rsm_b", posts=6); site.rule("rsm_b", "500")
site.add_channel("rsm_c", posts=12); site.rule("rsm_c", "429", retry_after=99999, after=1)     # page 1 arrives, page 2 is refused
site.add_channel("rsm_d", posts=6)
site.add_channel("rsm_e", posts=6)
seeds = ["rsm_a", "rsm_b", "rsm_c", "rsm_d"]
run1, p1, n1 = run(seeds, "resume1", depth=2)
check("resume: run 1 stopped part-way, mid-channel", p1["phase"] == "aborted" and n1["rsm_a"]["status"] == "crawled" and n1["rsm_b"]["status"] == "error" and "rsm_c" not in n1 and any(x["channel"] == "rsm_c" for x in posts(run1)), (p1["phase"], {k: v["status"] for k, v in n1.items()}))
rw = tgspider.remaining_work([run1], seeds, 2, 50)
check("resume: remaining_work sees the error, the cut-off channel, the unvisited seed and the linked one",
      rw["crawl"] == ["rsm_b", "rsm_c", "rsm_d", "rsm_e"] and [f["username"] for f in rw["failed"]] == ["rsm_b"] and rw["crawled"] == 1, rw)
site.rules.clear()
site.hits.clear()
run2, p2, n2 = run(seeds, "resume2", depth=2, resume_from=[run1])
check("resume: a channel finished before is not read again", site.hits["/s/rsm_a"] == 0 and "rsm_a" not in n2, dict(site.hits))
check("resume: the error, the cut-off channel and the rest are read", all(n2[x]["status"] == "crawled" for x in ("rsm_b", "rsm_c", "rsm_d", "rsm_e")) and p2["phase"] == "done", {k: v["status"] for k, v in n2.items()})
check("resume: the earlier run's links seeded the frontier (rsm_e was found through rsm_a)", "rsm_e" in n2)
check("resume: the cut-off channel is read from its first page", site.hits["/s/rsm_c"] == 3 and len([x for x in posts(run2) if x["channel"] == "rsm_c"]) == 12, dict(site.hits))
check("resume: the earlier run's folder was not touched", tgspider.read_jsonl(os.path.join(run1, "nodes.jsonl")) == tgspider.read_jsonl(os.path.join(run1, "nodes.jsonl")) and not os.path.exists(os.path.join(run1, "nodes.jsonl.part")) and prog(run1)["phase"] == "aborted")
check("resume: progress says what it continued", p2.get("resumed_from") == [os.path.abspath(run1)])
rw = tgspider.remaining_work([run1, run2], seeds, 2, 50)
check("resume: nothing left after the resumed run", rw["crawl"] == [] and rw["classify"] == [] and rw["failed"] == [] and rw["crawled"] == 5, rw)
run3, p3, n3 = run(seeds, "resume3", depth=2, resume_from=[run1, run2])
check("resume: a third run has nothing to do", p3["totals"]["requests"] == 0 and p3["phase"] == "done")
run2b, p2b, n2b = run(seeds, "resume2b", depth=2, resume_from=[run1], max_channels=3)
check("resume: --max-channels counts what earlier runs crawled", p2b["totals"]["channels_crawled"] == 2 and p2b["phase"] == "done", p2b["totals"]["channels_crawled"])

# ============================================================ 7. torn state on resume
run4 = os.path.join(TMP, "torn-run")
os.makedirs(run4)
with open(os.path.join(run4, "posts.jsonl"), "w") as f:
    f.write('{"channel": "lim_x", "id": 1}\n{"channel": "lim_x", "i')
out, p, n = run(["lim_x"], "torn-run", pages=1)
check("torn file: the crawl resumes and says a line was skipped", p["phase"] == "done" and "state_lines_skipped" in codes(p), (p["phase"], codes(p)))

# ============================================================ 7b. the other crawl modes still work in the new loop
site.add_channel("rechk", posts=30)
out, p, n = run(["rechk"], "recrawl", pages=10, recrawl={"rechk": {"recheck_min_id": 20, "have_media_ids": []}}, no_classify_leaves=True)
check("re-crawl (--state): paging stops once past the re-check window", n["rechk"]["pages"] == 3 and n["rechk"]["status"] == "crawled" and p["phase"] == "done", (n["rechk"].get("pages"), p["phase"]))
site.add_channel("netone", posts=8, links=["nettwo"]); site.add_channel("nettwo", posts=4)
out, p, n = run(["netone"], "netonly", depth=2, network_only=True, no_posts=True, no_save_pages=True)
check("--network-only: links are mapped, no posts and no page copies are kept",
      n["nettwo"]["status"] == "crawled" and posts(out) == [] and not os.path.exists(os.path.join(out, "pages")) and
      any(e["target"] == "nettwo" for e in tgspider.read_jsonl(os.path.join(out, "edges.jsonl"))), list(os.listdir(out)))

# ============================================================ 8. the command line
env = {**os.environ, "TGSPIDER_BASE_URL": site.base, "PYTHONUNBUFFERED": "1"}
site.add_channel("slow1", posts=8); site.rule("slow1", "slow", seconds=30)
out = os.path.join(TMP, "sigterm")
proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tgspider.py"), "slow1", "-o", out, "--delay", "0", "--retries", "0"],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
t = time.time()
while time.time() - t < 15 and not (os.path.exists(os.path.join(out, "progress.json")) and site.hits["/s/slow1"]):
    time.sleep(0.2)
proc.send_signal(signal.SIGTERM)
try:
    code = proc.wait(timeout=15)
except subprocess.TimeoutExpired:
    proc.kill(); code = "hung"
check("SIGTERM: the crawler exits cleanly", code == 0, code)
check("SIGTERM: progress says cancelled and reports were written", prog(out)["phase"] == "cancelled" and os.path.exists(os.path.join(out, "channels.csv")))
del site.rules["slow1"]

out = os.path.join(TMP, "crash")
real_final, real_argv, real_term = tgspider.final_checks, sys.argv, signal.getsignal(signal.SIGTERM)
tgspider.final_checks = lambda: 1 / 0
sys.argv = ["tgspider", "lim_x", "-o", out, "--delay", "0", "--pages", "1", "-q"]
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        tgspider.main()
    code = None
except SystemExit as e:
    code = e.code
finally:
    tgspider.final_checks, sys.argv = real_final, real_argv
    signal.signal(signal.SIGTERM, real_term)
check("crash: exit code 1, phase failed, reports still written", code == 1 and prog(out)["phase"] == "failed" and prog(out)["abort"]["kind"] == "crash" and os.path.exists(os.path.join(out, "channels.csv")), (code, prog(out).get("phase")))
proc = subprocess.run([sys.executable, os.path.join(ROOT, "tgspider.py"), "lim_x", "-o", os.path.join(TMP, "cli"), "--delay", "0", "--pages", "1", "-q",
                       "--resume-from", run1, "--max-requests", "50", "--max-runtime", "60", "--retries", "0"], env=env, capture_output=True, text=True)
check("CLI: the new flags are accepted", proc.returncode == 0, proc.stderr[-300:])


# ============================================================ 9. what ingest does with runs that went wrong
con = db.connect(os.path.join(TMP, "ws", "investigator.db"))
STORE = os.path.join(TMP, "ws", "store")
def imp(out):
    return ingest.ingest_run(con, out, STORE, actor="test", log=lambda m: None)
def one(sql, *a):
    r = con.execute(sql, a).fetchone()
    return tuple(r) if r is not None else None
def audits(action, target=None):
    return one("SELECT COUNT(*) FROM audit WHERE action = ?" + (" AND target = ?" if target else ""), action, *([target] if target else []))[0]

# --- a failed visit must not rewrite what is known about the channel
site.add_channel("ing1", posts=12, links=["ing2"])
site.add_channel("ing2", posts=4)
out, p, n = run(["ing1"], "ing-a", media="photos")
st = imp(out)
check("ingest: a clean run imports", st["posts_new"] == 12 and "channels_failed" not in st and "parse_warnings" not in st, st)
check("ingest: a clean import has the same stats keys as before", set(st) == {"channels_new", "channels_updated", "posts_new", "posts_updated", "posts_changed", "posts_deleted", "posts_reappeared", "channel_events", "media_new", "pages_new", "edges_new", "renames"}, set(st))
site.rule("ing1", "500")
out_err, p, n = run(["ing1"], "ing-err")
st = imp(out_err)
check("ingest: a failed visit is reported", st.get("channels_failed") == 1, st)
check("ingest: ...but the channel keeps its type and preview flag", one("SELECT type, preview FROM channels WHERE username = 'ing1'") == ("channel", 1))
check("ingest: ...and no channel event is invented", one("SELECT COUNT(*) FROM channel_events WHERE channel = 'ing1'")[0] == 0)
check("ingest: the failure is in the audit log, once", audits("channel_crawl_failed", "ing1") == 1)
imp(out_err)
check("ingest: importing the same run again does not repeat it", audits("channel_crawl_failed", "ing1") == 1)
del site.rules["ing1"]
out, p, n = run(["ing1"], "ing-again")
st = imp(out)
check("ingest: the next good crawl raises no 'preview restored'", one("SELECT COUNT(*) FROM channel_events WHERE channel = 'ing1'")[0] == 0, st)
site.rule("ing3x", "500")
out, p, n = run(["ing3x"], "ing-newfail")
imp(out)
check("ingest: a seed that failed the first time is still recorded, with no invented type", one("SELECT type, status FROM channels WHERE username = 'ing3x'") == (None, "seed"), one("SELECT type, status FROM channels WHERE username = 'ing3x'"))

# --- media that could not be fetched is audited per post
imp(run_dir_happy := os.path.join(TMP, "happy"))
check("ingest: a media failure is audited on its post", audits("media_download_failed") == 1)
st = imp(run_dir_happy)
check("ingest: ...once, however often the run is imported", audits("media_download_failed") == 1 and st.get("media_failed") == 1, st)

# --- a page that lost its view counts must not blank the ones already on file
site.rule("ing1", "noviews")
con.execute("UPDATE posts SET last_captured = '2020-01-01T00:00:00' WHERE channel = 'ing1'")
con.commit()
before = one("SELECT views FROM posts WHERE channel = 'ing1' AND msg_id = 12")[0]
out, p, n = run(["ing1"], "ing-noviews")
st = imp(out)
check("ingest: posts were re-captured and warned about", "views_missing" in codes(p) and st["posts_updated"] == 12 and st.get("parse_warnings"), (codes(p), st))
check("ingest: ...without losing their view counts", before == 120 and one("SELECT views FROM posts WHERE channel = 'ing1' AND msg_id = 12")[0] == before, before)
check("ingest: the warning is in the audit log", audits("crawl_parser_warning", "ing1") == 1)
del site.rules["ing1"]

# --- real deletions are found; a crawl that did not read properly finds none
site.add_channel("del1", posts=12); site.add_channel("del2", posts=12)
out, p, n = run(["del1", "del2"], "del-a")
imp(out)
for c in ("del1", "del2"):
    site.channels[c]["ids"] = [i for i in site.channels[c]["ids"] if i not in (9, 10)]
con.execute("UPDATE posts SET first_captured = '2020-01-01T00:00:00' WHERE channel IN ('del1', 'del2')")
con.commit()
site.rule("del2", "nodate")
out, p, n = run(["del1", "del2"], "del-b")
st = imp(out)
check("deletions: found in the channel that read properly", st["posts_deleted"] == 2 and one("SELECT COUNT(*) FROM posts WHERE channel = 'del1' AND deleted_detected IS NOT NULL")[0] == 2, st)
check("deletions: not guessed in the channel whose pages did not read", one("SELECT COUNT(*) FROM posts WHERE channel = 'del2' AND deleted_detected IS NOT NULL")[0] == 0 and one("SELECT COUNT(*) FROM posts WHERE channel = 'del2'")[0] == 12)
del site.rules["del2"]
hand = os.path.join(TMP, "hand"); os.makedirs(hand)
with open(os.path.join(hand, "nodes.jsonl"), "w") as f:
    f.write(json.dumps({"username": "del2", "status": "crawled", "checked_at": "2026-09-30T00:00:00", "covered": {"min_id": 1, "max_id": 12, "to_latest": True},
                        "parse_warnings": [{"code": "x", "message": "y"}]}) + "\n")
check("deletions: a channel with parse warnings is skipped even if a covered range is present", imp(hand)["posts_deleted"] == 0)

# --- torn lines
torn_dir = os.path.join(TMP, "torn-import"); os.makedirs(torn_dir)
with open(os.path.join(torn_dir, "nodes.jsonl"), "w") as f:
    f.write(json.dumps({"username": "ing2", "status": "crawled", "checked_at": "2026-09-30T00:00:00"}) + '\n{"username": "ing')
with open(os.path.join(torn_dir, "posts.jsonl"), "w") as f:
    f.write('{"channel": "ing2", "id": 99, "scraped_at": "2026-09-30T00:00:00", "text": "kept"}\n{"chan')
st = imp(torn_dir)
check("ingest: torn lines are skipped and counted, the rest imports", st.get("torn_lines") == 2 and st["posts_new"] == 1, st)

srv.shutdown()
print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
