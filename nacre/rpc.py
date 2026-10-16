"""Discord Rich Presence over the local IPC socket (real Discord or arRPC).

Media tabs on supported sites get full activity (art, linked title/creator, buttons, progress bar);
optionally any other tab is reported as "browsing". Each site can use its own Discord application ID,
so its name shows as e.g. "Watching YouTube".
"""
import json
import os
import struct
import time
import uuid

from PyQt6.QtCore import QObject, QTimer, QUrlQuery, pyqtSignal
from PyQt6.QtNetwork import QLocalSocket

from .config import cfg
from .services import host_of

# arRPC (Vesktop / Equibop / standalone) and Discord serve the same IPC protocol in different places.
DISCORD_DIRS = ["", "app/com.discordapp.Discord", "app/com.discordapp.DiscordCanary", "snap.discord"]
ARRPC_DIRS = ["", ".flatpak/dev.vencord.Vesktop/xdg-run", "app/dev.vencord.Vesktop"]

# host -> (display name, "listen" | "watch", button label)
SITES = {
    "music.youtube.com": ("YouTube Music", "listen", "Listen on YouTube Music"),
    "youtube.com": ("YouTube", "watch", "Watch on YouTube"),
    "soundcloud.com": ("SoundCloud", "listen", "Listen on SoundCloud"),
    "open.spotify.com": ("Spotify", "listen", "Listen on Spotify"),
    "music.apple.com": ("Apple Music", "listen", "Listen on Apple Music"),
    "deezer.com": ("Deezer", "listen", "Listen on Deezer"),
    "tidal.com": ("TIDAL", "listen", "Listen on TIDAL"),
    "bandcamp.com": ("Bandcamp", "listen", "Listen on Bandcamp"),
    "mixcloud.com": ("Mixcloud", "listen", "Listen on Mixcloud"),
    "audiomack.com": ("Audiomack", "listen", "Listen on Audiomack"),
    "pandora.com": ("Pandora", "listen", "Listen on Pandora"),
    "twitch.tv": ("Twitch", "watch", "Watch on Twitch"),
    "kick.com": ("Kick", "watch", "Watch on Kick"),
    "netflix.com": ("Netflix", "watch", "Watch on Netflix"),
    "hulu.com": ("Hulu", "watch", "Watch on Hulu"),
    "disneyplus.com": ("Disney+", "watch", "Watch on Disney+"),
    "primevideo.com": ("Prime Video", "watch", "Watch on Prime Video"),
    "max.com": ("Max", "watch", "Watch on Max"),
    "crunchyroll.com": ("Crunchyroll", "watch", "Watch on Crunchyroll"),
    "tv.apple.com": ("Apple TV+", "watch", "Watch on Apple TV+"),
    "peacocktv.com": ("Peacock", "watch", "Watch on Peacock"),
    "paramountplus.com": ("Paramount+", "watch", "Watch on Paramount+"),
    "vimeo.com": ("Vimeo", "watch", "Watch on Vimeo"),
    "dailymotion.com": ("Dailymotion", "watch", "Watch on Dailymotion"),
    "nebula.tv": ("Nebula", "watch", "Watch on Nebula"),
    "bilibili.com": ("bilibili", "watch", "Watch on bilibili"),
}
ACTIVITY = {"play": 0, "listen": 2, "watch": 3}
DISPLAY = {"name": 0, "state": 1, "details": 2}


def socket_paths(target):
    if os.name == "nt":                                   # Discord and arRPC both use \\.\pipe\discord-ipc-N
        return [f"discord-ipc-{i}" for i in range(10) if os.path.exists(rf"\\.\pipe\discord-ipc-{i}")]
    bases = [os.environ.get(v) for v in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")] + ["/tmp"]
    dirs = ARRPC_DIRS + DISCORD_DIRS if target == "arrpc" else DISCORD_DIRS + ARRPC_DIRS
    seen = []
    for b in filter(None, bases):
        for d in dirs:
            for i in range(10):
                p = os.path.join(b, d, f"discord-ipc-{i}")
                if p not in seen and os.path.exists(p):
                    seen.append(p)
    return seen


def site_for(host):
    """Longest matching supported/custom site for a host → (key, name, kind, button) or None."""
    host = host.removeprefix("www.")
    custom = {h.strip().removeprefix("www."): (h.strip(), "listen", f"Open on {h.strip()}")
              for h in cfg.get("rpc.custom") if h.strip()}
    table = {**SITES, **custom}
    for key in sorted(table, key=len, reverse=True):
        if host == key or host.endswith("." + key):
            if key in cfg.get("rpc.disabled_sites"):
                return None
            return (key, *table[key])
    return None


def clip(s, n=128):
    s = (s or "").strip()
    return (s[:n - 1] + "…" if len(s) > n else s).ljust(2)       # Discord wants 2–128 chars


def youtube_id(url):
    host = url.host()
    if host == "youtu.be":
        return url.path().strip("/")[:11] or None
    if host.endswith("youtube.com"):
        v = QUrlQuery(url).queryItemValue("v")
        if v:
            return v[:11]
        parts = url.path().strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            return parts[1][:11]
    return None


# media state plus the creator's page (YouTube channel / YT Music artist / generic byline link)
STATE_JS = r"""(() => { const s = window.__nacre && __nacre.state(); if (!s) return null;
  const a = document.querySelector('ytd-watch-metadata ytd-channel-name a, #owner ytd-channel-name a,'
    + ' ytmusic-player-bar .byline a, .content-info-wrapper .byline a, a[href*="/channel/"], a[rel=author]');
  s.ad = !!document.querySelector('#movie_player.ad-showing, ytmusic-player.ad-showing');
  s.creatorUrl = a && a.href && a.href.startsWith('https://') ? a.href : ''; return s; })()"""


# a Discord-friendly picture for a page: big PNG icons first (Discord can't show .ico/.svg), then og:image
PAGE_JS = r"""(() => { const ok = u => u && /^https:/.test(u) && !/\.(ico|svg)(\?|$)/i.test(u) && u.length <= 256;
  const size = l => Math.max(0, ...((l.sizes && l.sizes.value) || '').split(' ').map(s => parseInt(s) || 0));
  const links = [...document.querySelectorAll('link[rel="apple-touch-icon"], link[rel="apple-touch-icon-precomposed"]')]
    .concat([...document.querySelectorAll('link[rel~="icon"]')].sort((a, b) => size(b) - size(a)));
  const meta = p => (document.querySelector(`meta[property="${p}"], meta[name="${p}"]`) || {}).content || '';
  const icon = links.map(l => l.href).find(ok);
  return { title: document.title, site: meta('og:site_name') || meta('application-name'),
           image: icon || (ok(meta('og:image')) ? meta('og:image') : '') }; })()"""


class DiscordRPC(QObject):
    status = pyqtSignal(str)

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx
        self.sock = QLocalSocket(self)
        self.sock.connected.connect(self._handshake)
        self.sock.readyRead.connect(self._read)
        self.sock.disconnected.connect(lambda: self._set_state("Disconnected"))
        self.sock.errorOccurred.connect(lambda e: self._next_path())
        self.buf, self.ready, self.paths, self.last, self.state = b"", False, [], None, "Off"
        self.urls_ok, self.sticky, self.client_id, self.pending = True, None, None, None
        self.name_ok = True
        self.started, self.browse = {}, (None, 0.0)
        self.poll = QTimer(self, interval=5000, timeout=self.update_presence)   # Discord allows 5 updates / 20 s
        self.retry = QTimer(self, interval=20000, timeout=lambda: self.ensure(self.client_id or self.default_id()))
        cfg.changed.connect(self._cfg)
        self._cfg("rpc.enabled", cfg.get("rpc.enabled"))

    # ── settings / state ──
    def _cfg(self, k, v):
        if not k.startswith("rpc."):
            return
        if k in ("rpc.enabled", "rpc.target", "rpc.client_id"):
            self.disconnect_ipc()
        if cfg.get("rpc.enabled"):
            self.poll.start()
            self.retry.start()
            if self.sock.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                self.ensure(self.default_id())
            self.last = None
            QTimer.singleShot(300, self.update_presence)
        else:
            self.poll.stop()
            self.retry.stop()
            self.disconnect_ipc()
            self._set_state("Off")

    def default_id(self):
        return str(cfg.get("rpc.client_id") or "").strip()

    def id_for(self, key):
        return str(cfg.get("rpc.site_ids").get(key, "") or "").strip() or self.default_id()

    def _set_state(self, s):
        self.state = s
        self.ready = s == "Connected"
        self.status.emit(s)

    # ── IPC ──
    def ensure(self, client_id):
        """Be connected as `client_id` (each Discord app needs its own handshake)."""
        if not cfg.get("rpc.enabled"):
            return
        if not client_id:
            return self._set_state("Needs an application ID")
        connected = self.sock.state() != QLocalSocket.LocalSocketState.UnconnectedState
        if connected and client_id == self.client_id:
            return
        self.disconnect_ipc()
        self.client_id = client_id
        self.paths = socket_paths(cfg.get("rpc.target"))
        self._next_path()

    def _next_path(self):
        if self.sock.state() != QLocalSocket.LocalSocketState.UnconnectedState:
            self.sock.abort()
        if not self.paths:
            return self._set_state("arRPC isn't running" if cfg.get("rpc.target") == "arrpc"
                                   else "Discord isn't running")
        self.sock.connectToServer(self.paths.pop(0))

    def disconnect_ipc(self):
        self.paths = []
        self.last = None
        self.ready = False
        self.sock.abort()

    def shutdown(self):
        self.poll.stop()
        self.retry.stop()
        self.sock.blockSignals(True)
        self.sock.abort()

    def _send(self, op, payload):
        data = json.dumps(payload).encode()
        self.sock.write(struct.pack("<II", op, len(data)) + data)

    def _handshake(self):
        self.buf = b""
        self._send(0, {"v": 1, "client_id": self.client_id})

    def _read(self):
        self.buf += bytes(self.sock.readAll())
        while len(self.buf) >= 8:
            op, n = struct.unpack("<II", self.buf[:8])
            if len(self.buf) < 8 + n:
                return
            body, self.buf = self.buf[8:8 + n], self.buf[8 + n:]
            try:
                msg = json.loads(body or b"{}")
            except ValueError:
                continue
            if op == 1 and msg.get("evt") == "READY":
                self._set_state("Connected")
                self.last = None
                if self.pending is not None:
                    act, self.pending = self.pending, None
                    self.set_activity(act)
                else:
                    self.update_presence()
            elif op == 1 and msg.get("evt") == "ERROR":
                err = str(msg.get("data", {}).get("message", "error"))
                if self.urls_ok and "_url" in err:
                    self.urls_ok, self.last = False, None
                    return self.update_presence()
                if self.name_ok and "name" in err.lower():
                    self.name_ok, self.last = False, None      # client won't take a custom name: app name it is
                    return self.update_presence()
                self.status.emit("Discord: " + err)
            elif op == 2:
                self._set_state("Rejected: " + str(msg.get("message", "check the application ID")))
                self.sock.abort()
            elif op == 3:
                self._send(4, msg)

    def set_activity(self, activity, client_id=None):
        client_id = client_id or self.client_id or self.default_id()
        if client_id != self.client_id or not self.ready:
            self.pending = activity
            return self.ensure(client_id)
        key = json.dumps(activity, sort_keys=True)
        if key == self.last:
            return
        self.last = key
        self._send(1, {"cmd": "SET_ACTIVITY", "args": {"pid": os.getpid(), "activity": activity},
                       "nonce": str(uuid.uuid4())})

    # ── what to show ──
    def candidate(self):
        tabs = [t for w in self.ctx.windows if not w.incognito for t in w.tabs
                if not t.page.isAudioMuted() and site_for(host_of(t.page.url()))]
        playing = [t for t in tabs if t.item.audible]
        if playing:
            self.sticky = playing[-1]
        elif not any(self.sticky is t for t in tabs):
            self.sticky = None          # paused tabs stay current until closed / navigated away
        return self.sticky

    def update_presence(self):
        if not cfg.get("rpc.enabled"):
            return
        tab = self.candidate()
        if tab is not None:
            return tab.page.runJavaScript(STATE_JS, 0, lambda st, tab=tab: self._media(tab, st))
        self._browsing()

    def _browsing(self):
        mode = cfg.get("rpc.browsing")
        w = getattr(self.ctx, "last_active", None)
        if w not in self.ctx.windows:
            w = next((x for x in reversed(self.ctx.windows) if not x.incognito), None)
        t = w.tab() if mode != "off" and w is not None and not w.incognito else None
        if t is None or t.is_nacre() or t.page.url().scheme() not in ("http", "https"):
            return self.set_activity(None) if self.ready else None
        t.page.runJavaScript(PAGE_JS, 0, lambda info, t=t: self._browse_activity(t, info or {}))

    def _browse_activity(self, t, info):
        mode = cfg.get("rpc.browsing")
        url, host = t.page.url().toString(), host_of(t.page.url())
        if self.browse[0] != url:
            self.browse = (url, time.time())
        site = (info.get("site") or "").strip() or host
        title = (info.get("title") or t.title()) if mode == "title" else site
        # never "Playing" (0): Discord shows one activity per type, so it would fight games / shells for the slot
        act = {"type": ACTIVITY.get(cfg.get("rpc.browse_type"), 3),
               "status_display_type": DISPLAY.get(cfg.get("rpc.display"), 2),
               "details": clip(title), "state": clip(site if mode == "title" else host),
               "timestamps": {"start": int(self.browse[1] * 1000)}, "assets": {"large_text": clip(host)}}
        img = info.get("image") or ""
        if img.startswith("https://") and len(img) <= 256:
            act["assets"]["large_image"] = img
        if mode == "title" and cfg.get("rpc.button"):
            act["buttons"] = [{"label": f"Open on {site}"[:32], "url": url[:512]}]
        self.set_activity(act)

    def _media(self, tab, st):
        if st and st.get("ad"):
            return                                          # keep the previous activity through YouTube ads
        site = site_for(host_of(tab.page.url()))
        if not st or not site or (st.get("paused") and not cfg.get("rpc.show_paused")):
            return self._browsing()
        key, name, kind, button = site
        url = tab.page.url()
        title = (st.get("title") or tab.title()).strip()
        creator = (st.get("artist") or name).strip()
        vid = youtube_id(url)
        link = url.toString()
        art = st.get("art") or ""
        if vid:
            music = url.host().startswith("music.")
            link = f"https://{'music' if music else 'www'}.youtube.com/watch?v={vid}"
            if not (music and art.startswith("https://") and len(art) <= 256):
                art = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"      # thumbnail straight from the video ID
        if not (art.startswith("https://") and len(art) <= 256):
            icon = tab.page.iconUrl().toString()
            art = icon if icon.startswith("https://") and len(icon) <= 256 else ""
        by = f"by {creator}" if creator and creator != name else name
        act = {"type": ACTIVITY[kind], "status_display_type": DISPLAY.get(cfg.get("rpc.display"), 2),
               "details": clip(title), "state": clip(by),
               "assets": {"large_text": clip(st.get("album") or name)}}
        if self.name_ok:
            act["name"] = name                      # "Watching YouTube" where the client honours it
        if self.urls_ok:
            act["details_url"] = link[:256]
            if st.get("creatorUrl"):
                act["state_url"] = st["creatorUrl"][:256]
        if art:
            act["assets"]["large_image"] = art
            if self.urls_ok:
                act["assets"]["large_url"] = link[:256]
        dur, pos = st.get("dur") or 0, st.get("pos") or 0
        if st.get("paused"):
            act["state"] = clip(f"Paused · {by}")
            act["assets"]["small_text"] = "Paused"
        elif dur:                                     # Discord draws a real progress bar from start/end
            start = time.time() - pos
            k = (id(tab), title)
            prev = self.started.get(k)
            if prev is None or abs(prev - start) > 2.5:      # only move it on seeks, not on poll jitter
                self.started = {k: start}
                prev = start
            act["timestamps"] = {"start": int(prev * 1000), "end": int((prev + dur) * 1000)}
        if cfg.get("rpc.button"):
            act["buttons"] = [{"label": button[:32], "url": link[:512]}]
            if st.get("creatorUrl"):
                act["buttons"].append({"label": "View " + ("artist" if kind == "listen" else "channel"),
                                       "url": st["creatorUrl"][:512]})
        self.set_activity(act, self.id_for(key))
