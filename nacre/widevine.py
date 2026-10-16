"""Widevine CDM: find an installed copy (Chromium / Chrome / Firefox / our own), or fetch Google's from its update
service. Google's license doesn't allow shipping the module inside Nacre, so it's downloaded per machine."""
import glob
import hashlib
import io
import os
import struct
import zipfile

from PyQt6.QtCore import QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from .config import DATA_DIR

OWN = os.path.join(DATA_DIR, "WidevineCdm")
SO = ("_platform_specific/win_x64/widevinecdm.dll" if os.name == "nt"
      else "_platform_specific/linux_x64/libwidevinecdm.so")
# Firefox's GMP update service answers with Google's own download URL + a SHA-512 for the Widevine component
GMP = ("https://aus5.mozilla.org/update/3/GMP/140.0/20250601000000/Linux_x86_64-gcc3/null/release/Linux%206/"
       "default/default/update.xml")


def candidates():
    home = os.path.expanduser("~")
    fixed = [os.path.join(OWN, SO), f"/usr/lib/chromium/WidevineCdm/{SO}", f"/opt/google/chrome/WidevineCdm/{SO}",
             f"/usr/lib/chromium-browser/WidevineCdm/{SO}", f"/usr/lib64/chromium/WidevineCdm/{SO}",
             f"/opt/brave.com/brave/WidevineCdm/{SO}", f"{home}/.local/lib/libwidevinecdm.so",
             "/usr/lib/chromium/libwidevinecdm.so"]
    found = sorted(glob.glob(f"{home}/.config/*/WidevineCdm/*/{SO}"), reverse=True)              # component-updated
    found += sorted(glob.glob(f"{home}/.mozilla/firefox/*/gmp-widevinecdm/*/libwidevinecdm.so"), reverse=True)
    if os.name == "nt":                          # Chrome / Edge / Brave keep a component-updated copy
        local = os.environ.get("LOCALAPPDATA", "")
        fixed = [os.path.join(OWN, SO)]
        found = []
        for app in (r"Google\Chrome", r"Microsoft\Edge", r"BraveSoftware\Brave-Browser"):
            found += sorted(glob.glob(os.path.join(local, app, "User Data", "WidevineCdm", "*", SO)), reverse=True)
    return fixed + found


def find_cdm():
    return next((p for p in candidates() if os.path.isfile(p)), None)


def chromium_flag():
    p = find_cdm()
    return f"--widevine-path={p}" if p else ""


def parse_update(raw):
    """GMP update XML → (urls, sha512, version) for gmp-widevinecdm; only Google-hosted URLs are accepted."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(raw)
    for a in root.iter("addon"):
        if a.get("id") == "gmp-widevinecdm":
            urls = [a.get("URL")] + [m.get("URL") for m in a.iter("mirror")]
            urls = [u for u in urls if u and QUrl(u).scheme() == "https"
                    and QUrl(u).host().endswith(("gvt1.com", "google.com", "googleapis.com"))]
            if not urls or a.get("hashFunction") != "sha512":
                raise ValueError("unexpected update entry")
            return urls, a.get("hashValue"), a.get("version")
    raise ValueError("no Widevine entry in the update list")


def unpack_crx3(data, dest):
    if data[:4] != b"Cr24":
        raise ValueError("not a CRX file")
    version, header = struct.unpack("<II", data[4:12])
    if version != 3:
        raise ValueError(f"unexpected CRX version {version}")
    with zipfile.ZipFile(io.BytesIO(data[12 + header:])) as z:
        names = z.namelist()
        if not any(n.endswith("libwidevinecdm.so") for n in names):
            raise ValueError("package has no Linux CDM")
        os.makedirs(dest, exist_ok=True)
        for n in names:                                  # no absolute / parent paths
            if n.startswith("/") or ".." in n.split("/"):
                raise ValueError("unsafe path in package")
        z.extractall(dest)
    os.chmod(os.path.join(dest, SO), 0o755)


_nam = None


def install(parent, done, progress=None):
    """Async: look up the current CDM, download it from Google, verify SHA-512, unpack into the Nacre profile.
    `done(ok, message)` is called at the end; `progress(fraction)` while downloading."""
    global _nam
    if os.name == "nt":
        return done(False, "On Windows, install Chrome or Edge once; Nacre uses their Widevine copy")
    _nam = _nam or QNetworkAccessManager()
    r1 = _nam.get(QNetworkRequest(QUrl(GMP)))

    def got_list():
        try:
            if r1.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError(r1.errorString())
            urls, sha, ver = parse_update(bytes(r1.readAll()))
        except Exception as e:
            return done(False, f"Couldn't look up Widevine: {e}")
        finally:
            r1.deleteLater()
        dl = QNetworkRequest(QUrl(urls[0]))
        dl.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        dl.setTransferTimeout(900000)
        r2 = _nam.get(dl)
        if progress:
            r2.downloadProgress.connect(lambda got, total: total > 0 and progress(got / total))

        def got_package():
            try:
                if r2.error() != QNetworkReply.NetworkError.NoError:
                    raise ValueError(r2.errorString())
                data = bytes(r2.readAll())
                if hashlib.sha512(data).hexdigest() != sha:
                    raise ValueError("download didn't match the published checksum")
                unpack_crx3(data, OWN)
                done(True, f"Installed Widevine {ver} · restart Nacre to use it")
            except Exception as e:
                done(False, f"Widevine install failed: {e}")
            finally:
                r2.deleteLater()
        r2.finished.connect(got_package)
    r1.finished.connect(got_list)
