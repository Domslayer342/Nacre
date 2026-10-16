# CI smoke test (run through NACRE_SCRIPT): wait for the new tab page, save a screenshot, check the fonts, quit.
import os

from PyQt6.QtCore import QTimer

OUT = os.environ["NACRE_SMOKE_OUT"]
w = ctx.windows[0]
w.resize(1280, 800)
FONT_JS = ("JSON.stringify({symbols: document.fonts.check('24px \"Material Symbols Rounded\"'),"
           " rubik: document.fonts.check('16px Rubik'), title: document.title})")


def report(result):
    with open(os.path.join(OUT, "smoke.txt"), "w") as f:
        f.write(f"page: {result}\nwidevine: {getattr(ctx, 'widevine_loaded', None)}\n"
                f"flags: {os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS')}\n")
    w.grab().save(os.path.join(OUT, "newtab.png"))
    w.new_tab("nacre://settings")
    QTimer.singleShot(4000, lambda: (w.grab().save(os.path.join(OUT, "settings.png")), ctx.quit()))


QTimer.singleShot(20000, lambda: w.tab().page.runJavaScript(FONT_JS, 0, report))
