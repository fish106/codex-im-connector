from __future__ import annotations

import asyncio

import pytest

from client.codex_runtime import CodexRuntime
from core.config import get_settings
from model.connector_models import AvailableModelInfo


class DummySessionService:
    pass


class DummyApprovalService:
    pass


@pytest.mark.asyncio
async def test_resolve_effective_model_config_uses_default_model_metadata():
    runtime = CodexRuntime(
        settings=get_settings(),
        session_service=DummySessionService(),
        approval_service=DummyApprovalService(),
        loop=asyncio.get_running_loop(),
    )
    runtime._models_cache = [
        AvailableModelInfo(
            model_id="gpt-5.5",
            display_name="GPT-5.5",
            is_default=True,
            default_reasoning_effort="medium",
            supported_reasoning_efforts=["low", "medium", "high"],
        )
    ]

    model_name, reasoning_effort = await runtime._resolve_effective_model_config(None, None)

    assert model_name == "gpt-5.5"
    assert reasoning_effort == "medium"
