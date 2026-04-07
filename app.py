"""
HF Spaces entry point — просто импортирует и запускает app_web.main()
с параметрами для HF (без share, без custom port).
"""
import os
import json
import sys
from pathlib import Path

# HF Spaces секреты → environment variables (уже доступны через os.getenv)
# .env файл НЕ нужен на HF — секреты заданы через Settings > Secrets

# Подменяем launch параметры для HF Spaces
import app_web

# Перезаписываем main() чтобы убрать share=True (HF сам проксирует)
def hf_main():
    print("=" * 60)
    print("  TOPKOP Dispatcher — HF Spaces mode")
    print("=" * 60)

    kb     = app_web.load_knowledge_base()
    client = app_web.create_client()
    demo   = app_web.build_ui(kb, client)

    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True,
        strict_cors=False,
    )

if __name__ == "__main__":
    hf_main()
else:
    # HF Spaces может импортировать напрямую
    hf_main()
