"""
AES-128-GCM encryption/decryption untuk NISS Endoskopi IoT.

Digunakan untuk mengenkripsi payload MQTT antara Raspberry Pi 4 (device)
dan backend Node.js. Menjamin kerahasiaan (confidentiality) DAN
integritas/autentikasi (integrity/authentication) data dalam satu operasi
kriptografi — GCM auth tag menggantikan kebutuhan CRC32 terpisah.

Spesifikasi:
  - Algoritma : AES-128 (key 16 byte / 128 bit)
  - Mode      : GCM (Galois/Counter Mode) — AEAD
  - Nonce     : 12 byte (96 bit), sesuai NIST SP 800-38D
  - Auth Tag  : 16 byte (128 bit), default pycryptodome
  - Library   : pycryptodome (PyCryptodome)

Catatan Hardware:
  Raspberry Pi 4 (BCM2711, Cortex-A72) TIDAK memiliki hardware AES
  accelerator (ARMv8 Crypto Extensions tidak tersedia di chip ini).
  Semua operasi AES berjalan secara software-only. Untuk ukuran data
  kecil (8-150 KB, tipikal foto JPEG endoskopi dan payload CS), ini
  tidak menjadi bottleneck — waktu enkripsi/dekripsi di bawah 1 ms.

Key Management:
  Key HARUS persisten (identik antara device dan backend, tidak berubah
  saat restart). Dimuat dari:
    1. Environment variable NISS_AES_KEY (hex string, 32 karakter hex)
    2. File lokal aes_key.bin (16 byte raw)
  Jika keduanya tidak ada, key di-generate SEKALI lalu disimpan ke
  aes_key.bin. Hex key dicetak ke stdout agar bisa disalin ke .env backend.

Install:
  pip3 install pycryptodome
"""

import os
import sys
import json
import base64

from Crypto.Cipher import AES

# ── Konstanta ────────────────────────────────────────────────────────────────
_KEY_ENV_VAR = "NISS_AES_KEY"
_KEY_FILE = "aes_key.bin"
_KEY_LENGTH = 16        # 128 bit
_NONCE_LENGTH = 12      # 96 bit, standar NIST SP 800-38D untuk GCM
_TAG_LENGTH = 16        # 128 bit, default GCM

# ── Cache key di module-level ────────────────────────────────────────────────
_cached_key = None


def load_key(env_var=_KEY_ENV_VAR, key_file=_KEY_FILE):
    """Muat AES-128 key secara persisten.

    Urutan prioritas:
      1. Environment variable (hex string, 32 karakter)
      2. File lokal (16 byte raw binary)
      3. Generate baru → simpan ke file → cetak hex ke stdout

    Returns:
        bytes: Key AES-128 (16 byte)

    Raises:
        ValueError: Jika key dari env var bukan hex valid atau panjang salah
    """
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    # 1. Coba dari environment variable
    hex_key = os.environ.get(env_var, "").strip()
    if hex_key:
        try:
            key = bytes.fromhex(hex_key)
        except ValueError:
            raise ValueError(
                f"[AES] {env_var} bukan hex string valid. "
                f"Harus 32 karakter hex (16 byte). Diterima: '{hex_key}'"
            )
        if len(key) != _KEY_LENGTH:
            raise ValueError(
                f"[AES] {env_var} harus {_KEY_LENGTH} byte ({_KEY_LENGTH * 2} hex chars), "
                f"diterima {len(key)} byte."
            )
        _cached_key = key
        print(f"[AES] Key dimuat dari environment variable ${env_var}")
        return _cached_key

    # 2. Coba dari file lokal
    # Resolusi path relatif terhadap direktori script ini
    script_dir = os.path.dirname(os.path.abspath(__file__))
    key_path = os.path.join(script_dir, key_file)

    if os.path.isfile(key_path):
        with open(key_path, "rb") as f:
            key = f.read()
        if len(key) != _KEY_LENGTH:
            raise ValueError(
                f"[AES] File {key_path} berisi {len(key)} byte, "
                f"harus tepat {_KEY_LENGTH} byte."
            )
        _cached_key = key
        print(f"[AES] Key dimuat dari file: {key_path}")
        return _cached_key

    # 3. Generate key baru, simpan ke file
    key = os.urandom(_KEY_LENGTH)
    with open(key_path, "wb") as f:
        f.write(key)
    _cached_key = key

    hex_str = key.hex()
    print(f"[AES] Key baru di-generate dan disimpan ke: {key_path}")
    print(f"[AES] ════════════════════════════════════════════════════")
    print(f"[AES]  SALIN HEX KEY INI KE .env BACKEND (NISS_AES_KEY):")
    print(f"[AES]  {hex_str}")
    print(f"[AES] ════════════════════════════════════════════════════")
    print(f"[AES] Key HARUS identik di device dan backend!")

    return _cached_key


def encrypt_bytes(plaintext, key=None):
    """Enkripsi data dengan AES-128-GCM.

    Args:
        plaintext (bytes): Data yang akan dienkripsi.
        key (bytes, optional): AES key 16 byte. Default: key dari load_key().

    Returns:
        dict: {
            "nonce_b64":      str,  # base64-encoded nonce (12 byte)
            "ciphertext_b64": str,  # base64-encoded ciphertext
            "tag_b64":        str,  # base64-encoded auth tag (16 byte)
        }

    Raises:
        TypeError: Jika plaintext bukan bytes.
    """
    if key is None:
        key = load_key()
    if not isinstance(plaintext, bytes):
        raise TypeError(f"plaintext harus bytes, diterima {type(plaintext).__name__}")

    # Nonce 12 byte — HARUS unik per operasi enkripsi (JANGAN reuse!)
    nonce = os.urandom(_NONCE_LENGTH)

    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)

    return {
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "tag_b64": base64.b64encode(tag).decode("ascii"),
    }


def decrypt_bytes(packet, key=None):
    """Dekripsi data dari paket AES-128-GCM.

    Args:
        packet (dict): Paket terenkripsi dengan field nonce_b64,
                       ciphertext_b64, dan tag_b64.
        key (bytes, optional): AES key 16 byte. Default: key dari load_key().

    Returns:
        bytes: Data asli (plaintext).

    Raises:
        ValueError: Jika dekripsi gagal (auth tag tidak cocok, data dirusak,
                    atau key salah). TIDAK pernah mengembalikan data salah
                    secara diam-diam.
        KeyError: Jika field yang diperlukan tidak ada di packet.
    """
    if key is None:
        key = load_key()

    nonce = base64.b64decode(packet["nonce_b64"])
    ciphertext = base64.b64decode(packet["ciphertext_b64"])
    tag = base64.b64decode(packet["tag_b64"])

    if len(nonce) != _NONCE_LENGTH:
        raise ValueError(
            f"Nonce harus {_NONCE_LENGTH} byte, diterima {len(nonce)} byte"
        )

    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    try:
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except (ValueError, KeyError) as e:
        raise ValueError(
            f"Dekripsi AES-128-GCM gagal — data mungkin dirusak, "
            f"tag tidak cocok, atau key salah. Detail: {e}"
        ) from e

    return plaintext


# ── Helper untuk payload JSON (kasus umum: dict ↔ encrypted JSON string) ────

def encrypt_json(data):
    """Serialisasi dict ke JSON lalu enkripsi, return JSON string siap publish.

    Args:
        data (dict): Data yang akan dienkripsi.

    Returns:
        str: JSON string berisi {nonce_b64, ciphertext_b64, tag_b64}
    """
    plaintext = json.dumps(data, separators=(",", ":")).encode("utf-8")
    packet = encrypt_bytes(plaintext)
    return json.dumps(packet, separators=(",", ":"))


def decrypt_json(payload_str):
    """Dekripsi JSON payload string kembali ke dict asli.

    Args:
        payload_str (str): JSON string berisi {nonce_b64, ciphertext_b64, tag_b64}

    Returns:
        dict: Data asli yang sudah didekripsi dan di-parse dari JSON.

    Raises:
        ValueError: Jika dekripsi gagal atau JSON tidak valid.
        json.JSONDecodeError: Jika payload bukan JSON valid.
    """
    packet = json.loads(payload_str)
    plaintext = decrypt_bytes(packet)
    return json.loads(plaintext.decode("utf-8"))


# ── Helper untuk enkripsi/dekripsi file (foto/video) ─────────────────────────

def encrypt_file(filepath, key=None):
    """Baca file dari disk, enkripsi isi-nya, return paket + JSON bytes.

    Args:
        filepath (str): Path ke file yang akan dienkripsi.
        key (bytes, optional): AES key 16 byte. Default: key dari load_key().

    Returns:
        tuple: (packet_dict, json_bytes)
            - packet_dict: dict {nonce_b64, ciphertext_b64, tag_b64}
            - json_bytes: bytes JSON siap upload ke storage
    """
    with open(filepath, "rb") as f:
        raw = f.read()
    packet = encrypt_bytes(raw, key)
    json_bytes = json.dumps(packet, separators=(",", ":")).encode("utf-8")
    return packet, json_bytes


def decrypt_file(json_bytes, key=None):
    """Dekripsi JSON bytes (dari storage) kembali ke data file asli.

    Args:
        json_bytes (bytes): JSON bytes berisi {nonce_b64, ciphertext_b64, tag_b64}
        key (bytes, optional): AES key 16 byte. Default: key dari load_key().

    Returns:
        bytes: Data file asli (plaintext).

    Raises:
        ValueError: Jika dekripsi gagal.
    """
    packet = json.loads(json_bytes)
    return decrypt_bytes(packet, key)


# ── Entry point untuk generate/cetak key secara manual ───────────────────────
if __name__ == "__main__":
    print("=== NISS AES-128-GCM Key Utility ===\n")
    key = load_key()
    print(f"\nKey aktif (hex): {key.hex()}")
    print(f"Key length: {len(key)} byte ({len(key) * 8} bit)")

    # Quick self-test
    print("\n--- Self-test ---")
    test_data = {"cmd": "foto", "test": True, "angka": 42}
    encrypted = encrypt_json(test_data)
    print(f"Encrypted payload: {encrypted[:80]}...")
    decrypted = decrypt_json(encrypted)
    assert decrypted == test_data, "GAGAL: data tidak cocok!"
    print(f"Decrypted: {decrypted}")
    print("✓ Self-test BERHASIL — encrypt/decrypt round-trip OK")
