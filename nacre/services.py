"""Background services: ad blocking, downloads (built-in + AB Download Manager), nacre:// pages."""
import hashlib
import json
import os
import re
import threading
import time
from html import escape

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QLocale, QObject, QTimer, QUrl, QUrlQuery, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkCookieJar, QNetworkReply, QNetworkRequest
from PyQt6.QtWebEngineCore import (QWebEngineDownloadRequest, QWebEngineScript, QWebEngineUrlRequestInfo,
                                   QWebEngineUrlRequestInterceptor, QWebEngineUrlSchemeHandler)

from .config import CACHE_DIR, DATA_DIR, FILTER_LISTS, SEARCH_ENGINES, cfg

try:
    import adblock as _adblock
except ImportError:
    _adblock = None

FILTER_DIR = os.path.join(CACHE_DIR, "filters")
os.makedirs(FILTER_DIR, exist_ok=True)
RT = QWebEngineUrlRequestInfo.ResourceType
RT_NAMES = {RT.ResourceTypeScript: "script", RT.ResourceTypeImage: "image", RT.ResourceTypeStylesheet: "stylesheet",
            RT.ResourceTypeFontResource: "font", RT.ResourceTypeXhr: "xmlhttprequest", RT.ResourceTypeMedia: "media",
            RT.ResourceTypeSubFrame: "sub_frame", RT.ResourceTypePing: "ping", RT.ResourceTypeFavicon: "image",
            RT.ResourceTypeWebSocket: "websocket", RT.ResourceTypeSubResource: "other"}
_RULE = re.compile(r"^(@@)?\|\|([a-z0-9.\-_]+)\^(\$(.*))?$")
_HOSTS = re.compile(r"^(?:0\.0\.0\.0|127\.0\.0\.1)\s+([a-z0-9.\-_]+)")
_PLAIN = re.compile(r"^[a-z0-9\-_]+(\.[a-z0-9\-_]+)+$")
_SAFE_OPTS = {"third-party", "3p", "script", "image", "xmlhttprequest", "subdocument", "ping", "media", "font",
              "stylesheet", "other", "all", "important", "popup", "websocket"}


def host_of(url):
    h = QUrl(url).host() if not isinstance(url, QUrl) else url.host()
    return h[4:] if h.startswith("www.") else h


def _suffixes(host):
    parts = host.split(".")
    return (".".join(parts[i:]) for i in range(len(parts) - 1))


class AdBlock(QWebEngineUrlRequestInterceptor):
    """Profile-wide request filter + cosmetic hiding. Uses adblock-rust when importable, else host rules."""
    blocked = pyqtSignal(str, str)          # first-party host, blocked url
    lists_updated = pyqtSignal()
    progress = pyqtSignal(object)           # 0..1 while lists download, None when done
    _parsed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.block, self.allow, self.engine, self._n_rules = set(), set(), None, 0
        self.generic, self.specific, self.unhide = [], {}, {}
        self.page_counts = {}
        self.session_total = 0
        self.profiles = []
        self.busy = False
        self.nam = QNetworkAccessManager(self)
        self._pending = self._total = 0
        self._parsed.connect(self._swap)
        self.reload()
        stale = [k for k in self.enabled() if self._stale(k)]
        if stale:
            self.update_lists(stale)
        cfg.changed.connect(self._cfg)

    # ── lists ──
    @staticmethod
    def path(key):
        name = key if key in FILTER_LISTS else "custom-" + hashlib.sha1(key.encode()).hexdigest()[:12]
        return os.path.join(FILTER_DIR, name + ".txt")

    @staticmethod
    def url_of(key):
        return FILTER_LISTS[key][1] if key in FILTER_LISTS else key

    @staticmethod
    def enabled():
        return [k for k in cfg.get("adblock.lists") if k in FILTER_LISTS] + list(cfg.get("adblock.custom"))

    def _stale(self, key):
        age, days = self.list_age(key), cfg.get("adblock.update_days")
        return age is None or (days and age > days * 86400)

    def engine_name(self):
        return "adblock-rust" if self.engine else "host rules"

    def rule_count(self):
        return self._n_rules

    def list_age(self, key):
        try:
            return time.time() - os.path.getmtime(self.path(key))
        except OSError:
            return None

    def _cfg(self, k, v):
        if k in ("adblock.lists", "adblock.custom"):
            missing = [x for x in self.enabled() if not os.path.exists(self.path(x))]
            self.update_lists(missing) if missing else self.reload()
        elif k in ("adblock.cosmetic", "adblock.youtube", "adblock.enabled", "quirks.google_signin", "adblock.animate"):
            self.install_scripts()

    def update_lists(self, keys=None):
        for k in keys or self.enabled():
            self._pending += 1
            self._total += 1
            req = QNetworkRequest(QUrl(self.url_of(k)))
            req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            req.setTransferTimeout(120000)
            age = self.list_age(k)
            if age is not None:                   # 304 Not Modified costs a few bytes instead of megabytes
                req.setRawHeader(b"If-Modified-Since", time.strftime(
                    "%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() - age)).encode())
            reply = self.nam.get(req)
            reply.finished.connect(lambda r=reply, k=k: self._got(r, k))
        if self._pending:
            self.progress.emit(1 - self._pending / max(1, self._total))

    def _got(self, reply, key):
        code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if code == 304:
            os.utime(self.path(key))
        elif reply.error() == QNetworkReply.NetworkError.NoError:
            data = bytes(reply.readAll())
            if len(data) > 200:
                with open(self.path(key), "wb") as f:
                    f.write(data)
        reply.deleteLater()
        self._pending -= 1
        self.progress.emit(1 - self._pending / max(1, self._total) if self._pending > 0 else None)
        if self._pending <= 0:
            self._total = 0
            self.reload()

    def reload(self):
        """Parse in a worker thread — big host lists would otherwise stall the UI on a slow machine."""
        if self.busy:
            self._again = True
            return
        self.busy, self._again = True, False
        keys = self.enabled()
        paths = [self.path(k) for k in keys]

        def work():
            res = parse_lists(paths)
            self._parsed.emit(res + (build_engine(res[-1], paths),))
        threading.Thread(target=work, daemon=True).start()

    def _swap(self, res):
        self.block, self.allow, self.generic, self.specific, self.unhide, texts, self.engine = res
        self._n_rules = (sum(t.count("\n") for t in texts) if self.engine
                         else len(self.block) + len(self.generic) + len(self.specific))
        self.busy = False
        self.install_scripts()
        self.lists_updated.emit()
        if self._again:
            self.reload()

    # ── page scripts (cosmetic CSS + YouTube defuser) ──
    def attach(self, profile):
        self.profiles.append(profile)
        self.install_scripts()

    def install_scripts(self):
        on = cfg.get("adblock.enabled")
        rule = "{animation:__nz_detect 1ms!important}" if cfg.get("adblock.animate") else "{display:none!important}"
        css = "".join(f"{sel}{rule}\n" for sel in self.generic) if (on and cfg.get("adblock.cosmetic")) else ""
        for p in self.profiles:
            sc = p.scripts()
            for old in sc.find("nacre-cosmetic") + sc.find("nacre-yt"):
                sc.remove(old)
            if css:
                sc.insert(make_script("nacre-cosmetic", COSMETIC_JS.replace("%CSS%", json.dumps(css)),
                                      QWebEngineScript.ScriptWorldId.ApplicationWorld))
            if on and cfg.get("adblock.youtube"):
                sc.insert(make_script("nacre-yt", YOUTUBE_JS, QWebEngineScript.ScriptWorldId.MainWorld))
            for z in sc.find("nacre-zap"):
                sc.remove(z)
            if on and cfg.get("adblock.cosmetic"):
                sc.insert(make_script("nacre-zap", ZAP_JS, QWebEngineScript.ScriptWorldId.ApplicationWorld))
            for q in sc.find("nacre-quirks"):
                sc.remove(q)
            if cfg.get("quirks.google_signin"):
                sc.insert(quirk_script())

    def site_rules(self, url):
        """Per-page cosmetic rules → (selectors to remove, extra CSS). Removal is animated by the zapper script."""
        host = QUrl(url).host()
        if not (cfg.get("adblock.enabled") and cfg.get("adblock.cosmetic")) or self.site_allowed(host_of(host)):
            return [], ""
        sels, css = [], ""
        if self.engine:                          # adblock-rust knows each site's own hiding rules
            try:
                res = self.engine.url_cosmetic_resources(url)
                sels = [x for x in res.hide_selectors if not any(p in x for p in _PROCEDURAL)]
                css = "".join(f"{sel}{{{';'.join(st)}!important}}\n" for sel, st in res.style_selectors.items())
            except Exception:
                sels = []
        if not sels:
            unhide = set()
            for sfx in _suffixes(host) if "." in host else ():
                sels += self.specific.get(sfx, ())
                unhide |= self.unhide.get(sfx, set())
            sels = [x for x in sels if x not in unhide]
            css += "".join(f"{x}{{display:revert!important}}\n" for x in unhide)
        if cfg.get("adblock.youtube") and (host == "youtube.com" or host.endswith(".youtube.com")):
            sels += YT_AD_SELECTORS
        return list(dict.fromkeys(sels)), css

    # ── per-site state ──
    def site_allowed(self, host):
        return host in cfg.get("adblock.allow")

    def toggle_site(self, host):
        allow = list(cfg.get("adblock.allow"))
        allow.remove(host) if host in allow else allow.append(host)
        cfg.set("adblock.allow", allow)

    def count(self, host):
        return self.page_counts.get(host, 0)

    def reset_page(self, host):
        self.page_counts.pop(host, None)

    def _match(self, url, host, src, rtype):
        if self.engine:
            return self.engine.check_network_urls(url, src, rtype).matched
        if any(s in self.allow for s in _suffixes(host)):
            return False
        return any(s in self.block for s in _suffixes(host))

    def interceptRequest(self, info):
        if cfg.get("privacy.dnt"):
            info.setHttpHeader(b"DNT", b"1")
            info.setHttpHeader(b"Sec-GPC", b"1")
        if cfg.get("net.save_data"):
            info.setHttpHeader(b"Save-Data", b"on")
        if cfg.get("quirks.google_signin") and info.requestUrl().host() in QUIRK_HOSTS:
            info.setHttpHeader(b"User-Agent", FIREFOX_UA.encode())   # Google refuses sign-in from embedded Chromium
        if not cfg.get("adblock.enabled"):
            return
        rtype = RT_NAMES.get(info.resourceType())
        if rtype is None:                    # main frame / navigations / unknown
            return
        url, fp = info.requestUrl(), info.firstPartyUrl()
        if url.scheme() not in ("http", "https", "ws", "wss"):
            return
        page = host_of(fp)
        if page and self.site_allowed(page):
            return
        host = url.host().lower()
        if host == fp.host() or not self._match(url.toString(), host, fp.toString(), rtype):
            return
        info.block(True)
        self.page_counts[page] = self.page_counts.get(page, 0) + 1
        self.session_total += 1
        cfg.data["adblock.total"] = cfg.get("adblock.total") + 1
        cfg.touch()
        self.blocked.emit(page, url.toString())


# uBO-only selector syntax that a browser stylesheet can't express
_PROCEDURAL = (":has-text(", ":upward(", ":remove(", ":style(", ":matches-css", ":xpath(", ":min-text-length(",
               ":watch-attr(", ":others(", ":-abp-", ":contains(", ":matches-path(", ":matches-attr(", ":if(",
               ":if-not(", ":remove-attr(", ":remove-class(", ":nth-ancestor(", ":matches-prop(")


def parse_lists(paths):
    block, allow, generic, specific, unhide, texts = set(), set(), set(), {}, {}, []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        if _adblock:
            texts.append(text)
        for line in text.splitlines():
            line = line.strip()
            if not line or line[0] in "!#[" and not line.startswith("##"):
                continue
            if "##" in line or "#@#" in line:
                exc = "#@#" in line
                doms, _, sel = line.partition("#@#" if exc else "##")
                if not sel or sel.startswith(("+js", "^")) or any(p in sel for p in _PROCEDURAL) or "{" in sel:
                    continue
                if not doms:
                    if not exc:
                        generic.add(sel)
                    continue
                for d in doms.split(","):
                    d = d.strip().lower()
                    if d and not d.startswith("~") and "*" not in d:
                        d = d.removeprefix("www.")
                        if exc:
                            unhide.setdefault(d, set()).add(sel)
                        else:
                            specific.setdefault(d, []).append(sel)
                continue
            if _adblock or "#" in line:
                continue
            low = line.lower()
            m = _HOSTS.match(low)
            if m:
                if m.group(1) not in ("localhost", "0.0.0.0", "local", "broadcasthost"):
                    block.add(m.group(1))
                continue
            m = _RULE.match(low)
            if not m:
                if _PLAIN.match(low):        # bare-domain lists
                    block.add(low)
                continue
            opts = set(o.lstrip("~") for o in (m.group(4) or "").split(",") if o)
            if opts - _SAFE_OPTS:
                continue                     # domain=, redirect=, csp= etc. — too specific for a host set
            (allow if m.group(1) else block).add(m.group(2))
    return block, allow, sorted(generic), specific, unhide, texts


def zap_badge(primary, on_primary):
    """Cookie-shaped shield badge (SVG data URI) in the current theme colours."""
    import math
    pts = " ".join(f"{50 + 46 * (1 + 0.06 * math.cos(9 * t)) * math.cos(t):.1f},{50 + 46 * (1 + 0.06 * math.cos(9 * t)) * math.sin(t):.1f}"
                   for t in (math.tau * i / 90 for i in range(90)))
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><polygon points='{pts}' fill='{primary}'/>"
           f"<path d='M50 24 32 31v13c0 12 7.7 22.7 18 26 10.3-3.3 18-14 18-26V31z' fill='{on_primary}'/>"
           f"<path d='m43 50 5 5 10-11' fill='none' stroke='{primary}' stroke-width='5' stroke-linecap='round' "
           f"stroke-linejoin='round'/></svg>")
    return "data:image/svg+xml," + QUrl.toPercentEncoding(svg).data().decode()


def build_engine(texts, paths):
    """adblock-rust engine, cached on disk keyed by the lists' paths + mtimes (startup is then instant)."""
    if not (_adblock and texts):
        return None
    try:
        key = hashlib.sha1(repr([(p, os.path.getmtime(p)) for p in paths if os.path.exists(p)]).encode()).hexdigest()[:16]
    except OSError:
        key = None
    cache = os.path.join(FILTER_DIR, f"engine-{key}.dat") if key else None
    if cache and os.path.exists(cache):
        try:
            e = _adblock.Engine(_adblock.FilterSet(), optimize=True)
            e.deserialize_from_file(cache)
            return e
        except Exception:
            pass
    fs = _adblock.FilterSet()
    for t in texts:
        fs.add_filter_list(t)
    e = _adblock.Engine(fs, optimize=True)
    if cache:
        try:
            for old in os.listdir(FILTER_DIR):
                if old.startswith("engine-") and old.endswith(".dat"):
                    os.remove(os.path.join(FILTER_DIR, old))
            e.serialize_to_file(cache)
        except Exception:
            pass
    return e


# Google blocks sign-in from "embedded" Chromium (QtWebEngine). Presenting Firefox to the sign-in host only —
# the same quirk qutebrowser ships — gets past "This browser or app may not be secure".
FIREFOX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
QUIRK_HOSTS = {"accounts.google.com"}
QUIRK_JS = r"""(() => { if (!%HOSTS%.includes(location.hostname)) return;
  const ua = %UA%, nav = Navigator.prototype;
  for (const [k, v] of [['userAgent', ua], ['appVersion', '5.0 (X11)'], ['vendor', ''], ['platform', 'Linux x86_64']])
    try { Object.defineProperty(nav, k, { get: () => v, configurable: true }); } catch (e) {}
  try { Object.defineProperty(nav, 'userAgentData', { get: () => undefined, configurable: true }); } catch (e) {}
})();"""


def quirk_script():
    return make_script("nacre-quirks", QUIRK_JS.replace("%HOSTS%", json.dumps(sorted(QUIRK_HOSTS)))
                       .replace("%UA%", json.dumps(FIREFOX_UA)), QWebEngineScript.ScriptWorldId.MainWorld)


def make_script(name, src, world):
    s = QWebEngineScript()
    s.setName(name)
    s.setSourceCode(src)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    s.setWorldId(world)
    s.setRunsOnSubFrames(name in ("nacre-cosmetic", "nacre-zap"))
    return s


# constructable stylesheet: needs no <head>, one rule per selector so a bad selector only drops itself
COSMETIC_JS = r"""(() => { const css = %CSS%; let el = null;
  const attach = () => { const root = document.documentElement; if (!root) return false;
    if (!el) { el = document.createElement('style'); el.id = '__nacre_cosmetic'; el.textContent = css; }
    if (!el.isConnected && !root.hasAttribute('data-nacre-off')) (document.head || root).append(el); return true; };
  if (!attach()) new MutationObserver((_, o) => { if (attach()) o.disconnect(); }).observe(document, { childList: true });
  // pages that wipe <head> (SPAs) get it back
  addEventListener('DOMContentLoaded', () => new MutationObserver(() => attach()).observe(document.documentElement, { childList: true, subtree: false }));
})();"""

# Animated ad removal for EVERY cosmetic rule (generic + site + YouTube), like Pie Adblock.
# Rules don't hide elements directly: they give them a 1 ms "__nz_detect" animation, and the browser's own
# animationstart event tells us which element just rendered — no selector scanning at all.
# Video safety: anything that is/contains <video>/<audio> is never touched; ad bits inside a player are hidden
# without animation (the player keeps its layout); huge elements are hidden, never collapsed.
ZAP_JS = r"""(() => { if (window.__nacreZap) return;
  let opts = null, queue = [], live = 0;
  const base = document.createElement('style');
  base.textContent = '@keyframes __nz_detect{from{outline-color:inherit}to{outline-color:inherit}}' +
    '.__nz_gone{display:none!important}' +
    '@keyframes __nz_ov{0%{opacity:0;transform:none;clip-path:inset(0 round 12px)}18%{opacity:1}' +
    '40%{opacity:1;transform:scale(.97);clip-path:inset(0 round 28px)}70%{opacity:1;transform:scale(.72) rotate(-4deg);' +
    'clip-path:inset(8% round 48%)}100%{opacity:0;transform:scale(0) rotate(-14deg);clip-path:inset(30% round 50%)}}';
  const mount = () => { const r = document.documentElement; if (!r) return false; if (!base.isConnected) (document.head || r).append(base); return true; };
  if (!mount()) new MutationObserver((_, o) => { if (mount()) o.disconnect(); }).observe(document, { childList: true });
  const PLAYER = '.html5-video-player,.video-js,.jwplayer,.plyr,[data-vjs-player],.vjs-tech,.shaka-video-container,.dplayer';
  const gone = el => el.classList.add('__nz_gone');
  const collapse = el => { const st = el.style, set = (k, v) => st.setProperty(k, v, 'important');
    set('height', el.offsetHeight + 'px'); set('width', el.offsetWidth + 'px'); set('min-width', '0'); set('min-height', '0');
    set('flex', 'none'); set('box-sizing', 'border-box'); set('overflow', 'hidden');
    set('transition', 'height .34s cubic-bezier(.3,0,.2,1),width .34s cubic-bezier(.3,0,.2,1),margin .34s,padding .34s');
    requestAnimationFrame(() => requestAnimationFrame(() => { set('height', '0px'); set('width', '0px'); set('margin', '0'); set('padding', '0'); }));
    setTimeout(() => gone(el), 380); };
  const zap = (el, r) => {
    const ov = document.createElement('div');
    ov.style.cssText = `position:absolute;left:${r.left + scrollX}px;top:${r.top + scrollY}px;width:${r.width}px;height:${r.height}px;` +
      `z-index:2147483647;pointer-events:none;background:url("${opts.badge}") center/min(64px,40%) no-repeat,${opts.bg};` +
      `animation:__nz_ov ${opts.ms}ms cubic-bezier(.3,0,.2,1) forwards;transform-origin:center`;
    document.documentElement.append(ov);
    el.style.setProperty('transition', 'opacity .22s ease .12s', 'important');
    el.style.setProperty('opacity', '0', 'important');                      // the ad fades out under the overlay
    live++;
    setTimeout(() => { ov.remove(); live--; collapse(el); }, opts.ms);
  };
  const handle = el => {
    if (!(el instanceof Element) || el.__nz) return;
    if (!opts) return queue.push(el);
    if (opts.off) return;                                                                // ad blocking paused here
    el.__nz = 1;
    if (el.matches('video,audio') || el.querySelector('video,audio')) return;          // never kill a real player
    if (el.closest(PLAYER)) return gone(el);                                             // ad UI inside a player: quietly
    const r = el.getBoundingClientRect();
    const onScreen = r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
    const small = r.width < 24 || r.height < 24, huge = r.width * r.height > innerWidth * innerHeight * 0.6;
    if (!opts.animate || small || huge || !onScreen || live > 12) return gone(el);
    zap(el, r);
  };
  document.addEventListener('animationstart', e => { if (e.animationName === '__nz_detect') handle(e.target); }, true);
  let site = null;
  window.__nacreZap = (list, o) => {
    opts = o;
    if (o.off) {                       // paused on this site: drop the generic rules too
      document.documentElement.setAttribute('data-nacre-off', '');
      const g = document.getElementById('__nacre_cosmetic'); if (g) g.remove();
      queue = []; return;
    }
    if (!site) { site = document.createElement('style'); site.id = '__nacre_zap_site'; }
    const rule = o.animate ? '{animation:__nz_detect 1ms!important}' : '{display:none!important}';
    site.textContent = list.map(s => s + rule).join('\n') + '\n' + (o.extra || '');   // one rule per selector
    if (!site.isConnected) (document.head || document.documentElement).append(site);
    const q = queue; queue = []; q.forEach(el => { el.__nz = 0; handle(el); });
    setTimeout(() => { if (!site.isConnected) (document.head || document.documentElement).append(site); }, 1500);
  };
  setTimeout(() => { if (!opts) window.__nacreZap([], { animate: false, ms: 0, bg: 'transparent', badge: '' }); }, 4000);
})();"""

# uBlock-style hiding for YouTube's ad containers (the whole grid cell for in-feed ads, so no gap is left)
YT_AD_SELECTORS = [
    "ytd-rich-item-renderer:has(> #content > ytd-ad-slot-renderer)", "ytd-rich-item-renderer:has(ytd-ad-slot-renderer)",
    "ytd-ad-slot-renderer", "ytd-in-feed-ad-layout-renderer", "ytd-display-ad-renderer", "ytd-promoted-sparkles-web-renderer",
    "ytd-promoted-video-renderer", "ytd-compact-promoted-video-renderer", "ytd-search-pyv-renderer", "ytd-banner-promo-renderer",
    "ytd-statement-banner-renderer", "ytd-video-masthead-ad-v3-renderer", "#masthead-ad", "#player-ads",
    "ytd-engagement-panel-section-list-renderer[target-id='engagement-panel-ads']", "ytd-merch-shelf-renderer",
    "ytd-ads-engagement-panel-content-renderer", "ytd-rich-section-renderer:has(ytd-statement-banner-renderer)",
    "ytd-reel-video-renderer:has(.ytd-ad-slot-renderer)", "ytd-player-legacy-desktop-watch-ads-renderer",
    ".ytd-mealbar-promo-renderer", "ytmusic-mealbar-promo-renderer", "ytm-promoted-sparkles-web-renderer",
]


# json-prune style YouTube defuser: strip ad payloads before the player sees them; skip anything that slips by
YOUTUBE_JS = r"""(() => { const h = location.hostname; if (!/(^|\.)youtube(-nocookie)?\.com$/.test(h)) return;
  const KEYS = ['adPlacements', 'adSlots', 'playerAds', 'adBreakHeartbeatParams'];
  const prune = o => { try { if (o && typeof o === 'object') { for (const k of KEYS) if (k in o) delete o[k];
      if (o.playerResponse) prune(o.playerResponse); if (o.response) prune(o.response); } } catch (e) {} return o; };
  const jp = JSON.parse; JSON.parse = function () { return prune(jp.apply(this, arguments)); };
  const rj = Response.prototype.json; Response.prototype.json = function () { return rj.apply(this, arguments).then(prune); };
  for (const name of ['ytInitialPlayerResponse', 'ytInitialData']) { let v;
    try { Object.defineProperty(window, name, { configurable: true, get: () => v, set: x => { v = prune(x); } }); } catch (e) {} }
  const skip = () => { const p = document.querySelector('#movie_player.ad-showing, ytmusic-player .ad-showing');
    if (p) { const v = p.querySelector('video'); if (v && isFinite(v.duration)) { v.muted = true; v.currentTime = v.duration; } }
    const b = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern');
    if (b) b.click(); };
  new MutationObserver(skip).observe(document, { subtree: true, attributes: true, attributeFilter: ['class'], childList: true });
})();"""


# ── downloads ─────────────────────────────────────────────────────────────────
class DownloadEntry(QObject):
    changed = pyqtSignal()
    S = QWebEngineDownloadRequest.DownloadState

    def __init__(self, req=None, name="", url="", external=False):
        super().__init__()
        self.req, self.external = req, external
        self.name = name or (req.downloadFileName() if req else "download")
        self.url = url or (req.url().toString() if req else "")
        self.speed, self.eta, self.started = 0.0, None, time.time()
        self._last = (time.monotonic(), 0)
        if req:
            req.receivedBytesChanged.connect(self._progress)
            req.totalBytesChanged.connect(self.changed)
            req.stateChanged.connect(self.changed)
            req.isPausedChanged.connect(self.changed)

    def _progress(self):
        now, got = time.monotonic(), self.req.receivedBytes()
        t0, b0 = self._last
        if now - t0 >= 0.4:
            inst = (got - b0) / (now - t0)
            self.speed = inst if not self.speed else self.speed * 0.7 + inst * 0.3
            total = self.req.totalBytes()
            self.eta = (total - got) / self.speed if total > 0 and self.speed > 1 else None
            self._last = (now, got)
        self.changed.emit()

    @property
    def state(self):
        if self.external:
            return "external"
        s = self.req.state()
        if s == self.S.DownloadInProgress:
            return "paused" if self.req.isPaused() else "progress"
        return {self.S.DownloadCompleted: "done", self.S.DownloadCancelled: "cancelled",
                self.S.DownloadInterrupted: "failed"}.get(s, "progress")

    @property
    def fraction(self):
        if not self.req or self.req.totalBytes() <= 0:
            return None
        return self.req.receivedBytes() / self.req.totalBytes()

    @property
    def path(self):
        return os.path.join(self.req.downloadDirectory(), self.req.downloadFileName()) if self.req else ""

    def status(self):
        s = self.state
        if s == "external":
            return "Sent to AB Download Manager"
        if s == "done":
            return f"{human(self.req.totalBytes())} · Done"
        if s in ("cancelled", "failed"):
            return "Cancelled" if s == "cancelled" else (self.req.interruptReasonString() or "Failed")
        got, total = self.req.receivedBytes(), self.req.totalBytes()
        if s == "paused":
            return f"Paused · {human(got)}" + (f" of {human(total)}" if total > 0 else "")
        eta = f" · {fmt_eta(self.eta)} left" if self.eta is not None else ""
        return f"{human(self.speed)}/s · {human(got)}" + (f" of {human(total)}" if total > 0 else "") + eta


def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def fmt_eta(s):
    s = int(s)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s" if s < 3600 else f"{s // 3600}h {s // 60 % 60}m"


class Downloads(QObject):
    added = pyqtSignal(object)
    finished = pyqtSignal(object)
    handed_off = pyqtSignal(str)            # name — sent to ABDM
    failed_handoff = pyqtSignal(str)

    def __init__(self, profile, parent=None):
        super().__init__(parent)
        self.profile, self.entries = profile, []
        self.nam = QNetworkAccessManager(self)
        self.jar = QNetworkCookieJar(self)
        self.abdm_alive = False
        self.force_builtin = set()
        self.ask_path = None                 # set by window: callable(suggested) -> path | None
        store = profile.cookieStore()
        store.cookieAdded.connect(self.jar.insertCookie)
        store.cookieRemoved.connect(self.jar.deleteCookie)
        store.loadAllCookies()
        profile.downloadRequested.connect(self.handle)
        self.ping_timer = QTimer(self, interval=30000, timeout=self.ping)
        self.ping_timer.start()
        self.ping()
        cfg.changed.connect(lambda k, v: k.startswith("downloads.abdm") and self.ping())

    # ABDM
    def _abdm(self, path):
        return QUrl(f"http://localhost:{cfg.get('downloads.abdm_port')}{path}")

    def ping(self, cb=None):
        if not cfg.get("downloads.abdm"):
            self.abdm_alive = False
            return cb and cb(False)
        req = QNetworkRequest(self._abdm("/ping"))
        req.setTransferTimeout(1500)
        reply = self.nam.post(req, QByteArray())

        def done():
            self.abdm_alive = reply.error() == QNetworkReply.NetworkError.NoError
            reply.deleteLater()
            cb and cb(self.abdm_alive)
        reply.finished.connect(done)

    def _headers(self, url, referer):
        cookies = "; ".join(f"{bytes(c.name()).decode(errors='ignore')}={bytes(c.value()).decode(errors='ignore')}"
                            for c in self.jar.cookiesForUrl(QUrl(url)))
        h = {"User-Agent": self.profile.httpUserAgent()}
        if cookies:
            h["Cookie"] = cookies
        if referer:
            h["Referer"] = referer
        return h

    def hand_off(self, url, name, page_url, page=None):
        body = {"items": [{"link": url, "headers": self._headers(url, page_url), "downloadPage": page_url,
                           "suggestedName": name, "type": "http"}],
                "options": {"silentAdd": False, "silentStart": False}}
        req = QNetworkRequest(self._abdm("/add"))
        req.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, "application/json")
        req.setTransferTimeout(4000)
        reply = self.nam.post(req, json.dumps(body).encode())

        def done():
            ok = reply.error() == QNetworkReply.NetworkError.NoError
            reply.deleteLater()
            if ok:
                e = DownloadEntry(name=name, url=url, external=True)
                self.entries.insert(0, e)
                self.added.emit(e)
                self.handed_off.emit(name)
                return
            self.abdm_alive = False
            self.failed_handoff.emit(name)
            if page is not None:              # fall back to the built-in downloader
                self.force_builtin.add(url)
                try:
                    page.download(QUrl(url), name)
                except RuntimeError:
                    pass
        reply.finished.connect(done)

    # built-in
    def handle(self, req: QWebEngineDownloadRequest):
        url = req.url().toString()
        name = req.downloadFileName()
        external = (cfg.get("downloads.abdm") and self.abdm_alive and url not in self.force_builtin
                    and req.url().scheme() in ("http", "https") and not req.isSavePageDownload())
        self.force_builtin.discard(url)
        if external:
            page = req.page()
            page_url = page.url().toString() if page else ""
            req.cancel()
            self.hand_off(url, name, page_url, page)
            return
        req.setDownloadDirectory(cfg.get("downloads.dir"))
        if cfg.get("downloads.ask") and self.ask_path:
            path = self.ask_path(os.path.join(cfg.get("downloads.dir"), name))
            if not path:
                req.cancel()
                return
            req.setDownloadDirectory(os.path.dirname(path))
            req.setDownloadFileName(os.path.basename(path))
        req.accept()
        e = DownloadEntry(req)
        req.isFinishedChanged.connect(lambda: self.finished.emit(e))
        self.entries.insert(0, e)
        self.added.emit(e)

    def active(self):
        return [e for e in self.entries if e.state in ("progress", "paused")]

    def overall(self):
        act = [e for e in self.active() if e.fraction is not None]
        return sum(e.fraction for e in act) / len(act) if act else None

    def clear_finished(self):
        self.entries = [e for e in self.entries if e.state in ("progress", "paused")]


# ── nacre:// ──────────────────────────────────────────────────────────────────
NTP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ntp")
NTP_STATIC = {"/ntp.js": b"text/javascript", "/bg.js": b"text/javascript", "/ntp.css": b"text/css"}
# bundled fonts (nacre/fonts, fetched by the install scripts) for when they aren't installed system-wide
FONT_FILES = {"/fonts/rubik.ttf": "Rubik", "/fonts/symbols.ttf": "MaterialSymbolsRounded"}
_font_cache = {}


def bundled_font(path):
    if path not in _font_cache:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
        name = next((n for n in (sorted(os.listdir(d)) if os.path.isdir(d) else [])
                     if n.startswith(FONT_FILES[path] + "[") or n.startswith(FONT_FILES[path] + ".")), None)
        with open(os.path.join(d, name), "rb") if name else open(os.devnull, "rb") as f:
            _font_cache[path] = f.read() or None
    return _font_cache[path]
BG_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/avif": "avif",
            "image/bmp": "bmp", "image/svg+xml": "svg"}


def ntp_state(ctx):
    """Everything the new tab page needs on first paint (no extra round trips on a slow line)."""
    from .config import DEFAULTS, history, reading
    eng_name, eng_url = SEARCH_ENGINES.get(cfg.get("search.engine"), SEARCH_ENGINES["duckduckgo"])
    counts, titles = {}, {}
    for e in history.data[-3000:]:
        hst = host_of(e["url"])
        if hst:
            counts[hst] = counts.get(hst, 0) + 1
            titles[hst] = (e.get("title") or hst, f"https://{QUrl(e['url']).host()}/")
    top = sorted(counts, key=counts.get, reverse=True)[:6]
    known = set(dict(cfg.get("ntp.elements")))
    elements = [list(x) for x in cfg.get("ntp.elements")] + [list(x) for x in DEFAULTS["ntp.elements"] if x[0] not in known]
    keys = [k for k in DEFAULTS if k.startswith("ntp.") and k != "ntp.shortcuts"]
    return {"cfg": {**{k: cfg.get(k) for k in keys}, "ntp.elements": elements},
            "shortcuts": cfg.get("ntp.shortcuts"), "motion": cfg.get("ui.motion"),
            "imperial": QLocale.system().measurementSystem() == QLocale.MeasurementSystem.ImperialUSSystem,
            "engine": {"name": eng_name, "url": eng_url.split("?")[0]},
            "reading": [{"url": r["url"], "title": r.get("title", ""), "read": r.get("read", False)}
                        for r in reversed(reading.data[-20:])],
            "recent": [{"host": hh, "title": titles[hh][0], "url": titles[hh][1]} for hh in top]}


def ntp_html(css_vars, ctx=None):
    with open(os.path.join(NTP_DIR, "index.html"), encoding="utf-8") as f:
        page = f.read()
    state = json.dumps(ntp_state(ctx)).replace("</", "<\\/")
    return page.replace("%VARS%", "".join(f"--{k}:{v};" for k, v in css_vars.items())).replace("%STATE%", state)


def sniff_image(data):
    sig = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"GIF8": "gif", b"BM": "bmp"}
    for k, v in sig.items():
        if data.startswith(k):
            return v
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "avif"
    return None


US_STATES = dict(x.split(":") for x in (
    "AL:Alabama AK:Alaska AZ:Arizona AR:Arkansas CA:California CO:Colorado CT:Connecticut DE:Delaware FL:Florida "
    "GA:Georgia HI:Hawaii ID:Idaho IL:Illinois IN:Indiana IA:Iowa KS:Kansas KY:Kentucky LA:Louisiana ME:Maine "
    "MD:Maryland MA:Massachusetts MI:Michigan MN:Minnesota MS:Mississippi MO:Missouri MT:Montana NE:Nebraska "
    "NV:Nevada NH:New_Hampshire NJ:New_Jersey NM:New_Mexico NY:New_York NC:North_Carolina ND:North_Dakota OH:Ohio "
    "OK:Oklahoma OR:Oregon PA:Pennsylvania RI:Rhode_Island SC:South_Carolina SD:South_Dakota TN:Tennessee TX:Texas "
    "UT:Utah VT:Vermont VA:Virginia WA:Washington WV:West_Virginia WI:Wisconsin WY:Wyoming DC:District_of_Columbia"
).replace("_", "~").split())
US_STATES = {k: v.replace("~", " ") for k, v in US_STATES.items()}
_WEATHER_CACHE = {}


def _get_json(nam, url, done):
    req = QNetworkRequest(QUrl(url))
    req.setTransferTimeout(15000)
    reply = nam.get(req)

    def fin():
        try:
            obj = json.loads(bytes(reply.readAll())) if reply.error() == QNetworkReply.NetworkError.NoError else None
        except ValueError:
            obj = None
        reply.deleteLater()
        done(obj)
    reply.finished.connect(fin)


def geocode(nam, text, done):
    """'38382' (ZIP), 'Trenton, TN', 'Paris, France' or a plain name → up to 8 places."""
    text = (text or "").strip()
    if len(text) < 2:
        return done({"results": []})
    parts = [p.strip() for p in text.split(",") if p.strip()]
    name, hint = parts[0], (parts[1] if len(parts) > 1 else "")
    zipcode = re.fullmatch(r"\d{5}(-\d{4})?", name)
    q = QUrlQuery()
    q.addQueryItem("name", name[:5] if zipcode else name)
    q.addQueryItem("count", "20" if hint else "8")
    q.addQueryItem("language", "en")
    if zipcode or hint.upper() in US_STATES:
        q.addQueryItem("countryCode", "US")

    def got(obj):
        res = (obj or {}).get("results") or []
        if hint:
            h = hint.lower()
            full = US_STATES.get(hint.upper(), "").lower()
            res = [r for r in res if (r.get("admin1") or "").lower().startswith(full or h)
                   or (r.get("country") or "").lower().startswith(h) or (r.get("country_code") or "").lower() == h] or res
        out = []
        for r in res[:8]:
            region = r.get("admin1") or ""
            if r.get("country_code") == "US":
                region = next((k for k, v in US_STATES.items() if v == region), region)
            label = ", ".join(x for x in (r.get("name"), region, r.get("country_code") if r.get("country_code") != "US"
                                           else "") if x)
            out.append({"label": label, "detail": ", ".join(x for x in (r.get("admin2"), r.get("admin1"), r.get("country")) if x),
                        "lat": r["latitude"], "lon": r["longitude"]})
        done({"results": out})
    _get_json(nam, "https://geocoding-api.open-meteo.com/v1/search?" + q.toString(QUrl.ComponentFormattingOption.FullyEncoded), got)


def weather(nam, lat, lon, units, done):
    """Current conditions + today's range; cached 10 minutes (slow-line friendly)."""
    key = (round(lat, 3), round(lon, 3), units)
    hit = _WEATHER_CACHE.get(key)
    if hit and time.time() - hit[0] < 600:
        return done(hit[1])
    imperial = units == "f"
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&timezone=auto&forecast_days=1"
           "&current=temperature_2m,apparent_temperature,weather_code,is_day,relative_humidity_2m,wind_speed_10m"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           + ("&temperature_unit=fahrenheit&wind_speed_unit=mph" if imperial else ""))

    def got(obj):
        if not obj or "current" not in obj:
            return done({"error": "Weather service didn't answer"})
        c, d = obj["current"], obj.get("daily", {})
        first = lambda k: (d.get(k) or [None])[0]
        data = {"temp": c.get("temperature_2m"), "feels": c.get("apparent_temperature"), "code": c.get("weather_code"),
                "day": bool(c.get("is_day", 1)), "humidity": c.get("relative_humidity_2m"),
                "wind": c.get("wind_speed_10m"), "hi": first("temperature_2m_max"), "lo": first("temperature_2m_min"),
                "rain": first("precipitation_probability_max"), "unit": "°F" if imperial else "°C",
                "wind_unit": "mph" if imperial else "km/h"}
        _WEATHER_CACHE[key] = (time.time(), data)
        done(data)
    _get_json(nam, url, got)


def bg_path():
    name = cfg.get("ntp.bg_image")
    return os.path.join(DATA_DIR, name) if name else None


def clean_shortcuts(data):
    """Validate a shortcut list coming from the page: [[name, url], ...] with http(s) URLs only."""
    out = []
    for item in data if isinstance(data, list) else []:
        if (isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item)
                and QUrl(item[1]).scheme() in ("http", "https")):
            out.append([item[0][:40], item[1][:2000]])
    return out[:24]


class NacreScheme(QWebEngineUrlSchemeHandler):
    def __init__(self, css_vars, parent=None):
        super().__init__(parent)
        self.css_vars = css_vars              # callable -> dict
        self.ctx = parent
        self.jobs, self._nam, self.art_cache = set(), None, {}

    def requestStarted(self, job):
        url = job.requestUrl()
        host, path = url.host() or "newtab", url.path() or "/"
        if host != "newtab":                      # nacre://settings etc. are native widgets; serve a stub
            return self._reply(job, f"<!doctype html><title>{escape(host.title())}</title><body style='background:#000'></body>")
        if path in NTP_STATIC:
            with open(os.path.join(NTP_DIR, path[1:]), "rb") as f:
                return self._reply(job, f.read(), NTP_STATIC[path])
        if path in FONT_FILES:
            data = bundled_font(path)
            return self._reply(job, data, b"font/ttf") if data else job.fail(job.Error.UrlNotFound)
        if path == "/bg":
            p = bg_path()
            if not p or not os.path.exists(p):
                return job.fail(job.Error.UrlNotFound)
            with open(p, "rb") as f:
                ext = p.rsplit(".", 1)[-1]
                mime = next((m for m, e in BG_TYPES.items() if e == ext), "application/octet-stream")
                return self._reply(job, f.read(), mime.encode())
        if path == "/art":                       # artwork proxy: nacre:// pages can't load web images themselves
            return self._art(job, QUrlQuery(url).queryItemValue("u", QUrl.ComponentFormattingOption.FullyDecoded))
        if path.startswith("/api/"):
            if job.initiator().scheme() != "nacre":    # only Nacre's own pages may call the API
                return job.fail(job.Error.RequestDenied)
            if path in ("/api/geocode", "/api/weather", "/api/levels"):  # answered asynchronously
                return self._async(job, path[5:], QUrlQuery(url))
            try:
                body = self._api(job, path[5:], QUrlQuery(url))
            except Exception as e:
                body = {"ok": False, "error": str(e)}
            return self._reply(job, json.dumps(body), b"application/json")
        self._reply(job, ntp_html(self.css_vars(), self.ctx))

    # ── weather (fetched here: pages on nacre:// can't make web requests) ──
    def _async(self, job, name, q):
        self.jobs.add(job)                       # keep the job alive until we answer

        def answer(obj):
            self.jobs.discard(job)
            try:
                self._reply(job, json.dumps(obj), b"application/json")
            except RuntimeError:                 # the page went away meanwhile
                pass
        val = lambda k: q.queryItemValue(k, QUrl.ComponentFormattingOption.FullyDecoded)
        if name == "geocode":
            return geocode(self.nam(), val("q"), answer)
        if name == "levels":
            hub = getattr(self.ctx, "media", None)
            if hub is None or hub.tab is None:
                return answer(None)
            try:
                return hub.tab.page.runJavaScript("window.__nacre && __nacre.levels(28)", 0, answer)
            except RuntimeError:
                return answer(None)
        try:
            lat, lon = float(val("lat")), float(val("lon"))
        except ValueError:
            return answer({"error": "no location"})
        weather(self.nam(), lat, lon, val("units"), answer)

    def _art(self, job, u):
        if QUrl(u).scheme() not in ("http", "https"):
            return job.fail(job.Error.UrlInvalid)
        hit = self.art_cache.get(u)
        if hit:
            return self._reply(job, hit[1], hit[0])
        self.jobs.add(job)
        req = QNetworkRequest(QUrl(u))
        req.setTransferTimeout(15000)
        req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self.nam().get(req)

        def fin():
            self.jobs.discard(job)
            data = bytes(reply.readAll()) if reply.error() == QNetworkReply.NetworkError.NoError else b""
            mime = str(reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader) or "image/jpeg").encode()
            reply.deleteLater()
            try:
                if not data:
                    return job.fail(job.Error.RequestFailed)
                if len(self.art_cache) > 24:
                    self.art_cache.pop(next(iter(self.art_cache)))
                self.art_cache[u] = (mime, data)
                self._reply(job, data, mime)
            except RuntimeError:
                pass
        reply.finished.connect(fin)

    def nam(self):
        if self._nam is None:
            self._nam = QNetworkAccessManager(self)
        return self._nam

    def _api(self, job, name, q):
        val = lambda k: q.queryItemValue(k, QUrl.ComponentFormattingOption.FullyDecoded)
        if name == "shortcuts":
            cfg.set("ntp.shortcuts", clean_shortcuts(json.loads(val("set"))))
            return {"ok": True}
        if name == "set":
            k = val("k")
            from .config import DEFAULTS
            if not k.startswith("ntp.") or k not in DEFAULTS and k != "ntp.city_loc":
                return {"ok": False}
            cfg.set(k, json.loads(val("v")))
            return {"ok": True}
        if name == "bg":
            old = bg_path()
            if q.hasQueryItem("clear"):
                cfg.set("ntp.bg_image", "")
            else:
                dev = job.requestBody()
                if dev is not None and not dev.isOpen():
                    dev.open(QIODevice.OpenModeFlag.ReadOnly)
                data = bytes(dev.readAll()) if dev is not None else b""
                ext = sniff_image(data)                # trust the bytes, not the header
                if not data or not ext or len(data) > 40_000_000:
                    return {"ok": False, "error": "unsupported image"}
                name_ = f"ntp-bg-{int(time.time())}.{ext}"
                with open(os.path.join(DATA_DIR, name_), "wb") as f:
                    f.write(data)
                cfg.set("ntp.bg_image", name_)
            if old and os.path.exists(old) and old != bg_path():
                os.remove(old)
            return {"ok": True, "name": cfg.get("ntp.bg_image")}
        if name == "media":
            hub = getattr(self.ctx, "media", None)
            if hub is None:
                return {}
            act = val("act")
            if act in ("toggle", "next", "prev", "loop"):
                hub.act(act)
            elif act == "seek":
                hub.act("seek", float(val("v") or 0))
                if hub.st is not None:
                    hub.st = {**hub.st, "pos": float(val("v") or 0)}
            elif act == "focus" and hub.tab is not None:
                hub.tab.win.focus_tab(hub.tab)
                if hub.st is not None and act == "toggle":
                    hub.st = {**hub.st, "paused": not hub.st.get("paused")}     # answer optimistically
            st = dict(hub.st or {})
            if st and hub.tab is not None:
                st["host"] = host_of(hub.tab.page.url())
            return st
        return {"ok": False}

    @staticmethod
    def _reply(job, body, mime=b"text/html"):
        buf = QBuffer(job)
        buf.setData(body if isinstance(body, bytes) else body.encode())
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(mime, buf)
