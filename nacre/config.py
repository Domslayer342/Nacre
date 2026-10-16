"""Persistent settings + small JSON stores (history, bookmarks, session)."""
import json
import os
from PyQt6.QtCore import QObject, QTimer, pyqtSignal


def _xdg(var, fallback):
    if os.name == "nt" and not os.environ.get(var):            # Windows: %LOCALAPPDATA%\Nacre\<kind>
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Nacre", fallback.rsplit("/", 1)[-1].lstrip("."))
    return os.path.join(os.environ.get(var) or os.path.expanduser(fallback), "nacre")


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", "~/.config")
DATA_DIR = _xdg("XDG_DATA_HOME", "~/.local/share")
CACHE_DIR = _xdg("XDG_CACHE_HOME", "~/.cache")
for _d in (CONFIG_DIR, DATA_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)

UBO = "https://ublockorigin.github.io/uAssets/filters/"
ADG = "https://filters.adtidy.org/extension/ublock/filters/"
FANBOY = "https://secure.fanboy.co.nz/"
MALWARE = "https://malware-filter.gitlab.io/malware-filter/"
# group -> key -> (name, url, description)
FILTER_GROUPS = {
    "Ads": {
        "easylist": ("EasyList", "https://easylist.to/easylist/easylist.txt", "The main ad list"),
        "ublock": ("uBlock filters", UBO + "filters.txt", "uBlock Origin's own ad rules"),
        "adguard_base": ("AdGuard Base", ADG + "2_without_easylist.txt", "AdGuard ads, minus EasyList overlap"),
        "adguard_mobile": ("AdGuard Mobile Ads", ADG + "11.txt", "Ads on mobile-style sites"),
        "pgl": ("Peter Lowe's list", "https://pgl.yoyo.org/adservers/serverlist.php?hostformat=hosts&showintro=0"
                "&mimetype=plaintext", "Small, fast ad + tracker host list"),
    },
    "Privacy": {
        "easyprivacy": ("EasyPrivacy", "https://easylist.to/easylist/easyprivacy.txt", "Trackers and analytics"),
        "ublock_privacy": ("uBlock Privacy", UBO + "privacy.txt", "Extra tracking protection"),
        "adguard_tracking": ("AdGuard Tracking Protection", ADG + "3.txt", "Thorough tracker blocking"),
        "adguard_url": ("AdGuard URL Tracking", ADG + "17.txt", "Strips tracking parameters' endpoints"),
    },
    "Security": {
        "ublock_badware": ("uBlock Badware risks", UBO + "badware.txt", "Scam and fake-download sites"),
        "urlhaus": ("URLhaus Malicious URLs", MALWARE + "urlhaus-filter-ag-online.txt", "Active malware hosts"),
        "phishing": ("Phishing URL Blocklist", MALWARE + "phishing-filter-ag.txt", "Known phishing pages"),
    },
    "Annoyances": {
        "annoyances": ("uBlock Annoyances", UBO + "annoyances.txt", "Pop-ups, overlays, nags"),
        "cookies": ("EasyList Cookie List", FANBOY + "fanboy-cookiemonster.txt", "Hides cookie banners"),
        "fanboy_annoyance": ("Fanboy Annoyances", FANBOY + "fanboy-annoyance.txt", "Social widgets, newsletters"),
        "adguard_annoyances": ("AdGuard Annoyances", ADG + "14.txt", "AdGuard's annoyance collection"),
    },
    "Hosts & DNS lists": {
        "stevenblack": ("StevenBlack hosts", "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
                        "Unified ad/malware hosts"),
        "oisd": ("OISD small", "https://small.oisd.nl/", "Low false-positive DNS list"),
        "hagezi": ("HaGeZi Pro", "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.txt",
                   "Aggressive but well maintained"),
        "onehosts": ("1Hosts Lite", "https://o0.pages.dev/Lite/adblock.txt", "Light, balanced host list"),
    },
    "Fixes": {
        "ublock_quick": ("uBlock Quick fixes", UBO + "quick-fixes.txt", "Fast-moving fixes (YouTube, Twitch…)"),
        "ublock_unbreak": ("uBlock Unbreak", UBO + "unbreak.txt", "Exceptions that un-break sites"),
    },
}
FILTER_LISTS = {k: v[:2] for g in FILTER_GROUPS.values() for k, v in g.items()}

SEARCH_ENGINES = {
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q={}"),
    "google": ("Google", "https://www.google.com/search?q={}"),
    "brave": ("Brave", "https://search.brave.com/search?q={}"),
    "startpage": ("Startpage", "https://www.startpage.com/do/search?q={}"),
    "bing": ("Bing", "https://www.bing.com/search?q={}"),
    "kagi": ("Kagi", "https://kagi.com/search?q={}"),
}

# key -> (symbol, label, implemented)
TOOLBAR_ITEMS = {
    "Navigation": {
        "home": ("home", "Home", True),
        "forward": ("arrow_forward", "Forward", True),
        "split": ("splitscreen_right", "Open in split view", True),
        "incognito": ("domino_mask", "New Incognito window", True),
    },
    "Your Nacre": {
        "passwords": ("key", "Password Manager", True),
        "payments": ("credit_card", "Payment methods", True),
        "addresses": ("location_on", "Addresses and more", True),
        "bookmarks": ("bookmarks", "Bookmarks", True),
        "reading_list": ("chrome_reader_mode", "Reading list", True),
        "history": ("history", "History", True),
        "downloads": ("download", "Downloads", True),
        "clear_data": ("delete", "Delete browsing data", True),
    },
    "Tools and actions": {
        "print": ("print", "Print", True),
        "translate": ("translate", "Translate", True),
        "qr": ("qr_code", "Create QR Code", True),
        "reading_mode": ("auto_stories", "Reading mode", True),
        "copy_link": ("link", "Copy link", True),
        "task_manager": ("monitoring", "Task manager", True),
        "devtools": ("code", "Developer tools", True),
        "adblock": ("shield", "Ad blocker", True),
    },
}

DEFAULTS = {
    "theme.mode": "system",          # system | light | dark
    "theme.source": "website",       # website | kde | preset | custom
    "theme.fallback": "kde",         # kde | preset (when a site has no colour)
    "theme.preset": "#6750A4",
    "theme.custom": "#3F6EE8",
    "theme.variant": "tonal_spot",
    "theme.contrast": 0.0,
    "ui.motion": 1.0,                # 0 = off, 0.6 fast, 1 normal, 1.6 slow
    "ui.roundness": 1.0,
    "ui.frameless": True,
    "ui.floating_card": True,
    "web.force_dark": False,
    "startup.mode": "newtab",        # newtab | restore | home
    "home.url": "nacre://newtab",
    "search.engine": "duckduckgo",
    "adblock.enabled": True,
    "adblock.lists": ["easylist", "ublock", "pgl", "easyprivacy", "ublock_privacy", "ublock_badware", "urlhaus",
                      "ublock_quick", "ublock_unbreak", "cookies"],
    "adblock.custom": [],            # extra list URLs added by the user
    "adblock.cosmetic": True,        # element hiding (##) rules
    "adblock.youtube": True,
    "adblock.animate": True,         # removed ads squish away in a Material animation (visible ones only)         # built-in YouTube ad defuser
    "adblock.update_days": 7,        # 0 = manual only (kind to slow connections)
    "adblock.allow": [],
    "adblock.total": 0,
    "privacy.dnt": True,
    "downloads.dir": os.path.expanduser("~/Downloads"),
    "downloads.ask": False,
    "downloads.auto_open": True,
    "downloads.abdm": True,
    "downloads.abdm_port": 15151,
    "media.button": "auto",          # auto | always | never
    "media.visualizer": "auto",      # auto (cava if present) | simulated | off
    "media.spin": True,
    "rpc.enabled": False,
    "rpc.target": "arrpc",           # arrpc | discord
    "rpc.client_id": "",             # Discord application ID (discord.com/developers → New Application)
    "rpc.custom": [],                # extra media sites to report
    "rpc.disabled_sites": [],        # supported sites switched off
    "rpc.site_ids": {},              # site -> its own Discord application ID (shows as that app's name)
    "rpc.browsing": "off",           # off | site | title — report non-media tabs too
    "rpc.browse_type": "watch",      # watch | listen — never "playing", which games/shells use
    "rpc.display": "details",        # what Discord's member list shows: name | state | details
    "rpc.button": True,
    "rpc.show_paused": True,         # keep the activity (marked Paused, no progress bar) instead of clearing it
    "net.save_data": False,
    "tabs.lazy_restore": True,       # restored tabs load when first opened
    "tabs.freeze_min": 5,            # freeze hidden, silent tabs after N minutes (0 = never)
    "tabs.discard_min": 60,          # unload them entirely after N minutes (0 = never)
    "zoom.sites": {},
    "quirks.google_signin": True,    # look like Firefox on accounts.google.com so Google lets you sign in
    "vault.enabled": True,           # offer to save passwords, fill them in
    "vault.autofill": True,          # fill automatically when a site has exactly one saved login
    "vault.autofill_forms": True,    # suggest saved addresses / cards
    "vault.autolock_min": 15,        # master-password mode: lock after N idle minutes (0 = never)
    "vault.reauth": True,            # ask for the Linux login password before revealing saved passwords
    "vault.reauth_min": 5,           # …then trust this session for N minutes (0 = ask every time)
    "vault.gen_length": 20,
    "vault.gen_symbols": True,
    "vault.clipboard_clear": 30,     # seconds before a copied password is wiped from the clipboard (0 = never)                # host -> zoom factor
    "ntp.bg": "shapes",              # none | shapes | blobs | particles | waves | dots
    "ntp.bg_density": "medium",
    "ntp.bg_speed": "normal",
    "ntp.bg_mouse": True,
    "ntp.bg_image": "",              # file in DATA_DIR (image or GIF), shown under the animated layer
    "ntp.bg_dim": 0.35,
    "ntp.bg_blur": 0,
    "ntp.elements": [["clock", True], ["greeting", False], ["search", True], ["shortcuts", True], ["media", True],
                     ["weather", False], ["notes", False], ["reading", False], ["recent", False]],
    "ntp.clock24": False,
    "ntp.seconds": False,
    "ntp.name": "",
    "ntp.city": "",
    "ntp.city_loc": None,
    "ntp.units": "auto",
    "ntp.notes": "",
    "ntp.shortcuts": [
        ["YouTube", "https://www.youtube.com"], ["GitHub", "https://github.com"],
        ["Reddit", "https://www.reddit.com"], ["Wikipedia", "https://wikipedia.org"],
        ["Arch Wiki", "https://wiki.archlinux.org"], ["YT Music", "https://music.youtube.com"],
    ],
}
for _items in TOOLBAR_ITEMS.values():
    for _k in _items:
        DEFAULTS["toolbar." + _k] = _k in ("home", "forward", "passwords", "history", "downloads", "qr", "adblock")


class JsonStore(QObject):
    """A JSON file with a debounced save."""

    def __init__(self, path, default):
        super().__init__()
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = default
        self._timer = QTimer(self, singleShot=True, interval=500, timeout=self.save)

    def touch(self):
        self._timer.start()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)


class Config(JsonStore):
    changed = pyqtSignal(str, object)

    def __init__(self):
        super().__init__(os.path.join(CONFIG_DIR, "settings.json"), {})

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        if self.get(key) == value:
            return
        self.data[key] = value
        self.touch()
        self.changed.emit(key, value)


cfg = Config()
history = JsonStore(os.path.join(DATA_DIR, "history.json"), [])
bookmarks = JsonStore(os.path.join(DATA_DIR, "bookmarks.json"), [])
reading = JsonStore(os.path.join(DATA_DIR, "reading.json"), [])
session = JsonStore(os.path.join(DATA_DIR, "session.json"), [])
