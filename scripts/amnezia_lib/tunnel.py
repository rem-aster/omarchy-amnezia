"""Bringing a Premium config up and down.

AmneziaWG has two ways up, picked per machine (`awg_backend`):

  daemon — the AmneziaVPN service the official app installs. It runs as root
           from boot and takes newline-delimited JSON on a unix socket that any
           local process may use, which is why the app never asks for a
           password. It ships its own amneziawg-go, new enough for every knob
           a Premium config carries (AWG 2/3 headers, padding, I1–I5…), and
           reports real transfer counters. No password, no extra packages.
  quick  — `awg-quick` from amneziawg-tools, through amnezia-root and pkexec:
           one polkit prompt per switch. Needs tools that know the knobs the
           config uses.

VLESS is Xray's job: amnezia-root (pkexec) starts `xray` with a TUN inbound as
a transient systemd unit, routes everything but the server into the TUN,
points DNS at it, and blocks IPv6 for as long as it runs so nothing leaks
around it. Stopping the unit undoes all of it.
"""

import contextlib
import copy
import json
import os
import shutil
import socket
import subprocess
import time

from . import store

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_HELPER = os.path.join(SCRIPTS, "amnezia-root")

# How long a privileged step may take, password prompt included.
ROOT_TIMEOUT = 240

AWG_IFACE = "amnezia"        # awg-quick names the interface after the file
VLESS_IFACE = "amnvless"
VLESS_UNIT = "omarchy-amnezia-vless"
DAEMON_IFACE = "amn0"        # the AmneziaVPN service's interface
XRAY_CONFIG = os.path.join(store.RUNTIME, "xray.json")
AWG_CONFIG = os.path.join(store.RUNTIME, AWG_IFACE + ".conf")

SOCKET_PATHS = ("/run/amneziavpn/daemon.socket", "/var/run/amneziavpn/daemon.socket", "/tmp/amneziavpn.socket")
CONNECT_TIMEOUT = 2.0
STATUS_TIMEOUT = 4.0
ACTIVATE_TIMEOUT = 25.0
DEVICE_IPV6 = "fd58:baa6:dead::1"   # the app's own non-routable ULA, see LocalSocketController
DAEMON_AWG_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4",
                   "I1", "I2", "I3", "I4", "I5", "HeaderProtectionKey", "ContentPaddingAddition",
                   "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime", "KeepaliveTimeout",
                   "MaxHandshakeAttempts", "RandomTrailers", "DisableCookies")
# Keys that make Xray read or write a file — never handed to a root process.
XRAY_FILE_KEYS = {"certificateFile", "keyFile", "masterKeyLog", "access", "error"}


class TunnelError(Exception):
    pass


def have(command):
    return shutil.which(command) is not None


# ----------------------------------------------------------- the service


def daemon_socket():
    override = os.environ.get("AMNEZIA_DAEMON_SOCKET", "")
    candidates = (override,) if override else SOCKET_PATHS
    for path in candidates:
        try:
            if path and os.path.exists(path):
                return path
        except OSError:
            continue
    return ""


class _Daemon:
    """One connection, one command: the service keeps the tunnel up after we
    hang up."""

    def __init__(self, path):
        self.buffer = b""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(CONNECT_TIMEOUT)
        self.sock.connect(path)

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()

    def send(self, payload):
        self.sock.sendall(json.dumps(payload).encode() + b"\n")

    def wait_for(self, wanted, timeout):
        deadline = time.monotonic() + timeout
        while True:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    message = json.loads(line.strip() or b"null")
                except ValueError:
                    continue
                if isinstance(message, dict) and str(message.get("type", "")) in wanted:
                    return message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65536)
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            self.buffer += chunk


def daemon_status():
    path = daemon_socket()
    if not path:
        return {"available": False}
    try:
        daemon = _Daemon(path)
    except OSError:
        return {"available": False}
    try:
        daemon.send({"type": "status"})
        message = daemon.wait_for({"status"}, STATUS_TIMEOUT)
    except OSError:
        message = None
    finally:
        daemon.close()
    if not message:
        return {"available": True, "connected": False}
    return {
        "available": True,
        "connected": message.get("connected") is True,
        "rx": int(message.get("rxBytes") or 0),
        "tx": int(message.get("txBytes") or 0),
        "address": str(message.get("deviceIpv4Address") or ""),
        "date": str(message.get("date") or ""),
    }


def _parse_conf(text):
    sections = {"interface": {}, "peer": {}}
    current = None
    for raw in str(text).splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            continue
        if current not in sections:
            continue
        key, sep, value = line.partition("=")
        if sep and value.strip():
            sections[current].setdefault(key.strip().lower(), value.strip())
    return sections["interface"], sections["peer"]


def split_endpoint(endpoint):
    value = str(endpoint or "").strip()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = value.rpartition(":")
        if not host:
            host, port = value, ""
    try:
        return host, int(port)
    except ValueError:
        return host, 0


def _ranges(value):
    ranges = []
    for entry in str(value or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        address, _, prefix = entry.partition("/")
        is_ipv6 = ":" in address
        try:
            length = int(prefix) if prefix.strip() else (128 if is_ipv6 else 32)
        except ValueError:
            continue
        ranges.append({"address": address.strip(), "range": length, "isIpv6": is_ipv6})
    return ranges or [{"address": "0.0.0.0", "range": 0, "isIpv6": False},
                      {"address": "::", "range": 0, "isIpv6": True}]


def daemon_activate_payload(conf_text):
    """The `activate` message, field for field what LocalSocketController
    sends for an AmneziaWG config."""
    from .premium import resolve_ipv4
    interface, peer = _parse_conf(conf_text)
    address = next((a.strip() for a in interface.get("address", "").split(",") if a.strip() and ":" not in a), "")
    host, port = split_endpoint(peer.get("endpoint", ""))
    for label, value in (("PrivateKey", interface.get("privatekey")), ("PublicKey", peer.get("publickey")),
                         ("Address", address), ("Endpoint", host)):
        if not value:
            raise TunnelError("the config has no %s" % label)
    if port <= 0:
        raise TunnelError("the config's Endpoint has no port")
    try:
        server = resolve_ipv4(host)
    except Exception as error:  # PremiumError, without importing the class here
        raise TunnelError(str(error))
    payload = {
        "type": "activate",
        "privateKey": interface["privatekey"],
        "serverPublicKey": peer["publickey"],
        "serverPskKey": peer.get("presharedkey", ""),
        "serverPort": port,
        "deviceIpv4Address": address,
        "deviceIpv6Address": DEVICE_IPV6,
        "serverIpv4AddrIn": server,
        "serverIpv4Gateway": server,
        "allowedIPAddressRanges": _ranges(peer.get("allowedips", "")),
        "excludedAddresses": [server],
        "vpnDisabledApps": [],
        "allowedDnsServers": [],
        "killSwitchOption": "false",
    }
    # deviceMTU is read with toString().toInt(): a JSON number reads as 0.
    if interface.get("mtu"):
        payload["deviceMTU"] = str(interface["mtu"])
    if peer.get("persistentkeepalive"):
        payload["persistentKeepalive"] = str(peer["persistentkeepalive"])
    servers = [s.strip() for s in interface.get("dns", "").split(",") if s.strip()]
    if servers:
        payload["primaryDnsServer"] = servers[0]
    if len(servers) > 1:
        payload["secondaryDnsServer"] = servers[1]
    for key in DAEMON_AWG_KEYS:
        if interface.get(key.lower()):
            payload[key] = str(interface[key.lower()])
    return payload


def daemon_up(conf_text):
    path = daemon_socket()
    if not path:
        raise TunnelError("the AmneziaVPN service is not running")
    payload = daemon_activate_payload(conf_text)
    try:
        daemon = _Daemon(path)
    except OSError as error:
        raise TunnelError("cannot reach the AmneziaVPN service (%s)" % (error.strerror or error))
    try:
        daemon.send(payload)
        message = daemon.wait_for({"connected", "disconnected", "backendFailure"}, ACTIVATE_TIMEOUT)
    finally:
        daemon.close()
    if message is None:
        raise TunnelError("the AmneziaVPN service did not answer")
    kind = str(message.get("type"))
    if kind == "backendFailure":
        raise TunnelError("the AmneziaVPN service failed to connect (error %s); see /var/log/AmneziaVPN"
                          % message.get("errorCode"))
    if kind != "connected":
        raise TunnelError("the AmneziaVPN service rejected the config; see /var/log/AmneziaVPN")


def daemon_down():
    path = daemon_socket()
    if not path:
        return
    try:
        daemon = _Daemon(path)
    except OSError as error:
        raise TunnelError("cannot reach the AmneziaVPN service (%s)" % (error.strerror or error))
    try:
        daemon.send({"type": "deactivate"})
        daemon.wait_for({"disconnected"}, STATUS_TIMEOUT)
    finally:
        daemon.close()


# ------------------------------------------------------------- amnezia-root


def run_root(args):
    """One privileged step: amnezia-root through pkexec, so the session's
    polkit agent asks. stdin is closed so pkexec never falls back to asking on
    a terminal nobody is looking at)."""
    if os.geteuid() == 0:
        command = ["bash", ROOT_HELPER] + args
    elif have("pkexec"):
        command = ["pkexec", ROOT_HELPER] + args
    else:
        raise TunnelError("pkexec is missing — install polkit")
    try:
        os.chmod(ROOT_HELPER, 0o755)
    except OSError:
        pass
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=ROOT_TIMEOUT)
    except subprocess.TimeoutExpired:
        # A password dialog nobody answered — after a suspend the agent's
        # window can be gone while pkexec still waits for it. Waiting on
        # would hold every other action back.
        raise TunnelError("the password prompt was not answered in %d minutes — try again" % (ROOT_TIMEOUT // 60))
    if result.returncode == 0:
        return result.stdout
    text = (result.stderr or result.stdout or "").strip()
    if result.returncode in (126, 127) and "amnezia-root" not in text:
        if result.returncode == 126 or "dismissed" in text.lower() or "not authorized" in text.lower():
            raise TunnelError("authorization cancelled")
        raise TunnelError(text or "pkexec could not run the helper")
    lines = [line for line in text.splitlines() if line.strip()]
    message = lines[-1] if lines else "the helper failed (exit %d)" % result.returncode
    raise TunnelError(message.replace("amnezia-root: ", ""))


def _scrub(value):
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in XRAY_FILE_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def build_xray_config(xray, server_ip):
    """Amnezia's client config with its local socks inbound swapped for a TUN
    and the server pinned to an address, so Xray never has to resolve it
    through a tunnel that does not exist yet."""
    outbounds = _scrub(copy.deepcopy(xray.get("outbounds") or []))
    if not outbounds or not isinstance(outbounds[0], dict):
        raise TunnelError("the VLESS config has no server")
    first = outbounds[0]
    first.setdefault("tag", "proxy")
    settings = first.get("settings") or {}
    target = None
    if isinstance(settings.get("vnext"), list) and settings["vnext"]:
        target = settings["vnext"][0]
    elif settings.get("address"):
        target = settings
    host = str((target or {}).get("address") or "")
    if target is not None and host and host != server_ip:
        target["address"] = server_ip
        stream = first.setdefault("streamSettings", {})
        security = stream.get("security")
        if security == "tls":
            stream.setdefault("tlsSettings", {}).setdefault("serverName", host)
        network = stream.get("network")
        if network == "xhttp":
            stream.setdefault("xhttpSettings", {}).setdefault("host", host)
        elif network == "ws":
            stream.setdefault("wsSettings", {}).setdefault("host", host)
    text = json.dumps(outbounds)
    # The app's own runtime fix: old configs carry a fingerprint Xray rejects.
    text = text.replace('"Mozilla/5.0"', '"chrome"')
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{"tag": "tun-in", "port": 0, "protocol": "tun",
                      "settings": {"name": VLESS_IFACE, "mtu": 1500}}],
        "outbounds": json.loads(text),
    }


def xray_server_host(xray):
    outbounds = xray.get("outbounds") or []
    settings = (outbounds[0].get("settings") or {}) if outbounds and isinstance(outbounds[0], dict) else {}
    if isinstance(settings.get("vnext"), list) and settings["vnext"]:
        return str(settings["vnext"][0].get("address") or "")
    return str(settings.get("address") or "")


# ------------------------------------------------------------------ state


def iface_up(name):
    path = "/sys/class/net/%s" % name
    return os.path.isdir(path) and not os.path.exists(path + "/device")


def iface_bytes(name):
    def read(kind):
        try:
            with open("/sys/class/net/%s/statistics/%s_bytes" % (name, kind)) as handle:
                return int(handle.read().strip() or 0)
        except (OSError, ValueError):
            return 0
    return read("rx"), read("tx")


def awg_backend(preference="auto"):
    preference = os.environ.get("OMARCHY_AMNEZIA_AWG_BACKEND") or preference or "auto"
    if preference == "daemon":
        return "daemon" if daemon_socket() else ""
    if preference == "quick":
        return "quick" if have("awg-quick") else ""
    if daemon_socket():
        return "daemon"
    if have("awg-quick"):
        return "quick"
    return ""


def status():
    """What is up right now. Cheap — no network, at most one socket round
    trip — because the bar asks every few seconds."""
    active = store.read_json(store.ACTIVE, {}) or {}
    result = {"connected": False, "protocol": "", "backend": "", "country": "", "rx": 0, "tx": 0,
              "since": 0, "foreign": False, "iface": ""}

    def ours(protocol, backend):
        if active.get("protocol") == protocol and active.get("backend") == backend:
            result["country"] = str(active.get("country") or "")
            result["since"] = int(active.get("since") or 0)
        else:
            result["foreign"] = True

    if iface_up(VLESS_IFACE):
        result.update(connected=True, protocol="vless", backend="xray", iface=VLESS_IFACE)
        result["rx"], result["tx"] = iface_bytes(VLESS_IFACE)
        ours("vless", "xray")
    elif iface_up(AWG_IFACE):
        result.update(connected=True, protocol="awg", backend="quick", iface=AWG_IFACE)
        result["rx"], result["tx"] = iface_bytes(AWG_IFACE)
        ours("awg", "quick")
    else:
        daemon = daemon_status() if daemon_socket() else {}
        if daemon.get("connected"):
            result.update(connected=True, protocol="awg", backend="daemon", iface=DAEMON_IFACE,
                          rx=daemon.get("rx", 0), tx=daemon.get("tx", 0))
            recorded = str(active.get("address") or "").split("/")[0]
            if active.get("backend") == "daemon" and (not daemon.get("address") or not recorded
                                                      or daemon["address"].split("/")[0] == recorded):
                ours("awg", "daemon")
            else:
                result["foreign"] = True
    if not result["connected"] and active:
        store.remove(store.ACTIVE)
    return result


def _record_active(record, backend):
    store.write_json(store.ACTIVE, {
        "country": record.get("country", ""),
        "protocol": record.get("protocol", ""),
        "backend": backend,
        "since": int(time.time()),
        "address": record.get("address", ""),
        "endpoint": record.get("endpoint", ""),
    })


def up(record, awg_preference="auto"):
    """Bring `record` (a config from premium.Premium) up, taking whatever this
    machine is running down first: one tunnel at a time."""
    current = status()
    protocol = record.get("protocol")
    if protocol == "awg":
        backend = awg_backend(awg_preference)
        if not backend:
            raise TunnelError("nothing can raise AmneziaWG here: install the AmneziaVPN app (its service "
                              "does it without a password) or amneziawg-tools")
        if backend == "daemon":
            if current["backend"] in ("xray", "quick"):
                run_root(["down"])
            daemon_up(record["conf"])
        else:
            if current["backend"] == "daemon":
                daemon_down()
            store.write_private(AWG_CONFIG, record["conf"])
            run_root(["up-awg", AWG_CONFIG])
        _record_active(record, backend)
        return backend
    if protocol == "vless":
        if not have("xray"):
            raise TunnelError("xray is not installed — sudo pacman -S xray")
        from .premium import resolve_ipv4
        host = xray_server_host(record["xray"])
        if not host:
            raise TunnelError("the VLESS config has no server address")
        try:
            server_ip = resolve_ipv4(host)
        except Exception as error:
            raise TunnelError(str(error))
        config = build_xray_config(record["xray"], server_ip)
        store.write_private(XRAY_CONFIG, json.dumps(config, indent=1))
        if current["backend"] == "daemon":
            daemon_down()
        dns = [d for d in (record.get("dns") or []) if d][:2] or ["1.1.1.1", "1.0.0.1"]
        run_root(["up-vless", XRAY_CONFIG, server_ip] + dns)
        _record_active(record, "xray")
        return "xray"
    raise TunnelError("unknown protocol %r" % protocol)


def down():
    current = status()
    if not current["connected"]:
        store.remove(store.ACTIVE)
        return False
    if current["backend"] == "daemon":
        daemon_down()
    else:
        run_root(["down"])
    store.remove(store.ACTIVE)
    for path in (XRAY_CONFIG, AWG_CONFIG):
        store.remove(path)
    return True


def backend_label(backend):
    return {"daemon": "AmneziaVPN service", "quick": "awg-quick", "xray": "Xray (TUN)"}.get(backend, backend or "")
