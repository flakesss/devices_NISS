"""
Compressive Sensing codec untuk NISS — dipakai bersama oleh Pi (encode) dan
service cs-reconstruct (decode).

Diporting dari notebook eksperimen pembimbing
(simulasi_endoskop_CS_wavelet_ready.ipynb):
- Basis sparse: Wavelet 2D (default "haar"), dibangun lewat basis sintesis
  n x n (n = block_size^2) dari pywt.wavedec2/waverec2, mode "periodization"
  supaya jumlah koefisien wavelet = jumlah piksel (Psi tetap persegi).
- Sensing matrix: Bernoulli acak +-1/sqrt(M), seed tetap supaya Pi & service
  rekonstruksi menghasilkan matriks Phi yang identik tanpa perlu ditransfer
  lewat jaringan.
- Blok diproses sebagai vektor flatten (bukan per-baris tile) -- 1 blok NxN
  piksel diflatten jadi 1 vektor panjang n=N*N, sama seperti notebook.
- Rekonstruksi: Orthogonal Matching Pursuit (OMP), K = M // 4 (dibatasi
  1 <= K <= min(n, M-1)) -- lihat resolve_sparsity_k().

Encode-side (dipakai Pi) cuma butuh numpy -- basis wavelet cuma dipakai di
sisi rekonstruksi. Decode-side (dipakai cs-reconstruct, dan Pi utk fitur
"Foto via CS") butuh scipy/scikit-learn/pywavelets juga -- lihat
requirements.txt masing-masing service.
"""

import gzip
import multiprocessing
import os
import struct
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache

import cv2
import numpy as np

CS_SEED = 42
CS_BASIS = "wavelet"
CS_WAVELET_NAME = "haar"
CS_WAVELET_MODE = "periodization"
CS_BLOCK_SIZE = 8       # N -- ukuran sisi blok (flatten jadi vektor n=N*N)
CS_MR_PERCENT = 100     # measurement rate (%) default -- kualitas terbaik, penghematan dari kuantisasi int8+gzip
                        # (bukan dari mengurangi sampel), lihat catatan kuantisasi di bawah

# Kuantisasi ke int8 (bukan int16) -- separuh ukuran per sampel.
# Skala 50 dipilih karena nilai measurement Y = Phi @ block (Phi Bernoulli
# +-1/sqrt(M), block ternormalisasi [0,1]) secara empiris berkisar +-2.5,
# dan 127/2.5 ~= 50 -- skala lebih tinggi dari ini menyebabkan clipping
# (overflow int8) dan kualitas rekonstruksi turun drastis.
CS_QUANT_SCALE = 50

_MAGIC = b"NCS1"
_HEADER_FMT = ">4sHHHHHHB"   # magic, N, M, block_rows, block_cols, orig_w, orig_h, channels
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)

# ── Varian YCbCr: CS cuma di channel Y (luminance), Cb/Cr dikirim ringan ──
# lewat subsampling + JPEG -- warna hasil akhir tetap warna ASLI (bukan
# tebakan/colorization AI), cuma resolusi warnanya diturunkan -- sama
# prinsipnya dengan subsampling chroma 4:2:0 di JPEG biasa, karena mata
# manusia jauh kurang sensitif ke detail warna dibanding detail kecerahan.
CS_CHROMA_SCALE = 4          # turunkan resolusi Cb/Cr N kali (lebar & tinggi)
CS_CHROMA_JPEG_QUALITY = 80

_MAGIC_YCC = b"NCS2"
# magic, N, M, block_rows, block_cols, orig_w, orig_h, chroma_scale, cb_len, cr_len
_HEADER_YCC_FMT = ">4sHHHHHHBII"
_HEADER_YCC_SIZE = struct.calcsize(_HEADER_YCC_FMT)


# ───────────────────────── Encode-side (Pi, numpy only) ─────────────────────

def sensing_matrix(M, n, seed=CS_SEED):
    """Bernoulli +-1/sqrt(M), deterministik lewat seed tetap -- identik di kedua sisi."""
    rng = np.random.RandomState(seed)
    return (2 * rng.randint(0, 2, size=(M, n)) - 1).astype(np.float32) / np.sqrt(M)


def _pad_to_multiple(img, block):
    h, w = img.shape[:2]
    pad_h = (-h) % block
    pad_w = (-w) % block
    if pad_h or pad_w:
        img = np.pad(img, ((0, pad_h), (0, pad_w)) + ((0, 0),) * (img.ndim - 2), mode="edge")
    return img


def split_blocks_flat(channel_img, N):
    """channel_img: 2D array (H x W), sudah dipad ke kelipatan N. Iris jadi blok
    NxN row-major lalu flatten tiap blok jadi vektor panjang n=N*N.
    Return (blocks_flat: (n_blocks, n) array, rows, cols)."""
    h, w = channel_img.shape
    rows, cols = h // N, w // N
    blocks = []
    for r in range(rows):
        for c in range(cols):
            blocks.append(channel_img[r * N:(r + 1) * N, c * N:(c + 1) * N].reshape(-1))
    return np.stack(blocks, axis=0), rows, cols


def merge_blocks_flat(blocks_flat, rows, cols, N, orig_h, orig_w):
    """blocks_flat: (n_blocks, n) array, n=N*N. Balikkan tiap vektor ke blok
    NxN lalu susun row-major ke citra penuh, crop ke ukuran asli."""
    h, w = rows * N, cols * N
    out = np.zeros((h, w), dtype=blocks_flat.dtype)
    for i in range(blocks_flat.shape[0]):
        r, c = divmod(i, cols)
        out[r * N:(r + 1) * N, c * N:(c + 1) * N] = blocks_flat[i].reshape(N, N)
    return out[:orig_h, :orig_w]


def encode_frame(frame_rgb, N=CS_BLOCK_SIZE, mr_percent=CS_MR_PERCENT, seed=CS_SEED):
    """frame_rgb: HxWx3 uint8. Return payload biner (bytes): header + measurement
    Y terkuantisasi int8 + gzip, per channel per blok (blok diflatten, bukan per-baris)."""
    orig_h, orig_w = frame_rgb.shape[:2]
    n = N * N
    M = max(1, int(round(mr_percent / 100 * n)))
    Phi = sensing_matrix(M, n, seed)

    padded = _pad_to_multiple(frame_rgb, N)
    channels = padded.shape[2] if padded.ndim == 3 else 1
    if padded.ndim == 2:
        padded = padded[:, :, None]

    per_channel_Y = []
    rows = cols = None
    for ch in range(channels):
        blocks_flat, rows, cols = split_blocks_flat(padded[:, :, ch].astype(np.float32) / 255.0, N)
        # Satu matmul batched (bukan loop Python per-blok) -- OpenBLAS bisa
        # memparalelkan operasi ini lintas core, bukan cuma 1 thread.
        per_channel_Y.append(Phi @ blocks_flat.T)  # (M, n_blocks)

    stacked = np.concatenate(per_channel_Y, axis=1)  # (M, channels*n_blocks)
    # Kuantisasi float -> int8 (separuh ukuran int16, lihat CS_QUANT_SCALE di atas)
    quantized = np.clip(np.round(stacked * CS_QUANT_SCALE), -127, 127).astype(np.int8)
    raw = quantized.tobytes()
    compressed = gzip.compress(raw, compresslevel=6)

    header = struct.pack(_HEADER_FMT, _MAGIC, N, M, rows, cols, orig_w, orig_h, channels)
    return header + compressed


# ───────────────────────── Decode-side (cs-reconstruct, butuh scipy/sklearn/pywt) ─

@lru_cache(maxsize=8)
def build_wavelet_synthesis_basis(N, wavelet_name=CS_WAVELET_NAME, mode=CS_WAVELET_MODE):
    """Basis sintesis wavelet 2D Psi (n x n, n=N*N) -- port dari
    make_wavelet_synthesis_basis_2d() notebook pembimbing. Setiap kolom Psi
    adalah hasil inverse wavelet transform ketika satu koefisien wavelet
    dibuat bernilai 1 dan sisanya 0. Cache karena Psi konstan untuk (N, wavelet,
    mode) yang sama -- jangan dihitung ulang tiap request/tiap blok."""
    import pywt

    wavelet = pywt.Wavelet(wavelet_name)
    max_level = pywt.dwt_max_level(data_len=N, filter_len=wavelet.dec_len)
    if max_level < 1:
        raise ValueError(f"Wavelet '{wavelet_name}' terlalu panjang untuk blok {N}x{N}.")

    n = N * N
    zero_block = np.zeros((N, N), dtype=np.float64)
    template = pywt.wavedec2(zero_block, wavelet=wavelet, mode=mode, level=max_level)
    coeff_array, coeff_slices = pywt.coeffs_to_array(template)
    if coeff_array.size != n:
        raise ValueError(
            f"Jumlah koefisien wavelet ({coeff_array.size}) != jumlah piksel ({n}) -- "
            f"gunakan mode='periodization'."
        )

    Psi = np.zeros((n, n), dtype=np.float64)
    for j in range(n):
        coeffs_flat = np.zeros(n, dtype=np.float64)
        coeffs_flat[j] = 1.0
        coeffs = pywt.array_to_coeffs(coeffs_flat.reshape(coeff_array.shape), coeff_slices, output_format="wavedec2")
        basis_block = pywt.waverec2(coeffs, wavelet=wavelet, mode=mode)
        basis_block = basis_block[:N, :N]
        Psi[:, j] = basis_block.reshape(-1)

    return Psi.astype(np.float32)


def resolve_sparsity_k(M, n, requested_k=None):
    """Tentukan K untuk OMP -- port wavelet_resolve_sparsity_k() notebook.
    Default K = M // 4, dibatasi 1 <= K <= min(n, M-1)."""
    selected_k = max(1, M // 4) if requested_k is None else int(requested_k)
    max_k = min(n, max(1, M - 1))
    return min(selected_k, max_k) if selected_k > max_k else max(1, selected_k)


_executor = None


def _get_executor():
    """Pool proses persisten (bukan thread) untuk rekonstruksi OMP per-batch --
    OMP di sklearn tidak melepas GIL sepenuhnya, jadi cuma multiprocessing yang
    benar-benar memanfaatkan semua core CPU. Pakai spawn (bukan fork) supaya
    tidak mewarisi state proses induk (kamera/koneksi MQTT yang sedang terbuka)."""
    global _executor
    if _executor is None:
        ctx = multiprocessing.get_context("spawn")
        _executor = ProcessPoolExecutor(max_workers=os.cpu_count(), mp_context=ctx)
    return _executor


def _reconstruct_blocks_parallel(Y_blocks, Phi, Psi, K):
    """Y_blocks: (n_blocks_total, M) array -- tiap baris = 1 blok (measurement
    vector) independen. Dibagi jadi beberapa batch, tiap batch direkonstruksi
    di proses terpisah, tersebar ke semua core CPU yang tersedia. Batching di
    sini murni pengelompokan performa (banyak blok independen dalam 1
    panggilan OMP multi-target) -- tidak terikat geometri gambar, sama
    seperti pola orthogonal_mp(..., batch_size=...) di notebook."""
    n_blocks_total = Y_blocks.shape[0]
    ex = _get_executor()
    workers = os.cpu_count() or 1
    batch_size = max(1, -(-n_blocks_total // (workers * 2)))  # ceil(n / (workers*2))
    batches = [Y_blocks[i:i + batch_size] for i in range(0, n_blocks_total, batch_size)]
    results = list(ex.map(reconstruct, batches, [Phi] * len(batches), [Psi] * len(batches), [K] * len(batches)))
    return np.concatenate(results, axis=0)


def reconstruct(Y, Phi, Psi, K):
    """Y: (batch_n, M) -- tiap baris 1 blok (measurement vector) independen.
    Return (batch_n, n) blok hasil rekonstruksi (domain piksel, flatten).
    Sama pola dengan notebook: Theta = Phi @ Psi, normalisasi kolom, OMP multi
    target dalam 1 panggilan, koreksi skala balik."""
    from sklearn.linear_model import OrthogonalMatchingPursuit
    from threadpoolctl import threadpool_limits

    # Tiap panggilan ini jalan di proses worker terpisah (lihat
    # _reconstruct_blocks_parallel) -- kalau BLAS di dalamnya tetap
    # multi-thread, N proses x M thread BLAS akan rebutan core yang sama
    # (oversubscription) dan malah lebih lambat daripada jalan serial.
    with threadpool_limits(limits=1):
        Theta = Phi @ Psi  # (M, n)
        col_norms = np.linalg.norm(Theta, axis=0)
        col_norms[col_norms < 1e-12] = 1.0
        Theta_norm = Theta / col_norms

        Yw = Y.T  # (M, batch_n) -- tiap kolom = 1 blok/target, format yang diharap sklearn
        omp = OrthogonalMatchingPursuit(n_nonzero_coefs=int(K), fit_intercept=False)
        omp.fit(Theta_norm, Yw)
        S = omp.coef_ / col_norms  # (batch_n, n) -- koreksi skala krn OMP kerja di Theta_norm

        X = Psi @ S.T  # (n, batch_n)
        return np.clip(np.real(X.T), 0, 1)  # (batch_n, n)


def decode_payload(payload):
    """Return (Y_blocks: (n_blocks_total, M) float32 array, meta dict)."""
    header = payload[:_HEADER_SIZE]
    magic, N, M, rows, cols, orig_w, orig_h, channels = struct.unpack(_HEADER_FMT, header)
    if magic != _MAGIC:
        raise ValueError("Payload CS tidak valid (magic mismatch)")

    raw = gzip.decompress(payload[_HEADER_SIZE:])
    quantized = np.frombuffer(raw, dtype=np.int8).reshape(M, channels * rows * cols)
    stacked = quantized.astype(np.float32) / CS_QUANT_SCALE  # (M, channels*n_blocks)

    meta = dict(N=N, M=M, rows=rows, cols=cols, orig_w=orig_w, orig_h=orig_h, channels=channels)
    return stacked, meta


def reconstruct_frame(payload, seed=CS_SEED):
    """Decode payload biner penuh -> citra RGB uint8 (HxWx3)."""
    stacked, meta = decode_payload(payload)
    N, M = meta["N"], meta["M"]
    rows, cols, channels = meta["rows"], meta["cols"], meta["channels"]
    orig_w, orig_h = meta["orig_w"], meta["orig_h"]
    n = N * N

    Phi = sensing_matrix(M, n, seed)
    Psi = build_wavelet_synthesis_basis(N)
    K = resolve_sparsity_k(M, n)

    n_blocks = rows * cols
    out_channels = []
    for ch in range(channels):
        Y_blocks = stacked[:, ch * n_blocks:(ch + 1) * n_blocks].T  # (n_blocks, M)
        blocks_flat = _reconstruct_blocks_parallel(Y_blocks, Phi, Psi, K)
        out_channels.append(merge_blocks_flat(blocks_flat, rows, cols, N, orig_h, orig_w))

    frame = np.stack(out_channels, axis=-1) if channels > 1 else out_channels[0]
    return np.clip(frame * 255.0, 0, 255).astype(np.uint8)


# ───────────── Varian YCbCr: CS di Y saja, warna asli lewat Cb/Cr ringan ─────

def _encode_y_channel(y_plane, N, mr_percent, seed):
    """y_plane: 2D uint8 (H x W). Return (compressed_bytes, M, rows, cols)."""
    n = N * N
    M = max(1, int(round(mr_percent / 100 * n)))
    Phi = sensing_matrix(M, n, seed)
    padded = _pad_to_multiple(y_plane, N)
    blocks_flat, rows, cols = split_blocks_flat(padded.astype(np.float32) / 255.0, N)

    # Satu matmul batched (bukan loop Python per-blok) -- OpenBLAS bisa
    # memparalelkan operasi ini lintas core, bukan cuma 1 thread.
    stacked = Phi @ blocks_flat.T  # (M, n_blocks)
    quantized = np.clip(np.round(stacked * CS_QUANT_SCALE), -127, 127).astype(np.int8)
    compressed = gzip.compress(quantized.tobytes(), compresslevel=6)
    return compressed, M, rows, cols


def encode_frame_ycbcr(frame_rgb, N=CS_BLOCK_SIZE, mr_percent=CS_MR_PERCENT,
                        seed=CS_SEED, chroma_scale=CS_CHROMA_SCALE):
    """frame_rgb: HxWx3 uint8. CS diterapkan cuma di channel Y (luminance);
    Cb/Cr dikirim lewat subsampling + JPEG (warna asli, bukan colorization)."""
    orig_h, orig_w = frame_rgb.shape[:2]

    ycc = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2YCrCb)
    y_plane, cr_plane, cb_plane = ycc[:, :, 0], ycc[:, :, 1], ycc[:, :, 2]

    y_compressed, M, rows, cols = _encode_y_channel(y_plane, N, mr_percent, seed)

    small_w = max(1, orig_w // chroma_scale)
    small_h = max(1, orig_h // chroma_scale)
    cb_small = cv2.resize(cb_plane, (small_w, small_h), interpolation=cv2.INTER_AREA)
    cr_small = cv2.resize(cr_plane, (small_w, small_h), interpolation=cv2.INTER_AREA)
    ok_cb, cb_jpeg = cv2.imencode(".jpg", cb_small, [cv2.IMWRITE_JPEG_QUALITY, CS_CHROMA_JPEG_QUALITY])
    ok_cr, cr_jpeg = cv2.imencode(".jpg", cr_small, [cv2.IMWRITE_JPEG_QUALITY, CS_CHROMA_JPEG_QUALITY])
    if not (ok_cb and ok_cr):
        raise ValueError("Gagal encode JPEG untuk channel Cb/Cr")
    cb_bytes, cr_bytes = cb_jpeg.tobytes(), cr_jpeg.tobytes()

    header = struct.pack(_HEADER_YCC_FMT, _MAGIC_YCC, N, M, rows, cols,
                          orig_w, orig_h, chroma_scale, len(cb_bytes), len(cr_bytes))
    return header + cb_bytes + cr_bytes + y_compressed


def reconstruct_frame_ycbcr(payload, seed=CS_SEED):
    """Decode payload YCbCr -> citra RGB uint8 (HxWx3), warna asli dipertahankan."""
    header = payload[:_HEADER_YCC_SIZE]
    magic, N, M, rows, cols, orig_w, orig_h, chroma_scale, cb_len, cr_len = struct.unpack(
        _HEADER_YCC_FMT, header)
    if magic != _MAGIC_YCC:
        raise ValueError("Payload CS YCbCr tidak valid (magic mismatch)")

    offset = _HEADER_YCC_SIZE
    cb_bytes = payload[offset:offset + cb_len]; offset += cb_len
    cr_bytes = payload[offset:offset + cr_len]; offset += cr_len
    y_compressed = payload[offset:]

    # ── rekonstruksi Y lewat OMP+Wavelet ──
    n = N * N
    raw = gzip.decompress(y_compressed)
    quantized = np.frombuffer(raw, dtype=np.int8).reshape(M, rows * cols)
    stacked = quantized.astype(np.float32) / CS_QUANT_SCALE

    Phi = sensing_matrix(M, n, seed)
    Psi = build_wavelet_synthesis_basis(N)
    K = resolve_sparsity_k(M, n)
    Y_blocks = stacked.T  # (n_blocks, M)
    blocks_flat = _reconstruct_blocks_parallel(Y_blocks, Phi, Psi, K)
    y_plane = merge_blocks_flat(blocks_flat, rows, cols, N, orig_h, orig_w)
    y_plane = np.clip(y_plane * 255.0, 0, 255).astype(np.uint8)

    # ── Cb/Cr: decode JPEG lalu upsample ke ukuran asli (warna asli, bukan tebakan) ──
    cb_small = cv2.imdecode(np.frombuffer(cb_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    cr_small = cv2.imdecode(np.frombuffer(cr_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    cb_plane = cv2.resize(cb_small, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
    cr_plane = cv2.resize(cr_small, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)

    ycc = np.stack([y_plane, cr_plane, cb_plane], axis=-1)
    return cv2.cvtColor(ycc, cv2.COLOR_YCrCb2RGB)
