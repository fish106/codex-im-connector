from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from client.codex_runtime import CodexRuntime
from core.config import get_settings


class DummySessionService:
    pass


class DummyApprovalService:
    pass


class RetryableTestError(RuntimeError):
    pass


def build_runtime() -> CodexRuntime:
    return CodexRuntime(
        settings=get_settings(),
        session_service=DummySessionService(),
        approval_service=DummyApprovalService(),
        loop=asyncio.get_running_loop(),
    )


@pytest.mark.asyncio
async def test_list_threads_uses_async_codex_thread_list_and_maps_records():
    calls: list[tuple[bool, str | None, str | None, int | None]] = []

    class FakeCodex:
        async def thread_list(self, *, archived=None, search_term=None, cursor=None, limit=None):
            calls.append((archived, search_term, cursor, limit))
            return SimpleNamespace(
                next_cursor="cursor-2",
                data=[
                    SimpleNamespace(
                        id="thread-1",
                        name="Alpha",
                        cwd=SimpleNamespace(root="/tmp/project-a"),
                        preview="preview-a",
                        updated_at=1712345678,
                    ),
                    SimpleNamespace(
                        id="thread-2",
                        name=None,
                        cwd=SimpleNamespace(root="/tmp/project-b"),
                        preview="preview-b",
                        updated_at=1712345688,
                    ),
                ]
            )

    runtime = build_runtime()
    runtime._codex = FakeCodex()

    page = await runtime.list_threads("ou_1", search_term="hello", cursor="cursor-1", limit=5)

    assert calls == [(False, "hello", "cursor-1", 5)]
    assert [record.thread_id for record in page.items] == ["thread-1", "thread-2"]
    assert page.items[0].name == "Alpha"
    assert page.items[0].cwd == "/tmp/project-a"
    assert page.items[0].preview == "preview-a"
    assert page.current_cursor == "cursor-1"
    assert page.next_cursor == "cursor-2"


@pytest.mark.asyncio
async def test_list_threads_retries_retryable_error(monkeypatch):
    attempts = 0

    class FakeCodex:
        async def thread_list(self, *, archived=None, search_term=None, cursor=None, limit=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RetryableTestError("busy")
            return SimpleNamespace(
                next_cursor=None,
                data=[
                    SimpleNamespace(
                        id="thread-1",
                        name="Alpha",
                        cwd=SimpleNamespace(root="/tmp/project-a"),
                        preview="preview-a",
                        updated_at=1,
                    )
                ],
            )

    runtime = build_runtime()
    runtime._codex = FakeCodex()
    monkeypatch.setattr("client.codex_runtime.is_retryable_error", lambda exc: isinstance(exc, RetryableTestError))

    page = await runtime.list_threads("ou_1")

    assert attempts == 2
    assert page.items[0].thread_id == "thread-1"


@pytest.mark.asyncio
async def test_list_threads_does_not_retry_non_retryable_error(monkeypatch):
    attempts = 0

    class FakeCodex:
        async def thread_list(self, *, archived=None, search_term=None, cursor=None, limit=None):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("boom")

    runtime = build_runtime()
    runtime._codex = FakeCodex()
    monkeypatch.setattr("client.codex_runtime.is_retryable_error", lambda exc: False)

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.list_threads("ou_1")

    assert attempts == 1
