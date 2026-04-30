from __future__ import annotations

from types import SimpleNamespace

from connector.feishu_connector import FeishuConnector


def test_parse_inbound_supports_image_message():
    event = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id="m1",
                chat_id="oc_1",
                chat_type="p2p",
                message_type="image",
                content='{"image_key":"img-key-1"}',
            ),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_1")),
        )
    )

    connector = FeishuConnector.__new__(FeishuConnector)
    inbound = connector._parse_inbound(event)

    assert inbound is not None
    assert inbound.message_type == "image"
    assert inbound.image_key == "img-key-1"
    assert inbound.text == ""
