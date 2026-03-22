"""Audio format conversion utilities for Twilio <-> Gemini Live API bridging.

Uses the native ``audioop`` module when available (Python ≤ 3.12) for best
audio quality and performance, falling back to a pure-Python implementation
on Python 3.13+ where ``audioop`` was removed.

Audio formats:
  Twilio Media Streams:  mu-law 8 kHz mono
  Gemini Live API input: PCM16 16 kHz mono little-endian
  Gemini Live API output: PCM16 24 kHz mono little-endian
"""
from __future__ import annotations

import base64
import struct
from math import gcd

try:
    import audioop  # C implementation, available in Python ≤ 3.12
    _HAS_AUDIOOP = True
except ImportError:
    _HAS_AUDIOOP = False

# ── mu-law codec tables ─────────────────────────────────────────────────
# ITU-T G.711 mu-law: 8-bit compressed <-> 16-bit linear PCM.
# Pre-computed lookup tables are much faster than per-sample math.

_MULAW_BIAS = 0x84
_MULAW_CLIP = 0x7FFF

# mu-law byte -> signed 16-bit PCM
_MULAW_DECODE_TABLE: list[int] = []

def _build_decode_table() -> list[int]:
    """Build 256-entry mu-law decode table (ITU-T G.711)."""
    table = []
    for i in range(256):
        val = ~i
        sign = val & 0x80
        exponent = (val >> 4) & 0x07
        mantissa = val & 0x0F
        sample = ((mantissa << 3) + _MULAW_BIAS) << exponent
        sample -= _MULAW_BIAS
        if sign:
            sample = -sample
        # Clamp to int16 range
        sample = max(-32768, min(32767, sample))
        table.append(sample)
    return table

_MULAW_DECODE_TABLE = _build_decode_table()

# signed 16-bit PCM -> mu-law byte
_MULAW_ENCODE_TABLE: list[int] = []

def _encode_mulaw_sample(sample: int) -> int:
    """Encode a single signed 16-bit PCM sample to mu-law byte."""
    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    if sample > _MULAW_CLIP:
        sample = _MULAW_CLIP
    sample += _MULAW_BIAS

    exponent = 7
    for exp in range(7, -1, -1):
        if sample & (1 << (exp + 3)):
            exponent = exp
            break

    mantissa = (sample >> (exponent + 3)) & 0x0F
    mulaw_byte = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return mulaw_byte

def _build_encode_table() -> list[int]:
    """Build 65536-entry encode table for fast PCM->mu-law conversion."""
    table = []
    for i in range(65536):
        sample = i - 32768  # unsigned 16-bit -> signed 16-bit
        table.append(_encode_mulaw_sample(sample))
    return table

_MULAW_ENCODE_TABLE = _build_encode_table()


# ── Codec functions ──────────────────────────────────────────────────────

def _ulaw2lin(mulaw_bytes: bytes) -> bytes:
    """Convert mu-law bytes to signed 16-bit little-endian PCM."""
    out = bytearray(len(mulaw_bytes) * 2)
    for i, b in enumerate(mulaw_bytes):
        sample = _MULAW_DECODE_TABLE[b]
        struct.pack_into("<h", out, i * 2, sample)
    return bytes(out)


def _lin2ulaw(pcm_bytes: bytes) -> bytes:
    """Convert signed 16-bit little-endian PCM to mu-law bytes."""
    n_samples = len(pcm_bytes) // 2
    out = bytearray(n_samples)
    for i in range(n_samples):
        sample = struct.unpack_from("<h", pcm_bytes, i * 2)[0]
        # Map signed int16 (-32768..32767) to unsigned index (0..65535)
        out[i] = _MULAW_ENCODE_TABLE[sample + 32768]
    return bytes(out)


# ── Sample rate conversion ───────────────────────────────────────────────

def _resample_linear(pcm: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample signed 16-bit mono PCM using linear interpolation.

    This is a simple but effective approach for voice-quality audio where
    the ratio is an integer or simple fraction (8k->16k, 24k->8k).
    """
    if from_rate == to_rate:
        return pcm

    n_samples = len(pcm) // 2
    if n_samples == 0:
        return pcm

    # Unpack all input samples at once
    samples = struct.unpack(f"<{n_samples}h", pcm)

    # Calculate output length
    out_len = int(n_samples * to_rate / from_rate)
    if out_len == 0:
        return b""

    ratio = from_rate / to_rate
    out = []
    for i in range(out_len):
        src_pos = i * ratio
        idx = int(src_pos)
        frac = src_pos - idx

        if idx + 1 < n_samples:
            val = samples[idx] * (1.0 - frac) + samples[idx + 1] * frac
        else:
            val = samples[min(idx, n_samples - 1)]

        out.append(max(-32768, min(32767, int(val))))

    return struct.pack(f"<{len(out)}h", *out)


# ── Public API (drop-in replacements) ────────────────────────────────────

def twilio_mulaw_to_gemini_pcm(payload_b64: str) -> bytes:
    """Convert Twilio mulaw/8kHz base64 payload to Gemini PCM16/16kHz bytes.

    Steps:
    1. base64 decode the Twilio payload
    2. Convert mulaw to linear PCM16 (still 8kHz)
    3. Upsample 8kHz -> 16kHz
    """
    mulaw_bytes = base64.b64decode(payload_b64)
    if _HAS_AUDIOOP:
        pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
        pcm_16k = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)[0]
        return pcm_16k
    pcm_8k = _ulaw2lin(mulaw_bytes)
    pcm_16k = _resample_linear(pcm_8k, 8000, 16000)
    return pcm_16k


def gemini_pcm_to_twilio_mulaw(pcm_24k: bytes) -> str:
    """Convert Gemini PCM16/24kHz bytes to Twilio mulaw/8kHz base64 payload.

    Steps:
    1. Downsample 24kHz -> 8kHz
    2. Convert linear PCM16 to mulaw
    3. base64 encode
    """
    if _HAS_AUDIOOP:
        pcm_8k = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, None)[0]
        mulaw_bytes = audioop.lin2ulaw(pcm_8k, 2)
        return base64.b64encode(mulaw_bytes).decode("ascii")
    pcm_8k = _resample_linear(pcm_24k, 24000, 8000)
    mulaw_bytes = _lin2ulaw(pcm_8k)
    return base64.b64encode(mulaw_bytes).decode("ascii")
