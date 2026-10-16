import os as _os
import sys as _sys

# bundled third-party packages (qrcode, adblock) — appended so a system install still wins
_sys.path.append(_os.path.join(_os.path.dirname(__file__), "_vendor"))
