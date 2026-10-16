"""Password manager UI: toolbar flyout + nacre://passwords (passwords, addresses, cards, generator, security)."""
import os
import sys
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from .auth import gate
from .config import cfg
from .media import WavyProgress
from .pages import LinkRow, NativePage
from .settings_page import SetRow, group, seg, sw
from .vault import VaultError, generate, get_vault, passphrase, site_of, strength
from .widgets import Button, Flyout, Label, Row, Segmented, Switch, TextField, guard, hbox

DOTS = "••••••••••"


def copy_secret(win, text, what="Password"):
    cb = QGuiApplication.clipboard()
    cb.setText(text)
    secs = cfg.get("vault.clipboard_clear")
    if secs:
        QTimer.singleShot(secs * 1000, lambda: cb.text() == text and cb.clear())
        win.snack.show_msg(f"{what} copied · clears in {secs}s", ms=2500)
    else:
        win.snack.show_msg(f"{what} copied", ms=2000)


def luhn_ok(num):
    d = [int(c) for c in num if c.isdigit()]
    if not 12 <= len(d) <= 19:
        return False
    return (sum(d[-1::-2]) + sum(sum(divmod(2 * x, 10)) for x in d[-2::-2])) % 10 == 0


def brand(num):
    n = "".join(c for c in num if c.isdigit())
    return ("Visa" if n.startswith("4") else "Mastercard" if n[:2] in {"51", "52", "53", "54", "55"} or
            "2221" <= n[:4] <= "2720" else "Amex" if n[:2] in ("34", "37") else "Discover" if n.startswith("6")
            else "Card")


def secret_field(text="", placeholder="Password"):
    f = TextField(text, placeholder, "key")
    f.edit.setEchoMode(f.edit.EchoMode.Password)
    eye = Button("visibility", size=32, icon_px=18, checkable=True, tip="Show")
    eye.toggled.connect(lambda on: (f.edit.setEchoMode(f.edit.EchoMode.Normal if on else f.edit.EchoMode.Password),
                                    eye.set_icon("visibility_off" if on else "visibility")))
    f.layout().addWidget(eye)
    return f


class FormFlyout(Flyout):
    """Small edit form: fields = [(key, label, icon, secret)]."""

    def __init__(self, win, title, fields, values, on_save, extra=None):
        super().__init__(win, width=420)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.lay.addWidget(Label(title, "onSurface", 18, 500))
        self.lay.addSpacing(6)
        self.inputs = {}
        for key, label, icon, secret in fields:
            f = secret_field(values.get(key, ""), label) if secret else TextField(values.get(key, ""), label, icon)
            self.inputs[key] = f
            self.lay.addWidget(f)
            if secret and extra == "generate":
                gen = Button("autorenew", size=32, icon_px=18, tip="Generate a strong password")
                gen.clicked.connect(lambda _, f=f: f.edit.setText(generate(cfg.get("vault.gen_length"),
                                                                          symbols=cfg.get("vault.gen_symbols"))))
                f.layout().addWidget(gen)
        cancel = Button(None, "Cancel", kind="text", size=36)
        cancel.clicked.connect(self.dismiss)
        save = Button(None, "Save", kind="filled", size=36)
        save.clicked.connect(lambda: on_save({k: f.edit.text().strip() if k != "password" else f.edit.text()
                                              for k, f in self.inputs.items()}) is not False and self.dismiss())
        self.lay.addWidget(hbox(None, cancel, save, margins=(0, 10, 0, 0)))

    def open_center(self):
        win = self.parentWidget()
        self.popup(win.omni, align="left")
        self.move(win.mapToGlobal(win.rect().center()).x() - self.width() // 2, self.y())


# ── toolbar flyout ───────────────────────────────────────────────────────────
class PasswordsFlyout(Flyout):
    def __init__(self, win):
        super().__init__(win, width=380)
        self.win, self.v = win, get_vault()
        self.content = QWidget()
        self.bl = QVBoxLayout(self.content)
        self.bl.setContentsMargins(0, 0, 0, 0)
        self.bl.setSpacing(2)
        self.lay.addWidget(self.content)

    def open_for(self, anchor):
        self.rebuild()
        self.popup(anchor)

    def rebuild(self):
        while self.bl.count():
            w = self.bl.takeAt(0).widget()
            w and w.deleteLater()
        t = self.win.tab()
        url = t.url().toString() if t else ""
        manage = Button("open_in_new", size=36, tip="Manage passwords")
        manage.clicked.connect(lambda: (self.dismiss(), self.win.new_tab("nacre://passwords")))
        self.bl.addWidget(hbox(Label("Passwords", "onSurface", 18, 500), None, manage, margins=(8, 0, 0, 6)))
        if self.v.locked():
            f = secret_field(placeholder="Master password")
            err = Label("", "error", 12)
            unlock = Button(None, "Unlock", kind="filled", size=36)

            def go():
                try:
                    self.v.unlock(f.edit.text())
                    self.rebuild()
                except VaultError as e:
                    err.setText(str(e))
            unlock.clicked.connect(go)
            f.edit.returnPressed.connect(go)
            self.bl.addWidget(f)
            self.bl.addWidget(err)
            self.bl.addWidget(hbox(None, unlock, margins=(0, 6, 0, 0)))
            QTimer.singleShot(50, f.edit.setFocus)
            self.adjustSize()
            return
        site = site_of(url) if t and not t.is_nacre() else ""
        logins = self.v.logins_for(url) if site else []
        if site:
            self.bl.addWidget(Label(f"Saved for {site}", "primary", 13, 500))
        for e in logins:
            r = Row("key", e["username"] or "(no username)", DOTS)
            r.setFixedHeight(56)
            cu = Button("person", size=32, icon_px=18, tip="Copy username")
            cu.clicked.connect(lambda _, e=e: copy_secret(self.win, e["username"], "Username"))
            cp = Button("content_copy", size=32, icon_px=18, tip="Copy password")
            cp.clicked.connect(lambda _, e=e: gate.require(self.win, "copy this password",
                                                            lambda: copy_secret(self.win, e["password"])))
            self.bl.addWidget(hbox(r, cu, cp, spacing=0))
        if site and not logins:
            self.bl.addWidget(Label("Nothing saved for this site yet — sign in and Nacre will offer to save it.",
                                    "onSurfaceVariant", 13, wrap=True))
        pw = generate(cfg.get("vault.gen_length"), symbols=cfg.get("vault.gen_symbols"))
        gl = Label(pw, "onSurface", 14)
        gl.setFont(QFont("monospace", 11))
        gl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        regen = Button("autorenew", size=32, icon_px=18, tip="Another one")
        copy = Button("content_copy", size=32, icon_px=18, tip="Copy")
        regen.clicked.connect(lambda: gl.setText(generate(cfg.get("vault.gen_length"),
                                                          symbols=cfg.get("vault.gen_symbols"))))
        copy.clicked.connect(lambda: copy_secret(self.win, gl.text()))
        self.bl.addSpacing(8)
        self.bl.addWidget(Label("Strong password", "primary", 13, 500))
        self.bl.addWidget(hbox(gl, None, regen, copy, spacing=0, margins=(4, 0, 0, 0)))
        self.adjustSize()


# ── nacre://passwords ────────────────────────────────────────────────────────
SECTIONS = [("passwords", "Passwords", "key"), ("addresses", "Addresses", "location_on"),
            ("payments", "Payment methods", "credit_card"), ("generator", "Generator", "autorenew"),
            ("security", "Security", "lock")]
ADDRESS_FORM = [("name", "Full name", "person", False), ("organization", "Company", "business", False),
                ("street-address", "Street address", "home", False), ("address-level2", "City", "location_city", False),
                ("address-level1", "State / region", "map", False), ("postal-code", "Postal code", "markunread_mailbox", False),
                ("country-name", "Country", "public", False), ("email", "Email", "mail", False),
                ("tel", "Phone", "call", False)]
CARD_FORM = [("nickname", "Nickname (optional)", "label", False), ("name", "Name on card", "person", False),
             ("number", "Card number", "credit_card", True), ("exp_month", "Expiry month (MM)", "event", False),
             ("exp_year", "Expiry year (YYYY)", "event", False)]


class PasswordsPage(NativePage):
    TITLE, PLACEHOLDER = "Passwords & autofill", "Search"

    def __init__(self, win, section="passwords"):
        super().__init__(win)
        self.vault = get_vault()
        self.section = section
        self.revealed = set()
        self.tabs = Segmented([(k, label, icon) for k, label, icon in SECTIONS], section)
        self.tabs.changed.connect(self._switch)
        self.col_lay.insertWidget(1, self.tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.vault.changed.connect(guard(self, lambda: self.isVisible() and self.rebuild()))
        self.gen = {"mode": "random", "len": cfg.get("vault.gen_length"), "words": 5, "upper": True, "digits": True,
                    "symbols": cfg.get("vault.gen_symbols"), "ambiguous": True}

    def _switch(self, k):
        self.tabs.set_value(k)
        self.section = k
        self.search.edit.clear()
        self.rebuild()

    def rebuild(self):
        self.clear()
        self.search.setVisible(self.section in ("passwords", "addresses", "payments"))
        if self.vault.locked() and self.section != "generator":
            return self._unlock_card()
        getattr(self, "_" + self.section)()

    # locked
    def _unlock_card(self):
        f = secret_field(placeholder="Master password")
        f.setFixedWidth(320)
        err = Label("", "error", 13)
        btn = Button("lock_open", "Unlock", kind="filled", size=40)

        def go():
            try:
                self.vault.unlock(f.edit.text())
            except VaultError as e:
                err.setText(str(e))
        btn.clicked.connect(go)
        f.edit.returnPressed.connect(go)
        self.v.addWidget(group(SetRow("lock", "Your passwords are locked", "Enter your master password to unlock them",
                                      below=hbox(f, btn, None))))
        self.v.addWidget(err)
        QTimer.singleShot(50, f.edit.setFocus)

    # passwords
    def _passwords(self):
        q = self.q()
        add = Button("add", "Add password", kind="tonal", size=40)
        add.clicked.connect(lambda: self._edit_login(None))
        health = self.vault.weak_or_reused()
        weak = sum(1 for x in health.values() if x == "weak")
        reused = sum(1 for x in health.values() if x == "reused")
        summary = f"{len(self.vault.data['logins'])} saved" + (f" · {weak} weak" if weak else "") + \
                  (f" · {reused} reused" if reused else "")
        self.v.addWidget(hbox(Label(summary, "onSurfaceVariant", 14), None, add))
        items = sorted((e for e in self.vault.data["logins"] if not q or q in e["site"].lower() or q in e["username"].lower()),
                       key=lambda e: (e["site"], e["username"]))
        by_site = {}
        for e in items:
            by_site.setdefault(e["site"], []).append(e)
        rows = []
        for site, es in by_site.items():
            for e in es:
                shown = e["id"] in self.revealed
                tag = {"weak": " · Weak password", "reused": " · Reused password"}.get(health.get(e["id"]), "")
                sub = f"{e['username'] or '(no username)'} · {e['password'] if shown else DOTS}{tag}"
                eye = Button("visibility_off" if shown else "visibility", size=32, icon_px=18, tip="Show password")
                eye.clicked.connect(lambda _, e=e: self._toggle_reveal(e))
                cu = Button("person", size=32, icon_px=18, tip="Copy username")
                cu.clicked.connect(lambda _, e=e: copy_secret(self.win, e["username"], "Username"))
                cp = Button("content_copy", size=32, icon_px=18, tip="Copy password")
                cp.clicked.connect(lambda _, e=e: gate.require(self.win, "copy this password",
                                                                lambda: copy_secret(self.win, e["password"])))
                ed = Button("edit", size=32, icon_px=18, tip="Edit")
                ed.clicked.connect(lambda _, e=e: gate.require(self.win, "edit this password",
                                                               lambda: self._edit_login(e)))
                rm = Button("delete", size=32, icon_px=18, tip="Delete")
                rm.clicked.connect(lambda _, e=e: self._delete("logins", e, e["site"]))
                rows.append(LinkRow(self.win, "key", site, sub, e["origin"], hbox(eye, cu, cp, ed, rm, spacing=0)))
        if rows:
            self.v.addWidget(group(*rows))
        else:
            self.empty("No matches" if q else "Passwords you save while signing in will show up here.\n"
                                              "You can also import them from Chrome or Firefox under Security.")

    def _toggle_reveal(self, e):
        if e["id"] in self.revealed:                    # hiding never needs a password
            self.revealed.discard(e["id"])
            return self.rebuild()

        def show():
            self.revealed.add(e["id"])
            self.rebuild()
            # hide again when the grace period runs out
            QTimer.singleShot(max(1, cfg.get("vault.reauth_min")) * 60000 + 500,
                              guard(self, lambda: not gate.fresh() and (self.revealed.clear(), self.rebuild())))
        gate.require(self.win, "show this password", show)

    def _edit_login(self, e):
        vals = {"url": e["origin"] if e else "https://", "username": e["username"] if e else "",
                "password": e["password"] if e else "", "note": e.get("note", "") if e else ""}

        def save(d):
            if not d["url"] or "://" not in d["url"] or not d["password"]:
                self.win.snack.show_msg("A site address and a password are required")
                return False
            if e:
                self.vault.delete("logins", e)
            self.vault.upsert_login(d["url"], d["username"], d["password"], d.get("note", ""))
        FormFlyout(self.win, "Edit password" if e else "Add password",
                   [("url", "Site (https://…)", "public", False), ("username", "Username", "person", False),
                    ("password", "Password", "key", True), ("note", "Note", "notes", False)],
                   vals, save, extra="generate").open_center()

    def _delete(self, kind, item, label):
        self.vault.delete(kind, item)
        self.win.snack.show_msg(f"Deleted {label}", "Undo", lambda: (self.vault.data[kind].append(item), self.vault.save()))

    # addresses / cards
    def _list(self, kind, form, icon, title_of, sub_of, add_label, empty):
        q = self.q()
        add = Button("add", add_label, kind="tonal", size=40)
        add.clicked.connect(lambda: self._edit(kind, form, None))
        note = "Security codes (CVC) are never stored." if kind == "cards" else "Used to fill in forms with one click."
        if kind == "cards" and self.vault.mode() == "device":
            note += " Tip: add a master password under Security for stronger protection."
        self.v.addWidget(hbox(Label(note, "onSurfaceVariant", 14), None, add))
        rows = []
        for it in self.vault.data[kind]:
            if q and q not in " ".join(str(v) for v in it.values()).lower():
                continue
            ed = Button("edit", size=32, icon_px=18, tip="Edit")
            ed.clicked.connect(lambda _, it=it: gate.require(self.win, "edit this card", lambda: self._edit(kind, form, it))
                               if kind == "cards" else self._edit(kind, form, it))
            rm = Button("delete", size=32, icon_px=18, tip="Delete")
            rm.clicked.connect(lambda _, it=it: self._delete(kind, it, title_of(it)))
            rows.append(SetRow(icon, title_of(it), sub_of(it), hbox(ed, rm, spacing=0)))
        if rows:
            self.v.addWidget(group(*rows))
        else:
            self.empty("No matches" if q else empty)

    def _edit(self, kind, form, item):
        def save(d):
            if kind == "cards":
                d["number"] = "".join(c for c in d["number"] if c.isdigit())
                if not luhn_ok(d["number"]):
                    self.win.snack.show_msg("That card number doesn't look right")
                    return False
                d["exp_month"] = d["exp_month"].zfill(2)[:2]
            if item:
                item.update(d)
            else:
                self.vault.data[kind].append({"id": os.urandom(8).hex(), **d})
            self.vault.save()
        FormFlyout(self.win, ("Edit " if item else "Add ") + ("card" if kind == "cards" else "address"),
                   form, item or {}, save).open_center()

    def _addresses(self):
        self._list("addresses", ADDRESS_FORM, "location_on", lambda a: a.get("name") or "Address",
                   lambda a: ", ".join(x for x in (a.get("street-address"), a.get("address-level2"),
                                                   a.get("postal-code")) if x) or a.get("email", ""),
                   "Add address", "Save an address to fill in shipping and contact forms.")

    def _payments(self):
        self._list("cards", CARD_FORM, "credit_card",
                   lambda c: c.get("nickname") or brand(c.get("number", "")),
                   lambda c: f"{brand(c.get('number', ''))} •••• {c.get('number', '')[-4:]} · "
                             f"expires {c.get('exp_month', '??')}/{c.get('exp_year', '????')[-2:]}",
                   "Add card", "Save a card to fill in checkout forms. Its number is encrypted in your vault.")

    # generator
    def _generator(self):
        g = self.gen
        pw = passphrase(g["words"]) if g["mode"] == "phrase" else generate(
            g["len"], g["upper"], g["digits"], g["symbols"], g["ambiguous"])
        out = Label(pw, "onSurface", 22, 500)
        out.setFont(QFont("monospace", 16))
        out.setWordWrap(True)
        out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        score, label = strength(pw)
        meter = WavyProgress(interactive=False, height=22, stroke=4, thumb=False)
        meter.frac, meter.amp = (score + 1) / 5, 1.0           # strength as a (still) wave
        regen = Button("autorenew", "New", kind="tonal", size=40)
        regen.clicked.connect(self.rebuild)
        copy = Button("content_copy", "Copy", kind="filled", size=40)
        copy.clicked.connect(lambda: copy_secret(self.win, pw))
        self.v.addWidget(group(SetRow(None, label, f"{len(pw)} characters", hbox(regen, copy),
                                           below=self._stack(out, meter))))
        mode = Segmented([("random", "Random"), ("phrase", "Passphrase")], g["mode"])
        mode.changed.connect(lambda v: (g.update(mode=v), self.rebuild()))
        rows = [SetRow("tune", "Type", None, mode)]
        if g["mode"] == "phrase":
            wn = Segmented([(n, str(n)) for n in (4, 5, 6, 7, 8)], g["words"])
            wn.changed.connect(lambda v: (g.update(words=v), self.rebuild()))
            rows.append(SetRow("format_list_numbered", "Words", None, wn))
        else:
            ln = Segmented([(n, str(n)) for n in (12, 16, 20, 24, 32, 48)], g["len"])
            ln.changed.connect(lambda v: (g.update(len=v), cfg.set("vault.gen_length", v), self.rebuild()))
            rows.append(SetRow("straighten", "Length", None, ln))
            for key, title in (("upper", "Uppercase letters"), ("digits", "Numbers"), ("symbols", "Symbols"),
                               ("ambiguous", "Avoid look-alikes (I l 1 O 0)")):
                s = Switch(g[key])
                s.toggled.connect(lambda on, key=key: (g.update({key: on}), key == "symbols" and cfg.set(
                    "vault.gen_symbols", on), self.rebuild()))
                rows.append(SetRow(None, title, None, s))
        self.v.addWidget(group(*rows))

    @staticmethod
    def _stack(*ws):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)
        for w in ws:
            lay.addWidget(w)
        return box

    # security
    def _security(self):
        v = self.vault
        master = v.mode() == "master"
        cipher = {"aes-256-gcm": "AES-256-GCM", "hmac-sha256-ctr": "HMAC-SHA256 stream + tag (stdlib)"}.get(
            v.cipher(), v.cipher())
        status = ("Protected by your master password — locks when idle" if master else
                  "Protected by a key stored on this computer — anyone who can read your files can open it")
        p1, p2 = secret_field(placeholder="New master password"), secret_field(placeholder="Repeat it")
        err = Label("", "error", 12)
        setb = Button("password", "Change" if master else "Set", kind="filled", size=40)

        def set_master():
            a, b = p1.edit.text(), p2.edit.text()
            if a != b:
                return err.setText("Passwords don't match")
            if strength(a)[0] < 2:
                return err.setText("Pick something stronger — a passphrase from the Generator works well")
            v.set_master(a)
            self.win.snack.show_msg("Master password set")
        setb.clicked.connect(set_master)
        rows = [SetRow("verified_user", "Vault encryption", f"{cipher} · {status}"),
                SetRow("password", "Change master password" if master else "Add a master password",
                       "Your passwords are re-locked with it; there is no way to recover it if forgotten",
                       below=self._stack(p1, p2, err, hbox(None, setb)))]
        if master:
            lock = Button("lock", "Lock now", kind="tonal", size=40)
            lock.clicked.connect(lambda: (v.lock(), self.win.snack.show_msg("Passwords locked")))
            rm = Button(None, "Remove", kind="danger", size=40)
            rm.clicked.connect(lambda: self.win.snack.show_msg("Remove the master password?", "Remove",
                                                               lambda: v.remove_master(), 7000))
            rows += [SetRow("lock", "Lock", None, lock),
                     SetRow("timer", "Lock after", None, seg("vault.autolock_min", [(5, "5 min"), (15, "15 min"),
                                                                                    (60, "1 h"), (0, "Never")])),
                     SetRow("lock_open", "Remove master password", "Go back to the device key", rm)]
        self.v.addWidget(group(*rows))
        self.v.addWidget(group(
            SetRow("fingerprint", "Ask for your login password", "Before showing, copying, editing or exporting "
                   "passwords — checked by " + ("Windows" if sys.platform == "win32" else "Linux (PAM)")
                   + ", Nacre gets no extra rights", sw("vault.reauth")),
            SetRow("timer", "Then don't ask again for", None,
                   seg("vault.reauth_min", [(1, "1 min"), (5, "5 min"), (15, "15 min"), (0, "Ask every time")])),
            SetRow("key", "Offer to save passwords", None, sw("vault.enabled")),
            SetRow("bolt", "Sign in automatically", "Fill the form when a site has exactly one saved login",
                   sw("vault.autofill")),
            SetRow("location_on", "Suggest addresses and cards", None, sw("vault.autofill_forms")),
            SetRow("content_paste_off", "Clear copied passwords", None,
                   seg("vault.clipboard_clear", [(15, "15 s"), (30, "30 s"), (60, "1 min"), (0, "Never")]))))
        imp = Button("upload", "Import…", kind="tonal", size=40)
        imp.clicked.connect(self._import)
        exp = Button("download", "Export…", kind="text", size=40)
        exp.clicked.connect(lambda: self.win.snack.show_msg("Export saves passwords as plain text", "Export",
                                                            lambda: gate.require(self.win, "export your passwords",
                                                                                 self._export), 7000))
        never = v.data["never"]
        nv = Button("restart_alt", "Clear", kind="text", size=40)
        nv.clicked.connect(lambda: (never.clear(), v.save()))
        self.v.addWidget(group(
            SetRow("upload", "Import passwords", "CSV from Chrome, Firefox, Bitwarden or another browser", imp),
            SetRow("download", "Export passwords", "Unencrypted CSV — delete it after use", exp),
            SetRow("block", "Never saved for", ", ".join(never) or "None", nv if never else None)))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import passwords", os.path.expanduser("~"), "CSV (*.csv)")
        if path:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    n = self.vault.import_csv(f.read())
                self.win.snack.show_msg(f"Imported {n} passwords")
            except (OSError, ValueError, VaultError) as e:
                self.win.snack.show_msg(f"Import failed: {e}")

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export passwords",
                                              os.path.expanduser(f"~/nacre-passwords-{time.strftime('%Y%m%d')}.csv"),
                                              "CSV (*.csv)")
        if path:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.vault.export_csv())
            self.win.snack.show_msg("Exported — remember to delete the file when you're done")
