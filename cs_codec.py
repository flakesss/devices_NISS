"""
Compressive Sensing codec untuk NISS — dipakai bersama oleh Pi (encode) dan
service cs-reconstruct (decode).

Diporting dari eksperimen di CS_endoskop_colab_4.ipynb:
- Basis sparse: DCT (NMSE rata-rata terendah & paling konsisten di eksperimen)
- Sensing matrix: Bernoulli acak +-1/sqrt(M), seed tetap supaya Pi & service
  rekonstruksi menghasilkan matriks Phi yang identik tanpa perlu ditransfer
  lewat jaringan.
- Rekonstruksi: Orthogonal Matching Pursuit (OMP), K = K_RATIO * M.

Encode-side (dipakai Pi) cuma butuh numpy. Decode-side (dipakai cs-reconstruct)
butuh scipy/scikit-learn juga -- lihat requirements.txt masing-masing service.
"""

import gzip
import struct

import numpy as np

CS_SEED = 42
CS_BASIS = "dct"
CS_BLOCK_SIZE = 64      # N -- harus pangkat dua
CS_MR_PERCENT = 100     # measurement rate (%) default -- kualitas terbaik, penghematan dari kuantisasi int8+gzip
                        # (bukan dari mengurangi sampel), lihat catatan kuantisasi di bawah
K_RATIO = 0.25

# Kuantisasi ke int8 (bukan int16) -- separuh ukuran per sampel.
# Skala 50 dipilih karena nilai measurement Y = Phi @ block (Phi Bernoulli
# +-1/sqrt(M), block ternormalisasi [0,1]) secara empiris berkisar +-2.5,
# dan 127/2.5 ~= 50 -- skala lebih tinggi dari ini menyebabkan clipping
# (overflow int8) dan kualitas rekonstruksi turun drastis.
CS_QUANT_SCALE = 50

_MAGIC = b"NCS1"
_HEADER_FMT = ">4sHHHHHHB"   # magic, N, M, block_rows, block_cols, orig_w, orig_h, channels
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)


# ───────────────────────── Encode-side (Pi, numpy only) ─────────────────────

def sensing_matrix(M, N, seed=CS_SEED):
    """Bernoulli +-1/sqrt(M), deterministik lewat seed tetap -- identik di kedua sisi."""
    rng = np.random.RandomState(seed)
    return (2 * rng.randint(0, 2, size=(M, N)) - 1).astype(np.float32) / np.sqrt(M)


def _pad_to_multiple(img, block):
    h, w = img.shape[:2]
    pad_h = (-h) % block
    pad_w = (-w) % block
    if pad_h or pad_w:
        img = np.pad(img, ((0, pad_h), (0, pad_w)) + ((0, 0),) * (img.ndim - 2), mode="edge")
    return img


def split_blocks(channel_img, N):
    """channel_img: 2D array (H x W), sudah dipad ke kelipatan N. Return (blocks, rows, cols)."""
    h, w = channel_img.shape
    rows, cols = h // N, w // N
    blocks = []
    for r in range(rows):
        for c in range(cols):
            blocks.append(channel_img[r * N:(r + 1) * N, c * N:(c + 1) * N])
    return blocks, rows, cols


def merge_blocks(blocks, rows, cols, N, orig_h, orig_w):
    h, w = rows * N, cols * N
    out = np.zeros((h, w), dtype=blocks[0].dtype)
    for i, blk in enumerate(blocks):
        r, c = divmod(i, cols)
        out[r * N:(r + 1) * N, c * N:(c + 1) * N] = blk
    return out[:orig_h, :orig_w]


def encode_frame(frame_rgb, N=CS_BLOCK_SIZE, mr_percent=CS_MR_PERCENT, seed=CS_SEED):
    """frame_rgb: HxWx3 uint8. Return payload biner (bytes): header + measurement
    Y terkuantisasi int16 + gzip, per channel per block."""
    assert (N & (N - 1)) == 0, "CS_BLOCK_SIZE harus pangkat dua"
    orig_h, orig_w = frame_rgb.shape[:2]
    M = max(1, int(round(mr_percent / 100 * N)))
    Phi = sensing_matrix(M, N, seed)

    padded = _pad_to_multiple(frame_rgb, N)
    channels = padded.shape[2] if padded.ndim == 3 else 1
    if padded.ndim == 2:
        padded = padded[:, :, None]

    all_Y = []
    rows = cols = None
    for ch in range(channels):
        blocks, rows, cols = split_blocks(padded[:, :, ch].astype(np.float32) / 255.0, N)
        for blk in blocks:
            Y = Phi @ blk  # M x N
            all_Y.append(Y)

    stacked = np.stack(all_Y, axis=0)  # (channels*rows*cols, M, N)
    # Kuantisasi float -> int8 (separuh ukuran int16, lihat CS_QUANT_SCALE di atas)
    quantized = np.clip(np.round(stacked * CS_QUANT_SCALE), -127, 127).astype(np.int8)
    raw = quantized.tobytes()
    compressed = gzip.compress(raw, compresslevel=6)

    header = struct.pack(_HEADER_FMT, _MAGIC, N, M, rows, cols, orig_w, orig_h, channels)
    return header + compressed


# ───────────────────────── Decode-side (cs-reconstruct, butuh scipy/sklearn) ─

def build_dct_matrix(N):
    n = np.arange(N)
    k = n.reshape(-1, 1)
    W = np.sqrt(2 / N) * np.cos(np.pi * (2 * n + 1) * k / (2 * N))
    W[0, :] = np.sqrt(1 / N)
    return W.astype(np.float32)


def K_for(M):
    return min(max(4, int(np.floor(K_RATIO * M))), M - 1)


def reconstruct(Y, Phi, W, K):
    """Sama persis dengan notebook -- OMP di domain basis W, per kolom Y."""
    from sklearn.linear_model import OrthogonalMatchingPursuit

    A = Phi @ W.T
    Yw = Y @ W.T
    col_norms = np.linalg.norm(A, axis=0)
    col_norms[col_norms < 1e-12] = 1.0
    A_norm = A / col_norms
    omp = OrthogonalMatchingPursuit(n_nonzero_coefs=int(K), fit_intercept=False)
    omp.fit(A_norm, Yw)
    S = (omp.coef_ / col_norms).T
    return np.clip(np.real(W.T @ S @ W), 0, 1)


def decode_payload(payload):
    """Return (Y_blocks: list of (M,N) float32 arrays, meta dict)."""
    header = payload[:_HEADER_SIZE]
    magic, N, M, rows, cols, orig_w, orig_h, channels = struct.unpack(_HEADER_FMT, header)
    if magic != _MAGIC:
        raise ValueError("Payload CS tidak valid (magic mismatch)")

    raw = gzip.decompress(payload[_HEADER_SIZE:])
    quantized = np.frombuffer(raw, dtype=np.int8).reshape(channels * rows * cols, M, N)
    stacked = quantized.astype(np.float32) / CS_QUANT_SCALE

    meta = dict(N=N, M=M, rows=rows, cols=cols, orig_w=orig_w, orig_h=orig_h, channels=channels)
    return stacked, meta


def reconstruct_frame(payload, seed=CS_SEED):
    """Decode payload biner penuh -> citra RGB uint8 (HxWx3)."""
    stacked, meta = decode_payload(payload)
    N, M = meta["N"], meta["M"]
    rows, cols, channels = meta["rows"], meta["cols"], meta["channels"]
    orig_w, orig_h = meta["orig_w"], meta["orig_h"]

    Phi = sensing_matrix(M, N, seed)
    W = build_dct_matrix(N)
    K = K_for(M)

    out_channels = []
    n_blocks = rows * cols
    for ch in range(channels):
        blocks = []
        for i in range(n_blocks):
            Y = stacked[ch * n_blocks + i]
            blocks.append(reconstruct(Y, Phi, W, K))
        out_channels.append(merge_blocks(blocks, rows, cols, N, orig_h, orig_w))

    frame = np.stack(out_channels, axis=-1) if channels > 1 else out_channels[0]
    return np.clip(frame * 255.0, 0, 255).astype(np.uint8)
