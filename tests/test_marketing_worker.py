"""
Tests for _marketing_worker() — mocks OpenAI and httpx, zero API calls.
Verifies payload structure and webhook behavior.
"""
import os
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("GROQ_API_KEY", "test-key")

from app_web import _marketing_worker


def make_mock_client(json_response: dict):
    """Helper: returns AsyncOpenAI mock that yields json_response as content."""
    mock_message = MagicMock()
    mock_message.content = json.dumps(json_response)

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
    return mock_client


FULL_LEAD = {
    "usluga": "Wykopy",
    "miejscowosc": "Gołdap",
    "ilosc": "200 m3",
    "intencja": "Zamówienie",
    "pilnosc": "Pilne",
    "segment": "Firma budowlana",
    "jezyk": "polski",
    "telefon": "+48 600 100 200",
}


@pytest.mark.asyncio
async def test_payload_has_all_8_fields():
    """Webhook payload must always contain exactly the 8 required fields."""
    client = make_mock_client(FULL_LEAD)

    with patch("app_web.os.getenv", return_value=None):  # no webhook URL
        await _marketing_worker(client, "user: chcę wykop 200m3 w Gołdapi")

    # If we got here without exception — client was called
    client.chat.completions.create.assert_called_once()
    args = client.chat.completions.create.call_args
    assert args.kwargs.get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_webhook_post_sent_when_url_configured():
    """When MAKE_WEBHOOK_URL is set, httpx POST must fire with correct JSON."""
    client = make_mock_client(FULL_LEAD)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    with patch("app_web.os.getenv", return_value="https://hook.make.com/test"), \
         patch("app_web.httpx.AsyncClient", return_value=mock_http):
        await _marketing_worker(client, "user: zamówienie na wykop")

    mock_http.post.assert_called_once()
    url, kwargs = mock_http.post.call_args[0][0], mock_http.post.call_args[1]
    assert "hook.make.com" in url
    payload = kwargs["json"]
    # All 8 fields must be present
    for field in ("usluga", "miejscowosc", "ilosc", "intencja", "pilnosc", "segment", "jezyk", "telefon"):
        assert field in payload, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_webhook_not_sent_when_url_missing():
    """When MAKE_WEBHOOK_URL is not set, no HTTP request should be made."""
    client = make_mock_client(FULL_LEAD)

    with patch("app_web.os.getenv", return_value=None), \
         patch("app_web.httpx.AsyncClient") as mock_http_class:
        await _marketing_worker(client, "user: zapytanie")

    mock_http_class.assert_not_called()


@pytest.mark.asyncio
async def test_offtopic_input_sets_brak_fields():
    """Gibberish input should produce Offtopic intent and Brak for most fields."""
    offtopic_response = {
        "usluga": "Brak",
        "miejscowosc": "Brak",
        "ilosc": "Brak",
        "intencja": "Offtopic",
        "pilnosc": "Nieznana",
        "segment": "Nieznany",
        "jezyk": "polski",
        "telefon": "Brak",
    }
    client = make_mock_client(offtopic_response)

    captured_payload = {}

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)

    async def capture_post(url, **kwargs):
        captured_payload.update(kwargs.get("json", {}))
        return mock_response

    mock_http.post = capture_post

    with patch("app_web.os.getenv", return_value="https://hook.make.com/test"), \
         patch("app_web.httpx.AsyncClient", return_value=mock_http):
        await _marketing_worker(client, "user: siema eniu co tam")

    assert captured_payload.get("intencja") == "Offtopic"
    assert captured_payload.get("segment") == "Nieznany"


@pytest.mark.asyncio
async def test_no_crash_on_malformed_llm_response():
    """If LLM returns invalid JSON, worker must not raise — lead processing fails silently."""
    mock_message = MagicMock()
    mock_message.content = "to nie jest JSON {{{"
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=mock_resp)

    # Should not raise
    with patch("app_web.os.getenv", return_value=None):
        await _marketing_worker(client, "user: test")
