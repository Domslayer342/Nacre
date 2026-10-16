# Nacre — handoff (updated 2026-10-15, end of session 1)
Material You browser: PyQt6 6.11 / Qt 6.12 / Chromium 140, Python 3.14, Arch KDE Wayland. ~10k lines in nacre/.
Run: `python run.py` (`--install-desktop`, `--app <id>`). Nacre is the user's default browser (single instance).
Repo: https://github.com/Domslayer342/Nacre (branch main, public). Commit + push finished work there.
Install scripts: scripts/build.sh (Linux venv/--system, fonts, --desktop), scripts/install-windows.ps1
(irm | iex or from a clone), scripts/fetch_fonts.py → nacre/fonts (gitignored; app.load_fonts loads them).
Screenshots for the README live in docs/screenshots (regenerate with the NACRE_SCRIPT harness).

## Module map
| File | What |
|---|---|
| app.py | Nacre app/ctx, profiles, single-instance hand-off ($XDG_RUNTIME_DIR/nacre-<hash>.sock), shutdown order |
| window.py | BrowserWindow (tabs, omnibox + OmniPopup, split view, app mode for PWAs, zap_ads), WebTab |
| tabs.py / widgets.py / theme.py | tab strip; Flyout, Button, TextField, wavy progress helpers, guard(); materialyoucolor theme |
| panels.py | menu, shield, history, downloads, find, SiteInfoFlyout (lock icon) |
| settings_page.py | settings sections + search, nacre://settings#section deep links |
| pages.py | native pages: history, bookmarks, downloads, about, reading, tasks, apps (NATIVE map) |
| services.py | AdBlock (vendored adblock-rust, cached engine), ZAP_JS, Downloads (+ABDM hand-off), NacreScheme (nacre:// + /api/*), geocode/weather, quirks |
| ntp/ | new tab: index.html, ntp.css, ntp.js (elements, customize sheet, player clone, weather), bg.js (shapes/blobs/particles/waves/dots) |
| media.py / hub.py / mpris.py | media flyout + real Web Audio levels; MediaHub (playing-tab poller); MPRIS2 over QtDBus (Linux only) |
| rpc.py | Discord IPC: SITES profiles, browsing mode (Watching/Listening only, never Playing) |
| vault.py / autofill.py / passwords_ui.py / auth.py | encrypted vault (AES-GCM, scrypt; HMAC fallback), autofill bridge, UI, PAM re-auth gate |
| pwa.py | installable web apps, launcher entries, nacre://apps |
| reader.py | reading mode (shadow DOM, ApplicationWorld) |
| widevine.py | finds the CDM (/usr/lib/chromium/WidevineCdm) or installs it from Mozilla's GMP list (never bundled) |
| icon.py, config.py, _vendor/ | icon, cfg store, vendored qrcode + adblock |

## Gotchas (cost time before)
- Flyout subclasses must not assign `self.body`.
- `clicked(bool)` overwrites lambda defaults: use `lambda _, x=x:`. Use `widgets.guard(owner, fn)` on long-lived signals.
- Delete pages before their profiles (teardown/shutdown), or exit segfaults. Crashes go to ~/.cache/nacre/crash.log.
- nacre:// pages can't fetch the web or load web images: proxy through Python (/api/*, /art?u=).
  `requestBody()` must be `open()`ed; keep async replies in `self.jobs`.
- CSS `display:flex` beats `[hidden]` (ntp.css forces it). rAF loops must not bail before the element is attached.
- YouTube resets adoptedStyleSheets: use a re-attaching `<style>`.
- `s.replace("", X)` interleaves X everywhere: assert slice anchor order when patching files with scripts.
- Translucent popups skip QSS item-view backgrounds (OmniPopup paints its own).

## Testing
Isolated XDG dirs in the scratchpad + `NACRE_SCRIPT=steps.py` (gets ctx/app), `widget.grab()` screenshots, QTest
for input (no xdotool on Wayland). Local test pages via `python -m http.server` on a fresh port each run.
Fake IPC sockets go in $XDG_RUNTIME_DIR (108-char limit). Never push presence to the user's real Discord.
Never test PAM with wrong passwords against faillock services (vlock is pam_unix only).

## Ad zapper v2 (latest work)
Every cosmetic rule sets `animation: __nz_detect 1ms` (when adblock.animate) and an `animationstart` listener
catches matches → overlay tint + cookie badge → squish → collapse. Guards: never touch elements that are or
contain video/audio; inside known players hide only; >60% of viewport hide only; max 12 animations; paused
site sets data-nacre-off and drops the generic style. Verified on generic ads, iframes and YouTube playback.

## Windows port (2026-10-16, untested on real Windows)
Guards: config dirs → %LOCALAPPDATA%\Nacre\{config,share,cache}; MPRIS only on Linux (QtDBus not imported);
single-instance uses a named pipe; Discord RPC uses \\.\pipe\discord-ipc-N; tasks page empty without /proc;
Widevine found from Chrome/Edge/Brave (installer disabled); PAM gate is skipped (no reauth on Windows);
adblock comes from PyPI. Ideas: Windows Hello / CredUI for the reveal gate, SMTC media keys, tasks via psutil.

## Unverified (needs the user)
- Google sign-in quirk at the real password step; PAM success path with the real password.
- Zapper on more real sites (news sites, Reddit, Twitch) — watch for false positives / layout jumps.

## Known issues
- Built-in downloads fail on some hosts (w3.org 403, speed tests); ABDM handles them.
- adblock-rust scriptlets (+js) need uBO resources.json (not loaded); YouTube relies on the built-in defuser,
  which can break when YouTube changes its player response.
- Split view: toolbar/omnibox always act on the left tab; full screen in the right pane isn't special-cased.
- Passwords inside iframes aren't captured. PWA windows share one Wayland app_id (group under Nacre).
- Theme is global (follows the active window's tab).
- Seen while taking screenshots (check): a settings page opened right after switching light→dark had dim,
  low-contrast titles (widgets built mid theme transition keep stale colours?). Launching in dark was fine.
- Seen while taking screenshots (check): the weather card on the startup new tab stayed "Loading…" for 12 s+,
  but loaded right after a reload. Maybe the first /api/weather request fires before something is ready.

## Possible next work (ideas, not requested yet)
1. Load uBO resources.json (scriptlets) so +js rules work; cache it like the lists.
2. Split view: focus-follows-pane for toolbar/omnibox.
3. Tab groups (colored, collapsible) and/or vertical tabs; named session save/restore.
4. Per-window theming; per-PWA app_id (separate process) so apps get their own taskbar entries.
5. Autofill inside iframes (frame-aware injection).
6. History full-text search, better omnibox ranking, tab search.
7. Picture-in-picture for videos; page translation.
8. Zapper: animate ads present at first load once they scroll into view.
9. Packaging: PKGBUILD / AppImage; `git init` + a headless smoke-test script around the NACRE_SCRIPT harness.
10. Perf pass for the slow connection: lighter list updates, smaller NTP payload, prefetch off by default.
