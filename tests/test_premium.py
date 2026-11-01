import env  # noqa: F401  (must come first: it points the store at a temp dir)

import base64
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest

import fixtures
from amnezia_lib import app, crypto, gateway, premium, store, tunnel, vpnkey


def reset_store():
    shutil.rmtree(store.BASE, ignore_errors=True)
    shutil.rmtree(store.RUNTIME, ignore_errors=True)


class KeyTest(unittest.TestCase):
    def test_premium_key(self):
        parsed = vpnkey.parse_premium_key(fixtures.premium_key(protocol="vless"))
        self.assertEqual(parsed["api_key"], "test-api-key-0123456789")
        self.assertEqual(parsed["service_type"], "amnezia-premium")
        self.assertEqual(parsed["protocol"], "vless")
        self.assertEqual(parsed["user_country_code"], "ru")

    def test_rejections(self):
        with self.assertRaises(vpnkey.KeyError_):
            vpnkey.parse_premium_key(fixtures.premium_key(service="amnezia-free"))
        with self.assertRaises(vpnkey.KeyError_):
            vpnkey.parse_premium_key("[Interface]\nPrivateKey = x")
        with self.assertRaises(vpnkey.KeyError_):
            vpnkey.parse_premium_key(fixtures.vpn_key({"containers": [{"container": "amnezia-awg"}]}))
        with self.assertRaises(vpnkey.KeyError_):
            vpnkey.parse_premium_key("vpn://!!!")

    def test_unsigned_qcompress(self):
        text = vpnkey.decode(fixtures.vpn_key({"a": 1}, signed=False))
        self.assertEqual(json.loads(text), {"a": 1})


class RecordTest(unittest.TestCase):
    def setUp(self):
        reset_store()
        self.p = premium.Premium()
        self.p.set_key(fixtures.premium_key())

    def test_awg_record(self):
        private, public = crypto.wireguard_keypair()
        record = self.p._record_from_answer(fixtures.config_answer("awg"), "de", "awg", private, public)
        conf = record["conf"]
        fields = premium.conf_fields(conf)
        self.assertEqual(fields["privatekey"], private)
        self.assertEqual(fields["dns"], "1.1.1.1, 1.0.0.1")
        self.assertEqual(fields["headerprotectionkey"], "7f1b0c9a2e")
        # Carried next to the .conf text, so it is added to [Interface].
        self.assertEqual(fields["contentpaddingaddition"], "16")
        self.assertNotIn("$", conf)
        self.assertNotIn("I2", conf)
        self.assertEqual(record["endpoint"], "203.0.113.10:47213")
        self.assertEqual(record["country_name"], "Germany")
        # The answer's account facts land in the cache.
        account = self.p.account()
        self.assertEqual([c["code"] for c in account["countries"]][:2], ["fi", "de"])
        self.assertEqual(account["end_date"][:10], "2027-03-01")

    def test_country_from_answer(self):
        private, public = crypto.wireguard_keypair()
        record = self.p._record_from_answer(fixtures.config_answer("awg", country="nl"), "", "awg", private, public)
        self.assertEqual(record["country"], "nl")

    def test_vless_record_and_xray_config(self):
        record = self.p._record_from_answer(fixtures.config_answer("vless"), "de", "vless", "", "uuid-1234")
        self.assertEqual(record["endpoint"], "203.0.113.20:443")
        self.assertIn("uuid-1234", json.dumps(record["xray"]))
        config = tunnel.build_xray_config(record["xray"], "203.0.113.20")
        self.assertEqual(config["inbounds"][0]["protocol"], "tun")
        self.assertEqual(config["inbounds"][0]["settings"]["name"], tunnel.VLESS_IFACE)
        text = json.dumps(config)
        self.assertNotIn("masterKeyLog", text)
        self.assertNotIn("authorized_keys", text)
        self.assertNotIn("Mozilla/5.0", text)
        self.assertEqual(config["outbounds"][0]["tag"], "proxy")

    def test_xray_pins_hostname(self):
        xray = json.loads(json.dumps(fixtures.XRAY_CONFIG))
        xray["outbounds"][0]["settings"]["vnext"][0]["address"] = "vpn.example.net"
        xray["outbounds"][0]["streamSettings"] = {"network": "xhttp", "security": "tls"}
        config = tunnel.build_xray_config(xray, "198.51.100.7")
        out = config["outbounds"][0]
        self.assertEqual(out["settings"]["vnext"][0]["address"], "198.51.100.7")
        self.assertEqual(out["streamSettings"]["tlsSettings"]["serverName"], "vpn.example.net")
        self.assertEqual(out["streamSettings"]["xhttpSettings"]["host"], "vpn.example.net")

    def test_daemon_payload(self):
        private, public = crypto.wireguard_keypair()
        record = self.p._record_from_answer(fixtures.config_answer("awg"), "de", "awg", private, public)
        payload = tunnel.daemon_activate_payload(record["conf"])
        self.assertEqual(payload["privateKey"], private)
        self.assertEqual(payload["serverIpv4AddrIn"], "203.0.113.10")
        self.assertEqual(payload["serverPort"], 47213)
        self.assertEqual(payload["deviceIpv4Address"], "10.8.1.7/32")
        self.assertEqual(payload["H1"], "100000-200000")
        self.assertEqual(payload["HeaderProtectionKey"], "7f1b0c9a2e")
        self.assertEqual(payload["ContentPaddingAddition"], "16")
        self.assertNotIn("I2", payload)
        self.assertEqual(payload["primaryDnsServer"], "1.1.1.1")

    def test_account_normalization(self):
        me = self.p.installation_uuid()
        store.write_json(store.ACCOUNT, {"fetched_at": 1, "raw": fixtures.account_answer(me)})
        account = self.p.account()
        codes = [c["code"] for c in account["countries"]]
        self.assertNotIn("bad!", codes)
        usa = next(c for c in account["countries"] if c["code"] == "us-east")
        self.assertEqual(usa["protocols"], ["vless"])
        self.assertEqual(usa["flag"], premium.flag("us"))
        self.assertEqual(account["max_devices"], 5)
        self.assertTrue(account["devices"][0]["me"])
        self.assertFalse(account["devices"][1]["me"])

    def test_status_without_tunnel(self):
        self.p.choose(country="de", protocol="vless")
        info = app.status_dict()
        self.assertTrue(info["hasKey"])
        self.assertFalse(info["connected"])
        self.assertEqual(info["protocol"], "vless")
        self.assertIn("disconnected", app.status_line(info))

    def test_new_key_resets_choices(self):
        self.p.choose(country="de", protocol="vless")
        uid = self.p.installation_uuid()
        self.p.set_key(fixtures.premium_key(api_key="another-key-987654321"))
        self.assertEqual(self.p.country, "")
        self.assertEqual(self.p.installation_uuid(), uid)


@unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
class GatewayRoundTripTest(unittest.TestCase):
    """A local stand-in for Amnezia's gateway with its own RSA key, to check
    the whole envelope both ways."""

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp()
        cls.private = os.path.join(cls.work, "k.pem")
        cls.public = os.path.join(cls.work, "p.pem")
        subprocess.run(["openssl", "genrsa", "-out", cls.private, "2048"], capture_output=True, check=True)
        subprocess.run(["openssl", "rsa", "-in", cls.private, "-pubout", "-out", cls.public],
                       capture_output=True, check=True)
        cls.seen = []
        outer = cls

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                blob = os.path.join(outer.work, "blob")
                with open(blob, "wb") as handle:
                    handle.write(base64.b64decode(body["key_payload"]))
                keys = json.loads(subprocess.run(
                    ["openssl", "pkeyutl", "-decrypt", "-inkey", outer.private, "-in", blob,
                     "-pkeyopt", "rsa_padding_mode:pkcs1"], capture_output=True, check=True).stdout)
                key, iv = base64.b64decode(keys["aes_key"]), base64.b64decode(keys["aes_iv"])
                request = json.loads(crypto.aes256_cbc_decrypt(base64.b64decode(body["api_payload"]), key, iv))
                outer.seen.append((self.path, request))
                if request["auth_data"]["api_key"] == "bad-key-000000":
                    answer, code = {"http_status": 403, "message": "Invalid user API key."}, 403
                elif self.path.endswith("account_info"):
                    answer, code = fixtures.account_answer(request["installation_uuid"]), 200
                else:
                    answer, code = fixtures.config_answer(request["service_protocol"],
                                                          request.get("server_country_code", "de")), 200
                raw = crypto.aes256_cbc_encrypt(json.dumps(answer).encode(), key, iv)
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        cls.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.saved = (gateway.PEM_PATH, gateway.MAIN_GATEWAY, gateway.PROXY_LIST_BASES)
        gateway.PEM_PATH = cls.public
        gateway.MAIN_GATEWAY = "http://127.0.0.1:%d/" % cls.server.server_port
        gateway.PROXY_LIST_BASES = ()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        gateway.PEM_PATH, gateway.MAIN_GATEWAY, gateway.PROXY_LIST_BASES = cls.saved
        shutil.rmtree(cls.work, ignore_errors=True)

    def setUp(self):
        reset_store()
        self.seen.clear()

    def test_account_and_config(self):
        p = premium.Premium()
        p.set_key(fixtures.premium_key())
        account = p.account(refresh=True)
        self.assertEqual(len(account["countries"]), 6)
        self.assertTrue(account["devices"][0]["me"])
        path, request = self.seen[-1]
        self.assertEqual(path, "/v1/account_info")
        self.assertEqual(request["service_type"], "amnezia-premium")
        self.assertEqual(request["auth_data"], {"api_key": "test-api-key-0123456789"})
        self.assertEqual(request["cli_name"], "AmneziaVPN")

        record = p.issue("nl", "awg", connect_event=True)
        path, request = self.seen[-1]
        self.assertEqual(path, "/v1/config")
        self.assertEqual(request["server_country_code"], "nl")
        self.assertTrue(request["is_connect_event"])
        # The public half sent matches the private half filled in.
        private = premium.conf_fields(record["conf"])["privatekey"]
        self.assertEqual(crypto.wireguard_public_key(private), request["public_key"])
        self.assertEqual(p.cached_config("nl", "awg")["conf"], record["conf"])

        record = p.issue("de", "vless")
        path, request = self.seen[-1]
        self.assertIn(request["public_key"], json.dumps(record["xray"]))
        self.assertNotIn("is_connect_event", request)

    def test_connect_through_service_and_xray(self):
        """connect → issue → AmneziaVPN service (a fake on a unix socket), then
        a switch to VLESS that goes through amnezia-root."""
        import socket as socketlib
        path = os.environ["AMNEZIA_DAEMON_SOCKET"]
        state = {"connected": False, "payload": None}
        server = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
        if os.path.exists(path):
            os.unlink(path)
        server.bind(path)
        server.listen(5)

        def serve():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                with conn:
                    line = conn.makefile().readline()
                    message = json.loads(line)
                    if message["type"] == "activate":
                        state.update(connected=True, payload=message)
                        reply = {"type": "connected"}
                    elif message["type"] == "deactivate":
                        state["connected"] = False
                        reply = {"type": "disconnected"}
                    else:
                        reply = {"type": "status", "connected": state["connected"], "rxBytes": 1234,
                                 "txBytes": 99, "deviceIpv4Address": "10.8.1.7/32"}
                    conn.sendall(json.dumps(reply).encode() + b"\n")

        threading.Thread(target=serve, daemon=True).start()
        calls = []
        saved = (tunnel.run_root, tunnel.have)
        tunnel.run_root = lambda args: calls.append(args) or "up"
        tunnel.have = lambda command: command in ("xray", "pkexec")
        try:
            p = premium.Premium()
            p.set_key(fixtures.premium_key())
            message = app.connect(country="de", protocol="awg")
            self.assertIn("Germany", message)
            self.assertEqual(state["payload"]["serverIpv4AddrIn"], "203.0.113.10")
            info = app.status_dict()
            self.assertTrue(info["connected"])
            self.assertFalse(info["foreign"])
            self.assertEqual((info["country"], info["protocol"], info["backend"]), ("de", "awg", "daemon"))
            self.assertEqual(info["rx"], 1234)

            # Same location over VLESS: the service lets go, xray takes over.
            app.connect(country="de", protocol="vless")
            self.assertFalse(state["connected"])
            self.assertEqual(calls[-1][0], "up-vless")
            self.assertEqual(calls[-1][2:], ["203.0.113.20", "1.1.1.1", "1.0.0.1"])
            with open(calls[-1][1]) as handle:
                self.assertEqual(json.load(handle)["inbounds"][0]["protocol"], "tun")
            self.assertEqual(premium.Premium().protocol, "vless")

            # What the panel draws: recent picks newest first, one entry per
            # location, and the configs this device holds.
            state = app.state_dict()
            self.assertEqual(state["recent"][0], {"country": "de", "protocol": "vless", "at": state["recent"][0]["at"]})
            self.assertEqual(len(state["recent"]), 1)
            self.assertIn("de-awg", state["configs"])
            self.assertIn("de-vless", state["configs"])
            self.assertEqual(state["configs"]["de-vless"]["endpoint"], "203.0.113.20:443")
            self.assertEqual(state["picked"], {"country": "de", "protocol": "vless"})
            self.assertTrue(state["account"]["known"])
            self.assertLess(len(json.dumps(state)), 65536)

            # A location that only speaks VLESS keeps the protocol, and one
            # that does not offer the asked protocol falls back.
            app.choose(protocol="awg")
            tunnel.status = lambda real=tunnel.status: dict(real(), connected=False)
            app.connect(country="us-east")
            self.assertEqual(premium.Premium().protocol, "vless")
        finally:
            tunnel.run_root, tunnel.have = saved
            import importlib
            importlib.reload(tunnel)
            server.close()
            os.unlink(path)

    def test_refusal_is_explained(self):
        p = premium.Premium()
        p.set_key(fixtures.premium_key(api_key="bad-key-000000"))
        with self.assertRaises(premium.PremiumError) as caught:
            p.account(refresh=True)
        self.assertIn("rejected the key", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
