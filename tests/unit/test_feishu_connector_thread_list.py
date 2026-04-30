from __future__ import annotations

import json

from connector.feishu_connector import APPROVAL_ACTION_TYPE, THREAD_LIST_ACTION_TYPE, FeishuConnector
from model.connector_models import ApprovalPrompt, ThreadListPage, ThreadRecord


def test_build_thread_list_card_content_uses_table_and_pagination_buttons():
    page = ThreadListPage(
        items=[
            ThreadRecord(
                thread_id="thread-1",
                name="Alpha",
                cwd="/tmp/project-a",
                preview="preview-a",
                updated_at="2026-04-27 10:00:00",
            )
        ],
        current_cursor=None,
        next_cursor="cursor-2",
        search_term="hello",
    )

    content = json.loads(
        FeishuConnector._build_thread_list_card_content(
            page=page,
            current_thread_id="thread-1",
            can_go_prev=False,
            can_go_next=True,
        )
    )

    elements = content["body"]["elements"]
    assert elements[1]["tag"] == "table"
    assert elements[1]["rows"][0]["cwd"] == "/tmp/project-a"
    assert elements[1]["columns"][2]["name"] == "cwd"

    prev_button = elements[2]
    next_button = elements[3]
    assert prev_button["disabled"] is True
    assert next_button["disabled"] is False
    assert prev_button["behaviors"][0]["value"] == {"type": THREAD_LIST_ACTION_TYPE, "direction": "prev"}
    assert next_button["behaviors"][0]["value"] == {"type": THREAD_LIST_ACTION_TYPE, "direction": "next"}


def test_build_thread_list_card_content_disables_next_button_on_last_page():
    page = ThreadListPage(
        items=[],
        current_cursor="cursor-2",
        next_cursor=None,
        search_term=None,
    )

    content = json.loads(
        FeishuConnector._build_thread_list_card_content(
            page=page,
            current_thread_id=None,
            can_go_prev=True,
            can_go_next=False,
        )
    )

    elements = content["body"]["elements"]
    assert elements[3]["disabled"] is True


def test_build_card_content_keeps_approval_buttons_unchanged():
    prompt = ApprovalPrompt(
        request_id="req-1",
        thread_id="thread-1",
        turn_id="turn-1",
        request_method="item/commandExecution/requestApproval",
        reason="need approval",
        command="touch /tmp/test.txt",
        available_decisions=["accept", "cancel"],
    )

    content = json.loads(FeishuConnector._build_card_content("approval", approval_prompt=prompt))

    elements = content["body"]["elements"]
    approval_buttons = [element for element in elements if element.get("tag") == "button"]
    assert approval_buttons[0]["behaviors"][0]["value"]["type"] == APPROVAL_ACTION_TYPE
    assert approval_buttons[0]["behaviors"][0]["value"]["action"] == "accept"
