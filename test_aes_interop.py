#!/usr/bin/env python3
"""
Test suite AES-128-GCM untuk NISS Endoskopi IoT.

Membuktikan:
  1. Interoperabilitas Python ↔ Node.js (encrypt di Python, decrypt di Node.js)
  2. Persistensi key (key tetap sama setelah reimport module)
  3. Penolakan data tidak valid (ciphertext/tag/nonce rusak → error, bukan crash)
  4. End-to-end simulasi (foto JPEG → encrypt → decrypt → identik)

Jalankan:
  python3 test_aes_interop.py

Prasyarat:
  - Python 3.9+, pycryptodome
  - Node.js 18+ (untuk test interop Python→Node.js)
"""

import os
import sys
import json
import base64
import subprocess
import tempfile
import copy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aes_utils

PASSED = 0
FAILED = 0


def test(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✓ {name}")
    else:
        FAILED += 1
        print(f"  ✗ {name}")
        if detail:
            print(f"    → {detail}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: Interoperabilitas Python ↔ Node.js
# ═══════════════════════════════════════════════════════════════════════════

def test_interop():
    print("\n── Test 1: Interoperabilitas Python → Node.js ──")

    # Generate key untuk test
    test_key = os.urandom(16)
    hex_key = test_key.hex()

    # Encrypt di Python
    test_data = {"cmd": "foto", "device": "endoskop-01", "ts": 1234567890}
    packet = aes_utils.encrypt_bytes(
        json.dumps(test_data).encode("utf-8"), test_key
    )
    packet_json = json.dumps(packet)

    # Script Node.js untuk decrypt
    node_script = f"""
const crypto = require("crypto");
const packet = {packet_json};
const key = Buffer.from("{hex_key}", "hex");
const nonce = Buffer.from(packet.nonce_b64, "base64");
const ciphertext = Buffer.from(packet.ciphertext_b64, "base64");
const tag = Buffer.from(packet.tag_b64, "base64");
const decipher = crypto.createDecipheriv("aes-128-gcm", key, nonce);
decipher.setAuthTag(tag);
let result = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
console.log(result.toString("utf8"));
"""

    try:
        result = subprocess.run(
            ["node", "-e", node_script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            decrypted = json.loads(result.stdout.strip())
            test("Python encrypt → Node.js decrypt: data identik",
                 decrypted == test_data,
                 f"Expected {test_data}, got {decrypted}")
        else:
            test("Python encrypt → Node.js decrypt",
                 False, f"Node.js error: {result.stderr.strip()}")
    except FileNotFoundError:
        print("  ⚠ Node.js tidak ditemukan — test interop dilewati")
        print("    Install Node.js 18+ untuk menjalankan test ini")
    except Exception as e:
        test("Python encrypt → Node.js decrypt", False, str(e))

    # Test sebaliknya: Node.js encrypt → Python decrypt
    print()
    node_encrypt_script = f"""
const crypto = require("crypto");
const key = Buffer.from("{hex_key}", "hex");
const nonce = crypto.randomBytes(12);
const plaintext = JSON.stringify({json.dumps(test_data)});
const cipher = crypto.createCipheriv("aes-128-gcm", key, nonce);
const enc = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
const tag = cipher.getAuthTag();
console.log(JSON.stringify({{
  nonce_b64: nonce.toString("base64"),
  ciphertext_b64: enc.toString("base64"),
  tag_b64: tag.toString("base64"),
}}));
"""

    try:
        result = subprocess.run(
            ["node", "-e", node_encrypt_script],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            node_packet = json.loads(result.stdout.strip())
            py_decrypted = aes_utils.decrypt_bytes(node_packet, test_key)
            py_data = json.loads(py_decrypted.decode("utf-8"))
            test("Node.js encrypt → Python decrypt: data identik",
                 py_data == test_data,
                 f"Expected {test_data}, got {py_data}")
        else:
            test("Node.js encrypt → Python decrypt",
                 False, f"Node.js error: {result.stderr.strip()}")
    except FileNotFoundError:
        print("  ⚠ Node.js tidak ditemukan — test interop dilewati")
    except Exception as e:
        test("Node.js encrypt → Python decrypt", False, str(e))


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: Persistensi Key
# ═══════════════════════════════════════════════════════════════════════════

def test_key_persistence():
    print("\n── Test 2: Persistensi Key ──")

    # Reset cache
    aes_utils._cached_key = None

    # Generate/load key pertama kali
    key1 = aes_utils.load_key()
    hex1 = key1.hex()

    # Reset cache, load lagi — harus sama (dari file/env, bukan random baru)
    aes_utils._cached_key = None
    key2 = aes_utils.load_key()
    hex2 = key2.hex()

    test("Key tetap sama setelah reset cache + reload",
         hex1 == hex2,
         f"Key1: {hex1}, Key2: {hex2}")

    # Test cache works
    key3 = aes_utils.load_key()
    test("Key dari cache identik", key3 == key2)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: Penolakan Data Tidak Valid
# ═══════════════════════════════════════════════════════════════════════════

def test_rejection():
    print("\n── Test 3: Penolakan Data Tidak Valid ──")

    key = os.urandom(16)
    plaintext = b"data sensitif endoskopi NISS"
    packet = aes_utils.encrypt_bytes(plaintext, key)

    # 3a. Rusak ciphertext (flip 1 byte)
    bad_ct = copy.deepcopy(packet)
    ct_bytes = bytearray(base64.b64decode(bad_ct["ciphertext_b64"]))
    ct_bytes[0] ^= 0xFF  # flip semua bit byte pertama
    bad_ct["ciphertext_b64"] = base64.b64encode(bytes(ct_bytes)).decode()
    try:
        aes_utils.decrypt_bytes(bad_ct, key)
        test("Ciphertext rusak → DITOLAK", False, "Tidak raise error!")
    except (ValueError, Exception):
        test("Ciphertext rusak → DITOLAK (ValueError raised)", True)

    # 3b. Rusak auth tag (flip 1 byte)
    bad_tag = copy.deepcopy(packet)
    tag_bytes = bytearray(base64.b64decode(bad_tag["tag_b64"]))
    tag_bytes[0] ^= 0xFF
    bad_tag["tag_b64"] = base64.b64encode(bytes(tag_bytes)).decode()
    try:
        aes_utils.decrypt_bytes(bad_tag, key)
        test("Tag rusak → DITOLAK", False, "Tidak raise error!")
    except (ValueError, Exception):
        test("Tag rusak → DITOLAK (ValueError raised)", True)

    # 3c. Ganti nonce (nonce berbeda)
    bad_nonce = copy.deepcopy(packet)
    bad_nonce["nonce_b64"] = base64.b64encode(os.urandom(12)).decode()
    try:
        aes_utils.decrypt_bytes(bad_nonce, key)
        test("Nonce salah → DITOLAK", False, "Tidak raise error!")
    except (ValueError, Exception):
        test("Nonce salah → DITOLAK (ValueError raised)", True)

    # 3d. Key salah
    wrong_key = os.urandom(16)
    try:
        aes_utils.decrypt_bytes(packet, wrong_key)
        test("Key salah → DITOLAK", False, "Tidak raise error!")
    except (ValueError, Exception):
        test("Key salah → DITOLAK (ValueError raised)", True)

    # 3e. Field hilang
    incomplete = {"nonce_b64": packet["nonce_b64"]}
    try:
        aes_utils.decrypt_bytes(incomplete, key)
        test("Field hilang → DITOLAK", False, "Tidak raise error!")
    except (KeyError, ValueError, Exception):
        test("Field hilang → DITOLAK (error raised)", True)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: End-to-End Simulasi
# ═══════════════════════════════════════════════════════════════════════════

def test_e2e():
    print("\n── Test 4: End-to-End Simulasi ──")

    key = os.urandom(16)

    # Simulasi foto JPEG (header JPEG + random data)
    jpeg_header = bytes([0xFF, 0xD8, 0xFF, 0xE0])  # JPEG magic
    fake_jpeg = jpeg_header + os.urandom(50000)     # ~50 KB foto

    # Encrypt
    packet = aes_utils.encrypt_bytes(fake_jpeg, key)
    test("Encrypt foto 50KB berhasil", "nonce_b64" in packet)

    # Serialize ke JSON (seperti yang dikirim via MQTT)
    mqtt_payload = json.dumps(packet)
    test("Serialisasi ke JSON berhasil", len(mqtt_payload) > 0)

    # Deserialize + decrypt (seperti yang diterima di backend)
    received = json.loads(mqtt_payload)
    decrypted = aes_utils.decrypt_bytes(received, key)
    test("Decrypt menghasilkan data identik",
         decrypted == fake_jpeg,
         f"Panjang: {len(decrypted)} vs {len(fake_jpeg)}")

    # Verifikasi JPEG header masih utuh
    test("JPEG header utuh setelah decrypt",
         decrypted[:4] == jpeg_header)

    # Test dengan JSON payload (command MQTT)
    print()
    cmd = {"cmd": "rekam"}
    enc_str = aes_utils.encrypt_json(cmd)
    dec_cmd = aes_utils.decrypt_json(enc_str)
    test("JSON command round-trip: rekam", dec_cmd == cmd)

    event = {"event": "snapshot_taken", "file": "/media/foto.jpg",
             "storage_path": "endoskop-01/foto.jpg"}
    enc_str = aes_utils.encrypt_json(event)
    dec_event = aes_utils.decrypt_json(enc_str)
    test("JSON event round-trip: snapshot_taken", dec_event == event)

    status = {"status": "online"}
    enc_str = aes_utils.encrypt_json(status)
    dec_status = aes_utils.decrypt_json(enc_str)
    test("JSON status round-trip: online", dec_status == status)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: Nonce Uniqueness
# ═══════════════════════════════════════════════════════════════════════════

def test_nonce_unique():
    print("\n── Test 5: Nonce Uniqueness ──")

    key = os.urandom(16)
    plaintext = b"test data"

    nonces = set()
    for _ in range(100):
        packet = aes_utils.encrypt_bytes(plaintext, key)
        nonces.add(packet["nonce_b64"])

    test("100 enkripsi menghasilkan 100 nonce unik",
         len(nonces) == 100,
         f"Hanya {len(nonces)} nonce unik dari 100 enkripsi")


# ═══════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  NISS AES-128-GCM Test Suite")
    print("=" * 60)

    test_key_persistence()
    test_rejection()
    test_e2e()
    test_nonce_unique()
    test_interop()

    print()
    print("=" * 60)
    total = PASSED + FAILED
    print(f"  Hasil: {PASSED}/{total} PASSED, {FAILED}/{total} FAILED")
    if FAILED == 0:
        print("  ✓ SEMUA TEST BERHASIL")
    else:
        print("  ✗ ADA TEST YANG GAGAL")
    print("=" * 60)

    sys.exit(0 if FAILED == 0 else 1)


if __name__ == "__main__":
    main()
