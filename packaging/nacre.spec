# PyInstaller spec for the Windows build: `pyinstaller packaging/nacre.spec` from the repo root.
# Produces dist/Nacre/Nacre.exe (one folder: Python, Qt and Chromium included, nothing to install).
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "nacre", "ntp"), os.path.join("nacre", "ntp")),
         (os.path.join(ROOT, "assets"), "assets")]
fonts = os.path.join(ROOT, "nacre", "fonts")
if os.path.isdir(fonts):
    datas.append((fonts, os.path.join("nacre", "fonts")))

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    datas=datas,
    # nacre imports most modules lazily; qrcode/adblock/psutil come from pip in the build environment
    hiddenimports=collect_submodules("nacre") + collect_submodules("qrcode") + ["adblock", "psutil"],
    excludes=["tkinter", "PyQt6.QtDBus"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Nacre", console=False,
          icon=os.path.join(ROOT, "assets", "nacre.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="Nacre")
