#!/usr/bin/env python3
"""Test script for the real-time voice agent.

Usage:
  1. Terminal 1:  uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
  2. Terminal 2:  ngrok http 8000
  3. Terminal 3:  uv run python test_voice.py <NGROK_URL>

Example:
  uv run python test_voice.py https://abc123.ngrok-free.app
"""
from __future__ import annotations

import sys
import os

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    ngrok_url = sys.argv[1].rstrip("/")
    print(f"\n🔗 Using ngrok URL: {ngrok_url}")

    from app.config import get_settings
    settings = get_settings()

    # ── 1. Verify credentials ────────────────────────────────────────────
    print("\n── Step 1: Verify credentials ──")

    if not settings.vertex_ai_key:
        print("❌ VERTEX_AI_KEY not set in .env")
        sys.exit(1)
    print(f"  ✅ Gemini API key: {settings.vertex_ai_key[:8]}...")

    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        print("❌ Twilio credentials not set in .env")
        sys.exit(1)
    print(f"  ✅ Twilio SID: {settings.twilio_account_sid[:8]}...")
    print(f"  ✅ Twilio From: {settings.twilio_from_number}")
    print(f"  ✅ Twilio To: {settings.twilio_default_to}")

    # ── 2. Test Gemini Live API connection ───────────────────────────────
    print("\n── Step 2: Test Gemini Live API connection ──")
    import asyncio
    from google import genai

    async def test_gemini_live():
        client = genai.Client(api_key=settings.vertex_ai_key)
        config = {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": "Kore"},
                },
            },
            "output_audio_transcription": {},
        }
        try:
            async with client.aio.live.connect(
                model="gemini-2.5-flash-native-audio-preview-12-2025",
                config=config,
            ) as session:
                from google.genai import types
                await session.send_client_content(
                    turns=types.Content(
                        role="user",
                        parts=[types.Part(text="Say hello in one sentence.")],
                    ),
                    turn_complete=True,
                )
                got_audio = False
                async for msg in session.receive():
                    sc = msg.server_content
                    if not sc:
                        continue
                    if sc.output_transcription and sc.output_transcription.text:
                        print(f"  ✅ Gemini Live says: {sc.output_transcription.text}")
                    if sc.model_turn:
                        for part in sc.model_turn.parts:
                            if part.inline_data and part.inline_data.data and not got_audio:
                                print(f"  ✅ Audio: {len(part.inline_data.data)} bytes")
                                got_audio = True
                    if sc.turn_complete:
                        break
                return got_audio
        except Exception as e:
            print(f"  ❌ Gemini Live error: {e}")
            return False

    if not asyncio.run(test_gemini_live()):
        sys.exit(1)

    # ── 3. Configure Twilio webhook ──────────────────────────────────────
    print("\n── Step 3: Configure Twilio webhook ──")
    webhook_url = f"{ngrok_url}/api/v1/voice/incoming"
    print(f"  📞 Webhook URL: {webhook_url}")

    from twilio.rest import Client as TwilioClient
    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token)

    # Find and update the phone number
    numbers = client.incoming_phone_numbers.list(phone_number=settings.twilio_from_number)
    if numbers:
        number = numbers[0]
        number.update(voice_url=webhook_url, voice_method="POST")
        print(f"  ✅ Updated {settings.twilio_from_number} webhook → {webhook_url}")
    else:
        print(f"  ⚠️  Could not find number {settings.twilio_from_number} in your account.")
        print(f"     Manually set the voice webhook to: {webhook_url}")

    # ── 4. Test options ──────────────────────────────────────────────────
    print("\n── Step 4: Ready to test! ──")
    print()
    print("  Option A — INBOUND CALL (recommended for first test):")
    print(f"    📱 Call {settings.twilio_from_number} from your phone")
    print(f"    The AI coordinator will answer and you can talk to it.")
    print()

    if settings.twilio_default_to:
        print("  Option B — OUTBOUND CALL (via API):")
        print(f"    curl -X POST {ngrok_url}/api/v1/voice/outbound \\")
        print(f'      -H "Content-Type: application/json" \\')
        print(f'      -d \'{{"to": "{settings.twilio_default_to}"}}\'')
        print()

    print("  Option C — OUTBOUND CALL WITH INCIDENT CONTEXT:")
    print(f"    # First get an incident ID from your DB, then:")
    print(f"    curl -X POST {ngrok_url}/api/v1/voice/outbound \\")
    print(f'      -H "Content-Type: application/json" \\')
    print(f'      -d \'{{"to": "{settings.twilio_default_to}", "incident_id": "<INCIDENT_UUID>"}}\'')
    print()

    print("  📊 Monitor logs in Terminal 1 for real-time pipeline activity.")
    print("  📝 After the call, check sessions and transcripts:")
    print(f"    curl {ngrok_url}/api/v1/voice/sessions | python3 -m json.tool")
    print()

    # ── 5. Trial account warning ─────────────────────────────────────────
    print("  ⚠️  TWILIO TRIAL LIMITATIONS:")
    print("    • You'll hear a trial announcement at the start of each call")
    print("    • Only verified numbers can be called (add at twilio.com/console)")
    print("    • Inbound calls work from any phone to your Twilio number")
    print()


if __name__ == "__main__":
    main()
