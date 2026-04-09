"""
Tests for build_prompt() — checks that system prompt contains required sections.
No LLM calls needed.
"""
import os
os.environ.setdefault("GROQ_API_KEY", "test-key")

from app_web import build_prompt


MINIMAL_KB = {
    "companies": [
        {
            "name": "TOP KOP",
            "address": "ul. Graniczna 3, Gołdap",
            "phone": "+48 87 520 10 03",
            "email": "biuro.topkop@gmail.com"
        }
    ],
    "global_sales_rules_for_gpt": {
        "lead_fields_recommended": ["lokalizacja", "termin", "ilość"]
    },
    "services": []
}


def test_prompt_contains_company_phone():
    prompt = build_prompt(MINIMAL_KB)
    assert "+48 87 520 10 03" in prompt


def test_prompt_contains_company_name():
    prompt = build_prompt(MINIMAL_KB)
    assert "TOP KOP" in prompt


def test_prompt_contains_lead_fields():
    prompt = build_prompt(MINIMAL_KB)
    assert "lokalizacja" in prompt
    assert "termin" in prompt


def test_prompt_contains_style_rules():
    prompt = build_prompt(MINIMAL_KB)
    assert "STYL" in prompt


def test_prompt_contains_anti_hallucination_guard():
    """Critical: bot must never invent prices."""
    prompt = build_prompt(MINIMAL_KB)
    assert "kierownikiem" in prompt  # fallback to manager, not invented price


def test_prompt_contains_nonstandard_orders_rule():
    """Rule 11: bot should not hard-refuse unknown services."""
    prompt = build_prompt(MINIMAL_KB)
    assert "ZAMÓWIENIA NIESTANDARDOWE" in prompt


def test_prompt_is_string():
    prompt = build_prompt(MINIMAL_KB)
    assert isinstance(prompt, str)
    assert len(prompt) > 500
