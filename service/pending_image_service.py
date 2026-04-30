from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class PendingImageItem:
    message_id: str
    image_key: str
    created_at: datetime


class PendingImageService:
    def __init__(self, max_images_per_user: int = 3) -> None:
        self._max_images_per_user = max_images_per_user
        self._lock = asyncio.Lock()
        self._pending: dict[str, list[PendingImageItem]] = {}

    async def add_image(self, user_open_id: str, message_id: str, image_key: str) -> int:
        async with self._lock:
            images = self._pending.setdefault(user_open_id, [])
            if len(images) >= self._max_images_per_user:
                raise ValueError("too many pending images")
            images.append(
                PendingImageItem(
                    message_id=message_id,
                    image_key=image_key,
                    created_at=datetime.now(UTC),
                )
            )
            return len(images)

    async def get_images(self, user_open_id: str) -> list[PendingImageItem]:
        async with self._lock:
            return list(self._pending.get(user_open_id, []))

    async def clear(self, user_open_id: str) -> None:
        async with self._lock:
            self._pending.pop(user_open_id, None)

    async def has_images(self, user_open_id: str) -> bool:
        async with self._lock:
            return bool(self._pending.get(user_open_id))
