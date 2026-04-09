"""
Tests for strip_kb_for_prompt() — removes token-heavy fields from knowledge base.
No LLM calls needed.
"""
import os
os.environ.setdefault("GROQ_API_KEY", "test-key")

from app_web import strip_kb_for_prompt


SAMPLE_KB = {
    "services": [
        {
            "name": "Roboty ziemne",
            "price": "150 zł/h",
            "source_url": "https://topkop.pl/roboty",       # should be stripped
            "search_keywords": ["wykop", "niwelacja"],      # should be stripped
            "technical_notes": "Głębokość do 6m",           # should be stripped
            "description": "Wykopy, niwelacja terenu",      # should stay
        }
    ],
    "companies": [
        {
            "name": "TOP KOP",
            "phone": "+48 87 520 10 03",
            "source_urls": ["https://topkop.pl"],           # should be stripped
        }
    ]
}


def test_strips_source_url():
    result = strip_kb_for_prompt(SAMPLE_KB)
    service = result["services"][0]
    assert "source_url" not in service


def test_strips_search_keywords():
    result = strip_kb_for_prompt(SAMPLE_KB)
    service = result["services"][0]
    assert "search_keywords" not in service


def test_strips_technical_notes():
    result = strip_kb_for_prompt(SAMPLE_KB)
    service = result["services"][0]
    assert "technical_notes" not in service


def test_keeps_price():
    result = strip_kb_for_prompt(SAMPLE_KB)
    assert result["services"][0]["price"] == "150 zł/h"


def test_keeps_description():
    result = strip_kb_for_prompt(SAMPLE_KB)
    assert result["services"][0]["description"] == "Wykopy, niwelacja terenu"


def test_keeps_company_name_and_phone():
    result = strip_kb_for_prompt(SAMPLE_KB)
    company = result["companies"][0]
    assert company["name"] == "TOP KOP"
    assert company["phone"] == "+48 87 520 10 03"


def test_strips_source_urls_from_company():
    result = strip_kb_for_prompt(SAMPLE_KB)
    assert "source_urls" not in result["companies"][0]


def test_original_not_mutated():
    """strip_kb_for_prompt must not modify the original KB dict."""
    strip_kb_for_prompt(SAMPLE_KB)
    assert "source_url" in SAMPLE_KB["services"][0]
