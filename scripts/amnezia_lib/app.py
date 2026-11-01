"""What a click does: the actions the panel and the CLI share.

Every action that changes something runs under one lock, so a right click on
the bar during a switch started from the panel waits its turn instead of
interleaving with it.
"""

import os
import shutil
import subprocess
import time

from . import store, tunnel
from .premium import Premium, PremiumError, protocol_label, flag, PROTOCOLS


RECENT_MAX = 3
MAX_COUNTRIES = 120
MAX_DEVICES = 32


class ActionError(Exception):
    pass


def _remember(premium, country, protocol):
    """Recent locations, newest first: the panel pins them above the list."""
    recent = [r for r in premium.settings.get("recent") or []
              if isinstance(r, dict) and r.get("country") and r.get("country") != country]
    recent.insert(0, {"country": country, "protocol": protocol, "at": int(time.time())})
    premium.settings["recent"] = recent[:RECENT_MAX]
    premium.save()


def poke_bar():
    """Ask the bar widget to re-read the state now instead of on its timer."""
    if os.environ.get("OMARCHY_AMNEZIA_NO_POKE") or not shutil.which("omarchy-shell"):
        return
    try:
        subprocess.Popen(["omarchy-shell", "-q", "amnezia", "refresh"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def _wrap(fn):
    def run(*args, **kwargs):
        try:
            # A short wait rides over the moment a status poll peeks at the lock.
            with store.locked(wait=kwargs.pop("wait", 2.0)):
                return fn(*args, **kwargs)
        except store.Busy as error:
            raise ActionError(str(error))
        except (PremiumError, tunnel.TunnelError) as error:
            store.log("%s failed: %s" % (fn.__name__, error))
            raise ActionError(str(error))
        finally:
            poke_bar()
    run.__name__ = fn.__name__
    run.__doc__ = fn.__doc__
    return run


def _pick_protocol(premium, country, protocol, account):
    """The protocol to use for `country`: the one asked for when the country
    offers it, else the remembered one, else the first the country has."""
    info = premium.country_info(country, account) if country else None
    offered = info["protocols"] if info else list(PROTOCOLS)
    for candidate in (protocol, premium.protocol):
        if candidate in offered:
            return candidate, info
    return (offered[0] if offered else "awg"), info


@_wrap
def connect(country=None, protocol=None, renew=False):
    """Connect to `country` over `protocol` (both default to the remembered
    choice). Returns a sentence for the person."""
    premium = Premium()
    if not premium.has_key:
        raise ActionError("add your Amnezia Premium key first")
    account = premium.account()
    country = (country if country is not None else premium.country).lower()
    if country and account["countries"] and not premium.country_info(country, account):
        raise ActionError("your subscription has no location %r" % country)
    protocol, info = _pick_protocol(premium, country, protocol, account)
    record = None if renew or not country else premium.cached_config(country, protocol)
    if record is None:
        record = premium.issue(country, protocol, connect_event=True)
        country = record["country"]
    premium.choose(country=country, protocol=protocol)
    backend = tunnel.up(record, premium.settings.get("awg_backend", "auto"))
    _remember(premium, country, protocol)
    store.log("connected %s/%s via %s" % (country, protocol, backend))
    name = (info or {}).get("name") or record.get("country_name") or country.upper()
    return "Connected to %s over %s" % (name, protocol_label(protocol))


@_wrap
def disconnect():
    if tunnel.down():
        store.log("disconnected")
        return "Disconnected"
    return "Nothing was connected"


def toggle(wait=2.0):
    if tunnel.status()["connected"]:
        return disconnect(wait=wait)
    return connect(wait=wait)


@_wrap
def choose(country=None, protocol=None):
    """Remember a location and/or protocol without connecting."""
    premium = Premium()
    premium.choose(country=country, protocol=protocol)
    return "Saved"


def select(country=None, protocol=None):
    """Pick a location/protocol; a live tunnel moves to it right away."""
    if tunnel.status()["connected"]:
        return connect(country=country, protocol=protocol)
    choose(country=country, protocol=protocol)
    premium = Premium()
    label = premium.country.upper() or "the location"
    return "Picked %s over %s" % (label, protocol_label(premium.protocol))


@_wrap
def renew():
    """A fresh config (a new key) for the current location and protocol."""
    premium = Premium()
    if not premium.country:
        raise ActionError("pick a location first")
    current = tunnel.status()
    record = premium.issue(premium.country, premium.protocol, connect_event=current["connected"])
    if current["connected"] and not current["foreign"]:
        tunnel.up(record, premium.settings.get("awg_backend", "auto"))
        return "New config for %s is up (a fresh key can take a minute or two to carry traffic)" % premium.country.upper()
    return "New config for %s saved" % premium.country.upper()


@_wrap
def set_key(text):
    premium = Premium()
    parsed = premium.set_key(text)
    try:
        account = premium.account(refresh=True)
    except PremiumError as error:
        return "Key saved, but Amnezia did not answer yet: %s" % error
    return "Key saved: %d locations, %s" % (
        len(account["countries"]),
        "valid until " + account["end_date"][:10] if account["end_date"] else parsed["service_type"])


@_wrap
def refresh_account():
    return Premium().account(refresh=True)


@_wrap
def revoke(device_uuid, country=""):
    premium = Premium()
    premium.revoke(device_uuid, country)
    try:
        premium.account(refresh=True)
    except PremiumError:
        pass
    return "Device slot freed"


@_wrap
def forget():
    tunnel.down()
    Premium().forget()
    return "Key removed and this device's slot given back"


@_wrap
def set_backend(value):
    premium = Premium()
    if value not in ("auto", "daemon", "quick"):
        raise ActionError("the AmneziaWG backend is auto, daemon or quick")
    premium.settings["awg_backend"] = value
    premium.save()
    return "AmneziaWG backend: %s (in use: %s)" % (value, tunnel.awg_backend(value) or "none available")


def status_dict():
    """Everything the bar and `status` show. No network."""
    premium = Premium()
    live = tunnel.status()
    account = premium.normalize_account(store.read_json(store.ACCOUNT, {}) or {})
    names = {c["code"]: c for c in account["countries"]}
    country = live["country"] if live["connected"] else premium.country
    entry = names.get(country) or {}
    record = premium.cached_config(country, live["protocol"] or premium.protocol) if country else None
    days_left = int((account["end_ts"] - time.time()) // 86400) if account["end_ts"] else None
    protocol = live["protocol"] if live["connected"] else premium.protocol
    return {
        "ok": True,
        "hasKey": premium.has_key,
        "busy": store.is_locked(),
        "connected": live["connected"],
        "foreign": live["foreign"],
        "country": country,
        "countryName": entry.get("name") or (record or {}).get("country_name") or country.upper(),
        "flag": entry.get("flag") or flag(country),
        "protocol": protocol,
        "protocolLabel": protocol_label(protocol),
        "backend": live["backend"],
        "backendLabel": tunnel.backend_label(live["backend"]),
        "rx": live["rx"],
        "tx": live["tx"],
        "since": live["since"],
        "iface": live["iface"],
        "endpoint": (record or {}).get("endpoint", ""),
        "endDate": account["end_date"][:10],
        "daysLeft": days_left,
        "deviceSlots": "%d/%d" % (account["active_devices"], account["max_devices"]) if account["max_devices"] else "",
        "locations": len(account["countries"]),
    }


def _config_summaries(premium):
    out = {}
    if not os.path.isdir(store.CONFIGS):
        return out
    for name in sorted(os.listdir(store.CONFIGS))[:2 * MAX_COUNTRIES]:
        if not name.endswith(".json"):
            continue
        country, _, protocol = name[:-5].rpartition("-")
        record = premium.cached_config(country, protocol)
        if not record:
            continue
        out["%s-%s" % (country, protocol)] = {
            "endpoint": str(record.get("endpoint") or "")[:120],
            "address": str(record.get("address") or "")[:64],
            "transport": str(record.get("transport") or "")[:40],
            "dns": ", ".join(record.get("dns") or [])[:80],
            "issuedAt": int(record.get("issued_at") or 0),
        }
    return out


def state_dict():
    """Everything the panel draws, from local files only: the live tunnel,
    the subscription as last seen, the locations and devices, recent picks,
    and the configs this device already holds. Bounded, so a poll stays small."""
    premium = Premium()
    info = status_dict()
    account = premium.normalize_account(store.read_json(store.ACCOUNT, {}) or {})
    codes = {c["code"] for c in account["countries"]}
    preference = premium.settings.get("awg_backend", "auto")
    info.update({
        "plan": premium.settings.get("name") or premium.settings.get("service_type") or "",
        "description": premium.settings.get("description") or "",
        "picked": {"country": premium.country, "protocol": premium.protocol},
        "awgBackend": tunnel.awg_backend(preference),
        "awgBackendSetting": preference,
        "xray": tunnel.have("xray"),
        "pkexec": tunnel.have("pkexec"),
        "deviceId": premium.settings.get("installation_uuid") or "",
        "account": {
            "known": account["known"],
            "fetchedAt": account["fetched_at"],
            "endDate": account["end_date"][:10],
            "activeDevices": account["active_devices"],
            "maxDevices": account["max_devices"],
            "renewal": account["renewal"],
            "support": account["support"],
        },
        "countries": account["countries"][:MAX_COUNTRIES],
        "devices": account["devices"][:MAX_DEVICES],
        "recent": [r for r in premium.settings.get("recent") or []
                   if isinstance(r, dict) and r.get("country") in codes][:RECENT_MAX],
        "configs": _config_summaries(premium),
    })
    return info


def format_bytes(value):
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return ("%.0f %s" if unit == "B" or value >= 10 else "%.1f %s") % (value, unit)
        value /= 1024
    return "0 B"


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%ds" % seconds
    minutes = seconds // 60
    if minutes < 60:
        return "%dm" % minutes
    hours = minutes // 60
    if hours < 24:
        return "%dh %dm" % (hours, minutes % 60)
    return "%dd %dh" % (hours // 24, hours % 24)


def status_line(info=None):
    info = info or status_dict()
    if not info["hasKey"]:
        return "no Premium key — run: amnezia key"
    if info["connected"]:
        if info["foreign"]:
            return "connected by another app (%s)" % (info["backendLabel"] or "unknown")
        since = " · " + format_duration(time.time() - info["since"]) if info["since"] else ""
        return "connected: %s %s · %s%s · ↓ %s ↑ %s" % (
            info["flag"], info["countryName"], info["protocolLabel"], since,
            format_bytes(info["rx"]), format_bytes(info["tx"]))
    if info["country"]:
        return "disconnected (next: %s %s · %s)" % (info["flag"], info["countryName"], info["protocolLabel"])
    return "disconnected (no location picked yet)"
