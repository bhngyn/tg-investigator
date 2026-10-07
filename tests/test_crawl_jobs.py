"""Crawl jobs end to end: JobRunner starts tgspider as a subprocess against the fake t.me (tests/crawlfix.py),
imports the result into a scratch workspace, and reports how the run went. Covers the status a crawl gets from
what it says about itself, resuming, the stall watchdog and recovery after a restart.
Run from the repo root: .venv/bin/python tests/test_crawl_jobs.py"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault("INVESTIGATOR_DICT", os.path.join(HERE, "fixtures", "dictionary_fixture.db"))
from crawlfix import Site, serve
from investigator import db, jobs

fails = []
def check(name, cond, info=""):
    if not cond:
        fails.append(name)
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"   {info}"))

TMP = tempfile.mkdtemp(prefix="crawljobs-")
site = Site()
srv = serve(site)
os.environ["TGSPIDER_BASE_URL"] = site.base          # the crawler subprocesses inherit this
ws = os.path.join(TMP, "ws")
con = db.connect(os.path.join(ws, "investigator.db"))
runner = jobs.JobRunner(con, con.lock, ws)
FAST = {"delay": 0, "retries": 1, "pages": 3}        # a fake server needs no politeness


def row(jid):
    r = con.execute("SELECT * FROM jobs WHERE id = ?", (jid,)).fetchone()
    return dict(r)

def summary(jid):
    return json.loads(row(jid)["summary"] or "{}")

def wait(jid, timeout=90):
    t = time.time()
    while time.time() - t < timeout:
        if row(jid)["status"] not in ("queued", "running", "importing"):
            return row(jid)["status"]
        time.sleep(0.3)
    return "TIMEOUT:" + row(jid)["status"]

def crawl(seeds, **kw):
    return runner.submit("crawl", {"seeds": seeds, "depth": 1, "max_channels": 50, "media": "none", **FAST, **kw}, "test")

def post_counts():
    return {r[0]: r[1] for r in con.execute("SELECT channel, COUNT(*) FROM posts GROUP BY channel")}


# ============================================================ status mapping (no processes needed)
def outcome(phase, code=0, **kw):
    run_dir = tempfile.mkdtemp(dir=TMP)
    if phase:
        json.dump({"phase": phase, "totals": {}, "warnings": [], **({"abort": {"reason": "why", "kind": "limit"}} if phase == "aborted" else {})},
                  open(os.path.join(run_dir, "progress.json"), "w"))
    job = {"id": 0, "run_dir": run_dir}
    return runner._crawl_outcome(job, {"seeds": ["x" * 5], "depth": 1}, code, {}, **kw)[0]

for args, want in [
    (("done",), "done"), (("stopped",), "stopped"), (("aborted",), "interrupted"), (("cancelled",), "interrupted"),
    (("failed", 1), "failed"), (("crawling", -9), "interrupted"), (("crawling", 1), "failed"), ((None, 0), "done"),
    (("done", 0), "done"),
]:
    check(f"status: phase {args[0]}, exit {args[1] if len(args) > 1 else 0} is {want}", outcome(*args) == want, outcome(*args))
check("status: a cancel request wins", outcome("aborted", cancelled=True) == "cancelled")
check("status: a stall is interrupted", outcome("crawling", -2, stalled=True) == "interrupted")
check("status: STOP with a crawler that left no phase is stopped", outcome(None, 0, graceful=True) == "stopped")
check("status: recovered, never finished is interrupted", outcome("crawling", None, recovered=True) == "interrupted")

# ============================================================ a clean crawl
site.add_channel("jobone", posts=8, links=["jobtwo"])
site.add_channel("jobtwo", posts=6)
jid = crawl(["jobone"], depth=2)              # depth 2: the seed and what it links to
check("clean crawl: done", wait(jid) == "done", row(jid))
s = summary(jid)
check("clean crawl: counts, no note (so the UI shows them), no warnings", s["posts_new"] == 14 and "note" not in s and s["warnings"] == [] and s["remaining"]["crawl"] == 0, s)
check("clean crawl: the crawler was given its safety limits", "--max-runtime" in open(row(jid)["log_path"]).read() and "--max-requests" in open(row(jid)["log_path"]).read())

# ============================================================ a run that gives up cleanly is not "done"
site.add_channel("abtone", posts=4); site.add_channel("abttwo", posts=4); site.add_channel("abtthree", posts=4)
site.rule("abttwo", "429", retry_after=99999)
jid = crawl(["abtone", "abttwo", "abtthree"])
st = wait(jid)
s = summary(jid)
check("abort: a rate-limited crawl is interrupted, not done", st == "interrupted" and s["exit_code"] == 0, (st, s))
check("abort: the summary says why and what is left", s["abort"]["kind"] == "rate_limit" and s["remaining"]["crawl"] == 1 and s["remaining"]["channels"] == ["abttwo"], s)     # names are visited in order: abtthree came first
check("abort: the note carries the counts and the reason (the UI shows only the note)", "new posts" in s["note"] and "stopped early" in s["note"] and "Resume" in s["note"], s.get("note"))
check("abort: what was collected was imported", post_counts().get("abtone") == 4 and "abttwo" not in post_counts())

# ---- resume it
site.rules.clear()
site.hits.clear()
new = runner.resume_crawl(jid, "test")
check("resume: queued as a new job that points at the old one", json.loads(row(new)["params"])["resume_of"] == jid and row(new)["run_dir"] != row(jid)["run_dir"])
check("resume: finishes", wait(new) == "done", row(new))
check("resume: the finished channel was not read again", site.hits["/s/abtone"] == 0 and post_counts().get("abttwo") == 4 and post_counts().get("abtthree") == 4, (dict(site.hits), post_counts()))
check("resume: the crawler was told which run to continue", f"--resume-from {row(jid)['run_dir']}" in open(row(new)["log_path"]).read())
check("resume: nothing is duplicated in the database", set(post_counts().values()) <= {4, 6, 8} and post_counts()["abtone"] == 4, post_counts())
check("resume: the old job's folder is untouched", json.load(open(os.path.join(row(jid)["run_dir"], "progress.json")))["phase"] == "aborted")
check("resume: the summary of the resumed job is clean", summary(new)["remaining"]["crawl"] == 0 and "still to read" not in summary(new).get("note", ""), summary(new))
for bad, code in ((999, 404), (new, 409)):
    try:
        runner.resume_crawl(bad, "test")
        check(f"resume: refused for job {bad}", False)
    except jobs.ResumeError as e:
        check(f"resume: refused for job {bad} ({code}: {e})", e.code == code)
mid = runner.submit("comments", {"channel": "x" * 6}, "test")
try:
    runner.resume_crawl(mid, "test"); check("resume: refused for a non-crawl", False)
except jobs.ResumeError as e:
    check("resume: refused for a non-crawl", e.code == 400)

# ============================================================ a channel that fails, then is resumed
site.add_channel("failone", posts=4); site.add_channel("failtwo", posts=4)
site.rule("failtwo", "500")
jid = crawl(["failone", "failtwo"])
st = wait(jid)
s = summary(jid)
check("failed channel: the crawl finishes and says which channel failed", st == "done" and [f["username"] for f in s["failed_channels"]] == ["failtwo"] and "1 channel(s) failed" in s["note"], (st, s))
del site.rules["failtwo"]
new = runner.resume_crawl(jid, "test")
check("failed channel: a resume retries it", wait(new) == "done" and summary(new)["failed_channels"] == [] and post_counts().get("failtwo") == 4, (row(new), post_counts()))

# ============================================================ drift shows up on the job
site.add_channel("driftone", posts=8); site.rule("driftone", "nodate")
jid = crawl(["driftone"])
st = wait(jid)
s = summary(jid)
check("drift: the job is done but carries warnings, counts and a note", st == "done" and s["parser_warnings"] >= 1 and s["warnings"][0]["code"] and "parser warning" in s["note"] and "held back" in s["note"], (st, s))
del site.rules["driftone"]

# ============================================================ the stall watchdog
jobs.STALL_SECONDS = 3
site.add_channel("hangone", posts=4); site.rule("hangone", "slow", seconds=120)
t = time.time()
jid = crawl(["hangone"], retries=0)
st = wait(jid, 120)
s = summary(jid)
check("watchdog: a hung crawler is stopped and marked interrupted", st == "interrupted" and s.get("stalled") is True and "no progress" in s["note"], (st, s))
check("watchdog: quickly, and it can be resumed", time.time() - t < 60 and s["remaining"]["crawl"] == 1, (time.time() - t, s))
jobs.STALL_SECONDS = 600
del site.rules["hangone"]

# ============================================================ recovery after a restart
adopt_dir = os.path.join(runner.runs, "adopt-job")
os.makedirs(adopt_dir)
json.dump({"phase": "aborted", "totals": {"parser_warnings": 0}, "warnings": [], "abort": {"reason": "the request limit was reached", "kind": "limit"}},
          open(os.path.join(adopt_dir, "progress.json"), "w"))
open(os.path.join(adopt_dir, "nodes.jsonl"), "w").write(json.dumps({"username": "adoptone", "status": "crawled", "type": "channel", "depth": 0, "checked_at": "2026-09-30T00:00:00"}) + "\n")
con.execute("INSERT INTO jobs(kind, params, status, created, run_dir, log_path, pid) VALUES ('crawl', ?, 'running', ?, ?, ?, 999999)",
            (json.dumps({"seeds": ["adoptone"], "depth": 1}), db.now(), adopt_dir, adopt_dir + ".log"))
con.commit()
aid = con.execute("SELECT MAX(id) FROM jobs").fetchone()[0]
jobs.JobRunner(con, con.lock, ws)
t = time.time()
while time.time() - t < 30 and row(aid)["status"] in ("running", "importing"):
    time.sleep(0.3)
s = summary(aid)
check("recovery: an orphaned crawl that gave up is interrupted with the reason and a note", row(aid)["status"] == "interrupted" and "the request limit" in s["note"] and "recovered" in s["note"], (row(aid)["status"], s))
check("recovery: what it collected was imported", con.execute("SELECT 1 FROM channels WHERE username = 'adoptone'").fetchone() is not None)


# ============================================================ the API: POST /api/jobs {"resume_of": id}
with socket.socket() as sk:
    sk.bind(("127.0.0.1", 0)); PORT = sk.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"
api_ws = os.path.join(TMP, "ws-api")
server = subprocess.Popen([sys.executable, "-m", "investigator", "-w", api_ws, "--port", str(PORT), "--no-browser"], cwd=ROOT,
                          stdout=open(os.path.join(TMP, "server.log"), "w"), stderr=subprocess.STDOUT, env=os.environ.copy())
TOKEN = None
def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"X-Token": TOKEN or "", "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req)
        return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")
try:
    for _ in range(60):
        try:
            TOKEN = urllib.request.urlopen(BASE + "/").read().decode().split('x-token" content="')[1].split('"')[0]
            break
        except Exception:
            time.sleep(0.5)
    def api_wait(jid, timeout=90):
        t = time.time()
        while time.time() - t < timeout:
            st = next(j["status"] for j in call("GET", "/api/jobs")[1] if j["id"] == jid)
            if st not in ("queued", "running", "importing"):
                return st
            time.sleep(0.5)
        return "TIMEOUT"
    site.add_channel("apione", posts=6); site.add_channel("apitwo", posts=6)
    site.rule("apitwo", "429", retry_after=99999)
    code, r = call("POST", "/api/jobs", {"kind": "crawl", "seeds": "apione, apitwo", "depth": 1, "pages": 2, "media": "none"})
    first = r["id"]
    check("api: a crawl that gets rate limited ends interrupted", code == 200 and api_wait(first) == "interrupted")
    site.rules.clear()
    code, r = call("POST", "/api/jobs", {"resume_of": first})
    check("api: resume_of queues a resume", code == 200 and r["id"] != first, (code, r))
    second = r["id"]
    code, r = call("POST", "/api/jobs", {"resume_of": first})
    check("api: a second resume while one is active is refused", code == 409, (code, r))
    check("api: the resume finishes", api_wait(second) == "done")
    code, r = call("POST", "/api/jobs", {"resume_of": second})
    check("api: nothing left to resume is a 409", code == 409 and "Nothing is left" in r["detail"], (code, r))
    check("api: unknown job is a 404", call("POST", "/api/jobs", {"resume_of": 9999})[0] == 404)
    check("api: a bad id is a 400", call("POST", "/api/jobs", {"resume_of": "abc"})[0] == 400)
    check("api: only crawls resume", call("POST", "/api/jobs", {"kind": "media", "resume_of": first})[0] == 400)
    jobs_now = {j["id"]: j for j in call("GET", "/api/jobs")[1]}
    check("api: the job list carries the summary the UI shows", "still to read" in json.loads(jobs_now[first]["summary"])["note"] and json.loads(jobs_now[first]["summary"])["abort"]["kind"] == "rate_limit")
finally:
    server.terminate()
    server.wait(timeout=15)

srv.shutdown()
print(f"\n{'FAILED: ' + ', '.join(fails) if fails else 'all passed'}")
sys.exit(1 if fails else 0)
