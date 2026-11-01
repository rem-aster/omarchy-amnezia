"""Synthetic Amnezia answers, shaped like the gateway's, for tests and demos."""

import base64
import json
import zlib

AWG_TEMPLATE = """[Interface]
Address = 10.8.1.7/32
DNS = $PRIMARY_DNS, $SECONDARY_DNS
PrivateKey = $WIREGUARD_CLIENT_PRIVATE_KEY
Jc = 4
Jmin = 10
Jmax = 50
S1 = 64
S2 = 77
S3 = 21
S4 = 9
H1 = 100000-200000
H2 = 300000-400000
H3 = 500000-600000
H4 = 700000-800000
I1 = <b 0xc6000000010843290a47ba8ba2ed000044d0e3efd9326adb60561baa3bc4b52471b2d459ddcb9a1a5f0d1c3e0f2e6dd7f1e1a8>
I2 =
HeaderProtectionKey = 7f1b0c9a2e
RandomTrailers = 1

[Peer]
PublicKey = Fvi3YGodNUBd2UQGNWr8EHsA0ij4AAmrYqP8U0Yt3Hc=
PresharedKey = 4qEhW7q8gq6+vwbf1Yh3BiYxV8bF2jVgWJgS6eO2a1M=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 203.0.113.10:47213
PersistentKeepalive = 25
"""

XRAY_CONFIG = {
    "log": {"loglevel": "error", "access": "/root/.ssh/authorized_keys"},
    "inbounds": [{"listen": "127.0.0.1", "port": 10808, "protocol": "socks", "settings": {"udp": True}}],
    "outbounds": [{
        "protocol": "vless",
        "settings": {"vnext": [{"address": "203.0.113.20", "port": 443,
                                "users": [{"id": "$XRAY_CLIENT_ID", "flow": "xtls-rprx-vision", "encryption": "none"}]}]},
        "streamSettings": {"network": "tcp", "security": "reality",
                           "realitySettings": {"fingerprint": "Mozilla/5.0", "serverName": "www.example.com",
                                               "publicKey": "abc", "shortId": "12", "spiderX": "",
                                               "masterKeyLog": "/etc/shadow"}},
    }],
}

COUNTRIES = [
    {"server_country_code": "de", "server_country_name": "Germany", "available_protocols": ["awg", "vless"]},
    {"server_country_code": "nl", "server_country_name": "Netherlands", "available_protocols": ["awg", "vless"]},
    {"server_country_code": "fi", "server_country_name": "Finland", "available_protocols": ["awg"]},
    {"server_country_code": "us-east", "server_country_name": "USA (East)", "available_protocols": ["vless"],
     "server_country_code_l10n": "us"},
    {"server_country_code": "kz", "server_country_name": "Kazakhstan", "available_protocols": ["awg", "vless"]},
    {"server_country_code": "tr", "server_country_name": "Türkiye", "available_protocols": ["awg", "vless"]},
    {"server_country_code": "BAD!", "server_country_name": "ignored"},
]


def vpn_key(obj, signed=True):
    payload = json.dumps(obj).encode()
    compressed = zlib.compress(payload)
    prefix = b"\x00\x00\x00\xff" if signed else len(payload).to_bytes(4, "big")
    return "vpn://" + base64.urlsafe_b64encode(prefix + compressed).decode().rstrip("=")


def premium_key(api_key="test-api-key-0123456789", service="amnezia-premium", protocol="awg"):
    return vpn_key({
        "name": "Amnezia Premium", "description": "Test subscription", "config_version": 2,
        "api_config": {"service_type": service, "service_protocol": protocol, "user_country_code": "ru"},
        "auth_data": {"api_key": api_key},
    })


def config_answer(protocol, country="de", name="Germany"):
    if protocol == "awg":
        container = {"container": "amnezia-awg", "awg": {"last_config": json.dumps({
            "config": AWG_TEMPLATE, "hostName": "203.0.113.10", "port": "47213",
            "client_priv_key": "$WIREGUARD_CLIENT_PRIVATE_KEY", "ContentPaddingAddition": "16",
        })}}
    else:
        container = {"container": "amnezia-xray", "xray": {"last_config": json.dumps(XRAY_CONFIG)}}
    server = {
        "name": "Amnezia Premium", "config_version": 2, "hostName": "203.0.113.10",
        "dns1": "1.1.1.1", "dns2": "1.0.0.1", "defaultContainer": container["container"],
        "containers": [container],
        "api_config": {
            "service_type": "amnezia-premium", "service_protocol": protocol, "user_country_code": "ru",
            "server_country_code": country, "server_country_name": name,
            "available_countries": COUNTRIES,
            "public_key": {"expires_at": "2026-11-01T12:00:00Z"},
            "subscription": {"end_date": "2027-03-01T00:00:00Z"},
        },
    }
    return {"config": vpn_key(server, signed=False), "service_info": {"is_renewal_available": True}}


def account_answer(me="5b0d2c55-3d7c-4f3c-9d8e-1a2b3c4d5e6f"):
    return {
        "subscription_end_date": "2027-03-01T00:00:00Z",
        "active_device_count": 3, "max_device_count": 5,
        "available_countries": COUNTRIES,
        "issued_configs": [
            {"installation_uuid": me, "os_version": "arch", "server_country_code": "de",
             "server_country_name": "Germany", "source_type": "gateway_account",
             "last_downloaded": "2026-10-31T10:00:00Z"},
            {"installation_uuid": "0f8fad5b-d9cb-469f-a165-70867728950e", "os_version": "android",
             "server_country_code": "nl", "server_country_name": "Netherlands",
             "source_type": "gateway_account", "last_downloaded": "2026-10-20T10:00:00Z"},
            {"installation_uuid": "7c9e6679-7425-40de-944b-e07fc1f90ae7", "os_version": "ios",
             "server_country_code": "fi", "server_country_name": "Finland",
             "source_type": "gateway_account", "last_downloaded": "2026-09-01T10:00:00Z"},
        ],
        "support_info": {"email": "support@example.com", "telegram": "@example"},
        "service_info": {"is_renewal_available": True},
    }
