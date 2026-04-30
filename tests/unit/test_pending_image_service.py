from __future__ import annotations

import pytest

from service.pending_image_service import PendingImageService


@pytest.mark.asyncio
async def test_pending_image_service_adds_and_reads_images_in_order():
    service = PendingImageService(max_images_per_user=3)

    count1 = await service.add_image("ou_1", "m1", "img1")
    count2 = await service.add_image("ou_1", "m2", "img2")
    images = await service.get_images("ou_1")

    assert count1 == 1
    assert count2 == 2
    assert [item.message_id for item in images] == ["m1", "m2"]
    assert [item.image_key for item in images] == ["img1", "img2"]


@pytest.mark.asyncio
async def test_pending_image_service_rejects_more_than_limit():
    service = PendingImageService(max_images_per_user=3)
    await service.add_image("ou_1", "m1", "img1")
    await service.add_image("ou_1", "m2", "img2")
    await service.add_image("ou_1", "m3", "img3")

    with pytest.raises(ValueError, match="too many pending images"):
        await service.add_image("ou_1", "m4", "img4")


@pytest.mark.asyncio
async def test_pending_image_service_clear_removes_state():
    service = PendingImageService(max_images_per_user=3)
    await service.add_image("ou_1", "m1", "img1")

    assert await service.has_images("ou_1") is True
    await service.clear("ou_1")
    assert await service.has_images("ou_1") is False
