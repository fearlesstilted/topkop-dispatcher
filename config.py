"""
config.py
=========
Единственный источник констант и переменных окружения.
Все остальные модули импортируют отсюда — никакого os.getenv() в бизнес-логике.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Пути ──────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
KB_PATH         = BASE_DIR / "KnowledgeTopKop.json"
CHAT_LOG_PATH   = BASE_DIR / "chat_log.txt"
MARKETING_PATH  = BASE_DIR / "marketing_leads.csv"

# ── LLM ───────────────────────────────────────────────────────────────────────
MODEL           = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
TEMP            = float(os.getenv("LLM_TEMPERATURE", "0.4"))
MAX_TOK         = int(os.getenv("LLM_MAX_TOKENS", "1024"))

# ── Маркетинг-анализатор (дешевле и быстрее основной модели) ─────────────────
MARKETING_MODEL     = "llama-3.3-70b-versatile"
MARKETING_MAX_TOKENS = 80

# ── UI ────────────────────────────────────────────────────────────────────────
WEB_PORT        = 7860
HISTORY_LIMIT   = 20

# ── Debug ─────────────────────────────────────────────────────────────────────
DEBUG           = os.getenv("DEBUG", "0") == "1"
