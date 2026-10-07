"""
Background jobs: crawls (tgspider), full-media downloads (tgmedia) and comment
collection (tgcomments), plus the schedules that re-run comment collection.

Jobs run as subprocesses with no stdin (they can never prompt), in two lanes:
  - web: crawls of the public preview
  - api: everything that uses the logged-in session (media, comments). These must
    never overlap: the Telegram session file can only be used by one process.
Within a lane jobs run one at a time, in order. Each gets its own run folder under
<workspace>/runs/, and its output is imported into the database when it finishes.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db, ingest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TGSPIDER = os.path.join(ROOT, "tgspider.py")
TGMEDIA = os.path.join(ROOT, "tgmedia.py")
TGCOMMENTS = os.path.join(ROOT, "tgcomments.py")
TGDISCOVER = os.path.join(ROOT, "tgdiscover.py")
MEDIASCAN = os.path.join(ROOT, "mediascan.py")
# web: public preview; api: the logged-in session (one process at a time); local: no network
LANES = {"web": ("crawl",), "api": ("media", "comments", "discover"), "local": ("mediascan", "mediatext")}
VENV_PY = os.path.join(ROOT, ".venv", "bin", "python")


# A crawl that has not touched its progress file for this long is hung (waits and downloads refresh
# it every couple of seconds), so it is stopped and can be resumed.
STALL_SECONDS = 600
CRAWL_MAX_RUNTIME = 12 * 3600       # a crawl never runs longer than this; what it collected is kept


def _python():
    return VENV_PY if os.path.exists(VENV_PY) else sys.executable


class ResumeError(Exception):
    """A crawl cannot be resumed; `code` is the HTTP status the API answers with."""

    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class _Stall:
    """Has a job stopped writing its progress file? Judged on the monotonic clock, which does not
    advance while the laptop sleeps, so closing the lid can never look like a hang."""

    def __init__(self, run_dir, limit=None):
        self.path, self.limit = os.path.join(run_dir or "", "progress.json"), limit or STALL_SECONDS
        self.mtime, self.seen = None, time.monotonic()

    def check(self):
        try:
            m = os.stat(self.path).st_mtime_ns
        except OSError:
            m = None
        now = time.monotonic()
        if m != self.mtime:
            self.mtime, self.seen = m, now
        return now - self.seen > self.limit


def _terminate(proc, log=None):
    """Stop a hung child: Ctrl-C first (the crawler saves and writes its reports), then SIGTERM
    (same), then SIGKILL. Returns the exit code."""
    for sig, grace in ((signal.SIGINT, 30), (signal.SIGTERM, 15), (signal.SIGKILL, 10)):
        if log:
            log(f"stalled: sending {sig.name}")
        try:
            proc.send_signal(sig)
            return proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        except ProcessLookupError:
            break
    return proc.wait()


def _terminate_pid(pid, run_dir):
    """The same, for a crawler this server did not start (found again after a restart)."""
    for sig, grace in ((signal.SIGINT, 30), (signal.SIGTERM, 15), (signal.SIGKILL, 10)):
        try:
            os.kill(pid, sig)
        except OSError:
            return
        end = time.monotonic() + grace
        while time.monotonic() < end:
            if not _alive(pid, run_dir):
                return
            time.sleep(1)


class JobRunner:
    def __init__(self, con, lock, workspace):
        self.con, self.lock, self.ws = con, lock, workspace
        self.runs = os.path.join(workspace, "runs")
        self.store = os.path.join(workspace, "store")
        os.makedirs(self.runs, exist_ok=True)
        self.scheduler_error = None
        # other modules plug in here: "tick" runs every scheduler pass (watchlists),
        # "after_job" runs after a job's output is imported (alerts)
        self.hooks = {"tick": [], "after_job": []}
        self.procs = {lane: None for lane in LANES}
        self.currents = {lane: None for lane in LANES}
        self._wake = {lane: threading.Event() for lane in LANES}
        self._adopting = threading.Event()
        # Jobs left "running" by a previous server: if the crawler process is still
        # alive, wait for it and import when it ends; otherwise import what it
        # collected. Collected data must never be stranded in a run folder.
        with self.lock:
            orphans = [dict(r) for r in self.con.execute(
                "SELECT * FROM jobs WHERE status IN ('running', 'importing')").fetchall()]
        if orphans:
            self._adopting.set()
            threading.Thread(target=self._adopt, args=(orphans,), daemon=True).start()
        for lane in LANES:
            threading.Thread(target=self._loop, args=(lane,), daemon=True).start()
        threading.Thread(target=self._scheduler, daemon=True).start()

    def _wake_all(self):
        for ev in self._wake.values():
            ev.set()

    def _adopt(self, orphans):
        try:
            for job in orphans:
                pid = job.get("pid")
                stall = _Stall(job["run_dir"]) if job["kind"] == "crawl" else None
                stalled = False
                while pid and _alive(pid, job["run_dir"]):
                    if stall and stall.check():
                        stalled = True
                        _terminate_pid(pid, job["run_dir"])
                        break
                    time.sleep(2)
                params = json.loads(job["params"] or "{}")
                with self.lock:
                    self.con.execute("UPDATE jobs SET status = 'importing' WHERE id = ?", (job["id"],))
                    self.con.commit()
                    stats = ingest.ingest_run(self.con, job["run_dir"], self.store, actor="job",
                                              job_id=job["id"], only_media=(job["kind"] == "media"),
                                              network_only=bool(params.get("network_only")),
                                              log=lambda m: None) if os.path.isdir(job["run_dir"] or "") else {}
                if job["kind"] == "crawl":
                    status, extra = self._crawl_outcome(job, params, None, stats, stalled=stalled, recovered=True)
                else:
                    phase = _progress_phase(job["run_dir"])
                    status = {"done": "done", "stopped": "stopped"}.get(phase, "interrupted")
                    extra = {"note": "recovered after a server restart"}
                self._finish(job["id"], status, {**stats, **extra})
                self._after_job(job, stats)
        finally:
            self._adopting.clear()
            self._wake_all()

    # ---- public ---------------------------------------------------------------

    def submit(self, kind, params, actor):
        with self.lock:
            cur = self.con.execute("INSERT INTO jobs(kind, params, status, created) VALUES (?,?,?,?)",
                                   (kind, json.dumps(params, ensure_ascii=False), "queued", db.now()))
            jid = cur.lastrowid
            stamp = time.strftime("%Y%m%d-%H%M%S")
            run_dir = os.path.join(self.runs, f"{stamp}-job{jid}-{kind}")   # unique even across workspace rebuilds
            self.con.execute("UPDATE jobs SET run_dir = ?, log_path = ? WHERE id = ?",
                             (run_dir, run_dir + ".log", jid))
            db.audit(self.con, actor, f"job_{kind}_queued", f"job {jid}", params)
            self.con.commit()
        self._wake_all()
        return jid

    def stop(self, jid, actor):
        """Graceful stop: the job finishes its current channel (or video), then
        stops normally and everything is imported."""
        with self.lock:
            row = self.con.execute("SELECT status, run_dir FROM jobs WHERE id = ?", (jid,)).fetchone()
            if not row:
                return False
            if row["status"] == "queued":
                self.con.execute("UPDATE jobs SET status = 'cancelled', finished = ? WHERE id = ?",
                                 (db.now(), jid))
            else:
                os.makedirs(row["run_dir"], exist_ok=True)
                open(os.path.join(row["run_dir"], "STOP"), "w").close()
            db.audit(self.con, actor, "job_stop", f"job {jid}")
            self.con.commit()
        return True

    def cancel(self, jid, actor):
        with self.lock:
            row = self.con.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()
            if not row:
                return False
            if row["status"] == "queued":
                self.con.execute("UPDATE jobs SET status = 'cancelled', finished = ? WHERE id = ?",
                                 (db.now(), jid))
            db.audit(self.con, actor, "job_cancel", f"job {jid}")
            self.con.commit()
        for lane, cur in self.currents.items():
            proc = self.procs[lane]
            if cur == jid and proc and proc.poll() is None:
                proc.send_signal(signal.SIGINT)   # the tools save progress on Ctrl-C
        return True

    def resume_chain(self, jid):
        """Run folders of a crawl and of the runs it continued, oldest first."""
        dirs, seen = [], set()
        while jid and jid not in seen:
            seen.add(jid)
            with self.lock:
                row = self.con.execute("SELECT run_dir, params FROM jobs WHERE id = ?", (jid,)).fetchone()
            if not row:
                break
            if row["run_dir"] and os.path.isdir(row["run_dir"]):
                dirs.append(row["run_dir"])
            jid = json.loads(row["params"] or "{}").get("resume_of")
        return dirs[::-1]

    def resume_crawl(self, jid, actor):
        """Queue a crawl that continues a finished one: channels it read are skipped, the ones that
        failed, were cut off or were never reached are visited. New captures go to a new run folder;
        the earlier ones are only read. Returns the new job id."""
        import tgspider
        with self.lock:
            row = self.con.execute("SELECT kind, status, params FROM jobs WHERE id = ?", (jid,)).fetchone()
            active = self.con.execute("SELECT id, params FROM jobs WHERE kind = 'crawl' AND "
                                      "status IN ('queued','running','importing')").fetchall()
        if not row:
            raise ResumeError(f"There is no job #{jid}.", 404)
        if row["kind"] != "crawl":
            raise ResumeError("Only crawls can be resumed.")
        if row["status"] in ("queued", "running", "importing"):
            raise ResumeError(f"Job #{jid} is still {row['status']}.", 409)
        params = json.loads(row["params"] or "{}")
        seeds = params.get("seeds") or []
        for j in active:
            if sorted(json.loads(j["params"] or "{}").get("seeds", [])) == sorted(seeds):
                raise ResumeError(f"A crawl of {', '.join('@' + x for x in seeds)} is already queued or running "
                                  f"(job #{j['id']}).", 409)
        left = tgspider.remaining_work(self.resume_chain(jid), seeds, int(params.get("depth", 1)),
                                       int(params.get("max_channels", 50)),
                                       classify_leaves=not params.get("recheck_days"))
        if not left["crawl"] and not left["classify"]:
            raise ResumeError(f"Nothing is left to resume: job #{jid} read everything it was asked to.", 409)
        return self.submit("crawl", {**params, "resume_of": jid}, actor)

    # ---- worker ---------------------------------------------------------------

    def _loop(self, lane):
        kinds = LANES[lane]
        while True:
            if self._adopting.is_set():          # finish recovering old jobs first
                time.sleep(2)
                continue
            with self.lock:
                job = self.con.execute(f"SELECT * FROM jobs WHERE status = 'queued' AND kind IN "
                                       f"({','.join('?' * len(kinds))}) ORDER BY id LIMIT 1", kinds).fetchone()
            if not job:
                self._wake[lane].wait(5)
                self._wake[lane].clear()
                continue
            try:
                self._run(dict(job), lane)
            except Exception as e:                       # never kill the worker
                self._finish(job["id"], "failed", {"error": f"{type(e).__name__}: {e}"})

    # ---- schedules ------------------------------------------------------------

    def _scheduler(self):
        """Queue a comment collection for every schedule that is due. A schedule
        that was due while the app was closed runs once, not once per missed interval."""
        while True:
            time.sleep(15)
            if self._adopting.is_set():
                continue
            try:
                self.run_due_schedules()
                for fn in self.hooks["tick"]:
                    fn()
                self.scheduler_error = None
            except Exception as e:             # keep going, but make the failure visible
                self.scheduler_error = f"{db.now()} {type(e).__name__}: {e}"
                print(f"scheduler error: {self.scheduler_error}", file=sys.stderr, flush=True)

    def run_due_schedules(self, only_id=None):
        now = datetime.now(timezone.utc)
        with self.lock:
            if only_id:
                due = self.con.execute("SELECT * FROM schedules WHERE id = ?", (only_id,)).fetchall()
            else:
                due = self.con.execute("SELECT * FROM schedules WHERE enabled = 1 AND "
                                       "(next_run IS NULL OR next_run <= ?)", (db.now(),)).fetchall()
        queued = []
        for s in due:
            nxt = (now + timedelta(minutes=max(1, s["interval_min"]))).isoformat(timespec="seconds")
            busy = self.active_comment_job(s["channel"])
            jid = busy or self.submit("comments", {"channel": s["channel"], "media": s["media"],
                                                   "schedule_id": s["id"]}, "schedule")
            with self.lock:
                self.con.execute("UPDATE schedules SET last_run = ?, next_run = ?, last_job = ? WHERE id = ?",
                                 (db.now(), nxt, jid, s["id"]))
                self.con.commit()
            if not busy:
                queued.append(jid)
        return queued

    def active_comment_job(self, channel):
        with self.lock:
            for j in self.con.execute("SELECT id, params FROM jobs WHERE kind = 'comments' AND "
                                      "status IN ('queued','running','importing')").fetchall():
                if json.loads(j["params"] or "{}").get("channel") == channel:
                    return j["id"]
        return None

    def _setting(self, key, default):
        with self.lock:
            r = self.con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

    def _write_comment_state(self, run_dir, channel):
        """Where the last collection stopped, and which captured messages to re-check."""
        days = int(self._setting("recheck_days", "7") or 7)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with self.lock:
            g = self.con.execute("SELECT group_id, max_msg_id FROM comment_groups WHERE channel = ?",
                                 (channel,)).fetchone()
            ids = [r[0] for r in self.con.execute(
                "SELECT msg_id FROM comments WHERE group_id = ? AND deleted_detected IS NULL AND date >= ? "
                "ORDER BY msg_id DESC LIMIT 5000", (g["group_id"], since))] if g else []
        state = {"group_id": g["group_id"] if g else None, "max_msg_id": g["max_msg_id"] if g else 0,
                 "recheck_ids": ids, "recheck_days": days}
        path = os.path.join(run_dir, "state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        return path

    def _run(self, job, lane):
        jid, params = job["id"], json.loads(job["params"] or "{}")
        run_dir, log_path = job["run_dir"], job["log_path"]
        os.makedirs(run_dir, exist_ok=True)

        if job["kind"] == "crawl":
            cmd = [sys.executable, TGSPIDER, *params["seeds"], "-o", run_dir,
                   "--depth", str(params.get("depth", 1)), "--pages", str(params.get("pages", 10)),
                   "--max-channels", str(params.get("max_channels", 50)),
                   "--media", params.get("media", "photos")]
            # safety nets, not targets: a crawl that runs away (Telegram keeps answering, a loop) still ends
            cmd += ["--max-runtime", str(params.get("max_runtime", CRAWL_MAX_RUNTIME)),
                    "--max-requests", str(params.get("max_requests", 2 * (
                        int(params.get("max_channels", 50)) * int(params.get("pages", 10)) + 500) + 200))]
            for key in ("delay", "retries"):               # pace and patience (defaults: the crawler's own)
                if params.get(key) is not None:
                    cmd += [f"--{key}", str(params[key])]
            for d in self.resume_chain(params.get("resume_of")):
                cmd += ["--resume-from", d]
            if params.get("network_only"):
                cmd.append("--network-only")
            if params.get("recheck_days"):            # watchlist re-crawl: stop after the re-check window
                cmd += ["--state", self._write_crawl_state(run_dir, params["seeds"], int(params["recheck_days"])),
                        "--no-classify-leaves"]
        elif job["kind"] == "mediascan":
            n = self._write_scan_list(run_dir, params)
            if n == 0:
                return self._finish(jid, "done", {"note": "nothing new to scan"})
            cmd = [sys.executable, MEDIASCAN, "--list", os.path.join(run_dir, "scan_list.json"), "-o", run_dir]
        elif job["kind"] == "mediatext":
            from . import mediatext
            cmd = mediatext.job_command(self, run_dir, params)     # argv | None (nothing to read) | str (error)
            if cmd is None:
                return self._finish(jid, "done", {"note": "nothing new to read"})
            if isinstance(cmd, str):
                return self._finish(jid, "failed", {"error": cmd})
        elif job["kind"] == "discover":
            spec = os.path.join(run_dir, "discover.json")
            with open(spec, "w", encoding="utf-8") as f:
                json.dump(params, f, ensure_ascii=False)
            cmd = [_python(), TGDISCOVER, "--spec", spec, "-o", run_dir]
        elif job["kind"] == "media":
            n = self._write_media_targets(run_dir, params)
            if n == 0:
                return self._finish(jid, "done", {"note": "nothing to download"})
            cmd = [_python(), TGMEDIA, "-o", run_dir, "--what", params.get("what", "videos"),
                   "--max-mb", str(params.get("max_mb", 200))]
        elif job["kind"] == "comments":
            cmd = [_python(), TGCOMMENTS, params["channel"], "-o", run_dir,
                   "--state", self._write_comment_state(run_dir, params["channel"]),
                   "--limit", str(params.get("limit", 200)), "--media", params.get("media", "none"),
                   "--max-mb", str(params.get("max_mb", 50))]
            if self._setting("collect_phones", "1") == "0":
                cmd.append("--no-phones")
        else:
            return self._finish(jid, "failed", {"error": f"unknown job kind {job['kind']}"})

        with self.lock:
            self.con.execute("UPDATE jobs SET status = 'running', started = ? WHERE id = ?",
                             (db.now(), jid))
            self.con.commit()
        with open(log_path, "a", encoding="utf-8") as logf:
            logf.write(f"$ {' '.join(cmd)}\n")
            logf.flush()
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=logf,
                                    stderr=subprocess.STDOUT, cwd=ROOT,
                                    env={**os.environ, "PYTHONUNBUFFERED": "1"})
            self.procs[lane], self.currents[lane] = proc, jid
            with self.lock:
                self.con.execute("UPDATE jobs SET pid = ? WHERE id = ?", (proc.pid, jid))
                self.con.commit()
            stalled = False
            if job["kind"] == "crawl":
                stall = _Stall(run_dir)
                while True:
                    try:
                        code = proc.wait(timeout=5)
                        break
                    except subprocess.TimeoutExpired:
                        if stall.check():
                            stalled = True
                            code = _terminate(proc, lambda m: (logf.write(m + "\n"), logf.flush()))
                            break
            else:
                code = proc.wait()
        self.currents[lane], self.procs[lane] = None, None

        with self.lock:
            cancelled = self.con.execute("SELECT 1 FROM audit WHERE action = 'job_cancel' AND target = ?",
                                         (f"job {jid}",)).fetchone()
            self.con.execute("UPDATE jobs SET status = 'importing' WHERE id = ?", (jid,))
            self.con.commit()
            # import whatever was collected, even from a cancelled or failed run
            stats = ingest.ingest_run(self.con, run_dir, self.store, actor="job", job_id=jid,
                                      only_media=(job["kind"] == "media"),
                                      network_only=bool(params.get("network_only")),
                                      log=lambda m: open(log_path, "a").write(m + "\n"))
        graceful = os.path.exists(os.path.join(run_dir, "STOP"))
        if job["kind"] == "crawl":
            status, extra = self._crawl_outcome(job, params, code, stats, cancelled=bool(cancelled),
                                                stalled=stalled, graceful=graceful)
        else:
            status = "cancelled" if cancelled else ("stopped" if graceful and code == 0 else
                                                    "done" if code == 0 else "failed")
            extra = {}
        self._finish(jid, status, {**stats, "exit_code": code, **extra})
        self._after_job(job, stats)

    def _crawl_outcome(self, job, params, code, stats, cancelled=False, stalled=False, graceful=False,
                       recovered=False):
        """How a crawl ended, from what the crawler itself says (its progress file), not only its exit
        code: a run that gave up cleanly exits 0 and used to be filed as done. Returns
        (status, summary fields): the parser warnings, failed channels, what is left to do, and a
        `note` (the UI shows only the note when there is one, so it carries the counts too)."""
        import tgspider
        run_dir = job["run_dir"]
        prog = _read_progress(run_dir)
        phase = prog.get("phase")
        abort = prog.get("abort") or {}
        if cancelled:
            status = "cancelled"
        elif stalled:
            status = "interrupted"
        elif code is not None and code < 0:
            status = "interrupted"                       # killed from outside: nothing wrong with the crawler
        elif code:
            status = "failed"
        elif phase == "failed":
            status = "failed"
        elif phase in ("aborted", "cancelled"):
            status = "interrupted"
        elif phase == "stopped" or (graceful and phase in (None, "crawling", "classifying")):
            status = "stopped"
        elif phase == "done" or code == 0:
            status = "done"
        else:                                            # recovered after a restart, and it never finished
            status = "interrupted"
        chain = self.resume_chain(params.get("resume_of")) + ([run_dir] if run_dir and os.path.isdir(run_dir) else [])
        left = tgspider.remaining_work(chain, params.get("seeds") or [], int(params.get("depth", 1)),
                                       int(params.get("max_channels", 50)),
                                       classify_leaves=not params.get("recheck_days"))
        totals = prog.get("totals") or {}
        warnings = [{k: w.get(k) for k in ("code", "channel", "message", "count", "page_sha256")}
                    for w in (prog.get("warnings") or [])[:30]]
        extra = {"phase": phase, "warnings": warnings, "parser_warnings": totals.get("parser_warnings", 0),
                 "failed_channels": left["failed"][:50],
                 "remaining": {"crawl": len(left["crawl"]), "classify": len(left["classify"]),
                               "channels": left["crawl"][:20]},
                 "resume_of": params.get("resume_of")}
        if abort:
            extra["abort"] = abort
        if stalled:
            extra["stalled"] = True
        counts = [f"{stats[k]} {label}" for k, label in (("posts_new", "new posts"), ("posts_changed", "edited"),
                                                          ("posts_deleted", "found deleted"), ("media_new", "media"),
                                                          ("channels_new", "new channels")) if stats.get(k)]
        problems = []
        if stalled:
            problems.append(f"stopped because it made no progress for {STALL_SECONDS // 60} minutes")
        elif abort:
            problems.append(f"stopped early: {abort.get('reason')}")
        elif status == "interrupted":
            problems.append("the crawler stopped before it finished")
        elif status == "failed":
            problems.append(f"the crawler exited with code {code}; see the log")
        if left["failed"]:
            problems.append(f"{len(left['failed'])} channel(s) failed")
        if left["crawl"] and status != "done":
            problems.append(f"{len(left['crawl'])} channel(s) still to read (Resume continues from here)")
        if extra["parser_warnings"]:
            problems.append(f"{extra['parser_warnings']} parser warning(s): a page did not read as expected")
        if totals.get("posts_held_back"):
            problems.append(f"{totals['posts_held_back']} posts held back")
        if totals.get("media_failed"):
            problems.append(f"{totals['media_failed']} media file(s) not downloaded")
        if recovered:
            problems.append("recovered after a server restart")
        if problems:
            extra["note"] = " · ".join(counts + problems)
        if status == "failed":
            extra["error"] = abort.get("reason") or f"The crawler exited with code {code}; see the log."
        return status, extra

    def _after_job(self, job, stats):
        for fn in self.hooks["after_job"]:
            try:
                fn(job, stats)
            except Exception as e:                  # a broken alert rule must not break collection
                print(f"after-job hook error (job {job['id']}): {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    def _write_scan_list(self, run_dir, params):
        """Collected files that haven't been scanned yet (optionally one channel or case)."""
        where, args = "", []
        if params.get("channel"):
            where, args = " AND ch = ?", [params["channel"]]
        elif params.get("case"):
            where, args = " AND ch IN (SELECT channel FROM case_channels WHERE case_id = ?)", [int(params["case"])]
        with self.lock:
            rows = self.con.execute(f"""
                SELECT sha256, path, mime FROM (
                  SELECT m.sha256, m.path, m.mime, p.channel ch, m.id FROM media m JOIN posts p ON p.id = m.post_id
                  UNION ALL
                  SELECT m.sha256, m.path, m.mime, c.channel ch, m.id FROM comment_media m JOIN comments c ON c.id = m.comment_id)
                WHERE sha256 NOT IN (SELECT sha256 FROM media_scan) {where}
                GROUP BY sha256 ORDER BY MAX(id) DESC LIMIT ?""", (*args, int(params.get("limit", 5000)))).fetchall()
        files = [{"sha256": r["sha256"], "path": os.path.join(self.store, r["path"]), "mime": r["mime"]} for r in rows]
        with open(os.path.join(run_dir, "scan_list.json"), "w", encoding="utf-8") as f:
            json.dump(files, f)
        return len(files)

    def _write_crawl_state(self, run_dir, seeds, recheck_days):
        """For each channel: the lowest message id still inside the re-check window (the crawl
        stops once it pages past it) and the posts whose media is already stored."""
        since = (datetime.now(timezone.utc) - timedelta(days=recheck_days)).isoformat(timespec="seconds")
        state = {}
        with self.lock:
            for ch in seeds:
                r = self.con.execute("SELECT MIN(msg_id) lo FROM posts WHERE channel = ? AND date >= ?",
                                     (ch, since)).fetchone()
                top = self.con.execute("SELECT MAX(msg_id) FROM posts WHERE channel = ?", (ch,)).fetchone()[0]
                if top is None:
                    continue                         # never collected: normal crawl up to --pages
                lo = r["lo"] if r["lo"] is not None else top
                have = [x[0] for x in self.con.execute(
                    "SELECT DISTINCT p.msg_id FROM posts p JOIN media m ON m.post_id = p.id "
                    "WHERE p.channel = ? AND p.msg_id >= ?", (ch, lo))]
                state[ch] = {"recheck_min_id": lo, "known_max_id": top, "have_media_ids": have}
        path = os.path.join(run_dir, "crawl_state.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f)
        return path

    def _finish(self, jid, status, summary):
        with self.lock:
            self.con.execute("UPDATE jobs SET status = ?, finished = ?, summary = ? WHERE id = ?",
                             (status, db.now(), json.dumps(summary, ensure_ascii=False), jid))
            self.con.commit()

    def _write_media_targets(self, run_dir, params):
        """tgmedia reads posts.jsonl; write the selected posts from the database."""
        where, args = ["1"], []
        if params.get("post_ids"):
            where.append(f"p.id IN ({','.join('?' * len(params['post_ids']))})")
            args += params["post_ids"]
        if params.get("channel"):
            where.append("p.channel = ?")
            args.append(params["channel"])
        if params.get("only_refused", True) and not params.get("post_ids"):
            where.append("p.media_unavailable NOT IN ('[]', '') AND p.media_unavailable IS NOT NULL")
        # skip posts that already have a logged-in original
        where.append("NOT EXISTS (SELECT 1 FROM media m WHERE m.post_id = p.id AND m.kind = 'full')")
        with self.lock:
            rows = self.con.execute(f"""
                SELECT p.*, (SELECT group_concat(kind) FROM media m WHERE m.post_id = p.id) kinds
                FROM posts p WHERE {' AND '.join(where)} ORDER BY p.date DESC LIMIT ?""",
                                    (*args, int(params.get("limit", 50)))).fetchall()
        with open(os.path.join(run_dir, "posts.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                kinds = (r["kinds"] or "").split(",")
                f.write(json.dumps({
                    "channel": r["channel"], "id": r["msg_id"], "url": r["url"],
                    "media_types": json.loads(r["media_types"] or "[]"),
                    "media_unavailable": json.loads(r["media_unavailable"] or "[]"),
                    "video_durations": json.loads(r["video_durations"] or "[]"),
                    "media_files": [f"x_0_{k}" for k in kinds if k],
                }, ensure_ascii=False) + "\n")
        return len(rows)


def _alive(pid, run_dir):
    """Is this pid still our crawler (not a recycled pid)?"""
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    try:
        out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        return bool(run_dir) and run_dir in out
    except Exception:
        return False


def _read_progress(run_dir):
    try:
        with open(os.path.join(run_dir or "", "progress.json"), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _progress_phase(run_dir):
    return _read_progress(run_dir).get("phase")


# ---- Telegram login state (cached; it's a network call) -----------------------

_tg_cache = {"t": 0, "value": None}


def telegram_status(force=False):
    if not force and _tg_cache["value"] and time.time() - _tg_cache["t"] < 300:
        return _tg_cache["value"]
    if not os.path.exists(VENV_PY):
        val = {"ok": False, "message": "Telethon environment (.venv) not installed."}
    else:
        try:
            r = subprocess.run([VENV_PY, TGMEDIA, "status"], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=45, cwd=ROOT)
            out = (r.stdout + r.stderr).strip().splitlines()
            msg = out[-1] if out else ""
            val = {"ok": r.returncode == 0 and msg.startswith("Logged in"), "message": msg}
        except subprocess.TimeoutExpired:
            val = {"ok": False, "message": "Timed out talking to Telegram."}
    _tg_cache.update(t=time.time(), value=val)
    return val
