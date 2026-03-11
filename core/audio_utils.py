"""Audio format conversion utilities for Twilio mulaw <-> PCM16.

Twilio sends 8kHz mulaw audio. Silero-vad requires 16kHz PCM16.
This module handles all conversions.

Python 3.13 removed audioop, so we provide a struct-based fallback.
"""

from __future__ import annotations

import struct

# Try audioop first (available Python <= 3.12), fall back to pure-python
_HAS_AUDIOOP = False
try:
    import audioop  # type: ignore[import-not-found]

    _HAS_AUDIOOP = True
except ImportError:
    pass

# ---- mulaw lookup tables (ITU-T G.711) ----

# mulaw byte -> 16-bit signed PCM
_MULAW_DECODE_TABLE: list[int] = []

def _build_mulaw_decode_table() -> list[int]:
    """Build the mulaw-to-linear decode table per G.711 spec."""
    table: list[int] = []
    for i in range(256):
        val = ~i
        sign = val & 0x80
        exponent = (val >> 4) & 0x07
        mantissa = val & 0x0F
        sample = ((mantissa << 3) + 0x84) << exponent
        sample -= 0x84
        if sign:
            sample = -sample
        # Clamp to int16 range
        sample = max(-32768, min(32767, sample))
        table.append(sample)
    return table

_MULAW_DECODE_TABLE = _build_mulaw_decode_table()

# linear PCM16 -> mulaw byte
_MULAW_ENCODE_TABLE: list[int] | None = None

def _build_mulaw_encode_table() -> list[int]:
    """Build a 16-bit-to-mulaw lookup via the standard algorithm."""
    BIAS = 0x84
    MAX = 0x7FFF
    exp_lut = [0, 0, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 3,
               4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4,
               5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
               5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5,
               6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
               6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
               6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
               6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
               7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7]
    table: list[int] = []
    for i in range(65536):
        sample = i - 32768 if i >= 32768 else i - 32768  # interpret as signed
        sign = 0
        if sample < 0:
            sign = 0x80
            sample = -sample
        if sample > MAX:
            sample = MAX
        sample += BIAS
        exponent = exp_lut[(sample >> 7) & 0xFF]
        mantissa = (sample >> (exponent + 3)) & 0x0F
        mulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
        table.append(mulaw_byte)
    return table

def _get_mulaw_encode_table() -> list[int]:
    global _MULAW_ENCODE_TABLE
    if _MULAW_ENCODE_TABLE is None:
        _MULAW_ENCODE_TABLE = _build_mulaw_encode_table()
    return _MULAW_ENCODE_TABLE


def mulaw_to_pcm16(mulaw_bytes: bytes) -> bytes:
    """Convert mulaw (8kHz) to PCM16 (8kHz).

    Each mulaw byte becomes a 2-byte little-endian signed int16.
    """
    if _HAS_AUDIOOP:
        return audioop.ulaw2lin(mulaw_bytes, 2)

    # Pure-python fallback using lookup table
    samples = [_MULAW_DECODE_TABLE[b] for b in mulaw_bytes]
    return struct.pack(f"<{len(samples)}h", *samples)


def pcm16_to_mulaw(pcm16_bytes: bytes, sample_rate: int = 8000) -> bytes:
    """Convert PCM16 to mulaw at 8kHz.

    If sample_rate is not 8000, resamples to 8kHz first.
    """
    if sample_rate != 8000:
        pcm16_bytes = _resample_to_8k(pcm16_bytes, sample_rate)

    if _HAS_AUDIOOP:
        return audioop.lin2ulaw(pcm16_bytes, 2)

    # Pure-python fallback
    encode_table = _get_mulaw_encode_table()
    n_samples = len(pcm16_bytes) // 2
    samples = struct.unpack(f"<{n_samples}h", pcm16_bytes)
    # Map signed int16 (-32768..32767) to unsigned index (0..65535)
    return bytes(encode_table[(s + 32768) & 0xFFFF] for s in samples)


def resample_8k_to_16k(pcm16_8k: bytes) -> bytes:
    """Resample PCM16 from 8kHz to 16kHz using linear interpolation.

    Required by silero-vad which expects 16kHz input.
    Each sample is doubled with a linearly interpolated sample between.
    """
    n_samples = len(pcm16_8k) // 2
    if n_samples == 0:
        return b""

    samples = struct.unpack(f"<{n_samples}h", pcm16_8k)
    out: list[int] = []
    for i in range(n_samples):
        out.append(samples[i])
        if i < n_samples - 1:
            # Linear interpolation
            mid = (samples[i] + samples[i + 1]) // 2
            out.append(mid)
        else:
            # Last sample: duplicate
            out.append(samples[i])

    return struct.pack(f"<{len(out)}h", *out)


def _resample_to_8k(pcm16_bytes: bytes, src_rate: int) -> bytes:
    """Resample PCM16 from src_rate to 8kHz by decimation with averaging."""
    if src_rate == 8000:
        return pcm16_bytes

    n_samples = len(pcm16_bytes) // 2
    if n_samples == 0:
        return b""

    samples = struct.unpack(f"<{n_samples}h", pcm16_bytes)
    ratio = src_rate / 8000
    out_len = int(n_samples / ratio)
    out: list[int] = []
    for i in range(out_len):
        src_idx = i * ratio
        idx = int(src_idx)
        frac = src_idx - idx
        if idx + 1 < n_samples:
            val = int(samples[idx] * (1 - frac) + samples[idx + 1] * frac)
        else:
            val = samples[idx]
        out.append(max(-32768, min(32767, val)))

    return struct.pack(f"<{len(out)}h", *out)
