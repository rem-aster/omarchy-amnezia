"""Amnezia's API gateway, spoken the way the app's GatewayController does.

Every call is a POST of

    {"key_payload": base64(RSA-PKCS1(json{aes_key, aes_iv, aes_salt})),
     "api_payload": base64(AES-256-CBC(json(request), aes_key, aes_iv[:16]))}

and the answer body is the reply JSON under the same AES key. The RSA key is
the gateway's public key, which every AmneziaVPN build carries; it is in
gateway-public.pem byte for byte, because the encrypted lists of fallback
gateways are keyed on SHA-512 of exactly those bytes.

The main gateway is tried first. When it does not answer, or answers with
something that does not decrypt (a block page, a dead host), the request is
retried through the fallback gateways the app would use: lists of them are
published, encrypted, on two public buckets. The last working fallback is
remembered so the next call does not have to walk the list again.
"""

import base64
import hashlib
import json
import os
import random
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import crypto

MAIN_GATEWAY = os.environ.get("OMARCHY_AMNEZIA_GATEWAY", "http://gw.amnezia.org:80/")
# Where the app looks for fallback gateways when the main one is blocked
# (PROD_S3_ENDPOINT / FALLBACK_S3_ENDPOINT in its build).
PROXY_LIST_BASES = (
    "https://s3.eu-north-1.amazonaws.com/amnezia/",
    "https://storage.googleapis.com/lambda-list/",
)
PEM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gateway-public.pem")

REQUEST_TIMEOUT = 15
LIST_TIMEOUT = 6
HEALTH_TIMEOUT = 2
# The whole call, every gateway and list included. Each request gets what is
# left of it as its own timeout, and the call itself runs on a thread that is
# abandoned at the deadline, because a name lookup has no timeout of its own
# and can hang on a network that is not back yet after a wake-up.
CALL_DEADLINE = 45
MAX_ANSWER_BYTES = 4 * 1024 * 1024
PROXY_CACHE_TTL = 6 * 3600

# Answers that are the API's own word even though they are errors: these go
# back to the caller instead of being retried elsewhere (shouldBypassProxy).
_FINAL_404 = ("No active configuration found for", "No non-revoked public key found for",
              "Account not found.", "QR session not found", "Session not found")
_FINAL_501 = "client version update is required"
_SUBSCRIPTION_422 = "Failed to retrieve subscription information. Is it activated?"


class GatewayError(Exception):
    """A call that did not produce a usable answer. `status` is the API's
    http_status when there was one, `message` its message."""

    def __init__(self, text, status=0, message="", body=None):
        super().__init__(text)
        self.status = status
        self.message = message
        self.body = body or {}


def _read_pem():
    with open(PEM_PATH, "rb") as handle:
        return handle.read()


def _b64(data):
    return base64.b64encode(data).decode("ascii")


class _OutOfTime(Exception):
    pass


class Gateway:
    def __init__(self, cache_path=None, log=None):
        self.cache_path = cache_path
        self.log = log or (lambda message: None)
        self.pem = _read_pem()
        self.public_key = crypto.load_rsa_public_key(self.pem.decode("ascii"))
        self._ssl = ssl.create_default_context()
        self._deadline = None

    def _budget(self, limit):
        """`limit`, or what is left of the call's deadline if that is less."""
        if self._deadline is None:
            return limit
        left = self._deadline - time.monotonic()
        if left < 0.5:
            raise _OutOfTime()
        return min(limit, left)

    # -------------------------------------------------------------- transport

    def _http(self, url, body=None, timeout=REQUEST_TIMEOUT, headers=None):
        request = urllib.request.Request(url, data=body, method="POST" if body is not None else "GET")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=self._ssl))
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.status, response.read(MAX_ANSWER_BYTES + 1)
        except urllib.error.HTTPError as error:
            # The gateway answers API errors with an HTTP error code and the
            # same encrypted body, so the body still matters.
            try:
                return error.code, error.read(MAX_ANSWER_BYTES + 1)
            except OSError:
                return error.code, b""

    def _encrypt_request(self, payload):
        key, iv, salt = os.urandom(32), os.urandom(32), os.urandom(8)
        key_payload = json.dumps({"aes_key": _b64(key), "aes_iv": _b64(iv), "aes_salt": _b64(salt)}).encode()
        api_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        body = json.dumps({
            "key_payload": _b64(crypto.rsa_pkcs1_encrypt(key_payload, self.public_key)),
            "api_payload": _b64(crypto.aes256_cbc_encrypt(api_payload, key, iv)),
        }).encode()
        return body, key, iv

    def _try(self, base, path, body, key, iv):
        """(answer dict, final?) from one gateway, or (None, False) when the
        next gateway should be tried."""
        url = base.rstrip("/") + "/" + path.lstrip("/")
        headers = {"Content-Type": "application/json", "X-Client-Request-ID": str(uuid.uuid4())}
        try:
            code, raw = self._http(url, body, self._budget(REQUEST_TIMEOUT), headers)
        except (OSError, ValueError) as error:
            self.log("gateway %s: %s" % (base, error))
            return None, False
        if not raw or len(raw) > MAX_ANSWER_BYTES:
            self.log("gateway %s: HTTP %s with %d bytes" % (base, code, len(raw or b"")))
            return None, False
        try:
            plain = crypto.aes256_cbc_decrypt(raw, key, iv)
        except ValueError:
            self.log("gateway %s: HTTP %s, answer does not decrypt" % (base, code))
            return None, False
        if b"html" in plain:
            return None, False
        try:
            answer = json.loads(plain.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None, False
        if not isinstance(answer, dict):
            return None, False
        status = answer.get("http_status")
        status = status if isinstance(status, int) else (code if code >= 300 else 200)
        message = str(answer.get("message") or answer.get("error") or "").strip()
        answer["__status"] = status
        answer["__message"] = message
        if status < 300 or status in (401, 403, 408, 409, 402, 429):
            return answer, True
        if status == 404:
            return answer, any(p in plain.decode("utf-8", "replace") for p in _FINAL_404)
        if status == 501:
            return answer, _FINAL_501 in message
        if status == 422:
            return answer, message == _SUBSCRIPTION_422
        return answer, False

    # ------------------------------------------------------------ fallbacks

    def _cache(self):
        if not self.cache_path:
            return {}
        try:
            with open(self.cache_path) as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_cache(self, value):
        if not self.cache_path:
            return
        tmp = self.cache_path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as handle:
                json.dump(value, handle)
            os.replace(tmp, self.cache_path)
        except OSError:
            pass

    def _decrypt_list(self, raw):
        digest = hashlib.sha512(self.pem).hexdigest()
        key, iv = bytes.fromhex(digest[:64]), bytes.fromhex(digest[64:96])
        plain = crypto.aes256_cbc_decrypt(base64.b64decode(b"".join(raw.split())), key, iv)
        urls = json.loads(plain.decode("utf-8"))
        if not isinstance(urls, list):
            raise ValueError("not a list")
        # The list key comes from a public file, so a list proves nothing;
        # only plain https gateways are taken, and the answer's decryption is
        # what proves a gateway genuine.
        return [u for u in urls if isinstance(u, str) and u.startswith("https://") and len(u) < 300][:32]

    def proxy_urls(self, service_type, user_country):
        cache = self._cache()
        entry = cache.get("lists", {}).get("%s/%s" % (service_type, user_country))
        if entry and time.time() - entry.get("at", 0) < PROXY_CACHE_TTL and entry.get("urls"):
            return list(entry["urls"])
        name = "endpoints-%s-%s" % (service_type, user_country)
        named = base64.urlsafe_b64encode(name.encode()).decode().rstrip("=") + ".json"
        candidates = []
        for base in PROXY_LIST_BASES:
            if service_type:
                candidates.append(base + named)
        for base in PROXY_LIST_BASES:
            candidates.append(base + "endpoints.json")
        for url in candidates:
            try:
                code, raw = self._http(url, None, self._budget(LIST_TIMEOUT))
                if code != 200:
                    continue
                urls = self._decrypt_list(raw)
            except (OSError, ValueError):
                continue
            if urls:
                cache.setdefault("lists", {})["%s/%s" % (service_type, user_country)] = {"at": time.time(), "urls": urls}
                self._save_cache(cache)
                return urls
        return list(entry["urls"]) if entry and entry.get("urls") else []

    def _healthy(self, base):
        try:
            code, _ = self._http(base.rstrip("/") + "/lmbd-health", None, self._budget(HEALTH_TIMEOUT))
        except (OSError, ValueError):
            return False
        return 200 <= code < 300

    # ------------------------------------------------------------------ call

    def post(self, path, payload):
        """The decrypted answer of `path` (e.g. "v1/config"). Raises
        GatewayError when no gateway gave a usable answer in time, or when the
        API refused the request (status >= 300)."""
        outcome = {}

        def work():
            try:
                outcome["answer"] = self._post(path, payload)
            except BaseException as error:  # handed to the caller below
                outcome["error"] = error

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(CALL_DEADLINE + 5)
        if worker.is_alive():
            self.log("gateway: %s still hanging after %ds, abandoned" % (path, CALL_DEADLINE + 5))
            raise GatewayError("Amnezia's gateway did not answer in time — check the connection, "
                               "or try again in a minute")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["answer"]

    def _post(self, path, payload):
        body, key, iv = self._encrypt_request(payload)
        self._deadline = time.monotonic() + CALL_DEADLINE
        last = None
        try:
            answer, final = self._try(MAIN_GATEWAY, path, body, key, iv)
            last = answer
            if not final:
                cache = self._cache()
                service = str(payload.get("service_type") or "")
                country = str(payload.get("user_country_code") or "")
                tried = set()
                order = []
                if cache.get("working"):
                    order.append(cache["working"])
                urls = self.proxy_urls(service, country)
                random.shuffle(urls)
                order += urls
                for base in order:
                    if base in tried:
                        continue
                    tried.add(base)
                    # The app's own shortcut: a fallback that does not answer
                    # its health check within a moment is not worth a request.
                    if base != cache.get("working") and not self._healthy(base):
                        continue
                    answer, final = self._try(base, path, body, key, iv)
                    if answer is not None:
                        last = answer
                    if final:
                        if cache.get("working") != base:
                            cache = self._cache()
                            cache["working"] = base
                            self._save_cache(cache)
                        break
        except _OutOfTime:
            self.log("gateway: gave up on %s after %ds" % (path, CALL_DEADLINE))
        finally:
            self._deadline = None
        if last is None:
            raise GatewayError("no Amnezia gateway answered — check the connection, or try again in a minute")
        status = last.pop("__status", 200)
        message = last.pop("__message", "")
        if status >= 300:
            raise GatewayError(explain(status, message), status, message, last)
        return last


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # A gateway that redirects is not the gateway; never follow it.
    def redirect_request(self, *args, **kwargs):
        return None


def explain(status, message):
    """The API's refusal in words a person can act on (apiUtils mapping)."""
    lowered = message.lower()
    if status in (401, 403):
        return "Amnezia rejected the key%s — it was revoked, or the subscription moved to another key" % (
            " (" + message.rstrip(".") + ")" if message else "")
    if status == 409:
        return ("all device slots of the subscription are taken — free one under Devices "
                "(or in the Amnezia app) and try again")
    if status == 404:
        if "account not found" in lowered:
            return "Amnezia does not know this key — it was revoked or mistyped"
        return "Amnezia has no active config for this device — get a new one"
    if status == 422:
        if message == _SUBSCRIPTION_422:
            return "the subscription is not active — expired or not paid yet"
        return "Amnezia did not issue a config" + (": " + message if message else "")
    if status == 429:
        return "Amnezia asks to slow down (too many requests) — try again in a few minutes"
    if status == 402:
        return "Amnezia asks for a captcha or says the subscription is not active — open the Amnezia app once"
    if status == 501:
        return "Amnezia asks for a newer client version — update the plugin"
    if status == 408:
        return "Amnezia did not answer in time — try again"
    return "Amnezia refused the request (HTTP %s)%s" % (status, ": " + message if message else "")
