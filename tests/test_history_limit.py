"""
Tests for chat history trimming — verifies HISTORY_LIMIT is enforced.
No API calls needed.
"""
import os
os.environ.setdefault("GROQ_API_KEY", "test-key")

from app_web import HISTORY_LIMIT


def trim_history(messages: list, limit: int) -> list:
    """Replicates the trimming logic from respond() in app_web.py."""
    if len(messages) > limit:
        return messages[-limit:]
    return messages


def make_messages(n: int) -> list:
    """Generate n alternating user/assistant messages."""
    result = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        result.append({"role": role, "content": f"message {i}"})
    return result


def test_short_history_not_trimmed():
    msgs = make_messages(4)
    assert trim_history(msgs, HISTORY_LIMIT) == msgs


def test_long_history_trimmed_to_limit():
    msgs = make_messages(20)
    result = trim_history(msgs, HISTORY_LIMIT)
    assert len(result) == HISTORY_LIMIT


def test_trimmed_history_keeps_latest_messages():
    msgs = make_messages(10)
    result = trim_history(msgs, HISTORY_LIMIT)
    assert result == msgs[-HISTORY_LIMIT:]


def test_history_limit_value_is_sane():
    """HISTORY_LIMIT should be between 4 and 20 — sanity check."""
    assert 4 <= HISTORY_LIMIT <= 20


def test_empty_history_not_trimmed():
    assert trim_history([], HISTORY_LIMIT) == []
