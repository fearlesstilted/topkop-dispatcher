"""
Tests for extract_text() — strips hallucinated artifacts from 8b model output.
No LLM calls, no API keys needed.
"""
import os
os.environ.setdefault("GROQ_API_KEY", "test-key")  # prevents sys.exit on import

from app_web import extract_text


def test_plain_string_unchanged():
    assert extract_text("Zajmujemy się wykopami.") == "Zajmujemy się wykopami."


def test_none_returns_empty():
    assert extract_text(None) == ""


def test_strips_leading_artifact():
    raw = "[{'text': 'Dobry tekst odpowiedzi'}"
    result = extract_text(raw)
    assert "Dobry tekst odpowiedzi" in result
    assert "[{" not in result


def test_list_of_blocks_joined():
    raw = [{"text": "Część pierwsza."}, {"text": "Część druga."}]
    result = extract_text(raw)
    assert "Część pierwsza." in result
    assert "Część druga." in result


def test_empty_string_returns_empty():
    assert extract_text("") == ""


def test_non_string_converted():
    assert extract_text(42) == "42"


def test_escaped_newlines_restored():
    raw = "Linia pierwsza.\\nLinia druga."
    result = extract_text(raw)
    assert "\n" in result
