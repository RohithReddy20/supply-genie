"""Redis-backed ephemeral checkpoint store for active voice sessions."""
from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger("backend.voice_state_store")


class VoiceStateStore:
    """Persist and fetch active-call snapshots in Redis with TTL."""

    def __init__(self, redis_url: str, ttl_s: int) -> None:
        self._redis_url = redis_url
        self._ttl_s = ttl_s
        self._client: Redis | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._redis_url)

    async def checkpoint(self, call_sid: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        client = await self._get_client()
        if not client:
            return
        key = self._key(call_sid)
        await client.set(key, json.dumps(payload), ex=self._ttl_s)

    async def delete(self, call_sid: str) -> None:
        if not self.enabled:
            return
        client = await self._get_client()
        if not client:
            return
        await client.delete(self._key(call_sid))

    async def get(self, call_sid: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        client = await self._get_client()
        if not client:
            return None
        payload = await client.get(self._key(call_sid))
        if not payload:
            return None
        return json.loads(payload)

    async def _get_client(self) -> Redis | None:
        if self._client:
            return self._client
        try:
            self._client = Redis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()
            return self._client
        except Exception:
            logger.exception("Voice state store unavailable; checkpoints disabled until next attempt")
            self._client = None
            return None

    @staticmethod
    def _key(call_sid: str) -> str:
        return f"voice-session:{call_sid}"


_store: VoiceStateStore | None = None


def get_voice_state_store() -> VoiceStateStore:
    global _store
    if _store is None:
        settings = get_settings()
        _store = VoiceStateStore(
            redis_url=settings.voice_state_redis_url,
            ttl_s=settings.voice_state_ttl_s,
        )
    return _store
