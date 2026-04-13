from agent_studio.ui.internal_links import (
    MESSAGE_LINK_SCHEME,
    TASK_LINK_SCHEME,
    build_internal_link,
    message_anchor_name,
    parse_internal_link,
)


def test_internal_link_round_trip_for_task_and_message() -> None:
    task_link = build_internal_link(TASK_LINK_SCHEME, "task-123")
    message_link = build_internal_link(MESSAGE_LINK_SCHEME, "msg-abc")

    assert parse_internal_link(task_link) == (TASK_LINK_SCHEME, "task-123")
    assert parse_internal_link(message_link) == (MESSAGE_LINK_SCHEME, "msg-abc")


def test_parse_internal_link_ignores_unknown_and_empty_targets() -> None:
    assert parse_internal_link("https://example.com") is None
    assert parse_internal_link("task://") is None
    assert parse_internal_link("run://task-1") is None
    assert parse_internal_link("task://task-22?from=message-1") == (
        TASK_LINK_SCHEME,
        "task-22",
    )


def test_message_anchor_name_is_stable() -> None:
    assert message_anchor_name("msg-77") == "message-msg-77"
    assert message_anchor_name("  msg-88  ") == "message-msg-88"
