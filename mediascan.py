#!/usr/bin/env python3
"""
mediascan: local analysis of collected media files (no network, no models).

For each file:
  - perceptual hash (dHash, 64 bits): images once, videos at 10% / 50% / 90% of their length.
    Unlike SHA-256, it survives re-encoding, resizing and recompression, so a video re-uploaded
    by another channel still matches.
  - technical metadata from ffprobe (duration, resolution, codec) and embedded metadata from
    exiftool (camera, GPS, creation date, software) where the file still carries it. Telegram
    strips this from photos and videos sent normally, but not from files sent as documents.

    python3 mediascan.py --list files.json -o <run folder>
    files.json: [{"sha256": ..., "path": "/abs/path", "mime": ...}, ...]

Writes <out>/mediascan.jsonl (one row per file) and progress.json. Files are only read.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

FFMPEG, FFPROBE, EXIFTOOL = shutil.which("ffmpeg"), shutil.which("ffprobe"), shutil.which("exiftool")
EXIF_KEEP = ("Make", "Model", "LensModel", "Software", "DateTimeOriginal", "CreateDate", "ModifyDate",
             "MediaCreateDate", "GPSLatitude", "GPSLongitude", "GPSPosition", "GPSAltitude", "Artist", "Author",
             "Creator", "Title", "Description", "Comment", "UserComment", "HandlerVendorID", "Encoder",
             "AndroidVersion", "AndroidModel", "AndroidMake")   # identifying fields only, not dimensions


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dhash_frame(path, at=None):
    """64-bit difference hash of one frame, as 16 hex chars, or None."""
    cmd = [FFMPEG, "-v", "error", "-nostdin"]
    if at is not None:
        cmd += ["-ss", f"{at:.2f}"]
    cmd += ["-i", path, "-frames:v", "1", "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-"]
    try:
        px = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    except subprocess.TimeoutExpired:
        return None
    if len(px) < 72:
        return None
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    if bits == 0 or bits == (1 << 64) - 1:
        return None                          # flat frame (black/white): meaningless for matching
    return f"{bits:016x}"


def probe(path):
    if not FFPROBE:
        return {}
    try:
        out = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                             capture_output=True, text=True, timeout=60).stdout
        d = json.loads(out or "{}")
    except (subprocess.TimeoutExpired, ValueError):
        return {}
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    fmt = d.get("format", {})
    dur = fmt.get("duration") or v.get("duration")
    return {"duration": float(dur) if dur else None, "width": v.get("width"), "height": v.get("height"),
            "codec": v.get("codec_name"), "format": fmt.get("format_name"),
            "has_audio": any(s.get("codec_type") == "audio" for s in d.get("streams", [])),
            "container_tags": fmt.get("tags") or {}}


def exif(path):
    if not EXIFTOOL:
        return {}
    try:
        out = subprocess.run([EXIFTOOL, "-json", "-n", "-q", path], capture_output=True, text=True, timeout=60).stdout
        d = (json.loads(out or "[]") or [{}])[0]
    except (subprocess.TimeoutExpired, ValueError):
        return {}
    return {k: d[k] for k in EXIF_KEEP if d.get(k) not in (None, "", 0)
            and not str(d[k]).startswith("0000:00:00")          # "no date" placeholder
            and not (k == "HandlerVendorID" and d[k] == "appl")}  # written by every muxer, says nothing


def scan(f):
    path = f["path"]
    rec = {"sha256": f["sha256"], "scanned_at": _now()}
    if not os.path.exists(path):
        return {**rec, "error": "file missing"}
    meta = probe(path)
    rec["meta"] = {k: v for k, v in meta.items() if k != "container_tags"}
    tags = {k: v for k, v in (meta.get("container_tags") or {}).items()
            if k.lower() in ("creation_time", "encoder", "location", "com.apple.quicktime.location.iso6709",
                             "com.apple.quicktime.make", "com.apple.quicktime.model", "com.apple.quicktime.software")}
    rec["embedded"] = {**exif(path), **({"container": tags} if tags else {})}
    dur = meta.get("duration") or 0
    is_video = (f.get("mime") or "").startswith("video") or (dur and dur > 0.5 and meta.get("codec") not in ("mjpeg", "png"))
    if is_video and dur:
        hashes = [dhash_frame(path, dur * p) for p in (0.1, 0.5, 0.9)]
    else:
        hashes = [dhash_frame(path)]
    rec["phashes"] = [h for h in hashes if h]
    if not rec["phashes"]:
        rec["error"] = "no usable frame"
    return rec


def main():
    p = argparse.ArgumentParser(description="Perceptual hashes and metadata for collected media (local only).")
    p.add_argument("--list", required=True)
    p.add_argument("-o", "--out", required=True)
    args = p.parse_args()
    if not FFMPEG:
        sys.exit("ffmpeg is not installed (brew install ffmpeg).")
    os.makedirs(args.out, exist_ok=True)
    with open(args.list, encoding="utf-8") as fh:
        files = json.load(fh)
    prog = {"tool": "mediascan", "phase": "scanning", "started_at": _now(), "pid": os.getpid(),
            "totals": {"files": len(files), "done": 0, "errors": 0}, "events": []}
    ppath = os.path.join(args.out, "progress.json")

    def save_prog():
        prog["updated_at"] = _now()
        with open(ppath + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(prog, fh)
        os.replace(ppath + ".tmp", ppath)

    save_prog()
    last = 0
    with open(os.path.join(args.out, "mediascan.jsonl"), "a", encoding="utf-8") as out:
        for f in files:
            if os.path.exists(os.path.join(args.out, "STOP")):
                prog["events"].append({"t": _now(), "level": "warn", "msg": "Stopped on request"})
                break
            rec = scan(f)
            out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            prog["totals"]["done"] += 1
            prog["totals"]["errors"] += bool(rec.get("error"))
            if time.time() - last > 0.5:
                out.flush()
                save_prog()
                last = time.time()
    prog["phase"] = "done"
    prog["events"].append({"t": _now(), "level": "info", "msg": f"Scanned {prog['totals']['done']} files"})
    save_prog()


if __name__ == "__main__":
    main()
