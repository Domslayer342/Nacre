"""Media flyout: spinning cookie artwork with surface-clinging visualizer, wavy progress, expressive controls."""
import math
import os
import random
import shutil
import time

from PyQt6.QtCore import QPointF, QProcess, QRectF, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QFontMetrics, QImage, QPainterPath, QPen, QPixmap, QTransform
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QVBoxLayout, QWidget

from .config import CACHE_DIR, cfg
from .theme import EMPHASIZED, SPRING, alpha, animate, draw_icon, theme, ticker
from .widgets import Button, Flyout, Label, painter

INJECT_JS = r"""
(() => { if (window.__nacre) return;
  const H = {};
  try { const ms = navigator.mediaSession, set = ms.setActionHandler.bind(ms);
        ms.setActionHandler = (a, f) => { H[a] = f; return set(a, f); }; } catch (e) {}
  const A = new WeakMap(); let ctx = null;
  const pick = () => { const els = [...document.querySelectorAll('video,audio')];
    return els.find(e => !e.paused) || els.find(e => e.currentTime > 0) || els[0] || null; };
  window.__nacre = {
    state() {
      const m = pick(), md = navigator.mediaSession && navigator.mediaSession.metadata;
      if (!m && !md) return null;
      let art = '';
      if (md && md.artwork && md.artwork.length) {
        const a = [...md.artwork].sort((x, y) => (parseInt(y.sizes) || 0) - (parseInt(x.sizes) || 0))[0];
        try { art = new URL(a.src, location.href).href; } catch (e) {}
      }
      return { title: (md && md.title) || document.title, artist: (md && md.artist) || location.hostname,
        album: (md && md.album) || '', art: art || (m && m.poster) || '',
        pos: m ? m.currentTime : 0, dur: m && isFinite(m.duration) ? m.duration : 0,
        paused: m ? m.paused : navigator.mediaSession.playbackState !== 'playing',
        loop: m ? m.loop : false, next: !!H.nexttrack, prev: !!H.previoustrack };
    },
    act(a, v) {
      const m = pick();
      if (a === 'toggle') { if (m) m.paused ? m.play() : m.pause(); }
      else if (a === 'next') { if (H.nexttrack) H.nexttrack({action: 'nexttrack'}); else if (m) m.currentTime = m.duration; }
      else if (a === 'prev') { if (H.previoustrack && (!m || m.currentTime < 3)) H.previoustrack({action: 'previoustrack'}); else if (m) m.currentTime = 0; }
      else if (a === 'seek' && m) m.currentTime = v;
      else if (a === 'loop' && m) m.loop = !m.loop;
      return this.state();
    },
    // real audio levels: Web Audio analyser on the playing element (bands log-spaced, bass first)
    levels(n) {
      const m = pick(); if (!m || m.paused) return null;
      let a = A.get(m);
      if (!a) {
        if (A.pending) return null;
        const src = m.currentSrc || '';
        let same = src.startsWith('blob:') || src.startsWith('data:');
        try { same = same || new URL(src, location.href).origin === location.origin || !!m.crossOrigin; } catch (e) {}
        if (!same) return null;                          // cross-origin media would be silenced by Web Audio
        ctx = ctx || new AudioContext();
        A.pending = true;
        ctx.resume().then(() => {
          A.pending = false;
          if (ctx.state !== 'running' || A.get(m)) return;    // never reroute audio into a suspended context
          try { const s = ctx.createMediaElementSource(m), an = ctx.createAnalyser();
            an.fftSize = 4096; an.smoothingTimeConstant = 0.55; s.connect(an); an.connect(ctx.destination);
            A.set(m, { an, buf: new Uint8Array(an.frequencyBinCount) }); } catch (e) { A.set(m, { failed: 1 }); }
        }).catch(() => { A.pending = false; });
        return null;
      }
      if (a.failed) return null;
      a.an.getByteFrequencyData(a.buf);
      const hz = ctx.sampleRate / 2 / a.buf.length, out = [];
      for (let i = 0; i < n; i++) {
        const f0 = 35 * Math.pow(16000 / 35, i / n), f1 = 35 * Math.pow(16000 / 35, (i + 1) / n);
        let s = 0, c = 0;
        for (let b = Math.floor(f0 / hz); b <= Math.ceil(f1 / hz) && b < a.buf.length; b++) { s += a.buf[b]; c++; }
        const v = c ? s / c / 255 : 0;
        out.push(Math.round(Math.min(1, Math.pow(v, 1.6) * (1 + i / n * 0.8)) * 100) / 100);   // lift the treble a bit
      }
      return out;
    } };
})();
"""


LEVEL_BANDS = 28


def fmt_time(s):
    s = int(max(0, s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Cava:
    """Real system-audio levels through `cava` when it is installed."""

    def __init__(self, bars):
        self.bars, self.vals, self.proc = bars, None, None

    @staticmethod
    def available():
        return cfg.get("media.visualizer") == "auto" and shutil.which("cava") is not None

    def start(self):
        if self.proc or not self.available():
            return
        conf = os.path.join(CACHE_DIR, "cava.conf")
        with open(conf, "w") as f:
            f.write(f"[general]\nbars = {self.bars}\nframerate = 60\n[output]\nmethod = raw\n"
                    "raw_target = /dev/stdout\ndata_format = ascii\nascii_max_range = 1000\nchannels = stereo\n"
                    "[smoothing]\nnoise_reduction = 50\n")
        self.proc = QProcess()
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.start("cava", ["-p", conf])

    def _read(self):
        lines = bytes(self.proc.readAllStandardOutput()).decode(errors="ignore").strip().splitlines()
        if lines:
            try:
                self.vals = [int(v) / 1000 for v in lines[-1].split(";") if v]
            except ValueError:
                pass

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.waitForFinished(300)
            self.proc = None
        self.vals = None


class CookieArt(QWidget):
    """Artwork clipped to an M3 'cookie' that rotates while the image stays upright.
    Visualizer bars sit at fixed angles and ride the rotating scalloped surface."""
    N, LOBES, AMP = 56, 9, 0.055

    def __init__(self, size=236, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.R = size * 0.335
        self.rot = self.omega = self.sim_t = 0.0
        self.playing = False
        self.levels = [0.0] * self.N
        self.target = [0.0] * self.N
        self.pix, self.dpr = None, 1.0
        self.cava = Cava(self.N)
        self.page_vals, self.page_t = None, 0.0
        theme.changed.connect(self.update)

    def set_image(self, img):
        if img is None or img.isNull():
            self.pix = None
        else:
            self.dpr = self.devicePixelRatioF()
            side = int(2 * self.R * (1 + self.AMP) * self.dpr) + 2
            self.pix = QPixmap.fromImage(img.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                    Qt.TransformationMode.SmoothTransformation))
        self.update()

    def set_levels(self, vals):
        if vals:
            self.page_vals, self.page_t = vals, time.monotonic()

    def set_playing(self, on):
        self.playing = on
        if on and cfg.get("media.visualizer") == "auto" and self.page_vals is None:
            self.cava.start()
        else:
            self.cava.stop()

    def _simulate(self, dt):
        self.sim_t += dt
        t, n = self.sim_t, self.N
        beat = max(0.0, math.sin(t * math.tau * 1.9)) ** 10
        for i in range(n):
            k = abs(i - n / 2) / (n / 2)            # 0 at the bottom (bass) → 1 at the top
            low = 1 - k
            wob = 0.5 + 0.5 * math.sin(t * (2.3 + (i * 7 % 5)) + i * 1.7) * math.sin(t * 1.1 + i * 0.37)
            self.target[i] = min(1.0, 0.18 + 0.42 * low * wob + random.random() * 0.22 + beat * low * 0.45)

    def tick(self, dt):
        spin = 0.42 if (self.playing and cfg.get("media.spin")) else 0.0
        self.omega += (spin - self.omega) * min(1.0, dt * 2.5)
        self.rot = (self.rot + self.omega * dt) % math.tau
        vis = cfg.get("media.visualizer")
        fresh = self.page_vals is not None and time.monotonic() - self.page_t < 0.4
        if not self.playing or vis == "off":
            self.target = [0.0] * self.N
        elif vis == "simulated":
            self._simulate(dt)
        elif fresh:                                     # real levels from the tab (bass at the bottom)
            bands, n = self.page_vals, len(self.page_vals)
            for i in range(self.N):
                d = min(i, self.N - i) / (self.N / 2)
                self.target[i] = bands[min(n - 1, int(d * n))]
        elif self.cava.vals and len(self.cava.vals) >= self.N:
            self.target = self.cava.vals[:self.N]
        else:
            self.target = [0.0] * self.N               # no real data → still, not fake motion
        for i, tv in enumerate(self.target):
            lv = self.levels[i]
            self.levels[i] = lv + (tv - lv) * (0.55 if tv > lv else 0.13)
        self.update()

    def surface(self, theta):
        return self.R * (1 + self.AMP * math.cos(self.LOBES * (theta - self.rot)))

    def paintEvent(self, e):
        p = painter(self)
        cx = cy = self.width() / 2
        path = QPainterPath()
        for i in range(241):
            th = math.tau * i / 240
            r = self.surface(th)
            pt = QPointF(cx + r * math.cos(th), cy + r * math.sin(th))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        if self.pix is not None:
            b = QBrush(self.pix)
            ext = self.R * (1 + self.AMP)
            off = (self.pix.width() / self.dpr - 2 * ext) / 2, (self.pix.height() / self.dpr - 2 * ext) / 2
            b.setTransform(QTransform().translate(cx - ext - off[0], cy - ext - off[1]).scale(1 / self.dpr, 1 / self.dpr))
            p.setBrush(b)
            p.drawPath(path)
        else:
            p.setBrush(theme["primaryContainer"])
            p.drawPath(path)
            draw_icon(p, QRectF(self.rect()), "music_note", theme["onPrimaryContainer"], 64, 1)
        maxlen = self.width() / 2 - self.R * (1 + self.AMP) - 12
        col = theme["primary"]
        for i in range(self.N):
            th = math.tau * i / self.N + math.pi / 2
            lv = self.levels[i]
            r0 = self.surface(th) + 7
            r1 = r0 + 2.5 + lv * maxlen
            p.setPen(QPen(alpha(col, 0.55 + 0.45 * lv), 3.2, cap=Qt.PenCapStyle.RoundCap))
            c, s = math.cos(th), math.sin(th)
            p.drawLine(QPointF(cx + r0 * c, cy + r0 * s), QPointF(cx + r1 * c, cy + r1 * s))


class WavyProgress(QWidget):
    """M3 Expressive wavy progress: sine wave up to the thumb, flat track after, stop dot at the end."""
    seekRequested = pyqtSignal(float)

    def __init__(self, interactive=True, height=28, stroke=4.0, thumb=True, parent=None):
        super().__init__(parent)
        self.frac = 0.0
        self.amp = 0.0
        self.phase = 0.0
        self.interactive, self.stroke, self.thumb = interactive, stroke, thumb
        self.drag = None
        self._hover = 0.0
        self.setFixedHeight(height)
        self.setMinimumWidth(60)
        if interactive:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.changed.connect(self.update)

    def set_waving(self, on):
        animate(self, "amp", 1.0 if on else 0.0, 600, EMPHASIZED)

    def tick(self, dt):
        self.phase = (self.phase + dt * math.tau * 0.8) % math.tau
        self.update()

    def _frac_at(self, x):
        return max(0.0, min(1.0, (x - 6) / max(1, self.width() - 12)))

    def enterEvent(self, e):
        if self.interactive:
            animate(self, "_hover", 1, 150)

    def leaveEvent(self, e):
        if self.interactive:
            animate(self, "_hover", 0, 200)

    def mousePressEvent(self, e):
        if self.interactive:
            self.drag = self._frac_at(e.position().x())
            self.update()

    def mouseMoveEvent(self, e):
        if self.drag is not None:
            self.drag = self._frac_at(e.position().x())
            self.update()

    def mouseReleaseEvent(self, e):
        if self.drag is not None:
            self.frac = self.drag
            self.drag = None
            self.seekRequested.emit(self.frac)
            self.update()

    def paintEvent(self, e):
        p = painter(self)
        c = theme
        w, cy = self.width(), self.height() / 2
        x0, x1 = 6.0, w - 6.0
        frac = self.drag if self.drag is not None else self.frac
        px = x0 + (x1 - x0) * max(0.0, min(1.0, frac))
        gap = 7 if self.thumb else 4
        A = self.amp * self.height() * 0.115
        lam = 26.0
        pen = QPen(c["primary"], self.stroke, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin)
        end = px - gap
        if end > x0:
            path = QPainterPath(QPointF(x0, cy))
            x = x0
            while x < end:
                x = min(end, x + 1.5)
                taper = min(1.0, (x - x0) / 10, (end - x) / 10 + 0.15)
                path.lineTo(x, cy + A * taper * math.sin((x - x0) / lam * math.tau - self.phase))
            p.strokePath(path, pen)
        if x1 - (px + gap) > 0:
            p.setPen(QPen(c["secondaryContainer"], self.stroke, cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(px + gap, cy), QPointF(x1, cy))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c["primary"])
            p.drawEllipse(QPointF(x1, cy), self.stroke / 2, self.stroke / 2)
        if self.thumb:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c["primary"])
            th = self.height() * 0.72 + 4 * self._hover + (4 if self.drag is not None else 0)
            p.drawRoundedRect(QRectF(px - 2, cy - th / 2, 4, th), 2, 2)


class LiveWave(WavyProgress):
    """Self-animating wavy bar for anywhere in the app (hidden when idle)."""

    def __init__(self, height=14, stroke=3.5):
        super().__init__(interactive=False, height=height, stroke=stroke, thumb=False)
        self.hide()

    def set_frac(self, frac):
        """0..1 shows and animates the bar; None finishes it and fades it out."""
        if frac is None:
            self.frac, self._done = 1.0, True
            self.set_waving(False)
            QTimer.singleShot(500, lambda: self._done and self.hide())
        else:
            self._done = False
            if not self.isVisible():
                self.show()
                self.set_waving(True)
            self.frac = frac
        self.update()

    def showEvent(self, e):
        ticker.add(self)

    def hideEvent(self, e):
        ticker.remove(self)


class ElideLabel(Label):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.full = ""

    def set_full(self, text):
        self.full = text or ""
        self.setToolTip(self.full)
        self._elide()

    def resizeEvent(self, e):
        self._elide()

    def _elide(self):
        self.setText(QFontMetrics(self.font()).elidedText(self.full, Qt.TextElideMode.ElideRight, self.width()))


class MediaFlyout(Flyout):
    def __init__(self, win):
        super().__init__(win, width=620, radius=28)
        self.win = win
        self.tab = None
        self.state = None
        self.art_url = None
        self.nam = QNetworkAccessManager(self)
        self.art_cache = {}
        self.lay.setContentsMargins(18, 18, 22, 18)

        row = QHBoxLayout()
        row.setSpacing(20)
        self.cookie = CookieArt()
        row.addWidget(self.cookie)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = ElideLabel("", "onSurface", 22, 500)
        self.artist = ElideLabel("", "onSurfaceVariant", 15, 500)
        self.album = ElideLabel("", "onSurfaceVariant", 14)
        self.title.mousePressEvent = lambda e: self.tab and self.win.focus_tab(self.tab)
        self.title.setCursor(Qt.CursorShape.PointingHandCursor)
        for w in (self.title, self.artist, self.album):
            col.addWidget(w)
        col.addStretch(1)

        prog = QHBoxLayout()
        prog.setSpacing(8)
        self.t_pos = Label("0:00", "onSurfaceVariant", 12, 500)
        self.t_dur = Label("0:00", "onSurfaceVariant", 12, 500)
        self.wave = WavyProgress()
        self.wave.seekRequested.connect(self._seek)
        prog.addWidget(self.t_pos)
        prog.addWidget(self.wave, 1)
        prog.addWidget(self.t_dur)
        col.addLayout(prog)

        ctl = QHBoxLayout()
        ctl.setSpacing(6)
        self.b_shuffle = Button("shuffle", kind="tonal", size=48, width=44, radius=14,
                                tip="Shuffle isn't exposed by websites")
        self.b_shuffle.setEnabled(False)
        self.b_prev = Button("skip_previous", size=48, width=48, tip="Previous", icon_px=26)
        self.b_play = Button("play_arrow", kind="filled", size=52, width=96, radius=26, tip="Play / pause", icon_px=30)
        self.b_next = Button("skip_next", size=48, width=48, tip="Next", icon_px=26)
        self.b_loop = Button("repeat", kind="tonal", size=48, width=44, radius=14, checkable=True, tip="Repeat")
        for b in (self.b_shuffle, self.b_prev, self.b_play, self.b_next, self.b_loop):
            ctl.addWidget(b)
        ctl.addStretch(1)
        self.b_prev.clicked.connect(lambda: self._act("prev"))
        self.b_play.clicked.connect(lambda: self._act("toggle"))
        self.b_next.clicked.connect(lambda: self._act("next"))
        self.b_loop.clicked.connect(lambda: self._act("loop"))
        col.addSpacing(6)
        col.addLayout(ctl)

        self.player = Button("tab", "", kind="tonal", size=40, radius=20)
        self.player.clicked.connect(self._choose_player)
        col.addSpacing(8)
        col.addWidget(self.player)
        row.addLayout(col, 1)
        self.lay.addLayout(row)

        self.poll = QTimer(self, interval=700, timeout=self._poll)
        self.pos_label_t = 0.0

    # lifecycle
    def open_for(self, anchor):
        self.pick_default()
        self._poll()
        self.poll.start()
        ticker.add(self)
        self.popup(anchor)

    def hideEvent(self, e):
        self.poll.stop()
        ticker.remove(self)
        self.cookie.cava.stop()
        super().hideEvent(e)

    def pick_default(self):
        tabs = self.win.media_tabs()
        if self.tab not in tabs:
            audible = [t for t in tabs if t.page.recentlyAudible()]
            self.tab = (audible or tabs or [None])[-1]
        self._update_player_button()

    def _update_player_button(self):
        t = self.tab
        self.player.setText(t.host() if t else "No media playing")
        self.player.icon_name = "tab" if t else "music_off"
        self.player.setFixedWidth(min(320, self.player.sizeHint().width() + 8))
        self.player.setEnabled(bool(t))

    def _choose_player(self):
        m = QMenu(self)
        for t in self.win.media_tabs():
            a = m.addAction(f"{t.title()[:48]} — {t.host()}")
            a.setCheckable(True)
            a.setChecked(t is self.tab)
            a.triggered.connect(lambda _, t=t: self._select(t))
        m.exec(self.player.mapToGlobal(self.player.rect().bottomLeft()))

    def _select(self, tab):
        self.tab = tab
        self.art_url = None
        self._update_player_button()
        self._poll()

    # data
    def _poll(self):
        if self.tab is None:
            self._apply(None)
            return
        try:
            self.tab.page.runJavaScript("window.__nacre ? __nacre.state() : null", 0, self._apply)
        except RuntimeError:
            self.tab = None

    def _act(self, action, value=None):
        if self.tab:
            arg = "" if value is None else f", {value}"
            self.tab.page.runJavaScript(f"window.__nacre && __nacre.act('{action}'{arg})", 0, self._apply)

    def _seek(self, frac):
        if self.state and self.state.get("dur"):
            self._act("seek", frac * self.state["dur"])

    def _apply(self, st):
        self.state = st
        has = bool(st)
        for b in (self.b_prev, self.b_play, self.b_next, self.b_loop):
            b.setEnabled(has)
        if not st:
            self.title.set_full("Nothing playing")
            self.artist.set_full("Play something in a tab")
            self.album.set_full("")
            self.cookie.set_playing(False)
            self.b_play.set_icon("play_arrow")
            self.cookie.set_image(None)
            self.wave.set_waving(False)
            self.wave.frac = 0
            return
        playing = not st.get("paused", True)
        self.title.set_full(st.get("title"))
        self.artist.set_full(st.get("artist"))
        self.album.set_full(st.get("album"))
        if playing != self.cookie.playing:
            self.cookie.set_playing(playing)
            self.wave.set_waving(playing)
            self.b_play.set_icon("pause" if playing else "play_arrow")
            animate(self.b_play, "radius", 16 if playing else 26, 500, SPRING)   # expressive shape morph
        self.b_next.setEnabled(bool(st.get("next")) or st.get("dur", 0) > 0)
        self.b_loop.setChecked(bool(st.get("loop")))
        self._set_pos(st.get("pos", 0))
        self.t_dur.setText(fmt_time(st.get("dur", 0)))
        self._load_art(st.get("art") or "")

    def _set_pos(self, pos):
        if self.state is None:
            return
        self.state["pos"] = pos
        dur = self.state.get("dur") or 0
        self.wave.frac = pos / dur if dur else 0.0
        self.t_pos.setText(fmt_time(pos))

    def _load_art(self, url):
        if url == self.art_url:
            return
        self.art_url = url
        if url in self.art_cache:
            self.cookie.set_image(self.art_cache[url])
            return
        if not url.startswith("http"):
            icon = self.tab.item.icon if self.tab else None
            self.cookie.set_image(icon.pixmap(128).toImage() if icon and not url else None)
            return
        reply = self.nam.get(QNetworkRequest(QUrl(url)))

        def done():
            img = QImage()
            img.loadFromData(bytes(reply.readAll()))
            reply.deleteLater()
            self.art_cache[url] = img
            if self.art_url == url:
                self.cookie.set_image(img)
        reply.finished.connect(done)

    def tick(self, dt):
        self.cookie.tick(dt)
        self.wave.tick(dt)
        self._lv = getattr(self, "_lv", 0) + dt
        if self._lv > 1 / 30 and self.tab and self.cookie.playing and cfg.get("media.visualizer") == "auto":
            self._lv = 0
            try:
                self.tab.page.runJavaScript(f"window.__nacre && __nacre.levels({LEVEL_BANDS})", 0, self.cookie.set_levels)
            except RuntimeError:
                self.tab = None
        st = self.state
        if st and not st.get("paused") and self.wave.drag is None:
            self.pos_label_t += dt
            st["pos"] = min(st.get("dur") or 1e9, st.get("pos", 0) + dt)
            self.wave.frac = st["pos"] / st["dur"] if st.get("dur") else 0
            if self.pos_label_t > 0.25:
                self.pos_label_t = 0
                self.t_pos.setText(fmt_time(st["pos"]))
