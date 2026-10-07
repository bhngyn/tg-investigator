#!/usr/bin/env python3
"""
mediatext: local OCR (Apple Vision) and speech-to-text (mlx-whisper) for collected media.
No network, no downloads: the Whisper model must already be in the Hugging Face cache.

Run with the worker venv, not the app venv (see tools/models/README.md):

    tools/models/.venv/bin/python mediatext.py --list files.json -o <run folder> --derived <workspace>/derived

files.json: [{"sha256", "path" (absolute), "mime", "ocr": true, "speech": true,
              "ocr_languages": ["ar-SA", ...], "whisper_lang": "ar" | null}, ...]

Output is keyed by the file's SHA-256, never by post:
    <derived>/<sha[:2]>/<sha>/ocr.json | whisper.json | whisper.srt
The originals are only read. Each JSON records the tool, versions, model, macOS version, languages,
parameters, creation time (UTC) and the input's sha256. A finished output whose recorded settings match is
skipped, so a run can be repeated, interrupted or restarted (the memory guard re-execs this process).
One row per (file, kind) is appended to <run>/mediatext.jsonl; progress goes to <run>/progress.json.

Design notes (from an earlier archive pipeline by the same author):
  OCR    Vision called directly on a raw bitmap through a CFData-backed provider (the plain
         CGDataProviderCreateWithData variant leaked every frame and kernel-panicked an 8 GB Mac),
         ocrmac as the fallback; keyframe-only decode with an interval, blank / near-duplicate frame
         skipping and real pts timestamps; VP9 decoded on VideoToolbox; next video's frames prefetched.
  Speech mlx-whisper with the next file's audio decoded on a helper thread, one ffprobe per file,
         temperatures (0.0, 0.4, 0.8), condition_on_previous_text=False, MLX cache capped at 256 MB and
         cleared after each file, 60-minute chunks for audio longer than 90 minutes.
  Guard  memory_guard(): re-exec above an RSS cap, hold while macOS reports < 5% memory free, resume at 10%.
"""
import os

# Never reach for the network: the model is read from the local cache or the run fails with a clear message.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import argparse
import hashlib
import json
import platform
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_LANGS = ["auto"]       # Vision auto-detect; otherwise the FIRST language decides the recognizer (tools/models/README.md)
DEFAULT_TEMPS = (0.0, 0.4, 0.8)
MIN_CONF = 0.3
DUP_DIFF = 4.0
FF_THREADS = 1
HW_DECODE = {"vp9"}          # keyframe-only decode is ignored for VP9, so decode on the media engine instead
CHUNK, MAX_WHOLE = 3600, 5400
PREFETCH_MAX = 1800          # prefetch the next file's audio only if it is <= 30 min (115 MB of PCM)
MAX_RSS_GB = {"ocr": float(os.environ.get("MEDIATEXT_MAX_RSS_OCR_GB", 1.5)),      # env overrides are for testing the guard
              "speech": float(os.environ.get("MEDIATEXT_MAX_RSS_SPEECH_GB", 5.0))}
MEM_HOLD_PCT, MEM_RESUME_PCT = 5, 10
SCHEMA = 1


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"{now()} {msg}", flush=True)


def write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def sha256_file(p, block=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(block):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- ffprobe / srt

def media_info(p: Path) -> dict:
    """duration, has_audio and video codec from a single ffprobe call."""
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)],
                       capture_output=True, text=True)
    try:
        info = json.loads(r.stdout or "{}")
    except ValueError:
        info = {}
    streams = info.get("streams", [])
    try:
        dur = float(info["format"]["duration"])
    except (KeyError, ValueError):
        dur = 0.0
    return {"duration": dur, "has_audio": any(s.get("codec_type") == "audio" for s in streams),
            "vcodec": next((s.get("codec_name") for s in streams if s.get("codec_type") == "video"), None)}


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments, p: Path):
    out = [f"{i}\n{srt_time(s['start'])} --> {srt_time(s['end'])}\n{s['text'].strip()}\n"
           for i, s in enumerate(segments, 1)]
    p.write_text("\n".join(out), encoding="utf-8")


# ---------------------------------------------------------------- memory guard

def _rss_gb():
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
        return int(out.strip() or 0) / 2**20
    except (OSError, ValueError):       # ps unavailable (sandboxed): fall back to the peak so far
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30


def _mem_free_pct():
    """macOS's own 'memory free' percentage (what memory_pressure reports), or None."""
    try:
        return int(subprocess.run(["sysctl", "-n", "kern.memorystatus_level"], capture_output=True, text=True).stdout)
    except (OSError, ValueError):
        return None


def memory_guard(stage, run_dir=None):
    """Checked before each file. (1) This process above its RSS cap: re-exec itself (same pid, args and log,
    fresh memory; finished files are skipped on the way back in). (2) The Mac nearly out of memory: hold until
    it recovers (an unreleased worker on an 8 GB Mac took the whole machine down once)."""
    cap = MAX_RSS_GB[stage]
    rss = _rss_gb()
    if rss > cap:
        log(f"memory guard: RSS {rss:.1f} GB > {cap} GB cap, restarting this worker")
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])
    free = _mem_free_pct()
    if free is None or free >= MEM_HOLD_PCT:
        return
    log(f"memory guard: Mac has {free}% memory free, holding until {MEM_RESUME_PCT}%")
    while (free := _mem_free_pct()) is not None and free < MEM_RESUME_PCT:
        if run_dir and stop_requested(run_dir):
            return
        time.sleep(30)
    log(f"memory guard: {free if free is not None else '?'}% free, resuming")


def stop_requested(run_dir):
    return os.path.exists(os.path.join(run_dir, "STOP"))


# ---------------------------------------------------------------- OCR (Apple Vision)

_fallback_logged = []


def _lines(res):
    return [{"text": t.strip(), "conf": round(float(c), 3), "bbox": [round(v, 4) for v in b]}
            for t, c, b in res if t.strip() and c >= MIN_CONF]


def _ocrmac(img, langs):
    from ocrmac import ocrmac
    return _lines(ocrmac.OCR(img, recognition_level="accurate", language_preference=langs, unit="line").recognize())


def ocr_image(img, langs):
    """The same Vision request ocrmac makes, on the same (PIL-decoded) pixels, handed over as a raw bitmap
    instead of re-encoded as PNG. bbox = Vision's normalised [x, y, w, h], origin bottom-left.
    langs == ["auto"] switches on Vision's automatic language detection."""
    import objc, Quartz, Vision
    from Foundation import NSData
    try:
        with objc.autorelease_pool():
            im = img if img.mode == "RGBA" else img.convert("RGBA")
            w, h = im.size
            data = im.tobytes()
            # CFData-backed provider, NOT CGDataProviderCreateWithData(None, data, ...): with no release callback
            # PyObjC pins the bytes forever, leaking one full RGBA frame per call (kernel panic, 2026-09-26).
            provider = Quartz.CGDataProviderCreateWithCFData(NSData.dataWithBytes_length_(data, len(data)))
            cg = Quartz.CGImageCreate(w, h, 8, 32, 4 * w, Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB),
                                      Quartz.kCGImageAlphaLast, provider, None, False, Quartz.kCGRenderingIntentDefault)
            req = Vision.VNRecognizeTextRequest.alloc().init()
            req.setRecognitionLevel_(0)  # accurate
            if langs == ["auto"]:
                req.setAutomaticallyDetectsLanguage_(True)
            else:
                req.setRecognitionLanguages_(langs)
            ret = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None).performRequests_error_([req], None)
            ok, err = ret if isinstance(ret, tuple) else (bool(ret), None)
            if not ok or err is not None:
                raise RuntimeError(err)
            res = []
            for o in req.results() or []:
                b = o.boundingBox()
                res.append((o.topCandidates_(1)[0].string(), o.confidence(),
                            [b.origin.x, b.origin.y, b.size.width, b.size.height]))
            return _lines(res)
    except Exception as e:
        if not _fallback_logged:
            _fallback_logged.append(e)
            log(f"direct Vision call failed ({e!r}); using ocrmac for this and any further failures")
        return _ocrmac(img, [] if langs == ["auto"] else langs)


def thumb(img):
    import numpy as np
    from PIL import Image
    return np.asarray(img.convert("L").resize((32, 18), Image.BILINEAR), dtype=np.float32)


def extract_frames(p: Path, interval, vcodec=None):
    """-> (TemporaryDirectory, [(jpg, pts)], decoder). Keyframes only, at most one per `interval` seconds,
    scaled to <= 1280 wide. The caller cleans up the directory."""
    vf = (f"select='isnan(prev_selected_t)+gte(t-prev_selected_t\\,{max(interval - 0.5, 0)})',"
          "scale='min(1280\\,iw)':-2,showinfo")
    hw = (vcodec or media_info(p)["vcodec"]) in HW_DECODE
    td = tempfile.TemporaryDirectory()
    accel = []
    for accel in ((["-hwaccel", "videotoolbox"], []) if hw else ([],)):
        for f in Path(td.name).glob("f_*.jpg"):
            f.unlink()
        r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-threads", str(FF_THREADS), *accel,
                            "-skip_frame", "nokey", "-i", str(p), "-filter_threads", "1", "-vf", vf,
                            "-fps_mode", "vfr", "-q:v", "3", f"{td.name}/f_%06d.jpg"],
                           capture_output=True, text=True)
        files = sorted(Path(td.name).glob("f_*.jpg"))
        if r.returncode == 0 or files:
            break
    else:
        td.cleanup()
        raise RuntimeError(r.stderr[-500:])
    pts = [float(x) for x in re.findall(r"\bn:\s*\d+.*?pts_time:([\d.]+)", r.stderr)]
    return td, list(zip(files, pts)), "videotoolbox" if accel else "cpu"


def ocr_video(p: Path, interval, langs, extracted=None, progress=None):
    import numpy as np
    from PIL import Image
    td, pairs, decoder = extracted or extract_frames(p, interval)
    frames, sampled, last = [], 0, None
    try:
        for f, t in pairs:
            sampled += 1
            if progress and sampled % 10 == 0:
                progress(f"frame {sampled}/{len(pairs)} at {t:.0f}s")
            with Image.open(f) as img:
                small = thumb(img)
                if small.std() < 3:                                   # blank / black frame
                    continue
                if last is not None and np.abs(small - last).mean() < DUP_DIFF:   # near-duplicate of the last read frame
                    continue
                last = small
                lines = ocr_image(img, langs)
            if lines:
                frames.append({"t": round(t, 2), "text": "\n".join(l["text"] for l in lines), "lines": lines})
    finally:
        td.cleanup()
    return {"frames_sampled": sampled, "decoder": decoder, "frames": frames}


def _vision_version():
    from importlib.metadata import version, PackageNotFoundError
    out = []
    for name in ("pyobjc-framework-Vision", "ocrmac"):
        try:
            out.append(f"{name} {version(name)}")
        except PackageNotFoundError:
            pass
    return ", ".join(out)


def ocr_params(item, interval):
    langs = list(item.get("ocr_languages") or DEFAULT_LANGS)
    is_video = (item.get("mime") or "").startswith("video")
    return {"languages": langs, "min_conf": MIN_CONF, **({"interval_s": interval, "dup_diff": DUP_DIFF} if is_video else {})}


def same_params(a, b, keys):
    def norm(d):
        return {k: (sorted(d.get(k)) if k == "languages" else d.get(k)) for k in keys}
    return norm(a) == norm(b)


# ---------------------------------------------------------------- speech (mlx-whisper)

def load_audio(p: Path):
    """16 kHz mono float32 as numpy (decoded on a helper thread; MLX arrays only on the main thread)."""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-nostdin", "-i", str(p), "-threads", "2", "-f", "s16le", "-ac", "1",
                        "-acodec", "pcm_s16le", "-ar", "16000", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"Failed to load audio: {r.stderr.decode(errors='replace')[-500:]}")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def transcribe_file(mlx_whisper, audio, model_path, lang, temps):
    if isinstance(audio, Path):
        audio = str(audio)
    kw = {"language": lang} if lang else {}
    r = mlx_whisper.transcribe(audio, path_or_hf_repo=model_path, condition_on_previous_text=False,
                               temperature=tuple(temps), verbose=None, **kw)
    segs = [{"start": round(s["start"], 2), "end": round(s["end"], 2), "text": s["text"].strip()}
            for s in r.get("segments", []) if s.get("text", "").strip()]
    return r.get("language"), segs


def transcribe(mlx_whisper, p: Path, model_path, info, audio, lang, temps):
    dur = info["duration"]
    if not info["has_audio"]:
        return {"status": "no_audio", "duration": dur, "language": None, "segments": []}
    if dur <= MAX_WHOLE:
        detected, segs = transcribe_file(mlx_whisper, audio if audio is not None else p, model_path, lang, temps)
        return {"status": "ok" if segs else "no_text", "duration": dur, "language": detected, "segments": segs}
    segs, langs = [], []
    with tempfile.TemporaryDirectory() as td:
        for off in range(0, int(dur) + 1, CHUNK):
            wav = Path(td) / f"c{off}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-nostdin", "-ss", str(off), "-t", str(CHUNK), "-i", str(p),
                            "-vn", "-ac", "1", "-ar", "16000", str(wav)], check=True)
            detected, cs = transcribe_file(mlx_whisper, wav, model_path, lang, temps)
            langs.append(detected)
            segs += [s | {"start": round(s["start"] + off, 2), "end": round(s["end"] + off, 2)} for s in cs]
            wav.unlink()
    return {"status": "ok" if segs else "no_text", "duration": dur, "language": max(set(langs), key=langs.count),
            "chunk_languages": langs, "segments": segs}


def resolve_model(model):
    """Local snapshot directory of the model. Never downloads."""
    if os.path.isdir(model):
        return model
    try:
        from huggingface_hub import snapshot_download
        return snapshot_download(model, local_files_only=True)
    except Exception as e:
        sys.exit(f"Whisper model {model!r} is not in the local Hugging Face cache ({type(e).__name__}). "
                 f"Models are never downloaded at runtime; fetch it once by hand, e.g. "
                 f"`tools/models/.venv/bin/hf download {model}`.")


# ---------------------------------------------------------------- driver

def main():
    ap = argparse.ArgumentParser(description="Local OCR and speech-to-text for collected media.")
    ap.add_argument("--list", required=True)
    ap.add_argument("-o", "--out", required=True, help="run folder (mediatext.jsonl, progress.json, STOP)")
    ap.add_argument("--derived", required=True, help="<workspace>/derived")
    ap.add_argument("--interval", type=float, default=5.0, help="video OCR: at most one keyframe per N seconds")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--temperatures", default=",".join(str(t) for t in DEFAULT_TEMPS))
    ap.add_argument("--force", action="store_true", help="redo outputs whose recorded settings differ "
                                                         "(the old file is kept as *.prev-<created>.json)")
    a = ap.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} is not installed (brew install ffmpeg).")
    temps = tuple(float(t) for t in a.temperatures.split(","))
    files = json.load(open(a.list, encoding="utf-8"))
    derived, run = Path(a.derived), a.out
    os.makedirs(run, exist_ok=True)
    macos = platform.mac_ver()[0]
    ocr_todo = [f for f in files if f.get("ocr")]
    speech_todo = [f for f in files if f.get("speech")]
    prog = {"tool": "mediatext", "phase": "starting", "started_at": now(), "pid": os.getpid(),
            "totals": {"files": len(ocr_todo) + len(speech_todo), "done": 0, "errors": 0, "ocr": len(ocr_todo),
                       "speech": len(speech_todo)}, "events": [], "peak_rss_gb": 0.0}
    ppath = os.path.join(run, "progress.json")
    ppath_lock = threading.Lock()

    def save(detail=None):
        prog["updated_at"] = now()
        prog["peak_rss_gb"] = round(max(prog["peak_rss_gb"], resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**30), 3)
        if detail is not None:
            prog["detail"] = detail
        with ppath_lock:
            Path(ppath + ".tmp").write_text(json.dumps(prog, ensure_ascii=False))
            os.replace(ppath + ".tmp", ppath)

    if os.path.exists(ppath):               # restarted by the memory guard: keep the counters
        try:
            old = json.load(open(ppath))
            if old.get("started_at"):
                prog["started_at"], prog["totals"], prog["events"] = old["started_at"], old["totals"], old["events"]
                prog["peak_rss_gb"] = old.get("peak_rss_gb", 0.0)
        except (OSError, ValueError):
            pass
    out = open(os.path.join(run, "mediatext.jsonl"), "a", encoding="utf-8")

    def emit(item, kind, status, tool, model, params, out_path, err=None, extra=None):
        row = {"sha256": item["sha256"], "kind": kind, "status": status, "tool": tool, "model": model,
               "languages": (params or {}).get("languages"), "params": params,
               "output_path": str(out_path) if out_path else None, "error": err, "created": now(), **(extra or {})}
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        out.flush()
        prog["totals"]["done"] += 1
        prog["totals"]["errors"] += status == "error"
        save()

    def outdir(item):
        return derived / item["sha256"][:2] / item["sha256"]

    def keep_old(path, doc):
        """Archive an output with different settings before it is replaced (--force)."""
        path.replace(path.with_name(f"{path.stem}.prev-{re.sub(r'[^0-9A-Za-z]', '', doc.get('created', 'x'))}.json"))

    def check_input(item):
        p = Path(item["path"])
        if not p.exists():
            raise FileNotFoundError(f"file missing: {p}")
        got = sha256_file(p)
        if got != item["sha256"]:
            raise RuntimeError(f"file content changed: sha256 {got} != recorded {item['sha256']}")
        return p

    # ------------------------------------------------ OCR pass
    from PIL import Image
    tool_ocr = f"Apple Vision VNRecognizeTextRequest (accurate; direct raw-bitmap call, ocrmac fallback); {_vision_version()}"
    if ocr_todo:
        log(f"OCR: {len(ocr_todo)} files ({sum((f.get('mime') or '').startswith('video') for f in ocr_todo)} videos)")
        pool = ThreadPoolExecutor(1)
        ahead = {}

        def is_video(f):
            return (f.get("mime") or "").startswith("video")

        def pending(f):
            """(doc path, params, existing doc or None) - existing doc only when it can be reused."""
            path = outdir(f) / "ocr.json"
            params = ocr_params(f, a.interval)
            if path.exists():
                try:
                    doc = json.load(open(path, encoding="utf-8"))
                except ValueError:
                    doc = None
                if doc and same_params(doc.get("params", {}), params, params.keys()) and doc.get("input_sha256") == f["sha256"]:
                    return path, params, doc
                if doc and not a.force:
                    return path, params, {"_other_settings": doc}
            return path, params, None

        def prefetch(j):
            if j < len(ocr_todo) and is_video(ocr_todo[j]) and j not in ahead and not stop_requested(run):
                _, _, ex = pending(ocr_todo[j])
                if ex is None:
                    ahead[j] = pool.submit(extract_frames, Path(ocr_todo[j]["path"]), a.interval)

        phase_started = time.time()
        prog["phase"] = "ocr"
        for i, f in enumerate(ocr_todo):
            if stop_requested(run):
                prog["events"].append({"t": now(), "level": "warn", "msg": "Stopped on request"})
                break
            memory_guard("ocr", run)
            path, params, existing = pending(f)
            if existing is not None:
                doc = existing.get("_other_settings", existing)
                if "_other_settings" in existing:
                    log(f"[{i+1}/{len(ocr_todo)}] exists with other settings, kept (use --force): {f['sha256'][:12]}")
                emit(f, "ocr", doc.get("status", "ok"), doc.get("tool", tool_ocr), None, doc.get("params", params), path,
                     extra={"skipped": True, "output_sha256": sha256_file(path)})
                continue
            t0 = time.time()
            save(f"OCR {i+1}/{len(ocr_todo)} {f['sha256'][:12]}")
            try:
                p = check_input(f)
                if path.exists():
                    keep_old(path, json.load(open(path, encoding="utf-8")))
                if is_video(f):
                    info = media_info(p)
                    extracted = ahead.pop(i).result() if i in ahead else extract_frames(p, a.interval, info["vcodec"])
                    prefetch(i + 1)      # the next video's frames decode while this one is read
                    r = ocr_video(p, a.interval, params["languages"], extracted, progress=save)
                    doc_body = {"media_kind": "video", **r, "duration": info["duration"]}
                    has_text = bool(r["frames"])
                else:
                    with Image.open(p) as img:
                        lines = ocr_image(img, params["languages"])
                    doc_body = {"media_kind": "image", "text": "\n".join(l["text"] for l in lines), "lines": lines}
                    has_text = bool(lines)
                status = "ok" if has_text else "no_text"
                doc = {"schema": SCHEMA, "kind": "ocr", "input_sha256": f["sha256"], "status": status, "tool": tool_ocr,
                       "macos": macos, "params": params, "languages": params["languages"], "created": now(), **doc_body}
                write_json(path, doc)
                emit(f, "ocr", status, tool_ocr, None, params, path, extra={"output_sha256": sha256_file(path)})
            except Exception as e:
                log(f"[{i+1}/{len(ocr_todo)}] OCR ERROR {f['sha256'][:12]}: {e!r}")
                emit(f, "ocr", "error", tool_ocr, None, params, None, err=repr(e))
            if is_video(f):
                log(f"[{i+1}/{len(ocr_todo)}] video OCR {len(doc_body.get('frames', []))} frames w/ text in {time.time()-t0:.0f}s")
            elif (i + 1) % 25 == 0:
                log(f"[{i+1}/{len(ocr_todo)}] photos read, {(time.time()-phase_started)/(i+1):.2f}s each")
        pool.shutdown(wait=False, cancel_futures=True)
        save()

    # ------------------------------------------------ speech pass (model loaded only now)
    if speech_todo and not stop_requested(run):
        prog["phase"] = "speech"
        save("loading Whisper")
        model_path = resolve_model(a.model)
        memory_guard("speech", run)
        import mlx_whisper
        import mlx.core as mx
        from importlib.metadata import version
        # MLX keeps freed GPU buffers in a cache that otherwise grows by ~1 GB over a few files on an 8 GB Mac.
        mx.set_cache_limit(256 * 1024 ** 2)
        tool_w = f"mlx-whisper {version('mlx-whisper')}, mlx {version('mlx')}"
        pool = ThreadPoolExecutor(1)
        speed = {"audio": 0.0, "proc": 0.0}
        ahead_s = {}     # index -> future of prepare(), for the next file only

        def spath(f):
            return outdir(f) / "whisper.json"

        def spending(f):
            params = {"model": a.model, "language_hint": f.get("whisper_lang") or None, "temperatures": list(temps),
                      "condition_on_previous_text": False, "chunk_s": CHUNK, "max_whole_s": MAX_WHOLE}
            path = spath(f)
            if path.exists():
                try:
                    doc = json.load(open(path, encoding="utf-8"))
                except ValueError:
                    doc = None
                if doc and same_params(doc.get("params", {}), params, params.keys()) and doc.get("input_sha256") == f["sha256"]:
                    return params, doc, False
                if doc and not a.force:
                    return params, doc, True
            return params, None, False

        def prepare(f):
            info = media_info(Path(f["path"]))
            audio = load_audio(Path(f["path"])) if info["has_audio"] and 0 < info["duration"] <= PREFETCH_MAX else None
            return info, audio

        for i, f in enumerate(speech_todo):
            if stop_requested(run):
                prog["events"].append({"t": now(), "level": "warn", "msg": "Stopped on request"})
                break
            memory_guard("speech", run)
            params, existing, other = spending(f)
            path = spath(f)
            if existing is not None:
                if other:
                    log(f"[{i+1}/{len(speech_todo)}] whisper exists with other settings, kept (use --force)")
                emit(f, "speech", existing.get("status", "ok"), existing.get("tool", tool_w), a.model, existing.get("params", params),
                     path, extra={"skipped": True, "output_sha256": sha256_file(path)})
                continue
            t0 = time.time()
            try:
                p = check_input(f)
                info, audio = (ahead_s.pop(i) if i in ahead_s else pool.submit(prepare, f)).result()
                if i + 1 < len(speech_todo) and not stop_requested(run):
                    nxt = speech_todo[i + 1]
                    if spending(nxt)[1] is None:
                        ahead_s[i + 1] = pool.submit(prepare, nxt)     # decoded while the GPU works on this one
                dur = info["duration"]
                save(f"speech {i+1}/{len(speech_todo)} {dur/60:.1f} min of audio")
                r = transcribe(mlx_whisper, p, model_path, info, audio, params["language_hint"], temps)
                el = time.time() - t0
                if path.exists():
                    keep_old(path, json.load(open(path, encoding="utf-8")))
                doc = {"schema": SCHEMA, "kind": "speech", "input_sha256": f["sha256"], "tool": tool_w, "model": a.model,
                       "model_path": model_path, "macos": macos, "params": params, "created": now(),
                       "processing_s": round(el, 2), "detected_language": r.get("language"), **r}
                write_json(path, doc)
                if r["segments"]:
                    write_srt(r["segments"], path.with_name("whisper.srt"))
                emit(f, "speech", r["status"], tool_w, a.model, params, path,
                     extra={"output_sha256": sha256_file(path), "detected_language": r.get("language"),
                            "duration": dur, "processing_s": round(el, 2)})
                if r["status"] != "no_audio" and dur:
                    speed["audio"] += dur
                    speed["proc"] += el
                del audio
                mx.clear_cache()
                log(f"[{i+1}/{len(speech_todo)}] {r['status']} {r.get('language') or '-'} {dur/60:.1f} min in {el:.0f}s "
                    f"({dur/el if el else 0:.1f}x realtime, {len(r['segments'])} segs, gpu {mx.get_active_memory()/1e9:.1f} GB "
                    f"+ cache {mx.get_cache_memory()/1e9:.1f} GB)")
            except Exception as e:
                log(f"[{i+1}/{len(speech_todo)}] SPEECH ERROR {f['sha256'][:12]}: {e!r}")
                emit(f, "speech", "error", tool_w, a.model, params, None, err=repr(e))
                try:
                    mx.clear_cache()
                except Exception:
                    pass
        pool.shutdown(wait=False, cancel_futures=True)
    prog["phase"] = "done"
    prog["events"].append({"t": now(), "level": "info",
                           "msg": f"Processed {prog['totals']['done']} of {prog['totals']['files']} items"})
    save("finished")
    log(f"done (peak RSS {prog['peak_rss_gb']} GB)")


if __name__ == "__main__":
    main()
