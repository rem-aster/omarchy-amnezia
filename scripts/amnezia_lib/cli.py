"""Command line: every action of the panel, scriptable. The panel itself
runs these — `state --json` to draw, the verbs to act."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time

from . import app, store, tunnel
from .premium import Premium, PremiumError, protocol_label, PROTOCOLS

PROGRAM = "amnezia"

USAGE = """Usage: amnezia [command]

  status [--json]           what is connected (the default)
  state                     everything the panel draws, as JSON (no network)
  refresh                   ask Amnezia for the subscription again, then print `state`
  key [vpn://…|file|-]      set your Amnezia Premium key (asks when no argument;
                            --env reads it from $AMNEZIA_KEY)
  account [--refresh]       subscription: valid until, device slots
  locations [--json]        the locations and the protocols each one offers
  use <cc> [awg|vless]      pick a location (and protocol); a live tunnel moves to it
  protocol <awg|vless>      pick the protocol; a live tunnel moves to it
  up [cc] [awg|vless]       connect (defaults to the picked location and protocol)
  down                      disconnect
  toggle                    connect if off, disconnect if on
  renew                     a fresh config (new key) for the picked location
  devices                   devices holding a slot of the subscription
  revoke <device-id>        free a device's slot
  renewal                   print the subscription's renewal link
  export <cc> [file]        a standalone AmneziaWG .conf (router, AmneziaWG app)
  backend [auto|daemon|quick]  how AmneziaWG is raised
  forget                    remove the key, giving this device's slot back
  doctor                    check what this machine has

Locations are country codes as `amnezia locations` lists them (de, nl, us-east…).
"""


def fail(message):
    print("%s: %s" % (PROGRAM, message), file=sys.stderr)
    return 1


def read_key_argument(value):
    if value in (None, ""):
        if sys.stdin.isatty():
            try:
                return input("Paste your Amnezia Premium key (vpn://…): ").strip()
            except EOFError:
                return ""
        return sys.stdin.read().strip()
    if value == "-":
        return sys.stdin.read().strip()
    if os.path.isfile(value):
        with open(value, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    return value.strip()


def cmd_status(args):
    info = app.status_dict()
    if "--json" in args:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(app.status_line(info))
    return 0


def cmd_account(args):
    premium = Premium()
    if not premium.has_key:
        return fail("no Premium key yet — run: amnezia key")
    try:
        account = app.refresh_account() if "--refresh" in args else premium.account()
    except app.ActionError as error:
        return fail(error)
    print("Plan        %s" % (premium.settings.get("name") or premium.settings.get("service_type")))
    if account["end_date"]:
        days = int((account["end_ts"] - time.time()) // 86400) if account["end_ts"] else None
        print("Valid until %s%s" % (account["end_date"][:10], " (%d days)" % days if days is not None else ""))
    if account["max_devices"]:
        print("Devices     %d of %d" % (account["active_devices"], account["max_devices"]))
    print("Locations   %d" % len(account["countries"]))
    print("This device %s" % premium.settings.get("installation_uuid", ""))
    if account["fetched_at"]:
        print("Checked     %s ago" % app.format_duration(time.time() - account["fetched_at"]))
    return 0


def cmd_locations(args):
    premium = Premium()
    if not premium.has_key:
        return fail("no Premium key yet — run: amnezia key")
    try:
        account = app.refresh_account() if "--refresh" in args else premium.account()
    except (app.ActionError, PremiumError) as error:
        return fail(error)
    if "--json" in args:
        print(json.dumps(account["countries"], ensure_ascii=False))
        return 0
    live = tunnel.status()
    for entry in account["countries"]:
        mark = "*" if live["connected"] and live["country"] == entry["code"] else (
            ">" if entry["code"] == premium.country else " ")
        print("%s %-10s %s %-24s %s" % (mark, entry["code"], entry["flag"], entry["name"],
                                       " ".join(protocol_label(p) for p in entry["protocols"])))
    return 0


def cmd_devices(args):
    premium = Premium()
    if not premium.has_key:
        return fail("no Premium key yet — run: amnezia key")
    try:
        account = app.refresh_account()
    except app.ActionError as error:
        print("%s: %s — showing the last known list" % (PROGRAM, error), file=sys.stderr)
        account = premium.account()
    for device in account["devices"]:
        when = time.strftime("%Y-%m-%d", time.localtime(device["last"])) if device["last"] else "—"
        print("%s %s  %-10s %-18s %-12s %s" % ("*" if device["me"] else " ", device["uuid"] or "?",
                                               device["os"] or "?", device["country_name"] or device["country"],
                                               device["source"], when))
    if not account["devices"]:
        print("no devices hold a slot")
    return 0


def run_action(fn, *args, **kwargs):
    try:
        print(fn(*args, **kwargs))
        return 0
    except app.ActionError as error:
        return fail(error)


def cmd_export(args):
    if not args:
        return fail("usage: amnezia export <cc> [file]")
    premium = Premium()
    try:
        conf = premium.export_native(args[0].lower())
    except PremiumError as error:
        return fail(error)
    if len(args) > 1:
        store.write_private(args[1], conf)
        print("wrote %s" % args[1])
    else:
        sys.stdout.write(conf)
    return 0


def cmd_doctor(_args):
    problems = 0
    color = sys.stdout.isatty()
    good = "\033[32m✓\033[0m" if color else "✓"
    bad = "\033[31m✗\033[0m" if color else "✗"

    def check(ok, text, hint=""):
        nonlocal problems
        if ok:
            print("  %s %s" % (good, text))
        else:
            problems += 1
            print("  %s %s%s" % (bad, text, " — " + hint if hint else ""))

    def note(text):
        print("  · %s" % text)

    premium = Premium()
    print("Amnezia plugin")
    check(premium.has_key, "Premium key", "run: amnezia key")
    check(os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway-public.pem")),
          "gateway public key")
    print("AmneziaWG")
    socket_path = tunnel.daemon_socket()
    if socket_path:
        check(True, "AmneziaVPN service (%s) — no password prompts" % socket_path)
    else:
        note("no AmneziaVPN service — install the AmneziaVPN app to connect AmneziaWG without a password")
    check(bool(socket_path) or shutil.which("awg-quick") is not None, "a way to raise AmneziaWG",
          "install the AmneziaVPN app, or amneziawg-tools + amneziawg-dkms (AUR)")
    if not socket_path and shutil.which("awg-quick"):
        module = tunnel.awg_kernel()
        go = shutil.which("amneziawg-go") is not None
        check(module != "" or go,
              "something to make the interface: amneziawg module %s, amneziawg-go %s" % (
                  module or "not built for kernel " + os.uname().release, "found" if go else "not found"),
              "reinstall amneziawg-dkms and reboot after a kernel update, or install amneziawg-go")
    note("backend: %s (setting: %s)" % (tunnel.awg_backend(premium.settings.get("awg_backend", "auto")) or "none",
                                        premium.settings.get("awg_backend", "auto")))
    print("VLESS")
    xray = shutil.which("xray")
    check(xray is not None, "xray", "sudo pacman -S xray")
    if xray:
        try:
            version = subprocess.run([xray, "version"], capture_output=True, text=True, timeout=5).stdout.split("\n")[0]
            note(version.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    check(shutil.which("pkexec") is not None, "pkexec (one prompt per VLESS connect/disconnect)", "install polkit")
    resolved = subprocess.run(["systemctl", "is-active", "--quiet", "systemd-resolved"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0 \
        if shutil.which("systemctl") else False
    check(resolved or shutil.which("resolvconf") is not None, "DNS for the tunnel (systemd-resolved or resolvconf)",
          "enable systemd-resolved")
    print("Files")
    note("settings %s" % store.BASE)
    note("log      %s" % store.LOG)
    return 1 if problems else 0


# Hard ceilings, whatever hangs underneath: a read of local state, and an
# action (a gateway call, a password prompt and the tunnel coming up). An
# action that hits it exits and lets the lock go, instead of sitting on it.
READ_LIMIT = 20
ACTION_LIMIT = 360


class _Expired(BaseException):
    """Raised by the alarm; a BaseException so no `except OSError` along the
    way can swallow it."""


def _expire(signum, frame):
    raise _Expired()


def main(argv):
    command = argv[0] if argv else ""
    limit = READ_LIMIT if command in ("", "status", "state", "help", "-h", "--help") else ACTION_LIMIT
    previous = signal.signal(signal.SIGALRM, _expire)
    signal.alarm(limit)
    try:
        return _dispatch(argv)
    except _Expired:
        return fail("gave up after %ds — try again" % limit)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _dispatch(argv):
    command = argv[0] if argv else ""
    args = argv[1:]
    if command in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0
    if command in ("", "status"):
        return cmd_status(args)
    if command == "state":
        print(json.dumps(app.state_dict(), ensure_ascii=False))
        return 0
    if command == "refresh":
        try:
            app.refresh_account()
        except app.ActionError as error:
            return fail(error)
        print(json.dumps(app.state_dict(), ensure_ascii=False))
        return 0
    if command == "key":
        # The panel hands the key over in the environment: unlike the
        # command line, /proc/<pid>/environ is readable by this user only.
        if args and args[0] == "--env":
            text = os.environ.pop("AMNEZIA_KEY", "").strip()
        else:
            text = read_key_argument(args[0] if args else None)
        if not text:
            return fail("no key given")
        return run_action(app.set_key, text)
    if command == "account":
        return cmd_account(args)
    if command in ("locations", "ls"):
        return cmd_locations(args)
    if command == "use":
        if not args:
            return fail("usage: amnezia use <cc> [awg|vless]")
        protocol = args[1] if len(args) > 1 else None
        if protocol and protocol not in PROTOCOLS:
            return fail("the protocol is awg or vless")
        return run_action(app.select, country=args[0].lower(), protocol=protocol)
    if command == "protocol":
        if not args or args[0] not in PROTOCOLS:
            return fail("usage: amnezia protocol <awg|vless>")
        return run_action(app.select, protocol=args[0])
    if command in ("up", "connect"):
        country = args[0].lower() if args and args[0] not in PROTOCOLS else None
        protocol = next((a for a in args if a in PROTOCOLS), None)
        return run_action(app.connect, country=country, protocol=protocol)
    if command in ("down", "disconnect"):
        return run_action(app.disconnect)
    if command == "toggle":
        return run_action(app.toggle)
    if command == "renew":
        return run_action(app.renew)
    if command == "devices":
        return cmd_devices(args)
    if command == "revoke":
        if not args:
            return fail("usage: amnezia revoke <device-id>")
        account = Premium().normalize_account(store.read_json(store.ACCOUNT, {}) or {})
        country = next((d["country"] for d in account["devices"] if d["uuid"] == args[0]), "")
        return run_action(app.revoke, args[0], country)
    if command == "renewal":
        try:
            print(Premium().renewal_link())
            return 0
        except PremiumError as error:
            return fail(error)
    if command == "export":
        return cmd_export(args)
    if command == "backend":
        if not args:
            premium = Premium()
            print("AmneziaWG backend: %s (setting: %s)" % (
                tunnel.awg_backend(premium.settings.get("awg_backend", "auto")) or "none available",
                premium.settings.get("awg_backend", "auto")))
            return 0
        return run_action(app.set_backend, args[0])
    if command == "forget":
        return run_action(app.forget)
    if command in ("doctor", "check"):
        return cmd_doctor(args)
    print(USAGE, end="", file=sys.stderr)
    return fail("unknown command: %s" % command)
