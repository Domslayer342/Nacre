"""Built-in encrypted vault for passwords, addresses and payment cards — no keyring/wallet service needed.

Format (vault.json): the whole store is one AEAD-sealed JSON blob.
  device mode: the 256-bit vault key lives in vault.key (0600) — protects against casual snooping / backups.
  master mode: the vault key is wrapped by scrypt(master password); it is only ever held in memory.
Cipher: AES-256-GCM via `cryptography`; if that's missing, a stdlib encrypt-then-MAC construction
(HMAC-SHA256 counter-mode keystream + HMAC-SHA256 tag) so the feature never disappears.
Base64 is only used to put binary ciphertext into JSON — it is not the protection.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import string
import time
import uuid

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

from .config import DATA_DIR, cfg

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None

VAULT = os.path.join(DATA_DIR, "vault.json")
KEYFILE = os.path.join(DATA_DIR, "vault.key")
AAD = b"nacre-vault-v1"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
b64e = lambda b: base64.b64encode(b).decode()
b64d = base64.b64decode


class VaultError(Exception):
    pass


# ── crypto ────────────────────────────────────────────────────────────────────
def _stream_xor(key, nonce, data):
    out = bytearray()
    for i in range(0, len(data), 32):
        block = hmac.new(key, nonce + i.to_bytes(8, "big"), hashlib.sha256).digest()
        out += bytes(a ^ b for a, b in zip(data[i:i + 32], block))
    return bytes(out)


def seal(key, plain, cipher):
    if cipher == "aes-256-gcm":
        nonce = os.urandom(12)
        return nonce + AESGCM(key).encrypt(nonce, plain, AAD)
    ek = hmac.new(key, b"enc", hashlib.sha256).digest()
    mk = hmac.new(key, b"mac", hashlib.sha256).digest()
    nonce = os.urandom(16)
    ct = _stream_xor(ek, nonce, plain)
    return nonce + ct + hmac.new(mk, AAD + nonce + ct, hashlib.sha256).digest()


def unseal(key, blob, cipher):
    try:
        if cipher == "aes-256-gcm":
            if AESGCM is None:
                raise VaultError("This vault needs the 'cryptography' package (AES-GCM)")
            return AESGCM(key).decrypt(blob[:12], blob[12:], AAD)
        ek = hmac.new(key, b"enc", hashlib.sha256).digest()
        mk = hmac.new(key, b"mac", hashlib.sha256).digest()
        nonce, ct, tag = blob[:16], blob[16:-32], blob[-32:]
        if not hmac.compare_digest(tag, hmac.new(mk, AAD + nonce + ct, hashlib.sha256).digest()):
            raise VaultError("bad tag")
        return _stream_xor(ek, nonce, ct)
    except VaultError:
        raise
    except Exception as e:                     # InvalidTag etc. — wrong key or tampered file
        raise VaultError("Couldn't decrypt the vault (wrong password or damaged file)") from e


def kek(password, salt, n=SCRYPT["n"], r=SCRYPT["r"], p=SCRYPT["p"]):
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


# ── password generator ───────────────────────────────────────────────────────
AMBIGUOUS = set("Il1O0o")
WORDS = ("amber anchor apple arrow aspen atlas autumn badge bamboo banjo basil beacon birch bison blaze bloom "
         "breeze brook cabin cactus candle canyon cedar cherry cinder clover cobalt comet coral cosmic crane "
         "crystal dawn delta denim desert dune eagle ember fable falcon fern fjord flint forest frost galaxy "
         "garnet glacier granite harbor hazel heron honey indigo iris island ivory jade jasmine jungle kayak "
         "kestrel lagoon lantern lava lemon lilac lotus lunar maple marble meadow mesa meteor mint mirror "
         "monsoon moss nebula nectar noble nova oasis ocean olive onyx orbit orchid otter pebble pepper pine "
         "pixel planet plume polar prism quartz quill raven reef ridge river robin ruby saffron sage sapphire "
         "shadow sierra silver sky slate solar sparrow spruce storm summit sunset swift tango thistle thunder "
         "tide tiger topaz tulip tundra twilight valley velvet violet volcano walnut willow winter zephyr").split()


def generate(length=20, upper=True, digits=True, symbols=True, avoid_ambiguous=True):
    pools = [string.ascii_lowercase] + [p for on, p in ((upper, string.ascii_uppercase), (digits, string.digits),
                                                        (symbols, "!@#$%^&*-_=+?")) if on]
    if avoid_ambiguous:
        pools = ["".join(c for c in p if c not in AMBIGUOUS) for p in pools]
    length = max(length, len(pools))
    chars = [secrets.choice(p) for p in pools]                        # at least one from every class
    chars += [secrets.choice("".join(pools)) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def passphrase(words=5, sep="-"):
    w = [secrets.choice(WORDS) for _ in range(words)]
    w[secrets.randbelow(words)] += str(secrets.randbelow(10))
    return sep.join(x.capitalize() if i == 0 else x for i, x in enumerate(w))


COMMON = set("password passw0rd qwerty qwertyuiop letmein welcome admin login iloveyou monkey dragon football "
             "baseball sunshine princess master shadow superman trustno1 abc123 123456 12345678 123456789 "
             "1234567890 111111 000000 google".split())


def strength(pw):
    """Rough entropy estimate → (0..4, label)."""
    pool = sum(n for cls, n in ((string.ascii_lowercase, 26), (string.ascii_uppercase, 26), (string.digits, 10))
               if any(c in cls for c in pw)) + (33 if any(not c.isalnum() for c in pw) else 0)
    bits = len(pw) * (pool.bit_length() if pool else 0)
    score = 0 if bits < 28 else 1 if bits < 40 else 2 if bits < 64 else 3 if bits < 90 else 4
    if len(pw) < 8 or pw.lower().rstrip("0123456789!") in COMMON:
        score = 0
    elif len(set(pw)) < 5:
        score = min(score, 1)
    return score, ("Very weak", "Weak", "Fair", "Strong", "Very strong")[score]


def origin_of(url):
    u = QUrl(url)
    return f"{u.scheme()}://{u.host()}" + (f":{u.port()}" if u.port() > 0 else "")


def site_of(url):
    h = QUrl(url).host() or url
    return h[4:] if h.startswith("www.") else h


# ── vault ─────────────────────────────────────────────────────────────────────
class Vault(QObject):
    changed = pyqtSignal()
    lock_changed = pyqtSignal(bool)            # True = locked

    def __init__(self):
        super().__init__()
        self.key = None
        self.data = {"logins": [], "never": [], "addresses": [], "cards": []}
        self.meta = {}
        self.idle = QTimer(self, singleShot=True, timeout=self.lock)
        self._load_meta()
        if self.mode() == "device":
            self._unlock_with(self._device_key())

    # state
    def _load_meta(self):
        try:
            with open(VAULT) as f:
                self.meta = json.load(f)
        except (OSError, ValueError):
            self.meta = {"v": 1, "mode": "device", "cipher": "aes-256-gcm" if AESGCM else "hmac-sha256-ctr"}

    def mode(self):
        return self.meta.get("mode", "device")

    def cipher(self):
        return self.meta.get("cipher", "aes-256-gcm")

    def locked(self):
        return self.key is None

    def _device_key(self):
        try:
            with open(KEYFILE, "rb") as f:
                return b64d(f.read())
        except OSError:
            key = os.urandom(32)
            fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(b64e(key).encode())
            return key

    def _unlock_with(self, key):
        if "data" in self.meta:
            self.data = json.loads(unseal(key, b64d(self.meta["data"]), self.cipher()))
            for k in ("logins", "never", "addresses", "cards"):
                self.data.setdefault(k, [])
        self.key = key
        self.touch()
        self.lock_changed.emit(False)
        self.changed.emit()

    def unlock(self, password):
        kd = self.meta["kdf"]
        k = kek(password, b64d(kd["salt"]), kd["n"], kd["r"], kd["p"])
        self._unlock_with(unseal(k, b64d(self.meta["wrapped"]), self.cipher()))

    def lock(self):
        if self.mode() == "master" and self.key is not None:
            self.key = None
            self.data = {"logins": [], "never": [], "addresses": [], "cards": []}
            self.lock_changed.emit(True)
            self.changed.emit()

    def touch(self):
        """Restart the auto-lock countdown (master mode)."""
        mins = cfg.get("vault.autolock_min")
        if self.mode() == "master" and mins:
            self.idle.start(int(mins * 60000))

    def save(self):
        if self.key is None:
            raise VaultError("Vault is locked")
        self.meta["data"] = b64e(seal(self.key, json.dumps(self.data).encode(), self.cipher()))
        self.meta["saved"] = time.time()
        tmp = VAULT + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.meta, f)
        os.replace(tmp, VAULT)
        self.touch()
        self.changed.emit()

    # master password management
    def set_master(self, password):
        """Switch to (or change) master-password mode. The vault key itself stays the same."""
        if self.key is None:
            raise VaultError("Unlock first")
        salt = os.urandom(16)
        self.meta.update(mode="master", kdf={"salt": b64e(salt), **SCRYPT},
                         wrapped=b64e(seal(kek(password, salt), self.key, self.cipher())))
        self.save()
        try:
            os.remove(KEYFILE)                  # the key now only exists wrapped by the password
        except OSError:
            pass

    def remove_master(self):
        if self.key is None:
            raise VaultError("Unlock first")
        fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(b64e(self.key).encode())
        for k in ("kdf", "wrapped"):
            self.meta.pop(k, None)
        self.meta["mode"] = "device"
        self.idle.stop()
        self.save()

    # logins
    def logins_for(self, url):
        o, s = origin_of(url), site_of(url)
        exact = [x for x in self.data["logins"] if x["origin"] == o]
        return exact or [x for x in self.data["logins"] if x["site"] == s]

    def find(self, url, username):
        return next((x for x in self.logins_for(url) if x["username"] == username), None)

    def upsert_login(self, url, username, password, note=""):
        e = self.find(url, username)
        now = time.time()
        if e:
            e.update(password=password, updated=now)
        else:
            e = {"id": uuid.uuid4().hex, "origin": origin_of(url), "site": site_of(url), "username": username,
                 "password": password, "note": note, "created": now, "updated": now, "used": now}
            self.data["logins"].append(e)
        self.save()
        return e

    def delete(self, kind, item):
        if item in self.data[kind]:
            self.data[kind].remove(item)
            self.save()

    def never_for(self, url):
        return site_of(url) in self.data["never"]

    def add_never(self, url):
        if not self.never_for(url):
            self.data["never"].append(site_of(url))
            self.save()

    # import / export (Chrome & Firefox CSV)
    def import_csv(self, text):
        n = 0
        for row in csv.DictReader(io.StringIO(text)):
            row = {k.lower().strip(): (v or "") for k, v in row.items() if k}
            url = row.get("url") or row.get("origin") or row.get("hostname") or ""
            user, pw = row.get("username", ""), row.get("password", "")
            if url and pw:
                if "://" not in url:
                    url = "https://" + url
                e = self.find(url, user)
                if e:
                    e["password"] = pw
                else:
                    now = time.time()
                    self.data["logins"].append({"id": uuid.uuid4().hex, "origin": origin_of(url), "site": site_of(url),
                                                "username": user, "password": pw, "note": row.get("note", ""),
                                                "created": now, "updated": now, "used": 0})
                n += 1
        self.save()
        return n

    def export_csv(self):
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["name", "url", "username", "password", "note"])
        for e in self.data["logins"]:
            w.writerow([e["site"], e["origin"], e["username"], e["password"], e.get("note", "")])
        return out.getvalue()

    # stats for the UI
    def weak_or_reused(self):
        counts = {}
        for e in self.data["logins"]:
            counts[e["password"]] = counts.get(e["password"], 0) + 1
        return {e["id"]: ("reused" if counts[e["password"]] > 1 else "weak" if strength(e["password"])[0] < 2 else "")
                for e in self.data["logins"]}


vault = None


def get_vault():
    global vault
    if vault is None:
        vault = Vault()
    return vault
