"""MediaHub: polls the tab that is playing media (shared by MPRIS, the media flyout and the new tab page)."""
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

STATE_JS = "window.__nacre ? __nacre.state() : null"


class MediaHub(QObject):
    """Tracks the tab that's playing (or was, if paused) and polls its state once a second."""
    changed = pyqtSignal()

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx, self.tab, self.st = ctx, None, None
        self.timer = QTimer(self, interval=1000, timeout=self.poll)
        self.timer.start()

    def tabs(self):
        return [t for w in self.ctx.windows for t in w.tabs if not w.incognito]

    def poll(self):
        tabs = self.tabs()
        playing = [t for t in tabs if t.item.audible]
        if playing:
            self.tab = playing[-1]
        elif not any(self.tab is t for t in tabs):
            self.tab = None
        if self.tab is None:
            if self.st is not None:
                self.st = None
                self.changed.emit()
            return
        try:
            self.tab.page.runJavaScript(STATE_JS, 0, self._got)
        except RuntimeError:
            self.tab = None

    def _got(self, st):
        if st != self.st:
            self.st = st
            self.changed.emit()

    def act(self, action, value=None):
        if self.tab is None:
            return
        arg = "" if value is None else f", {float(value)}"
        try:
            self.tab.page.runJavaScript(f"window.__nacre && __nacre.act('{action}'{arg})", 0, self._got)
        except RuntimeError:
            self.tab = None
