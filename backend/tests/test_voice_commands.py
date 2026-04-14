from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    def _get_test_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _get_test_db
    yield TestClient(app)
    app.dependency_overrides.clear()


class _FakeSession:
    def __init__(self):
        self.dispatch_control_command = AsyncMock(return_value="graceful_close_started")


class TestVoiceControlCommandRouting:
    def test_local_command_dispatch(self, client):
        fake_session = _FakeSession()

        with patch("app.routers.voice.get_active_sessions", return_value={"CA_LOCAL": fake_session}):
            r = client.post(
                "/api/v1/voice/commands/CA_LOCAL",
                json={"command": "end_call", "payload": {"reason": "operator"}},
            )

        assert r.status_code == 200
        data = r.json()
        assert data["accepted"] is True
        assert data["dispatched_to"] == "local"
        assert data["result"] == "graceful_close_started"

    def test_remote_command_dispatch_via_bus(self, client):
        fake_bus = MagicMock()
        fake_bus.enabled = True
        fake_bus.publish = AsyncMock(return_value=None)

        fake_store = MagicMock()
        fake_store.get = AsyncMock(return_value={"call_sid": "CA_REMOTE"})

        with patch("app.routers.voice.get_active_sessions", return_value={}), \
             patch("app.routers.voice.get_voice_command_bus", return_value=fake_bus), \
             patch("app.routers.voice.get_voice_state_store", return_value=fake_store):
            r = client.post(
                "/api/v1/voice/commands/CA_REMOTE",
                json={"command": "end_call", "payload": {"reason": "operator"}},
            )

        assert r.status_code == 200
        data = r.json()
        assert data["accepted"] is True
        assert data["dispatched_to"] == "command_bus"
        assert data["result"] == "queued_for_owner"
        fake_bus.publish.assert_awaited_once_with("CA_REMOTE", "end_call", {"reason": "operator"})

    def test_remote_command_requires_checkpoint(self, client):
        fake_bus = MagicMock()
        fake_bus.enabled = True
        fake_bus.publish = AsyncMock(return_value=None)

        fake_store = MagicMock()
        fake_store.get = AsyncMock(return_value=None)

        with patch("app.routers.voice.get_active_sessions", return_value={}), \
             patch("app.routers.voice.get_voice_command_bus", return_value=fake_bus), \
             patch("app.routers.voice.get_voice_state_store", return_value=fake_store):
            r = client.post(
                "/api/v1/voice/commands/CA_REMOTE",
                json={"command": "end_call"},
            )

        assert r.status_code == 404

    def test_unsupported_command_rejected(self, client):
        r = client.post(
            "/api/v1/voice/commands/CA_ANY",
            json={"command": "mute"},
        )

        assert r.status_code == 422
