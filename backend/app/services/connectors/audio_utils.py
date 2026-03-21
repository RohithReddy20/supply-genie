"""Audio format conversion utilities for Twilio ↔ Gemini Live API bridging."""
from __future__ import annotations

import audioop
import base64


# Twilio Media Streams: mulaw 8kHz mono
# Gemini Live API input: PCM16 16kHz mono little-endian
# Gemini Live API output: PCM16 24kHz mono little-endian

def twilio_mulaw_to_gemini_pcm(payload_b64: str) -> bytes:
    """Convert Twilio mulaw/8kHz base64 payload to Gemini PCM16/16kHz bytes.

    Steps:
    1. base64 decode the Twilio payload
    2. Convert mulaw to linear PCM16 (still 8kHz)
    3. Upsample 8kHz → 16kHz (ratecv with factor 2)
    """
    mulaw_bytes = base64.b64decode(payload_b64)
    # mulaw → PCM16 (sample width 2 bytes)
    pcm_8k = audioop.ulaw2lin(mulaw_bytes, 2)
    # Upsample 8kHz → 16kHz
    pcm_16k, _ = audioop.ratecv(pcm_8k, 2, 1, 8000, 16000, None)
    return pcm_16k


def gemini_pcm_to_twilio_mulaw(pcm_24k: bytes) -> str:
    """Convert Gemini PCM16/24kHz bytes to Twilio mulaw/8kHz base64 payload.

    Steps:
    1. Downsample 24kHz → 8kHz (ratecv with factor 1/3)
    2. Convert linear PCM16 to mulaw
    3. base64 encode
    """
    # Downsample 24kHz → 8kHz
    pcm_8k, _ = audioop.ratecv(pcm_24k, 2, 1, 24000, 8000, None)
    # PCM16 → mulaw
    mulaw_bytes = audioop.lin2ulaw(pcm_8k, 2)
    return base64.b64encode(mulaw_bytes).decode("ascii")
