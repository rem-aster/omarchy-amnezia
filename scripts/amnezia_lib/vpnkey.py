"""`vpn://` keys: Amnezia's way of passing a JSON config around as one line.

The text after the scheme is base64url (padding optional) of either Qt's
qCompress output — a 4-byte big-endian length, then a zlib stream — or, for
keys the app signs (apiUtils::getPremiumV2VpnKey), the 4-byte signature
00 00 00 ff followed by the same zlib stream. Both have the zlib stream at
offset 4, so one decoder covers them; a bare zlib stream and an uncompressed
payload are tried as well.
"""

import base64
import json
import re
import zlib

PREMIUM_SERVICES = ("amnezia-premium", "external-premium")


class KeyError_(ValueError):
    """A key that is not something the plugin can use, with the reason."""


def decode(text):
    """The payload of a `vpn://` key as text. Raises KeyError_."""
    body = re.sub(r"\s+", "", str(text or ""))
    lowered = body.lower()
    for scheme in ("vpn://", "amnezia://"):
        if lowered.startswith(scheme):
            body = body[len(scheme):]
            break
    if not body:
        raise KeyError_("the key is empty")
    try:
        blob = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except (ValueError, TypeError):
        raise KeyError_("the key is not valid base64")
    for candidate in (blob[4:], blob):
        try:
            return zlib.decompress(candidate).decode("utf-8")
        except (zlib.error, UnicodeDecodeError):
            continue
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        raise KeyError_("the key does not decode to text")


def decode_json(text):
    payload = decode(text).strip()
    try:
        value = json.loads(payload)
    except ValueError:
        raise KeyError_("the key does not hold an Amnezia config")
    if not isinstance(value, dict):
        raise KeyError_("the key does not hold an Amnezia config")
    return value


def parse_premium_key(text):
    """The parts of a Premium key the gateway needs.

    Returns {"key", "api_key", "service_type", "user_country_code",
    "protocol", "name", "description"}; raises KeyError_ with a reason a person
    can act on when the key is something else.
    """
    text = str(text or "").strip()
    if not text.lower().startswith(("vpn://", "amnezia://")):
        raise KeyError_("a Premium key starts with vpn:// — copy it from the Amnezia app "
                        "(the subscription's “Share” / “Export key”) or the "
                        "e-mail you got with the subscription")
    config = decode_json(text)
    api_config = config.get("api_config") if isinstance(config.get("api_config"), dict) else {}
    auth = config.get("auth_data") if isinstance(config.get("auth_data"), dict) else {}
    api_key = str(auth.get("api_key") or "").strip()
    service = str(api_config.get("service_type") or "").strip()

    if not api_key:
        if isinstance(config.get("containers"), list) and config["containers"]:
            raise KeyError_("this is a ready server config, not a subscription key — the plugin "
                            "works with an Amnezia Premium subscription key")
        if config.get("api_key") or config.get("api_endpoint"):
            raise KeyError_("this is an old (v1) Amnezia Premium key; open it once in the current "
                            "Amnezia app and export the key again")
        raise KeyError_("this key has no api_key, so it is not an Amnezia Premium key")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=:-]{8,512}", api_key):
        raise KeyError_("the key's api_key has characters an Amnezia key never has")
    if service == "amnezia-free":
        raise KeyError_("this is an Amnezia Free key; the plugin is built for Premium "
                        "(Free needs a captcha on every new config)")
    if service not in PREMIUM_SERVICES:
        raise KeyError_("unknown subscription type %r" % (service or "none"))

    protocol = str(api_config.get("service_protocol") or "awg").lower()
    if protocol not in ("awg", "vless"):
        protocol = "awg"
    user_country = re.sub(r"[^a-z]", "", str(api_config.get("user_country_code") or "").lower())[:8]
    return {
        "key": text,
        "api_key": api_key,
        "service_type": service,
        "user_country_code": user_country,
        "protocol": protocol,
        "name": str(config.get("name") or "").strip()[:64],
        "description": str(config.get("description") or "").strip()[:128],
    }
