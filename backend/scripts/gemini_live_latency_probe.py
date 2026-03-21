#!/usr/bin/env python3
"""Measure model-side voice turn latency against Gemini Live API.

Run:
  /path/to/backend/.venv/bin/python scripts/gemini_live_latency_probe.py
"""
from __future__ import annotations

import asyncio
import statistics
import sys
from pathlib import Path
from time import perf_counter
from typing import cast

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from google import genai
from google.genai import types

from app.config import Settings, get_settings


PROMPT = "Please answer in one short sentence: confirm you can help with a delayed shipment."
DEFAULT_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
FALLBACK_MODEL_CANDIDATE = "gemini-live-2.5-flash-preview"


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(len(ordered) * q) - 1))
    return ordered[idx]


def _summary(label: str, values: list[float]) -> str:
    if not values:
        return f"{label}: no data"
    return (
        f"{label}: avg={statistics.fmean(values):.1f}ms "
        f"p95={_pct(values, 0.95):.1f}ms "
        f"max={max(values):.1f}ms "
        f"n={len(values)}"
    )


async def _single_round(
    client: genai.Client,
    model: str,
    config: types.LiveConnectConfigOrDict,
) -> tuple[float, float, float]:
    connect_start = perf_counter()
    async with client.aio.live.connect(model=model, config=config) as session:
        connect_ms = (perf_counter() - connect_start) * 1000.0

        send_start = perf_counter()
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=PROMPT)]),
            turn_complete=True,
        )

        first_audio_ms = 0.0
        turn_complete_ms = 0.0

        async for msg in session.receive():
            sc = msg.server_content
            if not sc:
                continue

            if sc.model_turn and sc.model_turn.parts and first_audio_ms == 0.0:
                for part in sc.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        first_audio_ms = (perf_counter() - send_start) * 1000.0
                        break

            if sc.turn_complete:
                turn_complete_ms = (perf_counter() - send_start) * 1000.0
                break

    return connect_ms, first_audio_ms, turn_complete_ms


def _base_config(settings: Settings) -> dict[str, object]:
    return {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": {
                "prebuilt_voice_config": {
                    "voice_name": "Kore",
                },
            },
        },
        "output_audio_transcription": {},
        "thinking_config": {
            "thinking_budget": settings.voice_thinking_budget,
        },
        "realtime_input_config": {
            "automatic_activity_detection": {
                "start_of_speech_sensitivity": settings.voice_vad_start_sensitivity,
                "end_of_speech_sensitivity": settings.voice_vad_end_sensitivity,
                "prefix_padding_ms": settings.voice_vad_prefix_padding_ms,
                "silence_duration_ms": settings.voice_vad_silence_duration_ms,
            },
            "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
            "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",
        },
    }


def _scenarios(settings: Settings) -> list[tuple[str, str, types.LiveConnectConfigOrDict]]:
    baseline = _base_config(settings)

    thinking_off = _base_config(settings)
    thinking_off["thinking_config"] = {"thinking_budget": 0}

    low_silence = _base_config(settings)
    low_silence["realtime_input_config"] = {
        "automatic_activity_detection": {
            "start_of_speech_sensitivity": settings.voice_vad_start_sensitivity,
            "end_of_speech_sensitivity": settings.voice_vad_end_sensitivity,
            "prefix_padding_ms": max(20, settings.voice_vad_prefix_padding_ms - 20),
            "silence_duration_ms": max(200, settings.voice_vad_silence_duration_ms - 80),
        },
        "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
        "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",
    }

    low_silence_thinking_off = _base_config(settings)
    low_silence_thinking_off["realtime_input_config"] = {
        "automatic_activity_detection": {
            "start_of_speech_sensitivity": settings.voice_vad_start_sensitivity,
            "end_of_speech_sensitivity": settings.voice_vad_end_sensitivity,
            "prefix_padding_ms": max(20, settings.voice_vad_prefix_padding_ms - 20),
            "silence_duration_ms": max(200, settings.voice_vad_silence_duration_ms - 80),
        },
        "activity_handling": "START_OF_ACTIVITY_INTERRUPTS",
        "turn_coverage": "TURN_INCLUDES_ONLY_ACTIVITY",
    }
    low_silence_thinking_off["thinking_config"] = {"thinking_budget": 0}

    return [
        (
            "baseline",
            DEFAULT_MODEL,
            cast(types.LiveConnectConfigOrDict, baseline),
        ),
        (
            "thinking_off",
            DEFAULT_MODEL,
            cast(types.LiveConnectConfigOrDict, thinking_off),
        ),
        (
            "low_silence_vad",
            DEFAULT_MODEL,
            cast(types.LiveConnectConfigOrDict, low_silence),
        ),
        (
            "low_silence_vad_thinking_off",
            DEFAULT_MODEL,
            cast(types.LiveConnectConfigOrDict, low_silence_thinking_off),
        ),
        (
            "fallback_model_candidate",
            FALLBACK_MODEL_CANDIDATE,
            cast(types.LiveConnectConfigOrDict, baseline),
        ),
    ]


async def main() -> None:
    settings = get_settings()

    if not settings.vertex_ai_key:
        print("VERTEX_AI_KEY is not configured; skipping live probe.")
        return

    rounds = 2
    client = genai.Client(api_key=settings.vertex_ai_key)

    for scenario_name, model, config in _scenarios(settings):
        print(f"\nscenario={scenario_name} model={model}")

        connect_samples: list[float] = []
        first_audio_samples: list[float] = []
        complete_samples: list[float] = []

        for i in range(1, rounds + 1):
            try:
                connect_ms, first_audio_ms, complete_ms = await _single_round(
                    client,
                    model,
                    config,
                )
            except Exception as exc:  # pragma: no cover
                print(f"round {i}: failed ({exc})")
                continue

            connect_samples.append(connect_ms)
            if first_audio_ms > 0:
                first_audio_samples.append(first_audio_ms)
            if complete_ms > 0:
                complete_samples.append(complete_ms)

            print(
                f"round {i}: connect={connect_ms:.1f}ms "
                f"first_audio={first_audio_ms:.1f}ms "
                f"turn_complete={complete_ms:.1f}ms"
            )

        print(_summary("connect", connect_samples))
        print(_summary("first_audio", first_audio_samples))
        print(_summary("turn_complete", complete_samples))


if __name__ == "__main__":
    asyncio.run(main())
