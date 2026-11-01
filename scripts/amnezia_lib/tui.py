"""The dashboard: one screen, locations on the left, details on the right.

Modelled on omarchy-vpn: a title with the live state, two rounded panes, and
a key line at the bottom that turns into a short message after each action.
Network calls and tunnel changes run on a worker thread so the screen keeps
answering (and the spinner keeps turning) while Amnezia or polkit takes its
time. Colors are the terminal's own ANSI ones, so the theme follows Omarchy's.
"""

import curses
import json
import locale
import os
import queue
import subprocess
import shutil
import sys
import threading
import time
import unicodedata

from . import app, store, tunnel
from .premium import Premium, PremiumError, protocol_label, PROTOCOLS

TITLE = "omarchy-amnezia"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
LIVE_EVERY = 2.0
ACCOUNT_STALE = 3600
MESSAGE_SECONDS = 4.0
LABEL_W = 12
SPLIT_MIN_WIDTH = 76

KEYS_MAIN = [
    ("↑↓/jk", "move"), ("enter", "connect / disconnect"), ("p", "switch protocol"),
    ("space", "toggle the picked location"), ("/", "filter locations"), ("d", "disconnect"),
    ("r", "refresh from Amnezia"), ("R", "new config (fresh key) for this location"),
    ("a", "account and devices"), ("?", "this help"), ("q", "quit (the tunnel stays up)"),
]
KEYS_ACCOUNT = [
    ("↑↓/jk", "move"), ("x", "free the selected device's slot"), ("o", "open the renewal page"),
    ("k", "replace the Premium key"), ("F", "forget the key on this device"),
    ("r", "refresh"), ("esc / a", "back to locations"),
]


def version():
    try:
        manifest = os.path.join(os.path.dirname(__file__), "..", "..", "manifest.json")
        with open(manifest, encoding="utf-8") as handle:
            return "v" + str(json.load(handle).get("version") or "")
    except (OSError, ValueError):
        return ""


# ------------------------------------------------------------------ text


def char_width(ch):
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text):
    return sum(char_width(ch) for ch in text)


def truncate(text, width):
    if width <= 0:
        return ""
    if text_width(text) <= width:
        return text
    out, used = "", 0
    for ch in text:
        w = char_width(ch)
        if used + w > width - 1:
            break
        out += ch
        used += w
    return out + "…"


def human_age(ts):
    """5m ago, 3h ago, 12d ago — short enough for a list row."""
    if not ts:
        return ""
    seconds = max(0, int(time.time() - ts))
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return "%d%s ago" % (seconds // size, unit)
    return "just now"


OS_NAMES = {"ios": "iOS", "macos": "macOS", "osx": "macOS", "android": "Android", "windows": "Windows",
            "linux": "Linux", "arch": "Arch Linux", "ubuntu": "Ubuntu", "debian": "Debian", "fedora": "Fedora"}
SOURCES = {"gateway_account": "app", "native_config": "config file", "native": "config file"}


def device_name(os_version):
    text = str(os_version or "").strip()
    return OS_NAMES.get(text.lower(), text[:1].upper() + text[1:]) or "Device"


# ----------------------------------------------------------------- state


class Dashboard:
    def __init__(self, screen):
        self.screen = screen
        self.premium = Premium()
        self.account = self.premium.normalize_account(store.read_json(store.ACCOUNT, {}) or {})
        self.live = tunnel.status()
        self.live_at = time.monotonic()
        self.mode = "main" if self.premium.has_key else "key"
        self.previous_mode = "main"
        self.cursor = 0
        self.offset = 0
        self.dev_cursor = 0
        self.filter = ""
        self.filtering = False
        self.input = ""
        self.message = ""
        self.message_attr = 0
        self.message_until = 0.0
        self.confirm = None          # (question, callback)
        self.job = None              # label of the running job
        self.job_target = None       # (country, protocol) being connected
        self.results = queue.Queue()
        self.frame = 0
        self.colors = {}
        self.version = version()
        self._place_cursor_on_current()

    # ------------------------------------------------------------- colors

    def init_colors(self):
        curses.curs_set(0)
        attrs = {"normal": curses.A_NORMAL, "bold": curses.A_BOLD, "dim": curses.A_DIM,
                 "green": curses.A_BOLD, "red": curses.A_BOLD, "yellow": curses.A_BOLD,
                 "accent": curses.A_BOLD | curses.A_UNDERLINE, "border": curses.A_NORMAL}
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                background = -1
            except curses.error:
                background = curses.COLOR_BLACK
            dim_color = 8 if curses.COLORS >= 16 else curses.COLOR_WHITE
            for index, (name, color) in enumerate(
                    (("green", curses.COLOR_GREEN), ("red", curses.COLOR_RED), ("yellow", curses.COLOR_YELLOW),
                     ("accent", curses.COLOR_BLUE), ("dim", dim_color), ("border", curses.COLOR_WHITE)), start=1):
                curses.init_pair(index, color, background)
                attrs[name] = curses.color_pair(index)
            attrs["accent"] |= curses.A_BOLD
            if curses.COLORS < 16:
                attrs["dim"] |= curses.A_DIM
        self.colors = attrs

    def c(self, name):
        return self.colors.get(name, curses.A_NORMAL)

    # -------------------------------------------------------------- data

    def countries(self):
        rows = self.account["countries"]
        if self.filter:
            needle = self.filter.lower()
            rows = [r for r in rows if needle in r["name"].lower() or needle in r["code"]]
        return rows

    def _place_cursor_on_current(self):
        target = self.live["country"] if self.live["connected"] else self.premium.country
        for index, row in enumerate(self.countries()):
            if row["code"] == target:
                self.cursor = index
                return

    def current_row(self):
        rows = self.countries()
        if not rows:
            return None
        self.cursor = max(0, min(self.cursor, len(rows) - 1))
        return rows[self.cursor]

    def reload(self):
        self.premium = Premium()
        self.account = self.premium.normalize_account(store.read_json(store.ACCOUNT, {}) or {})
        self.reload_live()

    def reload_live(self):
        self.live = tunnel.status()
        self.live_at = time.monotonic()

    def flash(self, text, kind="normal"):
        self.message = str(text)
        self.message_attr = {"error": self.c("red"), "ok": self.c("green"), "warn": self.c("yellow")}.get(kind, self.c("normal"))
        self.message_until = time.monotonic() + MESSAGE_SECONDS + (2 if kind == "error" else 0)

    # --------------------------------------------------------------- jobs

    def start(self, label, fn, *args, target=None, after=None, quiet=False, **kwargs):
        if self.job:
            self.flash("still working on: %s" % self.job, "warn")
            return
        self.job = label
        self.job_target = target

        def work():
            try:
                value = fn(*args, **kwargs)
                self.results.put((True, value, after, quiet))
            except (app.ActionError, PremiumError, tunnel.TunnelError, store.Busy) as error:
                self.results.put((False, str(error), after, quiet))
            except Exception as error:  # keep the screen alive whatever happens
                store.log("tui: %r" % error)
                self.results.put((False, "unexpected error: %s" % error, after, quiet))

        threading.Thread(target=work, daemon=True).start()

    def collect(self):
        while True:
            try:
                ok, value, after, quiet = self.results.get_nowait()
            except queue.Empty:
                return
            self.job = None
            self.job_target = None
            self.reload()
            if after:
                after(ok, value)
            elif isinstance(value, str) and (not quiet or not ok):
                self.flash(value, "ok" if ok else "error")

    # ------------------------------------------------------------ actions

    def connect_row(self, row, protocol=None):
        if row is None:
            return
        live = self.live
        protocol = protocol or (self.premium.protocol if self.premium.protocol in row["protocols"]
                                else (row["protocols"] or ["awg"])[0])
        if live["connected"] and not live["foreign"] and live["country"] == row["code"] and live["protocol"] == protocol:
            self.start("disconnecting", app.disconnect)
            return
        verb = "switching to" if live["connected"] else "connecting to"
        self.start("%s %s" % (verb, row["name"]), app.connect, country=row["code"], protocol=protocol,
                   target=(row["code"], protocol))

    def switch_protocol(self):
        row = self.current_row()
        offered = row["protocols"] if row else list(PROTOCOLS)
        current = self.premium.protocol
        options = [p for p in PROTOCOLS if p in offered] or list(PROTOCOLS)
        nxt = options[(options.index(current) + 1) % len(options)] if current in options else options[0]
        if nxt == current:
            self.flash("%s offers only %s" % (row["name"] if row else "this location", protocol_label(current)), "warn")
            return
        live = self.live
        if live["connected"] and not live["foreign"] and row and live["country"] == row["code"]:
            self.start("switching to %s" % protocol_label(nxt), app.connect, country=row["code"], protocol=nxt,
                       target=(row["code"], nxt))
        else:
            self.premium.choose(protocol=nxt)
            self.flash("Protocol: %s" % protocol_label(nxt), "ok")

    def toggle(self):
        if self.live["connected"]:
            self.start("disconnecting", app.disconnect)
        else:
            target = (self.premium.country, self.premium.protocol)
            name = next((r["name"] for r in self.account["countries"] if r["code"] == self.premium.country),
                        self.premium.country.upper() or "a location")
            self.start("connecting to %s" % name, app.connect, target=target)

    def refresh(self, quiet=False):
        def done(ok, value):
            if not ok:
                self.flash(value, "error")
            elif not quiet:
                self.flash("Updated: %d locations" % len(self.account["countries"]), "ok")
        self.start("asking Amnezia", app.refresh_account, after=done, quiet=quiet)

    def renew(self):
        row = self.current_row()
        if row is None:
            return
        if row["code"] != self.premium.country:
            self.premium.choose(country=row["code"])

        def go():
            self.start("getting a new config", app.renew, target=(row["code"], self.premium.protocol))
        self.ask("Get a new config (fresh key) for %s? The old one stops working. [y/n]" % row["name"], go)

    def open_renewal(self):
        def done(ok, value):
            if ok and shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", value], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                self.flash("Opened the renewal page", "ok")
            elif ok:
                self.flash(value)
            else:
                self.flash(value, "error")
        self.start("asking for the renewal link", lambda: Premium().renewal_link(), after=done)

    def revoke_selected(self):
        devices = self.account["devices"]
        if not devices:
            return
        device = devices[max(0, min(self.dev_cursor, len(devices) - 1))]
        if not device["uuid"]:
            self.flash("this device has no id to free", "warn")
            return
        if device["me"]:
            question = "Free THIS device's slot? Its configs stop working. [y/n]"
        else:
            question = "Free the slot of %s (%s)? Its configs stop working. [y/n]" % (
                device_name(device["os"]), device["country_name"] or device["country"].upper())
        self.ask(question, lambda: self.start("freeing the slot", app.revoke, device["uuid"], device["country"]))

    def ask(self, question, callback):
        self.confirm = (question, callback)

    def submit_key(self):
        text = self.input.strip()
        if not text:
            return

        def done(ok, value):
            if ok:
                self.input = ""
                self.mode = "main"
                self.cursor = 0
                self._place_cursor_on_current()
                self.flash(value, "ok")
            else:
                self.flash(value, "error")
        self.start("checking the key", app.set_key, text, after=done)

    # ------------------------------------------------------------ drawing

    def put(self, y, x, text, attr=0, width=None):
        height, screen_width = self.screen.getmaxyx()
        if y < 0 or y >= height or x >= screen_width:
            return 0
        limit = screen_width - x if width is None else min(width, screen_width - x)
        if y == height - 1:
            limit = min(limit, screen_width - x - 1)
        text = truncate(text, limit)
        try:
            self.screen.addstr(y, x, text, attr)
        except curses.error:
            pass
        return text_width(text)

    def put_segments(self, y, x, segments, width):
        used = 0
        for text, attr in segments:
            if used >= width:
                break
            used += self.put(y, x + used, text, attr, width - used)
        return used

    def box(self, y, x, h, w, title, lines, active=True):
        """A rounded pane exactly h×w with `title` in its top edge. `lines`
        are lists of (text, attr) segments."""
        if h < 3 or w < 6:
            return
        edge = self.c("border") if active else self.c("dim")
        inner = w - 2
        label = "─ %s " % truncate(title, max(1, inner - 4))
        self.put(y, x, "╭" + label + "─" * max(0, inner - text_width(label)) + "╮", edge)
        pad = 2 if inner >= 30 else 1
        for row in range(1, h - 1):
            self.put(y + row, x, "│", edge)
            self.put(y + row, x + 1, " " * inner)
            index = row - 1 - 1   # one blank line of top padding
            if 0 <= index < len(lines):
                self.put_segments(y + row, x + 1 + pad, lines[index], inner - pad * 2)
            self.put(y + row, x + w - 1, "│", edge)
        self.put(y + h - 1, x, "╰" + "─" * inner + "╯", edge)

    def kv(self, label, value, attr=None, width=None):
        return [(label.ljust(LABEL_W), self.c("dim")), (value if value not in (None, "") else "—",
                                                        self.c("normal") if attr is None else attr)]

    def title_lines(self, width):
        live = self.live
        left = [("Amnezia Premium", self.c("bold"))]
        right = self.version
        lines = [left]
        if self.job:
            spin = SPINNER[self.frame % len(SPINNER)]
            second = [(spin + " ", self.c("accent")), (self.job[:1].upper() + self.job[1:] + "…", self.c("accent"))]
        elif live["connected"] and live["foreign"]:
            second = [("Connected", self.c("green")), ("  by another app (%s)" % tunnel.backend_label(live["backend"]), self.c("dim"))]
        elif live["connected"]:
            row = next((r for r in self.account["countries"] if r["code"] == live["country"]), None)
            name = row["name"] if row else live["country"].upper()
            bits = ["%s %s" % (row["flag"] if row else "", name), protocol_label(live["protocol"])]
            if live["since"]:
                bits.append(app.format_duration(time.time() - live["since"]))
            bits.append("↓ %s  ↑ %s" % (app.format_bytes(live["rx"]), app.format_bytes(live["tx"])))
            second = [("Connected", self.c("green")), ("  " + " · ".join(b.strip() for b in bits), self.c("dim"))]
        else:
            second = [("Disconnected", self.c("dim"))]
        lines.append(second)
        return lines, right

    def location_lines(self, width, height):
        rows = self.countries()
        live = self.live
        lines = []
        if not self.account["known"]:
            lines.append([("Asking Amnezia for the locations…" if self.job else "No locations yet — press r.", self.c("dim"))])
            return lines
        if not rows:
            lines.append([("Nothing matches “%s”." % self.filter, self.c("dim"))])
            return lines
        visible = max(1, height)
        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + visible:
            self.offset = self.cursor - visible + 1
        self.offset = max(0, min(self.offset, max(0, len(rows) - visible)))
        for index in range(self.offset, min(len(rows), self.offset + visible)):
            row = rows[index]
            selected = index == self.cursor
            connected = live["connected"] and not live["foreign"] and live["country"] == row["code"]
            working = self.job and self.job_target and self.job_target[0] == row["code"]
            if working:
                mark, mark_attr = SPINNER[self.frame % len(SPINNER)], self.c("accent")
            elif connected:
                mark, mark_attr = "●", self.c("green")
            elif row["code"] == self.premium.country:
                mark, mark_attr = "○", self.c("dim")
            else:
                mark, mark_attr = " ", 0
            tags = []
            for protocol in PROTOCOLS:
                if protocol in row["protocols"]:
                    short = "AWG" if protocol == "awg" else "VLESS"
                    if connected and live["protocol"] == protocol:
                        attr = self.c("green")
                    elif protocol == self.premium.protocol:
                        attr = self.c("normal")
                    else:
                        attr = self.c("dim")
                    tags.append((short, attr))
            tag_width = sum(text_width(t) for t, _ in tags) + max(0, len(tags) - 1)
            name = (row["flag"] + " " if row["flag"] else "") + row["name"]
            name_attr = self.c("accent") if selected else (self.c("bold") if connected else self.c("normal"))
            name_width = max(4, width - 4 - tag_width - 1)
            name = truncate(name, name_width)
            segments = [("›" if selected else " ", self.c("accent")), (" ", 0), (mark, mark_attr), (" ", 0),
                        (name, name_attr), (" " * max(1, name_width - text_width(name) + 1), 0)]
            for i, tag in enumerate(tags):
                if i:
                    segments.append((" ", 0))
                segments.append(tag)
            lines.append(segments)
        return lines

    def detail_lines(self, width):
        row = self.current_row()
        live = self.live
        lines = []
        if row is None:
            if self.account["known"]:
                lines.append(self.kv("Status", "Connected" if live["connected"] else "Disconnected"))
            else:
                lines.append([("Press r to load your locations.", self.c("dim"))])
        else:
            here = live["connected"] and not live["foreign"] and live["country"] == row["code"]
            protocol = live["protocol"] if here else (self.premium.protocol if self.premium.protocol in row["protocols"]
                                                       else (row["protocols"] or ["awg"])[0])
            if self.job and self.job_target and self.job_target[0] == row["code"]:
                lines.append(self.kv("Status", self.job[:1].upper() + self.job[1:] + "…", self.c("accent")))
            elif here:
                since = " · " + app.format_duration(time.time() - live["since"]) if live["since"] else ""
                lines.append(self.kv("Status", "Connected" + since, self.c("green")))
            else:
                lines.append(self.kv("Status", "Disconnected", self.c("dim")))
            lines.append([])
            lines.append(self.kv("Location", "%s %s  (%s)" % (row["flag"], row["name"], row["code"])))
            protocols = []
            for p in PROTOCOLS:
                if p in row["protocols"]:
                    if protocols:
                        protocols.append(("  ", 0))
                    attr = self.c("accent") if p == protocol else self.c("dim")
                    protocols.append((("▸ " if p == protocol else "  ") + protocol_label(p), attr))
            lines.append([("Protocol".ljust(LABEL_W), self.c("dim"))] + protocols)
            record = self.premium.cached_config(row["code"], protocol)
            if record:
                lines.append(self.kv("Endpoint", record.get("endpoint", "")))
                if protocol == "awg":
                    lines.append(self.kv("Address", record.get("address", "")))
                else:
                    lines.append(self.kv("Transport", record.get("transport", "")))
                lines.append(self.kv("DNS", ", ".join(record.get("dns") or [])))
                lines.append(self.kv("Config", "issued " + human_age(record.get("issued_at"))))
            else:
                lines.append(self.kv("Config", "issued by Amnezia on first connect", self.c("dim")))
            if here:
                lines.append(self.kv("Download", app.format_bytes(live["rx"])))
                lines.append(self.kv("Upload", app.format_bytes(live["tx"])))
                lines.append(self.kv("Via", tunnel.backend_label(live["backend"])))
            elif protocol == "awg":
                backend = tunnel.awg_backend(self.premium.settings.get("awg_backend", "auto"))
                lines.append(self.kv("Via", tunnel.backend_label(backend) if backend else "nothing installed",
                                     None if backend else self.c("red")))
            else:
                lines.append(self.kv("Via", "Xray (TUN), asks for your password" if shutil.which("xray")
                                     else "xray is not installed", None if shutil.which("xray") else self.c("red")))
        lines.append([])
        lines.append([("Subscription", self.c("bold"))])
        account = self.account
        if account["end_date"]:
            days = int((account["end_ts"] - time.time()) // 86400) if account["end_ts"] else None
            attr = self.c("red") if days is not None and days < 0 else (self.c("yellow") if days is not None and days < 7 else None)
            text = account["end_date"][:10] + ("  (%d days)" % days if days is not None and days >= 0 else "  (expired)" if days is not None else "")
            lines.append(self.kv("Valid until", text, attr))
        if account["max_devices"]:
            lines.append(self.kv("Devices", "%d of %d" % (account["active_devices"], account["max_devices"])))
        if account["fetched_at"]:
            lines.append(self.kv("Checked", human_age(account["fetched_at"]), self.c("dim")))
        return lines

    def device_lines(self, width, height):
        devices = self.account["devices"]
        if not devices:
            return [[("No device holds a slot yet." if self.account["known"] else "Press r to ask Amnezia.", self.c("dim"))]]
        lines = []
        self.dev_cursor = max(0, min(self.dev_cursor, len(devices) - 1))
        for index, device in enumerate(devices):
            selected = index == self.dev_cursor
            name = "This device" if device["me"] else device_name(device["os"])
            source = SOURCES.get(device["source"], device["source"].replace("_", " "))
            meta = " · ".join(b for b in (device["country_name"] or device["country"].upper(), source,
                                          human_age(device["last"])) if b)
            lines.append([("›" if selected else " ", self.c("accent")), (" ", 0),
                          (name, self.c("accent") if selected else (self.c("green") if device["me"] else self.c("bold")))])
            lines.append([("  " + meta, self.c("dim"))])
            lines.append([])
        return lines[:max(1, height)]

    def account_detail_lines(self, width):
        settings = self.premium.settings
        account = self.account
        lines = [self.kv("Plan", settings.get("name") or settings.get("service_type") or "")]
        if settings.get("description"):
            lines.append(self.kv("", settings["description"], self.c("dim")))
        lines.append([])
        if account["end_date"]:
            days = int((account["end_ts"] - time.time()) // 86400) if account["end_ts"] else None
            lines.append(self.kv("Valid until", account["end_date"][:10] + ("  (%d days)" % days if days is not None else "")))
        if account["max_devices"]:
            lines.append(self.kv("Devices", "%d of %d" % (account["active_devices"], account["max_devices"])))
        lines.append(self.kv("Locations", str(len(account["countries"]))))
        lines.append(self.kv("This device", settings.get("installation_uuid", "")[:8] + "…"))
        lines.append(self.kv("Key", "…" + str(settings.get("api_key", ""))[-4:]))
        backend = tunnel.awg_backend(settings.get("awg_backend", "auto"))
        lines.append(self.kv("AmneziaWG", tunnel.backend_label(backend) or "nothing installed"))
        lines.append(self.kv("VLESS", "Xray (TUN)" if shutil.which("xray") else "install xray"))
        for key in ("email", "telegram", "website"):
            if account["support"].get(key):
                lines.append(self.kv(key.capitalize(), account["support"][key]))
        if account["fetched_at"]:
            lines.append(self.kv("Checked", human_age(account["fetched_at"]), self.c("dim")))
        return lines

    def footer(self, width):
        if self.confirm:
            return [(self.confirm[0], self.c("yellow"))]
        if self.filtering:
            return [("/", self.c("accent")), (self.filter, self.c("normal")), ("▏", self.c("accent")),
                    ("   enter keep · esc clear", self.c("dim"))]
        if self.message and time.monotonic() < self.message_until:
            return [(self.message, self.message_attr)]
        if self.mode == "account":
            keys = [("x", "free slot"), ("o", "renew"), ("k", "new key"), ("r", "refresh"), ("esc", "back")]
        elif self.mode == "key":
            keys = [("enter", "save"), ("esc", "quit" if not self.premium.has_key else "cancel")]
        else:
            keys = [("enter", "connect"), ("p", "protocol"), ("/", "filter"), ("a", "account"), ("?", "help"), ("q", "quit")]
            if self.filter:
                keys.insert(2, ("esc", "clear filter"))
        segments = []
        for i, (key, text) in enumerate(keys):
            if i:
                segments.append((" • ", self.c("dim")))
            segments.append((key, self.c("normal")))
            segments.append((" " + text, self.c("dim")))
        return segments

    def draw(self):
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        if height < 10 or width < 40:
            self.put(0, 0, "Make the window a little bigger.", self.c("dim"))
            self.screen.refresh()
            return
        pad = 1
        inner = width - pad * 2
        title, right = self.title_lines(inner)
        self.put_segments(0, pad, title[0], inner)
        if right:
            self.put(0, pad + inner - text_width(right), right, self.c("dim"))
        self.put_segments(1, pad, title[1], inner)
        body_top = 3
        body_height = height - body_top - 2
        if self.mode == "key":
            self.draw_key_entry(body_top, pad, body_height, inner)
        elif self.mode == "help":
            self.draw_help(body_top, pad, body_height, inner)
        else:
            stacked = width < SPLIT_MIN_WIDTH
            if stacked:
                wanted = (len(self.countries()) if self.mode == "main" else len(self.account["devices"]) * 3) + 3
                top_h = max(6, min(wanted, body_height - 10, body_height // 2 + 4))
                bottom_h = body_height - top_h
                left_w = right_w = inner
            else:
                left_w = max(34, (inner - 2) * 2 // 5)
                right_w = inner - 2 - left_w
                top_h = bottom_h = body_height
            if self.mode == "account":
                left = self.device_lines(left_w - 6, top_h - 3)
                self.box(body_top, pad, top_h, left_w, "Devices", left)
                right = self.account_detail_lines(right_w - 6)
                title_right = "Subscription"
            else:
                left = self.location_lines(left_w - 6, top_h - 3)
                count = len(self.countries())
                label = "Locations" + (" · %d" % count if count else "")
                if self.filter:
                    label += " · “%s”" % self.filter
                self.box(body_top, pad, top_h, left_w, label, left)
                right = self.detail_lines(right_w - 6)
                title_right = "Details"
            if stacked:
                self.box(body_top + top_h, pad, bottom_h, right_w, title_right, right)
            else:
                self.box(body_top, pad + left_w + 2, top_h, right_w, title_right, right)
        self.put_segments(height - 1, pad, self.footer(inner), inner)
        self.screen.refresh()

    def draw_key_entry(self, top, left, height, width):
        box_w = min(width, 78)
        x = left + (width - box_w) // 2
        field_w = box_w - 8
        shown = self.input[-field_w:] if text_width(self.input) > field_w else self.input
        lines = [
            [("Paste your Amnezia Premium key.", self.c("bold"))],
            [],
            [("It starts with vpn:// — find it in the Amnezia app under the", self.c("dim"))],
            [("subscription's Share / Export key, or in the purchase e-mail.", self.c("dim"))],
            [],
            [("› ", self.c("accent")), (shown or "", self.c("normal")), ("▏", self.c("accent"))],
            [],
            [("The key stays in ~/.config/omarchy/amnezia (mode 600).", self.c("dim"))],
        ]
        box_h = min(height, len(lines) + 3)
        self.box(top + max(0, (height - box_h) // 3), x, box_h, box_w, "Premium key", lines)

    def draw_help(self, top, left, height, width):
        keys = KEYS_ACCOUNT if self.previous_mode == "account" else KEYS_MAIN
        lines = [[(k.ljust(10), self.c("normal")), (text, self.c("dim"))] for k, text in keys]
        lines += [[], [("AmneziaWG goes through the AmneziaVPN service when it is installed", self.c("dim"))],
                  [("(no password). VLESS runs Xray and asks for your password once per", self.c("dim"))],
                  [("connect and disconnect. Any key closes this.", self.c("dim"))]]
        box_w = min(width, 78)
        box_h = min(height, len(lines) + 3)
        self.box(top + max(0, (height - box_h) // 3), left + (width - box_w) // 2, box_h, box_w,
                 "Keys " + self.version, lines)

    # ------------------------------------------------------------- input

    def handle(self, key):
        if self.confirm:
            question, callback = self.confirm
            self.confirm = None
            if key in ("y", "Y"):
                callback()
            return True
        if self.mode == "help":
            self.mode = self.previous_mode
            return True
        if self.mode == "key":
            return self.handle_key_entry(key)
        if self.filtering:
            return self.handle_filter(key)
        if key in ("q", "Q"):
            return False
        if key == "?":
            self.previous_mode = self.mode
            self.mode = "help"
            return True
        if self.mode == "account":
            return self.handle_account(key)
        return self.handle_main(key)

    def move(self, delta, count):
        if count:
            self.cursor = max(0, min(count - 1, self.cursor + delta))

    def handle_main(self, key):
        rows = self.countries()
        height = self.screen.getmaxyx()[0]
        page = max(1, height - 8)
        if key in (curses.KEY_UP, "k"):
            self.move(-1, len(rows))
        elif key in (curses.KEY_DOWN, "j"):
            self.move(1, len(rows))
        elif key == curses.KEY_PPAGE:
            self.move(-page, len(rows))
        elif key == curses.KEY_NPAGE:
            self.move(page, len(rows))
        elif key in (curses.KEY_HOME, "g"):
            self.cursor = 0
        elif key in (curses.KEY_END, "G"):
            self.cursor = max(0, len(rows) - 1)
        elif key in ("\n", "\r", curses.KEY_ENTER):
            self.connect_row(self.current_row())
        elif key in ("p", "\t"):
            self.switch_protocol()
        elif key in (" ", "t"):
            self.toggle()
        elif key == "d":
            if self.live["connected"]:
                self.start("disconnecting", app.disconnect)
        elif key == "r":
            self.refresh()
        elif key == "R":
            self.renew()
        elif key == "/":
            self.filtering = True
        elif key == "\x1b":
            if self.filter:
                self.filter = ""
                self._place_cursor_on_current()
            else:
                return False
        elif key == "a":
            self.mode = "account"
            if time.time() - self.account["fetched_at"] > 60:
                self.refresh(quiet=True)
        return True

    def handle_filter(self, key):
        if key == "\x1b":
            self.filter = ""
            self.filtering = False
            self._place_cursor_on_current()
        elif key in ("\n", "\r", curses.KEY_ENTER):
            self.filtering = False
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            self.filter = self.filter[:-1]
            self.cursor = 0
        elif key in (curses.KEY_UP, curses.KEY_DOWN):
            self.move(-1 if key == curses.KEY_UP else 1, len(self.countries()))
        elif isinstance(key, str) and key.isprintable():
            self.filter += key
            self.cursor = 0
        return True

    def handle_account(self, key):
        devices = self.account["devices"]
        if key in (curses.KEY_UP, "k"):
            self.dev_cursor = max(0, self.dev_cursor - 1)
        elif key in (curses.KEY_DOWN, "j"):
            self.dev_cursor = min(max(0, len(devices) - 1), self.dev_cursor + 1)
        elif key == "x":
            self.revoke_selected()
        elif key == "o":
            self.open_renewal()
        elif key == "r":
            self.refresh()
        elif key == "k":
            self.input = ""
            self.mode = "key"
        elif key == "F":
            self.ask("Forget the key on this device and free its slot? [y/n]",
                     lambda: self.start("forgetting the key", app.forget,
                                        after=lambda ok, value: self._after_forget(ok, value)))
        elif key in ("\x1b", "a"):
            self.mode = "main"
        return True

    def _after_forget(self, ok, value):
        if ok:
            self.mode = "key"
            self.flash(value, "ok")
        else:
            self.flash(value, "error")

    def handle_key_entry(self, key):
        if key == "\x1b":
            if self.premium.has_key:
                self.mode = "account"
                return True
            return False
        if key in ("\n", "\r", curses.KEY_ENTER):
            self.submit_key()
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            self.input = self.input[:-1]
        elif key == "\x15":   # ctrl-u
            self.input = ""
        elif isinstance(key, str) and (key.isprintable() and key not in "\n\r"):
            self.input += key
        return True

    # -------------------------------------------------------------- loop

    def loop(self):
        self.init_colors()
        self.screen.timeout(120)
        self.screen.keypad(True)
        if self.mode == "main" and (not self.account["known"]
                                    or time.time() - self.account["fetched_at"] > ACCOUNT_STALE):
            self.refresh(quiet=True)
        while True:
            self.collect()
            if not self.job and time.monotonic() - self.live_at > LIVE_EVERY:
                self.reload_live()
            self.frame += 1
            self.draw()
            try:
                key = self.screen.get_wch()
            except curses.error:
                continue
            except KeyboardInterrupt:
                return 0
            if key == curses.KEY_RESIZE:
                continue
            if not self.handle(key):
                return 0


def run():
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")
    # The window title is how the bar finds an open dashboard to focus.
    sys.stdout.write("\033]0;%s\007" % TITLE)
    sys.stdout.flush()
    try:
        return curses.wrapper(lambda screen: Dashboard(screen).loop())
    except KeyboardInterrupt:
        return 0
