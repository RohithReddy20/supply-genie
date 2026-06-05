"""Redis-backed command bus for active voice sessions."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger("backend.voice_command_bus")


class VoiceCommandBus:
    """Publish and consume per-call control commands (e.g. end_call)."""

    def __init__(self, redis_url: str, queue_ttl_s: int) -> None:
        self._redis_url = redis_url
        self._queue_ttl_s = queue_ttl_s
        self._client: Redis | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._redis_url)

    async def publish(self, call_sid: str, command: str, payload: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            raise RuntimeError("Voice command bus is not enabled")

        client = await self._get_client()
        if not client:
            raise RuntimeError("Voice command bus unavailable")

        key = self._key(call_sid)
        message = {
            "call_sid": call_sid,
            "command": command,
            "payload": payload or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await client.rpush(key, json.dumps(message))
        await client.expire(key, self._queue_ttl_s)

    async def pop(self, call_sid: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        client = await self._get_client()
        if not client:
            return None

        raw = await client.lpop(self._key(call_sid))
        if not raw:
            return None
        return json.loads(raw)

    async def _get_client(self) -> Redis | None:
        if self._client:
            return self._client
        try:
            self._client = Redis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()
            return self._client
        except Exception:
            logger.exception("Voice command bus unavailable; commands cannot be routed")
            self._client = None
            return None

    @staticmethod
    def _key(call_sid: str) -> str:
        return f"voice-command:{call_sid}"


_bus: VoiceCommandBus | None = None


def get_voice_command_bus() -> VoiceCommandBus:
    global _bus
    if _bus is None:
        settings = get_settings()
        _bus = VoiceCommandBus(
            redis_url=settings.voice_state_redis_url,
            queue_ttl_s=settings.voice_command_queue_ttl_s,
        )
    return _bus
