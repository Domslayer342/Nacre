"""MPRIS2 on the session bus: hardware media keys, the Plasma media widget and lock screen control the playing tab."""
import os

from PyQt6.QtCore import QMetaType, QObject, pyqtClassInfo, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import (QDBusAbstractAdaptor, QDBusArgument, QDBusConnection, QDBusMessage, QDBusObjectPath,
                          QDBusVariant)

PATH = "/org/mpris/MediaPlayer2"


@pyqtClassInfo("D-Bus Interface", "org.mpris.MediaPlayer2")
class RootAdaptor(QDBusAbstractAdaptor):
    def __init__(self, parent, ctx):
        super().__init__(parent)
        self.ctx = ctx

    @pyqtProperty(bool)
    def CanQuit(self):
        return True

    @pyqtProperty(bool)
    def CanRaise(self):
        return True

    @pyqtProperty(bool)
    def HasTrackList(self):
        return False

    @pyqtProperty(str)
    def Identity(self):
        return "Nacre"

    @pyqtProperty(str)
    def DesktopEntry(self):
        return "nacre"

    @pyqtProperty("QStringList")
    def SupportedUriSchemes(self):
        return ["http", "https"]

    @pyqtProperty("QStringList")
    def SupportedMimeTypes(self):
        return []

    @pyqtSlot()
    def Raise(self):
        hub = self.ctx.media
        if hub.tab is not None:
            hub.tab.win.focus_tab(hub.tab)

    @pyqtSlot()
    def Quit(self):
        self.ctx.quit()


@pyqtClassInfo("D-Bus Interface", "org.mpris.MediaPlayer2.Player")
class PlayerAdaptor(QDBusAbstractAdaptor):
    Seeked = pyqtSignal("qlonglong")

    def __init__(self, parent, hub):
        super().__init__(parent)
        self.hub = hub
        self.setAutoRelaySignals(True)

    def st(self):
        return self.hub.st or {}

    # methods
    @pyqtSlot()
    def Next(self):
        self.hub.act("next")

    @pyqtSlot()
    def Previous(self):
        self.hub.act("prev")

    @pyqtSlot()
    def PlayPause(self):
        self.hub.act("toggle")

    @pyqtSlot()
    def Play(self):
        if self.st().get("paused", True):
            self.hub.act("toggle")

    @pyqtSlot()
    def Pause(self):
        if not self.st().get("paused", True):
            self.hub.act("toggle")

    @pyqtSlot()
    def Stop(self):
        self.Pause()

    @pyqtSlot("qlonglong")
    def Seek(self, offset_us):
        self.hub.act("seek", max(0.0, self.st().get("pos", 0) + offset_us / 1e6))

    @pyqtSlot(QDBusObjectPath, "qlonglong")
    def SetPosition(self, track, pos_us):
        self.hub.act("seek", pos_us / 1e6)

    @pyqtSlot(str)
    def OpenUri(self, uri):
        w = self.hub.ctx.windows[0] if self.hub.ctx.windows else None
        w and w.new_tab(uri)

    # properties
    @pyqtProperty(str)
    def PlaybackStatus(self):
        st = self.hub.st
        return "Stopped" if not st else "Paused" if st.get("paused") else "Playing"

    @pyqtProperty(str)
    def LoopStatus(self):
        return "Track" if self.st().get("loop") else "None"

    @pyqtProperty(float)
    def Rate(self):
        return 1.0

    @pyqtProperty(bool)
    def Shuffle(self):
        return False

    @pyqtProperty("QVariantMap")
    def Metadata(self):
        return metadata(self.hub)

    @pyqtProperty(float)
    def Volume(self):
        return 1.0

    @pyqtProperty("qlonglong")
    def Position(self):
        return int(self.st().get("pos", 0) * 1e6)

    @pyqtProperty(float)
    def MinimumRate(self):
        return 1.0

    @pyqtProperty(float)
    def MaximumRate(self):
        return 1.0

    @pyqtProperty(bool)
    def CanGoNext(self):
        return bool(self.st().get("next")) or self.st().get("dur", 0) > 0

    @pyqtProperty(bool)
    def CanGoPrevious(self):
        return bool(self.hub.st)

    @pyqtProperty(bool)
    def CanPlay(self):
        return bool(self.hub.st)

    @pyqtProperty(bool)
    def CanPause(self):
        return bool(self.hub.st)

    @pyqtProperty(bool)
    def CanSeek(self):
        return self.st().get("dur", 0) > 0

    @pyqtProperty(bool)
    def CanControl(self):
        return True


def metadata(hub):
    st = hub.st or {}
    if not st:
        return {"mpris:trackid": QDBusObjectPath("/org/mpris/MediaPlayer2/TrackList/NoTrack")}
    tid = "".join(c if c.isalnum() else "_" for c in (st.get("title") or "track"))[:60] or "track"
    md = {"mpris:trackid": QDBusObjectPath(f"/nacre/track/{tid}"),
          "xesam:title": st.get("title") or "",
          "xesam:artist": QDBusVariant([st.get("artist") or ""]),
          "xesam:album": st.get("album") or ""}
    if st.get("dur"):
        md["mpris:length"] = QDBusVariant(int(st["dur"] * 1e6))
    if (st.get("art") or "").startswith(("http://", "https://")):
        md["mpris:artUrl"] = st["art"]
    if hub.tab is not None:
        md["xesam:url"] = hub.tab.page.url().toString()
    return md


class Mpris(QObject):
    """Owns the D-Bus object; emits PropertiesChanged whenever the hub's state changes."""

    def __init__(self, ctx, hub):
        super().__init__(ctx)
        self.hub, self.ok = hub, False
        self.obj = QObject(self)
        RootAdaptor(self.obj, ctx)
        self.player = PlayerAdaptor(self.obj, hub)
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        name = f"org.mpris.MediaPlayer2.nacre.instance{os.getpid()}"
        self.ok = bus.registerService(name) and bus.registerObject(
            PATH, self.obj, QDBusConnection.RegisterOption.ExportAdaptors)
        self.last = {}
        self.last_pos = 0.0
        hub.changed.connect(self._changed)

    def _changed(self):
        p = self.player
        now = {"PlaybackStatus": p.PlaybackStatus, "Metadata": p.Metadata, "LoopStatus": p.LoopStatus,
               "CanGoNext": p.CanGoNext, "CanGoPrevious": p.CanGoPrevious, "CanPlay": p.CanPlay,
               "CanPause": p.CanPause, "CanSeek": p.CanSeek}
        diff = {k: v for k, v in now.items() if repr(self.last.get(k)) != repr(v)}
        self.last = now
        pos = (self.hub.st or {}).get("pos", 0.0)
        if abs(pos - self.last_pos) > 3:           # a jump bigger than the poll interval = the user seeked
            p.Seeked.emit(int(pos * 1e6))
        self.last_pos = pos
        if not diff or not self.ok:
            return
        msg = QDBusMessage.createSignal(PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged")
        empty = QDBusArgument()
        empty.beginArray(QMetaType.Type.QString.value)
        empty.endArray()
        msg.setArguments(["org.mpris.MediaPlayer2.Player", diff, empty])
        QDBusConnection.sessionBus().send(msg)
