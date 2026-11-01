import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from amnezia_lib import crypto  # noqa: E402

PEM = os.path.join(os.path.dirname(__file__), "..", "scripts", "amnezia_lib", "gateway-public.pem")


class AesTest(unittest.TestCase):
    def test_fips197_vector(self):
        # FIPS-197 Appendix C.3.
        key = bytes(range(32))
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        cipher = crypto._encrypt_block(plain, crypto._expand_key(key))
        self.assertEqual(cipher.hex(), "8ea2b7ca516745bfeafc49904b496089")
        self.assertEqual(crypto._decrypt_block(cipher, crypto._expand_key(key)), plain)

    def test_sp800_38a_cbc(self):
        # NIST SP 800-38A F.2.5, first two blocks (no padding involved).
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172aae2d8a571e03ac9c9eb76fac45af8e51")
        cipher = crypto.aes256_cbc_encrypt(plain, key, iv)
        self.assertEqual(cipher[:32].hex(),
                         "f58c4c04d6e5f1ba779eabfb5f7bfbd69cfc4e967edb808d679f777bc6702c7d")

    def test_roundtrip_and_padding(self):
        key, iv = os.urandom(32), os.urandom(32)
        for size in (0, 1, 15, 16, 17, 1000):
            data = os.urandom(size)
            self.assertEqual(crypto.aes256_cbc_decrypt(crypto.aes256_cbc_encrypt(data, key, iv), key, iv), data)
        with self.assertRaises(ValueError):
            crypto.aes256_cbc_decrypt(b"x" * 16, key, iv)

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_matches_openssl(self):
        key, iv = os.urandom(32), os.urandom(32)
        data = b'{"hello": "world", "n": 12345}' * 7
        out = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-K", key.hex(), "-iv", iv[:16].hex()],
                             input=data, capture_output=True, check=True).stdout
        self.assertEqual(crypto.aes256_cbc_encrypt(data, key, iv), out)


class RsaTest(unittest.TestCase):
    def test_gateway_key_parses(self):
        with open(PEM) as handle:
            n, e = crypto.load_rsa_public_key(handle.read())
        self.assertEqual(n.bit_length(), 4096)
        self.assertEqual(e, 65537)

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_decrypts_with_openssl(self):
        with tempfile.TemporaryDirectory() as work:
            private = os.path.join(work, "k.pem")
            public = os.path.join(work, "p.pem")
            subprocess.run(["openssl", "genrsa", "-out", private, "2048"], capture_output=True, check=True)
            subprocess.run(["openssl", "rsa", "-in", private, "-pubout", "-out", public],
                           capture_output=True, check=True)
            with open(public) as handle:
                key = crypto.load_rsa_public_key(handle.read())
            message = b'{"aes_key": "abc", "aes_iv": "def", "aes_salt": "ghi"}'
            blob = crypto.rsa_pkcs1_encrypt(message, key)
            cipher_path = os.path.join(work, "c.bin")
            with open(cipher_path, "wb") as handle:
                handle.write(blob)
            out = subprocess.run(["openssl", "pkeyutl", "-decrypt", "-inkey", private, "-in", cipher_path,
                                  "-pkeyopt", "rsa_padding_mode:pkcs1"],
                                 capture_output=True, check=True).stdout
            self.assertEqual(out, message)


class X25519Test(unittest.TestCase):
    def test_rfc7748_vectors(self):
        scalar = bytes.fromhex("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
        u = bytes.fromhex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
        self.assertEqual(crypto.x25519(scalar, u).hex(),
                         "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552")
        alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        self.assertEqual(crypto.x25519(alice, (9).to_bytes(32, "little")).hex(),
                         "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")

    def test_keypair_shape(self):
        private, public = crypto.wireguard_keypair()
        self.assertEqual(len(base64.b64decode(private)), 32)
        self.assertEqual(crypto.wireguard_public_key(private), public)

    @unittest.skipUnless(shutil.which("wg"), "wireguard-tools not installed")
    def test_matches_wg(self):
        private, public = crypto.wireguard_keypair()
        out = subprocess.run(["wg", "pubkey"], input=private.encode(), capture_output=True, check=True)
        self.assertEqual(out.stdout.decode().strip(), public)


if __name__ == "__main__":
    unittest.main()
