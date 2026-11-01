"""The three primitives Amnezia's gateway needs, in plain Python.

The plugin has to run on a fresh Omarchy box without pulling a crypto
package in, and the amounts involved are tiny (a request is a few hundred
bytes, an answer a few kilobytes), so speed does not matter here:

  * AES-256-CBC with PKCS#7 padding — the request and answer bodies;
  * RSA PKCS#1 v1.5 encryption — wraps the per-request AES key for the
    gateway's public key;
  * X25519 — the WireGuard/AmneziaWG key pair the app would make with
    `wg genkey`, generated here so no tunnel tool is needed to get a config.

Each is checked against published test vectors in tests/test_crypto.py.
"""

import base64
import os

# ------------------------------------------------------------------ AES


def _xtime(a):
    a <<= 1
    return (a ^ 0x11B) if a & 0x100 else a


def _gmul(a, b):
    result = 0
    while b:
        if b & 1:
            result ^= a
        a = _xtime(a)
        b >>= 1
    return result


def _build_sbox():
    # Multiplicative inverse in GF(2^8) followed by the affine map, walked with
    # the generator 3 so every element is visited once (FIPS-197 §5.1.1).
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        rot = lambda v, n: ((v << n) | (v >> (8 - n))) & 0xFF
        sbox[p] = q ^ rot(q, 1) ^ rot(q, 2) ^ rot(q, 3) ^ rot(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox


_SBOX = _build_sbox()
_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i
_M2 = [_gmul(i, 2) for i in range(256)]
_M3 = [_gmul(i, 3) for i in range(256)]
_M9 = [_gmul(i, 9) for i in range(256)]
_M11 = [_gmul(i, 11) for i in range(256)]
_M13 = [_gmul(i, 13) for i in range(256)]
_M14 = [_gmul(i, 14) for i in range(256)]


def _expand_key(key):
    if len(key) != 32:
        raise ValueError("AES-256 needs a 32-byte key")
    words = [list(key[i:i + 4]) for i in range(0, 32, 4)]
    rcon = 1
    for i in range(8, 60):
        temp = list(words[i - 1])
        if i % 8 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= rcon
            rcon = _xtime(rcon)
        elif i % 8 == 4:
            temp = [_SBOX[b] for b in temp]
        words.append([a ^ b for a, b in zip(words[i - 8], temp)])
    # Fifteen 16-byte round keys, column-major like the state.
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(15)]


def _encrypt_block(block, round_keys):
    s = [b ^ k for b, k in zip(block, round_keys[0])]
    for rnd in range(1, 15):
        # SubBytes + ShiftRows: row r of column c comes from column c + r.
        t = [_SBOX[s[(r + 4 * ((c + r) % 4))]] for c in range(4) for r in range(4)]
        if rnd != 14:
            mixed = []
            for c in range(4):
                a0, a1, a2, a3 = t[4 * c:4 * c + 4]
                mixed += [
                    _M2[a0] ^ _M3[a1] ^ a2 ^ a3,
                    a0 ^ _M2[a1] ^ _M3[a2] ^ a3,
                    a0 ^ a1 ^ _M2[a2] ^ _M3[a3],
                    _M3[a0] ^ a1 ^ a2 ^ _M2[a3],
                ]
            t = mixed
        key = round_keys[rnd]
        s = [b ^ k for b, k in zip(t, key)]
    return bytes(s)


def _decrypt_block(block, round_keys):
    s = [b ^ k for b, k in zip(block, round_keys[14])]
    for rnd in range(13, -1, -1):
        # InvShiftRows + InvSubBytes: row r of column c comes from column c - r.
        t = [_INV_SBOX[s[(r + 4 * ((c - r) % 4))]] for c in range(4) for r in range(4)]
        key = round_keys[rnd]
        t = [b ^ k for b, k in zip(t, key)]
        if rnd != 0:
            mixed = []
            for c in range(4):
                a0, a1, a2, a3 = t[4 * c:4 * c + 4]
                mixed += [
                    _M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3],
                    _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                    _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3],
                    _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3],
                ]
            t = mixed
        s = t
    return bytes(s)


def aes256_cbc_encrypt(plaintext, key, iv):
    """PKCS#7-padded AES-256-CBC. Only the first 16 bytes of `iv` are used,
    as OpenSSL does with the 32-byte IV the app generates."""
    round_keys = _expand_key(key)
    pad = 16 - len(plaintext) % 16
    data = plaintext + bytes([pad]) * pad
    previous = iv[:16]
    out = bytearray()
    for i in range(0, len(data), 16):
        block = bytes(a ^ b for a, b in zip(data[i:i + 16], previous))
        previous = _encrypt_block(block, round_keys)
        out += previous
    return bytes(out)


def aes256_cbc_decrypt(ciphertext, key, iv):
    """Raises ValueError when the ciphertext or its padding is malformed —
    which is how an answer that is not for this request shows up."""
    if not ciphertext or len(ciphertext) % 16:
        raise ValueError("ciphertext is not a whole number of AES blocks")
    round_keys = _expand_key(key)
    previous = iv[:16]
    out = bytearray()
    for i in range(0, len(ciphertext), 16):
        block = ciphertext[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, round_keys), previous))
        previous = block
    pad = out[-1]
    if pad < 1 or pad > 16 or out[-pad:] != bytes([pad]) * pad:
        raise ValueError("bad padding")
    return bytes(out[:-pad])


# ------------------------------------------------------------------ RSA


def _der_read(data, offset):
    """One DER element at `offset`: (tag, value bytes, offset after it)."""
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    return tag, data[offset:offset + length], offset + length


def load_rsa_public_key(pem_text):
    """(n, e) from a PEM SubjectPublicKeyInfo ("BEGIN PUBLIC KEY")."""
    lines = [line.strip() for line in pem_text.strip().splitlines()]
    body = "".join(line for line in lines if line and not line.startswith("-----"))
    der = base64.b64decode(body)
    tag, spki, _ = _der_read(der, 0)
    if tag != 0x30:
        raise ValueError("not a SubjectPublicKeyInfo")
    _, _, after_algorithm = _der_read(spki, 0)
    tag, bit_string, _ = _der_read(spki, after_algorithm)
    if tag != 0x03 or not bit_string or bit_string[0] != 0:
        raise ValueError("unexpected public key encoding")
    tag, rsa_key, _ = _der_read(bit_string[1:], 0)
    if tag != 0x30:
        raise ValueError("not an RSA public key")
    tag, modulus, offset = _der_read(rsa_key, 0)
    tag2, exponent, _ = _der_read(rsa_key, offset)
    if tag != 0x02 or tag2 != 0x02:
        raise ValueError("not an RSA public key")
    return int.from_bytes(modulus, "big"), int.from_bytes(exponent, "big")


def rsa_pkcs1_encrypt(message, public_key, randbytes=os.urandom):
    n, e = public_key
    k = (n.bit_length() + 7) // 8
    if len(message) > k - 11:
        raise ValueError("message too long for the RSA key")
    padding = bytearray()
    while len(padding) < k - 3 - len(message):
        padding += bytes(b for b in randbytes(k) if b != 0)
    padded = b"\x00\x02" + bytes(padding[:k - 3 - len(message)]) + b"\x00" + message
    return pow(int.from_bytes(padded, "big"), e, n).to_bytes(k, "big")


# ------------------------------------------------------------------ X25519

_P = 2 ** 255 - 19
_A24 = 121665


def x25519(scalar, u_point):
    """RFC 7748 §5, constant-time in shape only (this is a key generator
    run once per config, not a server)."""
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k_int = int.from_bytes(k, "little")
    x1 = int.from_bytes(u_point, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3 = 1, 0, x1, 1
    swap = 0
    for t in range(254, -1, -1):
        bit = (k_int >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) * (da + cb) % _P
        z3 = x1 * (da - cb) * (da - cb) % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def wireguard_keypair(randbytes=os.urandom):
    """(private, public) as the base64 strings `wg genkey | wg pubkey` print."""
    private = bytearray(randbytes(32))
    private[0] &= 248
    private[31] &= 127
    private[31] |= 64
    public = x25519(bytes(private), (9).to_bytes(32, "little"))
    return base64.b64encode(bytes(private)).decode(), base64.b64encode(public).decode()


def wireguard_public_key(private_b64):
    private = base64.b64decode(private_b64)
    return base64.b64encode(x25519(private, (9).to_bytes(32, "little"))).decode()
