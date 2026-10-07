"""A stand-in for t.me for the crawler tests: a local HTTP server that serves synthetic channel
previews, profile pages and media, and can misbehave on demand (rate limits, 5xx, cut-off pages,
changed markup, expired media). The pages are made up; none of it is real channel content.

    site = Site(); site.add_channel("alpha", posts=12, links=["beta"]); srv = serve(site)
    tgspider.BASE_URL = site.base       # in-process; or set TGSPIDER_BASE_URL for a subprocess

`site.rule(name, mode, times=None, **kw)` makes requests for `/s/<name>` (or `/media/<name>`) misbehave
for the next `times` requests (None = always). Modes for pages: "500", "429" (retry_after=...),
"truncate", "short_body", "drift_wrapper", "drift_datapost", "nodate", "noviews", "stuck", "slow" (seconds=...).
`after=N` lets the first N requests through before the rule starts to apply.
Modes for media: "500" (then it works), "short", "html", "gone" (404), "denied" (403).
"""

import base64
import json
import threading
import time
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PER_PAGE = 5          # posts per preview page (real ones have ~20; the crawler doesn't care)
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8


def _token(tg_id):
    return base64.urlsafe_b64encode(json.dumps({"c": tg_id, "t": 1}).encode()).decode().rstrip("=")


class Site:
    def __init__(self):
        self.channels = {}                  # name -> spec (see add_channel)
        self.profiles = {}                  # name -> (kind, title): group / bot / user
        self.rules = {}                     # name -> {"mode", "times", ...}
        self.hits = defaultdict(int)        # request path -> count
        self.paths = []                     # every request path, in order
        self.base = None                    # set by serve()
        self._lock = threading.Lock()

    # ---- content -------------------------------------------------------------
    def add_channel(self, name, posts=12, links=(), bio=(), tg_id=None, title=None, subs=1200, media=None,
                    videos=None):
        """`links`: usernames linked from the newest posts (one per post). `media`: {post_id: [media
        names]} photos to show on that post. `videos`: {post_id: media name} for a video with thumbnail."""
        self.channels[name] = {"ids": list(range(1, posts + 1)), "links": list(links), "bio": list(bio),
                               "tg_id": tg_id or (1000 + len(self.channels)), "title": title or name.title(),
                               "subs": subs, "media": media or {}, "videos": videos or {}}
        return self.channels[name]

    def add_profile(self, name, kind="group", title=None):
        self.profiles[name] = (kind, title or name.title())

    def rule(self, name, mode, times=None, **kw):
        self.rules[name] = {"mode": mode, "times": times, **kw}

    def take(self, name):
        """The rule that applies to this request, if any (and count it)."""
        with self._lock:
            r = self.rules.get(name)
            if not r:
                return None
            if r.get("after"):                      # let the first N requests through (a failure part-way)
                r["after"] -= 1
                return None
            if r["times"] is not None:
                if r["times"] <= 0:
                    return None
                r["times"] -= 1
            return r

    def _post(self, name, i):
        ch = self.channels[name]
        idx = len(ch["ids"]) - i                       # 0 = newest
        text = f"post {i} of {name}"
        if 0 <= idx < len(ch["links"]):
            text += f' see <a href="https://t.me/{ch["links"][idx]}">t.me/{ch["links"][idx]}</a>'
        photos = "".join(
            f'<a class="tgme_widget_message_photo_wrap" href="https://t.me/{name}/{i}" '
            f'style="width:400px;background-image:url(\'{self.base}/media/{m}\')"></a>'
            for m in ch["media"].get(i, []))
        video = ""
        if i in ch["videos"]:
            m = ch["videos"][i]
            video = ('<a class="tgme_widget_message_video_player" href="https://t.me/%s/%d">'
                     '<i class="tgme_widget_message_video_thumb" style="background-image:url(\'%s/media/thumb_%s.jpg\')"></i>'
                     '<video src="%s/media/%s"></video></a>' % (name, i, self.base, m, self.base, m))
        return (f'<div class="tgme_widget_message_wrap js-widget_message_wrap">'
                f'<div class="tgme_widget_message text_not_supported_wrap js-widget_message" '
                f'data-post="{name}/{i}" data-view="{_token(ch["tg_id"])}">{photos}{video}'
                f'<div class="tgme_widget_message_text js-message_text">{text}</div>'
                f'<div class="tgme_widget_message_footer"><span class="tgme_widget_message_views">{i * 10}</span>'
                f'<span class="tgme_widget_message_meta"><a class="tgme_widget_message_date" '
                f'href="https://t.me/{name}/{i}"><time datetime="2026-09-{1 + i // 24:02d}T{i % 24:02d}:00:00+00:00" '
                f'class="time">x</time></a></span></div></div></div>')

    def channel_page(self, name, before=None, mode=None):
        ch = self.channels[name]
        ids = sorted(i for i in ch["ids"] if before is None or i < before)
        window = ids[-PER_PAGE:]
        bio = " ".join(f'<a href="https://t.me/{u}">@{u}</a>' for u in ch["bio"])
        head = (f'<div class="tgme_channel_info"><div class="tgme_channel_info_header">'
                f'<div class="tgme_channel_info_header_title"><span>{ch["title"]}</span></div></div>'
                f'<div class="tgme_channel_info_counters"><div class="tgme_channel_info_counter">'
                f'<span class="counter_value">{ch["subs"]}</span> <span class="counter_type">subscribers</span>'
                f'</div></div><div class="tgme_channel_info_description">{bio}</div></div>')
        body = "".join(self._post(name, i) for i in window)
        html = f"<!DOCTYPE html><html><head><title>{ch['title']}</title></head><body>{head}{body}</body></html>"
        if mode == "drift_wrapper":
            html = html.replace("tgme_widget_message_wrap", "tgme_msg_wrap")
        elif mode == "drift_datapost":
            html = html.replace("data-post=", "data-pst=")
        elif mode == "nodate":
            html = html.replace('<time datetime=', '<time data-x=')
        elif mode == "noviews":
            html = html.replace('class="tgme_widget_message_views"', 'class="x_views"')
        elif mode == "truncate":
            html = html.split("</html>")[0]
        return html

    def profile_page(self, name):
        kind, title = self.profiles.get(name, (None, None))
        if kind is None:                                # nonexistent: 200 with no title
            return ('<!DOCTYPE html><html><body><div class="tgme_page"><div class="tgme_page_extra">'
                    'If you have Telegram, you can contact this name right away.</div></div></body></html>')
        extra, button = {"group": ("34 members", "Join Group"), "bot": ("120 monthly users", "Start Bot"),
                         "user": ("@" + name, "Send Message")}[kind]
        return (f'<!DOCTYPE html><html><body><div class="tgme_page"><div class="tgme_page_title"><span>{title}</span>'
                f'</div><div class="tgme_page_extra">{extra}</div>'
                f'<a class="tgme_action_button_new shine" href="https://t.me/{name}">{button}</a>'
                f'</div></body></html>')


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"                       # each response ends by closing the connection

    def log_message(self, *a):
        pass

    def send(self, code, body=b"", ctype="text/html; charset=utf-8", length=None, extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body) if length is None else length))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        site = self.server.site
        path, _, query = self.path.partition("?")
        with site._lock:
            site.hits[path] += 1
            site.paths.append(self.path)
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[0] == "media":
            return self.media(site, parts[1])
        if len(parts) == 2 and parts[0] == "s":
            return self.preview(site, parts[1], query)
        if len(parts) == 1:
            return self.send(200, site.profile_page(parts[0]).encode())
        self.send(404)

    def preview(self, site, name, query):
        r = site.take(name)
        mode = r["mode"] if r else None
        if mode == "500":
            return self.send(500, b"oops")
        if mode == "429":
            return self.send(429, b"slow down", extra=[("Retry-After", str(r.get("retry_after", 1)))])
        if mode == "slow":
            time.sleep(r.get("seconds", 30))
        if name not in site.channels:                    # real t.me redirects a non-channel to its profile
            return self.send(302, extra=[("Location", f"/{name}")])
        before = None
        for kv in query.split("&"):
            if kv.startswith("before=") and kv[7:].isdigit() and mode != "stuck":
                before = int(kv[7:])
        html = site.channel_page(name, before, mode).encode()
        if mode == "short_body":
            return self.send(200, html[: len(html) // 2], length=len(html))
        self.send(200, html)

    def media(self, site, name):
        r = site.take("media/" + name)
        mode = r["mode"] if r else None
        if mode == "500":
            return self.send(500, b"cdn hiccup")
        if mode == "gone" or name.startswith("gone"):
            return self.send(404)
        if mode == "denied":
            return self.send(403)
        if mode == "html" or name.startswith("html"):
            return self.send(200, b"<html>This link has expired</html>")
        if mode == "short" or name.startswith("short"):
            return self.send(200, JPEG[:500], ctype="image/jpeg", length=len(JPEG))
        if name.startswith("bigstream"):                 # no Content-Length: only found out on the way
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b"\x00" * 65536)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        if name.startswith("big"):                       # announced larger than any cap the tests use
            return self.send(200, b"\x00" * 10, ctype="video/mp4", length=3 * 1024 * 1024)
        ctype = "video/mp4" if name.endswith(".mp4") else "image/jpeg"
        self.send(200, JPEG, ctype=ctype)


def serve(site):
    """Start the fixture on a free local port. Returns the server (call .shutdown())."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.site = site
    site.base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
