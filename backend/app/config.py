from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "Supply Chain Coordinator")
    environment: str = os.getenv("APP_ENV", "development")
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://rohith:Rohith%4018@localhost:5432/happy_robot",
    )
    require_human_approval: bool = os.getenv("REQUIRE_HUMAN_APPROVAL", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Slack
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
    slack_default_channel: str = os.getenv("SLACK_DEFAULT_CHANNEL", "#general")

    # Twilio Voice
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from_number: str = os.getenv("TWILIO_FROM_NUMBER", "")
    twilio_default_to: str = os.getenv("TWILIO_DEFAULT_TO", "")
    twilio_mock_mode: bool = os.getenv("TWILIO_MOCK_MODE", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    # Resend (Email)
    resend_api_key: str = os.getenv("RESEND_API_KEY", "")
    email_from: str = os.getenv("EMAIL_FROM", "coordinator@resend.dev")

    # Gemini (Google GenAI)
    vertex_ai_key: str = os.getenv("VERTEX_AI_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # Retry
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))


def get_settings() -> Settings:
    return Settings()
