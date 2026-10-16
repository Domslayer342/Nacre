"""Password / address / card autofill: an isolated-world script talks to the vault over QWebChannel.

Page scripts can't see the ApplicationWorld, so websites can't call the bridge. The bridge always uses the
tab's real URL (never one the page claims), so credentials only ever fill on their own site.
"""
import json
import time

from PyQt6.QtCore import QFile, QObject, Qt, QTimer, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEngineScript

from .config import cfg
from .theme import theme
from .vault import generate, get_vault, site_of
from .widgets import Button, Flyout, Label, TextField, hbox

ADDRESS_FIELDS = ["name", "organization", "street-address", "address-level2", "address-level1", "postal-code",
                  "country-name", "email", "tel"]


def address_tokens(a):
    """Stored address → autocomplete-token values (including derived ones)."""
    first, _, last = (a.get("name") or "").partition(" ")
    lines = (a.get("street-address") or "").split("\n")
    return {**{k: a.get(k, "") for k in ADDRESS_FIELDS}, "given-name": first, "family-name": last,
            "address-line1": lines[0], "address-line2": "\n".join(lines[1:]), "country": a.get("country-name", "")}


def card_tokens(c):
    m, y = c.get("exp_month", ""), c.get("exp_year", "")
    return {"cc-name": c.get("name", ""), "cc-number": c.get("number", ""), "cc-exp-month": m, "cc-exp-year": y,
            "cc-exp": f"{m}/{y[-2:]}" if m and y else ""}


class Bridge(QObject):
    """One per tab. Every slot re-checks the tab's actual URL."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.v = get_vault()
        self.seen = set()

    def url(self):
        return self.tab.page.url().toString()

    def usable(self):
        u = self.tab.page.url()                   # secure contexts only: https, or localhost (like Chrome)
        secure = u.scheme() == "https" or (u.scheme() == "http" and u.host() in ("localhost", "127.0.0.1", "[::1]"))
        return cfg.get("vault.enabled") and not self.tab.win.incognito and secure

    @pyqtSlot(result=str)
    def theme(self):
        return json.dumps(theme.css_vars())

    @pyqtSlot(result=str)
    def accounts(self):
        if not self.usable():
            return "[]"
        if self.v.locked():
            QTimer.singleShot(0, self.tab.win.vault_locked_hint)
            return "[]"
        return json.dumps([{"id": e["id"], "username": e["username"] or "(no username)"}
                           for e in sorted(self.v.logins_for(self.url()), key=lambda e: -e.get("used", 0))])

    @pyqtSlot(str, result=str)
    def credential(self, ident):
        if not self.usable() or self.v.locked():
            return "{}"
        e = next((x for x in self.v.logins_for(self.url()) if x["id"] == ident), None)
        if not e:
            return "{}"
        e["used"] = time.time()
        self.v.save()
        return json.dumps({"username": e["username"], "password": e["password"]})

    @pyqtSlot(result=bool)
    def autofill_on_load(self):
        return bool(cfg.get("vault.autofill"))

    @pyqtSlot(result=str)
    def generated(self):
        return generate(cfg.get("vault.gen_length"), symbols=cfg.get("vault.gen_symbols"))

    @pyqtSlot(str, str)
    def captured(self, username, password):
        if not self.usable() or not password or self.v.locked() or self.v.never_for(self.url()):
            return
        key = (username, password)
        if key in self.seen:
            return
        self.seen.add(key)
        e = self.v.find(self.url(), username)
        if e and e["password"] == password:
            return
        QTimer.singleShot(0, lambda: SavePrompt(self.tab.win, self.url(), username, password, bool(e)).show_at())

    @pyqtSlot(str, result=str)
    def profiles(self, kind):
        if not self.usable() or self.v.locked() or not cfg.get("vault.autofill_forms"):
            return "[]"
        if kind == "card":
            return json.dumps([{"label": f"{c.get('nickname') or 'Card'} •••• {c.get('number', '')[-4:]}",
                                "values": card_tokens(c)} for c in self.v.data["cards"]])
        return json.dumps([{"label": f"{a.get('name', '')} · {a.get('street-address', '').split(chr(10))[0]}",
                            "values": address_tokens(a)} for a in self.v.data["addresses"]])


class SavePrompt(Flyout):
    def __init__(self, win, url, username, password, update):
        super().__init__(win, width=380)
        self.win, self.url, self.v = win, url, get_vault()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.lay.addWidget(Label(("Update" if update else "Save") + f" password for {site_of(url)}?",
                                 "onSurface", 17, 500))
        self.lay.addSpacing(8)
        self.user = TextField(username, "Username", "person")
        self.pw = TextField(password, "Password", "key")
        self.pw.edit.setEchoMode(self.pw.edit.EchoMode.Password)
        eye = Button("visibility", size=32, icon_px=18, checkable=True, tip="Show password")
        eye.toggled.connect(lambda on: self.pw.edit.setEchoMode(
            self.pw.edit.EchoMode.Normal if on else self.pw.edit.EchoMode.Password))
        self.pw.layout().addWidget(eye)
        for w in (self.user, self.pw):
            self.lay.addWidget(w)
        never = Button(None, "Never for this site", kind="text", size=36)
        never.clicked.connect(lambda: (self.v.add_never(url), self.dismiss()))
        later = Button(None, "Not now", kind="text", size=36)
        later.clicked.connect(self.dismiss)
        save = Button(None, "Update" if update else "Save", kind="filled", size=36)
        save.clicked.connect(self._save)
        self.lay.addWidget(hbox(never, None, later, save, margins=(0, 10, 0, 0)))

    def _save(self):
        self.v.upsert_login(self.url, self.user.edit.text().strip(), self.pw.edit.text())
        self.win.snack.show_msg("Password saved", "Manage", lambda: self.win.new_tab("nacre://passwords"))
        self.dismiss()

    def show_at(self):
        w = self.win
        anchor = w.items["passwords"] if w.items["passwords"].isVisible() else w.omni.site
        self.popup(anchor, align="right" if anchor is w.items["passwords"] else "left")


_CHANNEL_JS = None


def channel_js():
    global _CHANNEL_JS
    if _CHANNEL_JS is None:
        f = QFile(":/qtwebchannel/qwebchannel.js")
        f.open(QFile.OpenModeFlag.ReadOnly)
        _CHANNEL_JS = bytes(f.readAll()).decode()
    return _CHANNEL_JS


def install(profile):
    for old in profile.scripts().find("nacre-autofill"):
        profile.scripts().remove(old)
    s = QWebEngineScript()
    s.setName("nacre-autofill")
    s.setSourceCode(channel_js() + "\n" + AUTOFILL_JS)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    s.setRunsOnSubFrames(False)
    profile.scripts().insert(s)


def attach(tab):
    ch = QWebChannel(tab.page)
    tab.bridge = Bridge(tab)
    ch.registerObject("nacre", tab.bridge)
    tab.page.setWebChannel(ch, QWebEngineScript.ScriptWorldId.ApplicationWorld)


AUTOFILL_JS = r"""
(() => { if (window.__nacreFill || !window.isSecureContext || location.protocol === 'nacre:') return; window.__nacreFill = 1;
new QWebChannel(qt.webChannelTransport, ch => { const B = ch.objects.nacre; let V = {};
  B.theme(t => V = JSON.parse(t));
  const vis = e => e && e.offsetParent !== null && !e.disabled && !e.readOnly;
  const setVal = (el, v) => { if (!el || v == null) return;
    const proto = el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
    el.style.transition = 'background-color .6s'; const bg = el.style.backgroundColor;
    el.style.backgroundColor = (V.pc || '#d0bcff') + '55'; setTimeout(() => el.style.backgroundColor = bg, 900); };
  const pwFields = () => [...document.querySelectorAll('input[type=password]')].filter(vis);
  const userFor = pw => { const scope = pw.form || document; const ins = [...scope.querySelectorAll(
      'input[type=email],input[type=text],input[type=tel],input:not([type])')].filter(vis);
    const before = ins.filter(i => i.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING);
    const pref = before.filter(i => /user|email|login|account|phone|name/i.test(
      (i.autocomplete || '') + i.name + i.id + (i.placeholder || '')));
    return (pref.length ? pref : before).pop() || null; };
  const isNew = pw => pw.autocomplete === 'new-password' || (pw.form && pw.form.querySelectorAll('input[type=password]').length > 1)
    || /sign.?up|register|join|create|new.?account/i.test(location.pathname + ' ' + (pw.form ? pw.form.id + pw.form.className + (pw.form.action || '') : ''));

  // ── suggestion popup (shadow DOM, themed) ──
  let pop = null, popField = null;
  const closePop = () => { if (pop) { pop.remove(); pop = popField = null; } };
  const showPop = (field, items) => { closePop(); if (!items.length || document.activeElement !== field) return;
    popField = field;
    pop = document.createElement('div'); const r = field.getBoundingClientRect();
    pop.style.cssText = `position:fixed;left:${r.left}px;top:${r.bottom + 4}px;z-index:2147483647;min-width:${Math.max(240, r.width)}px`;
    const sh = pop.attachShadow({mode: 'closed'});
    sh.innerHTML = `<style>.p{background:${V.high};color:${V.fg};border-radius:16px;padding:6px;font:14px Rubik,system-ui,sans-serif;
      box-shadow:0 6px 24px #0005;animation:a .22s cubic-bezier(.05,.7,.1,1)}@keyframes a{from{opacity:0;transform:translateY(-6px) scaleY(.9)}}
      .i{display:flex;gap:12px;align-items:center;padding:10px 12px;border-radius:12px;cursor:pointer}
      .i:hover{background:${V.highest}}.s{font-size:12px;color:${V.var}}.ic{font-family:'Material Symbols Rounded';font-size:20px;color:${V.p}}
      .h{font-size:11px;color:${V.var};padding:4px 12px;letter-spacing:.04em;text-transform:uppercase}</style>
      <div class="p"><div class="h">Nacre</div></div>`;
    const box = sh.querySelector('.p');
    for (const it of items) { const d = document.createElement('div'); d.className = 'i';
      d.innerHTML = `<span class="ic">${it.icon}</span><div><div class="t"></div><div class="s"></div></div>`;
      d.querySelector('.t').textContent = it.title; d.querySelector('.s').textContent = it.sub || '';
      d.addEventListener('mousedown', e => { e.preventDefault(); e.stopPropagation(); closePop(); it.go(); });
      box.append(d); }
    document.documentElement.append(pop); };
  addEventListener('scroll', closePop, true); addEventListener('resize', closePop);
  document.addEventListener('focusout', () => setTimeout(() => { if (document.activeElement !== popField) closePop(); }, 150), true);

  const fillLogin = (pw, id) => B.credential(id, c => { c = JSON.parse(c); if (!c.password) return;
    const u = userFor(pw); if (u && c.username && !u.value) setVal(u, c.username); setVal(pw, c.password); });
  let accounts = null;
  const getAccounts = cb => accounts ? cb(accounts) : B.accounts(a => cb(accounts = JSON.parse(a)));

  const onFocus = e => { const el = e.target; if (!(el instanceof HTMLInputElement)) return;
    const pws = pwFields(); const ac = (el.autocomplete || '').toLowerCase();
    if (el.type === 'password' && isNew(el)) {
      B.generated(g => showPop(el, [{icon: 'key', title: 'Use a strong password', sub: g, go: () => {
        setVal(el, g); (el.form ? [...el.form.querySelectorAll('input[type=password]')] : [el]).forEach(f => setVal(f, g));
        B.captured((userFor(el) || {}).value || '', g); }}]));
      return; }
    const pw = el.type === 'password' ? el : pws.find(p => userFor(p) === el);
    if (pw) return getAccounts(acc => showPop(el, acc.map(a => ({icon: 'key', title: a.username,
      sub: 'Saved password', go: () => fillLogin(pw, a.id)}))));
    const card = /^cc-/.test(ac) || /card.?num|cc.?num|cardnumber/i.test(el.name + el.id);
    const addr = /name|email|tel|street|address|postal|country|organization/.test(ac);
    if (card || addr) B.profiles(card ? 'card' : 'address', p => { p = JSON.parse(p);
      showPop(el, p.map(x => ({icon: card ? 'credit_card' : 'location_on', title: x.label,
        sub: card ? 'Saved card' : 'Saved address', go: () => {
          const scope = el.form || document;
          for (const f of scope.querySelectorAll('input,select,textarea')) {
            const t = (f.autocomplete || '').toLowerCase().split(/\s+/).pop();
            if (t && x.values[t]) setVal(f, x.values[t]); }
          if (card && !el.value) setVal(el, x.values['cc-number']); }}))); }); };
  document.addEventListener('focusin', onFocus, true);

  // one saved account → fill on load (Chrome-style)
  const autofill = () => B.autofill_on_load(on => { if (!on) return; const pws = pwFields();
    if (pws.length !== 1 || isNew(pws[0]) || pws[0].value) return;
    getAccounts(acc => { if (acc.length === 1) fillLogin(pws[0], acc[0].id); }); });
  autofill(); let t = 0; new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => { if (!pwFields().some(p => p.value)) autofill(); }, 600); })
    .observe(document.documentElement, {childList: true, subtree: true});

  // capture on submit / submit-click / Enter
  const capture = scope => { const pw = [...(scope || document).querySelectorAll('input[type=password]')].find(p => p.value);
    if (!pw) return; const u = userFor(pw); B.captured(u ? u.value : '', pw.value); };
  document.addEventListener('submit', e => capture(e.target), true);
  document.addEventListener('click', e => { const b = e.target.closest('button,input[type=submit],[role=button]');
    if (b) capture(b.form || b.closest('form') || document); }, true);
  document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.type === 'password') capture(e.target.form || document); }, true);
}); })();
"""
