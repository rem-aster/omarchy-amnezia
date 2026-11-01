"""Amnezia Premium: the key, the account, and the configs it hands out.

This is what the app's SubscriptionController does, minus the GUI:

  * the key (`vpn://…`) carries an api_key and the service type, no servers;
  * `v1/account_info` answers with the subscription — end date, device slots,
    the countries on offer and which protocols each of them speaks;
  * `v1/config` issues a config for one country and one protocol to this
    device. For AmneziaWG the device sends the public half of a fresh X25519
    key pair and fills its private half into the template it gets back; for
    VLESS it sends a fresh UUID that becomes its user id on the server;
  * `v1/revoke_config` gives a device slot back.

A device is an `installation_uuid`. This plugin makes one the first time and
keeps it, so every country and protocol it uses counts as one device, the way
one installation of the app does.
"""

import json
import os
import re
import socket
import time
import uuid

from . import store, vpnkey

APP_VERSION = os.environ.get("OMARCHY_AMNEZIA_APP_VERSION", "5.0.4.2")
CLI_NAME = "AmneziaVPN"
LANGUAGES = ("en", "ru", "zh", "fa", "ar", "my", "uk", "ur", "hi")
PROTOCOLS = ("awg", "vless")
PROTOCOL_LABELS = {"awg": "AmneziaWG", "vless": "VLESS"}

# The AmneziaWG knobs, as the app names them in configs (configKeys.h).
AWG_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4",
            "I1", "I2", "I3", "I4", "I5", "HeaderProtectionKey", "ContentPaddingAddition",
            "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime", "KeepaliveTimeout",
            "MaxHandshakeAttempts", "RandomTrailers", "DisableCookies")
COUNTRY_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]{1,12})?$")
UUID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


class PremiumError(Exception):
    pass


def protocol_label(protocol):
    return PROTOCOL_LABELS.get(str(protocol), str(protocol or "").upper())


def flag(code, l10n=""):
    """🇩🇪 for "de", "us-east" → 🇺🇸 (the app's getCountryFlagCode)."""
    letters = (l10n or code or "")[:2].upper()
    if not re.fullmatch(r"[A-Z]{2}", letters):
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in letters)


def os_version():
    # What QSysInfo::productType() reports on Linux: the os-release ID.
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("ID="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    if re.fullmatch(r"[a-z0-9._-]{1,32}", value):
                        return value
    except OSError:
        pass
    return "linux"


def app_language():
    for name in ("OMARCHY_AMNEZIA_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        code = value.split("_")[0].split(".")[0].lower()
        if code in LANGUAGES:
            return code
    return "en"


def _parse_time(value):
    """Seconds since the epoch from an ISO-8601 string, or 0."""
    from datetime import datetime, timezone
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _text(value, limit=128):
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value if value is not None else ""))[:limit]


def conf_fields(conf):
    """Lowercased key → value for each `key = value` line of a .conf."""
    fields = {}
    for line in str(conf or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("["):
            continue
        key, sep, value = line.partition("=")
        if sep and value.strip():
            fields.setdefault(key.strip().lower(), value.strip())
    return fields


class Premium:
    def __init__(self):
        self.settings = store.read_json(store.PREMIUM, {}) or {}

    # ------------------------------------------------------------------ key

    @property
    def has_key(self):
        return bool(self.settings.get("api_key"))

    def save(self):
        store.write_json(store.PREMIUM, self.settings)

    def installation_uuid(self):
        value = str(self.settings.get("installation_uuid") or "")
        if not UUID_RE.fullmatch(value):
            value = str(uuid.uuid4())
            self.settings["installation_uuid"] = value
            self.save()
        return value

    def set_key(self, text):
        try:
            parsed = vpnkey.parse_premium_key(text)
        except vpnkey.KeyError_ as error:
            raise PremiumError(str(error)) from error
        same = parsed["api_key"] == self.settings.get("api_key")
        settings = dict(self.settings) if same else {
            # A device is a device whatever key it holds.
            "installation_uuid": self.settings.get("installation_uuid"),
        }
        settings.update({k: parsed[k] for k in ("key", "api_key", "service_type", "user_country_code",
                                                "name", "description")})
        if not same:
            settings["protocol"] = parsed["protocol"]
            settings["country"] = ""
            store.remove(store.ACCOUNT)
            for name in os.listdir(store.CONFIGS) if os.path.isdir(store.CONFIGS) else []:
                store.remove(os.path.join(store.CONFIGS, name))
        self.settings = settings
        self.installation_uuid()
        self.save()
        store.log("key set (%s)" % parsed["service_type"])
        return parsed

    @property
    def country(self):
        return str(self.settings.get("country") or "")

    @property
    def protocol(self):
        value = str(self.settings.get("protocol") or "awg")
        return value if value in PROTOCOLS else "awg"

    def choose(self, country=None, protocol=None):
        if country is not None:
            country = str(country).lower()
            if country and not COUNTRY_RE.fullmatch(country):
                raise PremiumError("not a country code: %s" % country)
            self.settings["country"] = country
        if protocol is not None:
            if protocol not in PROTOCOLS:
                raise PremiumError("the protocol is awg or vless")
            self.settings["protocol"] = protocol
        self.save()

    # -------------------------------------------------------------- gateway

    def _gateway(self):
        from . import gateway
        return gateway.Gateway(cache_path=store.GATEWAY_CACHE, log=store.log)

    def _base(self, **fields):
        if not self.has_key:
            raise PremiumError("no Premium key yet — add one first")
        payload = {
            "os_version": os_version(),
            "app_version": APP_VERSION,
            "cli_name": CLI_NAME,
            "distribution": "github",
            "app_language": app_language(),
            "installation_uuid": self.installation_uuid(),
        }
        if self.settings.get("user_country_code"):
            payload["user_country_code"] = self.settings["user_country_code"]
        payload["service_type"] = self.settings.get("service_type") or "amnezia-premium"
        payload["auth_data"] = {"api_key": self.settings["api_key"]}
        for key, value in fields.items():
            if value not in (None, "", {}, []):
                payload[key] = value
        return payload

    def _post(self, path, payload):
        from . import gateway
        try:
            return self._gateway().post(path, payload)
        except gateway.GatewayError as error:
            store.log("%s: %s" % (path, error))
            raise PremiumError(str(error)) from error

    # -------------------------------------------------------------- account

    def account(self, refresh=False):
        """The subscription as last seen, asking Amnezia when `refresh` is set
        or nothing is known yet. Always a normalized dict."""
        cached = store.read_json(store.ACCOUNT, {}) or {}
        if refresh or not cached.get("raw"):
            raw = self._post("v1/account_info", self._base(cli_version=APP_VERSION,
                                                           subscription_status=self._status_hint(cached)))
            cached = {"fetched_at": int(time.time()), "raw": raw}
            store.write_json(store.ACCOUNT, cached)
        return self.normalize_account(cached)

    @staticmethod
    def _status_hint(cached):
        end = _parse_time(((cached or {}).get("raw") or {}).get("subscription_end_date"))
        if end and end < time.time():
            return "expired"
        if end and end - time.time() < 7 * 86400:
            return "expire_soon"
        return "active"

    def normalize_account(self, cached):
        raw = (cached or {}).get("raw") or {}
        me = str(self.settings.get("installation_uuid") or "")
        countries = []
        seen = set()
        for entry in raw.get("available_countries") or []:
            if not isinstance(entry, dict):
                continue
            code = str(entry.get("server_country_code") or "").lower()
            if not COUNTRY_RE.fullmatch(code) or code in seen:
                continue
            seen.add(code)
            protocols = [p for p in (entry.get("available_protocols") or []) if p in PROTOCOLS]
            if not protocols and not entry.get("available_protocols"):
                protocols = ["awg"]
            countries.append({
                "code": code,
                "name": _text(entry.get("server_country_name") or code.upper(), 64),
                "protocols": protocols,
                "flag": flag(code, str(entry.get("server_country_code_l10n") or "")),
            })
        countries.sort(key=lambda c: c["name"].lower())
        devices = []
        for entry in raw.get("issued_configs") or []:
            if not isinstance(entry, dict):
                continue
            device_id = str(entry.get("installation_uuid") or "")
            devices.append({
                "uuid": device_id if UUID_RE.fullmatch(device_id) else "",
                "os": _text(entry.get("os_version") or "", 32),
                "country": str(entry.get("server_country_code") or "").lower()[:16],
                "country_name": _text(entry.get("server_country_name") or "", 48),
                "source": _text(entry.get("source_type") or "", 32),
                "last": _parse_time(entry.get("last_downloaded")),
                "me": bool(me) and device_id == me,
            })
        end_date = raw.get("subscription_end_date") or (raw.get("subscription") or {}).get("end_date") or ""
        return {
            "known": bool(raw),
            "fetched_at": int((cached or {}).get("fetched_at") or 0),
            "end_date": _text(end_date, 40),
            "end_ts": _parse_time(end_date),
            "active_devices": _int(raw.get("active_device_count")),
            "max_devices": _int(raw.get("max_device_count")),
            "countries": countries,
            "devices": devices,
            "description": _text(raw.get("subscription_description") or self.settings.get("description") or "", 200),
            "renewal": bool((raw.get("service_info") or {}).get("is_renewal_available")),
            "support": {k: _text(v, 120) for k, v in (raw.get("support_info") or {}).items()
                        if isinstance(v, str)} if isinstance(raw.get("support_info"), dict) else {},
        }

    def country_info(self, code, account=None):
        account = account or self.account()
        for entry in account["countries"]:
            if entry["code"] == code:
                return entry
        return None

    # -------------------------------------------------------------- configs

    @staticmethod
    def config_path(country, protocol):
        return os.path.join(store.CONFIGS, "%s-%s.json" % (country, protocol))

    def cached_config(self, country, protocol):
        if not COUNTRY_RE.fullmatch(str(country or "")) or protocol not in PROTOCOLS:
            return None
        record = store.read_json(self.config_path(country, protocol))
        if not isinstance(record, dict):
            return None
        if protocol == "awg" and not record.get("conf"):
            return None
        if protocol == "vless" and not isinstance(record.get("xray"), dict):
            return None
        return record

    def issue(self, country, protocol, connect_event=False):
        """Ask Amnezia for a fresh config for `country` over `protocol`; the
        record is saved and returned. Supersedes the one this device had for
        that pair."""
        from . import crypto
        country = str(country or "").lower()
        # No country lets the gateway pick, as the app does on first import.
        if country and not COUNTRY_RE.fullmatch(country):
            raise PremiumError("not a country code: %s" % country)
        if protocol not in PROTOCOLS:
            raise PremiumError("the protocol is awg or vless")
        private = ""
        if protocol == "awg":
            private, public = crypto.wireguard_keypair()
        else:
            public = str(uuid.uuid4())
        answer = self._post("v1/config", self._base(
            server_country_code=country,
            service_protocol=protocol,
            public_key=public,
            is_connect_event=True if connect_event else None,
        ))
        record = self._record_from_answer(answer, country, protocol, private, public)
        if not COUNTRY_RE.fullmatch(record["country"]):
            raise PremiumError("Amnezia did not say which location the config is for")
        store.write_json(self.config_path(record["country"], protocol), record)
        store.log("issued %s/%s" % (record["country"], protocol))
        return record

    def _record_from_answer(self, answer, country, protocol, private, public):
        key_text = str(answer.get("config") or "")
        if not key_text:
            raise PremiumError("Amnezia answered without a config")
        try:
            text = vpnkey.decode(key_text)
        except vpnkey.KeyError_ as error:
            raise PremiumError("the config Amnezia sent does not decode (%s)" % error)
        if protocol == "awg":
            text = text.replace("$WIREGUARD_CLIENT_PRIVATE_KEY", private)
        else:
            text = text.replace("$XRAY_CLIENT_ID", public)
        try:
            server = json.loads(text)
        except ValueError:
            raise PremiumError("the config Amnezia sent is not JSON")
        if not isinstance(server, dict):
            raise PremiumError("the config Amnezia sent is not an object")
        containers = [c for c in server.get("containers") or [] if isinstance(c, dict)]
        if not containers:
            raise PremiumError("the config Amnezia sent has no server in it")
        api = server.get("api_config") if isinstance(server.get("api_config"), dict) else {}
        dns = [str(server.get(k) or "").strip() for k in ("dns1", "dns2")]
        dns = [d for d in dns if d]
        answered = str(api.get("server_country_code") or "").lower()
        record = {
            "country": country or answered,
            "country_name": _text(api.get("server_country_name") or "", 64),
            "protocol": protocol,
            "issued_at": int(time.time()),
            "expires_at": _text(((api.get("public_key") or {}) if isinstance(api.get("public_key"), dict) else {}).get("expires_at") or "", 40),
            "host": _text(server.get("hostName") or "", 253),
            "dns": dns,
        }
        if protocol == "awg":
            record.update(self._awg_from_container(containers, dns))
        else:
            record.update(self._xray_from_container(containers))
        self._merge_account(api)
        return record

    @staticmethod
    def _last_config(block):
        last = block.get("last_config")
        if isinstance(last, str):
            try:
                last = json.loads(last)
            except ValueError:
                last = {}
        return last if isinstance(last, dict) else {}

    def _awg_from_container(self, containers, dns):
        block = None
        for container in containers:
            for name in ("awg", "wireguard"):
                if isinstance(container.get(name), dict):
                    block = container[name]
                    break
            if block:
                break
        if block is None:
            raise PremiumError("Amnezia sent no AmneziaWG config")
        last = self._last_config(block)
        conf = str(last.get("config") or "")
        if "[Interface]" not in conf:
            raise PremiumError("Amnezia's AmneziaWG config has no [Interface]")
        primary = dns[0] if dns else "1.1.1.1"
        secondary = dns[1] if len(dns) > 1 else ("1.0.0.1" if not dns else primary)
        conf = conf.replace("$PRIMARY_DNS", primary).replace("$SECONDARY_DNS", secondary)
        # Knobs carried next to the .conf text rather than in it go into
        # [Interface] too, so the .conf is complete on its own.
        present = conf_fields(conf)
        extra = []
        for key in AWG_KEYS:
            value = last.get(key, block.get(key))
            if isinstance(value, (int, float)):
                value = str(value)
            if isinstance(value, str) and value.strip() and key.lower() not in present:
                extra.append("%s = %s" % (key, value.strip()))
        lines = []
        for line in conf.splitlines():
            stripped = line.strip()
            # A knob the server left empty (`I2 =`): the service skips it,
            # but wg/awg-quick reject the line.
            if re.fullmatch(r"[A-Za-z0-9]+\s*=\s*", stripped):
                continue
            # An optional knob the server left as a placeholder is dropped; a
            # missing key or endpoint is caught below.
            if re.search(r"=\s*.*\$[A-Z_]{3,}", stripped):
                key = stripped.split("=", 1)[0].strip()
                if key.lower() in [k.lower() for k in AWG_KEYS] or key.lower() == "dns":
                    continue
            lines.append(line)
            if stripped == "[Interface]" and extra:
                lines.extend(extra)
                extra = []
        conf = "\n".join(lines).strip() + "\n"
        fields = conf_fields(conf)
        for required in ("privatekey", "publickey", "endpoint", "address"):
            if not fields.get(required) or "$" in fields[required]:
                raise PremiumError("Amnezia's AmneziaWG config has no usable %s" % required)
        return {"conf": conf, "endpoint": fields.get("endpoint", ""), "address": fields.get("address", "")}

    def _xray_from_container(self, containers):
        block = None
        for container in containers:
            for name in ("xray", "vless", "ssxray"):
                if isinstance(container.get(name), dict):
                    block = container[name]
                    break
            if block:
                break
        if block is None:
            raise PremiumError("Amnezia sent no VLESS config")
        last = self._last_config(block)
        if "outbounds" not in last and isinstance(last.get("config"), str):
            try:
                last = json.loads(last["config"])
            except ValueError:
                last = {}
        outbounds = last.get("outbounds") if isinstance(last, dict) else None
        if not isinstance(outbounds, list) or not outbounds or not isinstance(outbounds[0], dict):
            raise PremiumError("Amnezia's VLESS config has no server in it")
        server = (((outbounds[0].get("settings") or {}).get("vnext") or [{}])[0]) if isinstance(outbounds[0].get("settings"), dict) else {}
        address = str(server.get("address") or "")
        port = server.get("port")
        stream = outbounds[0].get("streamSettings") or {}
        return {
            "xray": last,
            "endpoint": "%s:%s" % (address, port) if address else "",
            "transport": _text("%s/%s" % (stream.get("network") or "tcp", stream.get("security") or "none"), 40),
        }

    def _merge_account(self, api):
        """The config answer carries a fresh copy of some account facts."""
        if not api:
            return
        cached = store.read_json(store.ACCOUNT, {}) or {}
        raw = dict(cached.get("raw") or {})
        changed = False
        if isinstance(api.get("available_countries"), list) and api["available_countries"]:
            raw["available_countries"] = api["available_countries"]
            changed = True
        for key in ("active_device_count", "max_device_count"):
            if api.get(key):
                raw[key] = api[key]
                changed = True
        end = (api.get("subscription") or {}).get("end_date") if isinstance(api.get("subscription"), dict) else ""
        if end:
            raw["subscription_end_date"] = end
            changed = True
        if changed:
            store.write_json(store.ACCOUNT, {"fetched_at": cached.get("fetched_at") or int(time.time()), "raw": raw})

    # --------------------------------------------------------------- devices

    def revoke(self, device_uuid, country=""):
        """Give a device's slot back. A device Amnezia already dropped answers
        404, which the app also takes as done."""
        if not UUID_RE.fullmatch(str(device_uuid or "")):
            raise PremiumError("not a device id: %s" % device_uuid)
        payload = self._base(server_country_code=country or None)
        payload["installation_uuid"] = device_uuid
        try:
            self._post("v1/revoke_config", payload)
        except PremiumError as error:
            cause = error.__cause__
            if getattr(cause, "status", 0) != 404:
                raise
        store.log("revoked a device slot")

    def renewal_link(self):
        answer = self._post("v1/renewal_link", self._base(cli_version=APP_VERSION,
                                                          subscription_status=self._status_hint(store.read_json(store.ACCOUNT, {}))))
        url = str(answer.get("renewal_url") or "")
        if not url.startswith("https://"):
            raise PremiumError("Amnezia sent no renewal link")
        return url

    def export_native(self, country):
        """A standalone AmneziaWG .conf for a router or the AmneziaWG app
        (“Configuration files” in the app)."""
        from . import crypto
        private, public = crypto.wireguard_keypair()
        answer = self._post("v1/native_config", self._base(
            server_country_code=country, service_protocol="awg", public_key=public))
        conf = str(answer.get("config") or "").replace("$WIREGUARD_CLIENT_PRIVATE_KEY", private)
        if "[Interface]" not in conf:
            raise PremiumError("Amnezia sent no config")
        return conf

    def forget(self):
        """Drop the key and everything issued with it, giving this device's
        slot back first (best effort: offline, the slot stays taken until it
        is freed from another device)."""
        if self.has_key and self.settings.get("installation_uuid"):
            try:
                self.revoke(self.settings["installation_uuid"])
            except PremiumError as error:
                store.log("forget: slot not returned: %s" % error)
        for path in (store.PREMIUM, store.ACCOUNT, store.GATEWAY_CACHE):
            store.remove(path)
        if os.path.isdir(store.CONFIGS):
            for name in os.listdir(store.CONFIGS):
                store.remove(os.path.join(store.CONFIGS, name))
        self.settings = {}


def resolve_ipv4(host):
    """An IPv4 address for `host` (itself when it already is one)."""
    host = str(host or "").strip().strip("[]")
    try:
        socket.inet_aton(host)
        if host.count(".") == 3:
            return host
    except OSError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    except socket.gaierror as error:
        raise PremiumError("could not resolve %s (%s)" % (host, error.strerror or error))
    for info in infos:
        return info[4][0]
    raise PremiumError("could not resolve %s" % host)
