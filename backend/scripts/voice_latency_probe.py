#!/usr/bin/env python3
# pyright: reportPrivateUsage=false
"""Synthetic latency probe for the realtime voice pipeline.

Run from backend/:
  /path/to/backend/.venv/bin/python scripts/voice_latency_probe.py
"""
from __future__ import annotations

import asyncio
import base64
import statistics
import sys
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, cast

from starlette.websockets import WebSocket

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.connectors.audio_utils import (
    gemini_pcm_to_twilio_mulaw,
    twilio_mulaw_to_gemini_pcm,
)
from app.services.voice_session import VoicePipeline


def _pct(samples: list[float], percentile: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * percentile) - 1))
    return ordered[index]


def _summary_ms(samples: list[float]) -> str:
    if not samples:
        return "avg=0.00ms p95=0.00ms max=0.00ms n=0"
    return (
        f"avg={statistics.fmean(samples):.2f}ms "
        f"p95={_pct(samples, 0.95):.2f}ms "
        f"max={max(samples):.2f}ms "
        f"n={len(samples)}"
    )


class _DummyWebSocket:
    def __init__(self, *, send_delay_s: float = 0.0) -> None:
        self.send_delay_s = send_delay_s
        self.sent_count = 0

    async def send_json(self, _msg: dict[str, Any]) -> None:
        if self.send_delay_s > 0:
            await asyncio.sleep(self.send_delay_s)
        self.sent_count += 1

    async def receive_text(self) -> str:
        await asyncio.sleep(3600)
        return ""


class _DummyGeminiSession:
    def __init__(self, *, send_delay_s: float = 0.0) -> None:
        self.send_delay_s = send_delay_s
        self.sent_count = 0

    async def send_realtime_input(self, *, audio: Any) -> None:
        if self.send_delay_s > 0:
            await asyncio.sleep(self.send_delay_s)
        _ = audio
        self.sent_count += 1


def benchmark_audio_conversion(iterations: int = 2500) -> None:
    print("\n[1/3] Audio conversion microbench")

    twilio_payload = base64.b64encode(bytes([255]) * 160).decode("ascii")
    gemini_pcm = bytes(1920)

    twilio_to_gemini_ms: list[float] = []
    gemini_to_twilio_ms: list[float] = []

    for _ in range(iterations):
        t0 = perf_counter()
        twilio_mulaw_to_gemini_pcm(twilio_payload)
        twilio_to_gemini_ms.append((perf_counter() - t0) * 1000.0)

    for _ in range(iterations):
        t0 = perf_counter()
        gemini_pcm_to_twilio_mulaw(gemini_pcm)
        gemini_to_twilio_ms.append((perf_counter() - t0) * 1000.0)

    print(f"twilio_mulaw_to_gemini_pcm: {_summary_ms(twilio_to_gemini_ms)}")
    print(f"gemini_pcm_to_twilio_mulaw: {_summary_ms(gemini_to_twilio_ms)}")


async def _measure_loop_lag(
    workload: Awaitable[None],
    *,
    sample_interval_s: float = 0.01,
) -> list[float]:
    lags_ms: list[float] = []
    stop = asyncio.Event()

    async def monitor() -> None:
        expected = perf_counter() + sample_interval_s
        while not stop.is_set():
            await asyncio.sleep(sample_interval_s)
            now = perf_counter()
            lag = max(0.0, now - expected)
            lags_ms.append(lag * 1000.0)
            expected = now + sample_interval_s

    mon_task = asyncio.create_task(monitor())
    try:
        await workload
    finally:
        stop.set()
        await mon_task
    return lags_ms


async def benchmark_pipeline_queues(
    *,
    chunks: int = 240,
    chunk_interval_s: float = 0.02,
    gemini_send_delay_s: float = 0.001,
    twilio_send_delay_s: float = 0.001,
) -> None:
    print("\n[2/3] Pipeline queue + send benchmark")

    ws = _DummyWebSocket(send_delay_s=twilio_send_delay_s)
    pipeline = VoicePipeline(cast(WebSocket, ws), call_sid="bench-call")
    pipeline.stream_sid = "bench-stream"

    gemini_session = _DummyGeminiSession(send_delay_s=gemini_send_delay_s)
    twilio_payload = base64.b64encode(bytes([255]) * 160).decode("ascii")
    pcm_16k = twilio_mulaw_to_gemini_pcm(twilio_payload)
    pcm_24k = bytes(1920)

    async def workload() -> None:
        pipeline._stopped = False
        send_in_task = asyncio.create_task(pipeline._send_queued_audio_to_gemini(gemini_session))
        send_out_task = asyncio.create_task(pipeline._send_queued_audio_to_twilio())

        for _ in range(chunks):
            pipeline._enqueue_inbound_audio(pcm_16k)
            pipeline._enqueue_outbound_audio(pcm_24k)
            await asyncio.sleep(chunk_interval_s)

        while not pipeline._audio_queue.empty() or not pipeline._twilio_out_queue.empty():
            await asyncio.sleep(0.005)

        pipeline._stopped = True
        await send_in_task
        await send_out_task

    loop_lag_ms = await _measure_loop_lag(workload())

    stages = [
        "inbound_queue_delay_ms",
        "gemini_send_realtime_input_ms",
        "outbound_queue_delay_ms",
        "twilio_mulaw_encode_ms",
        "twilio_ws_send_ms",
    ]

    for stage in stages:
        window = pipeline._stage_latency.get(stage)
        if window is None:
            print(f"{stage}: no samples")
            continue
        avg, p95, max_ms, count = window.summary()
        print(f"{stage}: avg={avg:.2f}ms p95={p95:.2f}ms max={max_ms:.2f}ms n={count}")

    print(f"event_loop_lag: {_summary_ms(loop_lag_ms)}")
    print(
        "drops: "
        f"inbound={pipeline._dropped_inbound_audio} "
        f"outbound={pipeline._dropped_outbound_audio} "
        f"gemini_sends={gemini_session.sent_count} twilio_sends={ws.sent_count}"
    )


async def benchmark_lock_risk(chunks: int = 1200) -> None:
    print("\n[3/3] Event-loop stall (lock-risk) stress check")

    ws = _DummyWebSocket(send_delay_s=0.0)
    pipeline = VoicePipeline(cast(WebSocket, ws), call_sid="lock-check")
    pipeline.stream_sid = "lock-stream"

    pcm_24k = bytes(1920)

    async def workload() -> None:
        async def producer() -> None:
            for _ in range(chunks):
                pipeline._enqueue_outbound_audio(pcm_24k)
                await asyncio.sleep(0)
            await asyncio.sleep(0.05)
            pipeline._stopped = True

        pipeline._stopped = False
        send_task = asyncio.create_task(pipeline._send_queued_audio_to_twilio())
        await producer()
        await send_task

    loop_lag_ms = await _measure_loop_lag(workload())
    print(f"event_loop_lag_under_stress: {_summary_ms(loop_lag_ms)}")


def main() -> None:
    benchmark_audio_conversion()
    asyncio.run(benchmark_pipeline_queues())
    asyncio.run(benchmark_lock_risk())


if __name__ == "__main__":
    main()
