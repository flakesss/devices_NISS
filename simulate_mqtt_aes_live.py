#!/usr/bin/env python3
"""
Simulasi Live CLI — Enkripsi MQTT AES-128-GCM untuk NISS Endoskopi IoT.

Script ini mensimulasikan komunikasi langsung antara Raspberry Pi 4 (devices_NISS)
dan Node.js Backend (backend_NISS) secara real-time di terminal, memperlihatkan
pembentukan paket {nonce_b64, ciphertext_b64, tag_b64}, dekripsi sukses,
serta penolakan otomatis terhadap serangan manipulasi data (Man-in-the-Middle).

Jalankan di Terminal / PowerShell:
  python3 simulate_mqtt_aes_live.py
"""

import os
import sys
import time
import json
import base64
import copy

# Import modul utama aes_utils
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aes_utils

# ANSI Colors untuk output terminal yang indah
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'

def print_step(title):
    print(f"\n{Colors.BOLD}{Colors.CYAN}=== {title} ==={Colors.RESET}")

def print_pkt(packet):
    print(f"  {Colors.YELLOW}► Nonce (12B)     :{Colors.RESET} {packet['nonce_b64']}")
    print(f"  {Colors.BLUE}► Ciphertext      :{Colors.RESET} {packet['ciphertext_b64'][:40]}...")
    print(f"  {Colors.RED}► Auth Tag (16B)  :{Colors.RESET} {packet['tag_b64']}")

def main():
    print(f"{Colors.BOLD}{Colors.HEADER}" + "═"*68)
    print("   SIMULASI LIVE ENKRIPSI AES-128-GCM — NISS ENDOSKOPI IOT")
    print("═"*68 + f"{Colors.RESET}")
    
    # 1. Load Key
    print_step("1. SINKRONISASI KUNCI AES-128 PERSISTEN")
    key = aes_utils.load_key()
    print(f"  {Colors.GREEN}✓ Key Aktif (16 byte/128-bit) :{Colors.RESET} {key.hex()}")
    print(f"  {Colors.GREEN}✓ Status Sinkronisasi         :{Colors.RESET} Identik antara Device & Backend")
    time.sleep(1)

    # 2. Simulasi Status Online
    print_step("2. DEVICE CONNECT → MENGIRIM STATUS ONLINE TERENKRIPSI")
    status_payload = {"status": "online", "device": "endoskop-01", "ts": int(time.time())}
    print(f"  {Colors.BOLD}Plaintext (JSON):{Colors.RESET} {status_payload}")
    
    enc_status_str = aes_utils.encrypt_json(status_payload)
    pkt_status = json.loads(enc_status_str)
    print(f"  {Colors.BOLD}Mengenkripsi dengan AES-128-GCM (Nonce Random 12B)...{Colors.RESET}")
    print_pkt(pkt_status)
    print(f"  {Colors.GREEN}► Publish MQTT ke Topic:{Colors.RESET} endoskop/endoskop-01/status (QoS 1, Retain)")
    
    time.sleep(1)
    print(f"  {Colors.BOLD}[Backend Node.js Menerima Pesan]{Colors.RESET}")
    dec_status = aes_utils.decrypt_json(enc_status_str)
    print(f"  {Colors.GREEN}✓ Verifikasi Auth Tag BERHASIL! Dekripsi OK:{Colors.RESET} {dec_status}")
    time.sleep(1.2)

    # 3. Simulasi Backend Mengirim Command
    print_step("3. BACKEND MENGIRIM PERINTAH KAMERA KE DEVICE (COMMAND)")
    cmd_payload = {"cmd": "foto"}
    print(f"  {Colors.BOLD}Backend memicu perintah (Plaintext):{Colors.RESET} {cmd_payload}")
    
    enc_cmd_str = aes_utils.encrypt_json(cmd_payload)
    pkt_cmd = json.loads(enc_cmd_str)
    print(f"  {Colors.BOLD}Mengenkripsi perintah dengan kunci yang sama...{Colors.RESET}")
    print_pkt(pkt_cmd)
    print(f"  {Colors.GREEN}► Publish MQTT ke Topic:{Colors.RESET} endoskop/endoskop-01/command")
    
    time.sleep(1)
    print(f"  {Colors.BOLD}[Device Pi 4 Menerima Pesan]{Colors.RESET}")
    dec_cmd = aes_utils.decrypt_json(enc_cmd_str)
    print(f"  {Colors.GREEN}✓ Verifikasi Auth Tag BERHASIL! Perintah didekripsi:{Colors.RESET} {dec_cmd}")
    print(f"  {Colors.BOLD}► Eksekusi Kamera:{Colors.RESET} Mengambil foto frame endoskopi...")
    time.sleep(1.2)

    # 4. Simulasi Device Mengirim Event Snapshot
    print_step("4. DEVICE MENGIRIM EVENT HASIL FOTO KE BACKEND")
    event_payload = {
        "event": "snapshot_taken",
        "file": "/media/endoskop_01.jpg",
        "storage_path": "endoskop-01/endoskop_01.jpg",
        "integrity": "verified"
    }
    print(f"  {Colors.BOLD}Plaintext Event:{Colors.RESET} {event_payload}")
    enc_event_str = aes_utils.encrypt_json(event_payload)
    pkt_event = json.loads(enc_event_str)
    print_pkt(pkt_event)
    print(f"  {Colors.GREEN}► Publish MQTT ke Topic:{Colors.RESET} endoskop/endoskop-01/event")
    
    time.sleep(1)
    dec_event = aes_utils.decrypt_json(enc_event_str)
    print(f"  {Colors.GREEN}✓ Backend mendekripsi event & mencatat metadata ke Supabase DB!{Colors.RESET}")
    time.sleep(1.2)

    # 5. Simulasi Serangan Tampering (Man-in-the-Middle Attack)
    print_step("5. SIMULASI SERANGAN MANIPULASI DATA (TAMPERING ATTACK)")
    print(f"  {Colors.BOLD}Skenario:{Colors.RESET} Penyerang (Man-in-the-Middle) menyusup ke jaringan dan merusak 2 byte Ciphertext di tengah perjalanan MQTT.")
    
    bad_pkt = copy.deepcopy(pkt_event)
    ct_bytes = bytearray(base64.b64decode(bad_pkt["ciphertext_b64"]))
    ct_bytes[0] ^= 0xFF  # Flip bit pada byte pertama
    ct_bytes[1] ^= 0xFF
    bad_pkt["ciphertext_b64"] = base64.b64encode(bytes(ct_bytes)).decode()
    
    print(f"  {Colors.RED}► Paket Dirusak oleh Penyerang:{Colors.RESET} {bad_pkt['ciphertext_b64'][:40]}...")
    print(f"  {Colors.BOLD}[Backend Node.js Menerima Paket yang Dirusak...]{Colors.RESET}")
    time.sleep(1)
    
    try:
        aes_utils.decrypt_bytes(bad_pkt, key)
        print(f"  {Colors.RED}✗ GAGAL: Paket rusak malah lolos!{Colors.RESET}")
    except ValueError as e:
        print(f"  {Colors.RED}{Colors.BOLD}🚨 [SECURITY ALERT] Dekripsi/autentikasi GAGAL!{Colors.RESET}")
        print(f"  {Colors.RED}   Detail Error:{Colors.RESET} {e}")
        print(f"  {Colors.GREEN}✓ SISTEM AMAN:{Colors.RESET} GCM Auth Tag menolak data yang telah dimanipulasi! Pesan diabaikan.")

    print(f"\n{Colors.BOLD}{Colors.HEADER}" + "═"*68)
    print("   SIMULASI SELESAI — SELURUH ALUR KERJA TERBUKTI AMAN & BERFUNGSI")
    print("═"*68 + f"{Colors.RESET}\n")

if __name__ == "__main__":
    main()
