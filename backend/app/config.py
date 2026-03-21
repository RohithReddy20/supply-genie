from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, min_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if min_value is not None:
        return max(min_value, value)
    return value


def _env_float(name: str, default: float, *, min_value: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    if min_value is not None:
        return max(min_value, value)
    return value


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Supply Chain Coordinator")
    environment: str = os.getenv("APP_ENV", "development")
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://rohith:Rohith%4018@localhost:5432/happy_robot",
    )
    require_human_approval: bool = _env_bool("REQUIRE_HUMAN_APPROVAL", True)

    # Slack
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
    slack_default_channel: str = os.getenv("SLACK_DEFAULT_CHANNEL", "#general")

    # Twilio Voice
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from_number: str = os.getenv("TWILIO_FROM_NUMBER", "")
    twilio_default_to: str = os.getenv("TWILIO_DEFAULT_TO", "")
    twilio_mock_mode: bool = _env_bool("TWILIO_MOCK_MODE", False)

    # Resend (Email)
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    email_from: str = os.getenv("EMAIL_FROM", "coordinator@resend.dev")

    # Gemini (Google GenAI)
    vertex_ai_key: str = os.getenv("VERTEX_AI_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_live_model: str = os.getenv(
        "GEMINI_LIVE_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"
    )

    # Voice latency tuning
    voice_opening_prompt_delay_s: float = _env_float(
        "VOICE_OPENING_PROMPT_DELAY_S", 0.03, min_value=0.0
    )
    voice_audio_batch_ms: int = _env_int("VOICE_AUDIO_BATCH_MS", 40, min_value=10)
    voice_inbound_audio_queue_max: int = _env_int(
        "VOICE_INBOUND_AUDIO_QUEUE_MAX", 24, min_value=2
    )
    voice_outbound_audio_queue_max: int = _env_int(
        "VOICE_OUTBOUND_AUDIO_QUEUE_MAX", 24, min_value=2
    )

    # Gemini Live VAD tuning
    voice_vad_start_sensitivity: str = os.getenv(
        "VOICE_VAD_START_SENSITIVITY", "START_SENSITIVITY_HIGH"
    )
    voice_vad_end_sensitivity: str = os.getenv(
        "VOICE_VAD_END_SENSITIVITY", "END_SENSITIVITY_HIGH"
    )
    voice_vad_prefix_padding_ms: int = _env_int(
        "VOICE_VAD_PREFIX_PADDING_MS", 60, min_value=0
    )
    voice_vad_silence_duration_ms: int = _env_int(
        "VOICE_VAD_SILENCE_DURATION_MS", 260, min_value=100
    )
    voice_thinking_budget: int = _env_int("VOICE_THINKING_BUDGET", 0)

    # Retry
    max_retries: int = _env_int("MAX_RETRIES", 3, min_value=1)


def get_settings() -> Settings:
    return Settings()
