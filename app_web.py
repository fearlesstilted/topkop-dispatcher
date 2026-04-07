"""
TOPKOP RAG Dispatcher — Web UI
================================
v5: full audit pass (2026-04-07).
  - Gradio 6 content-block parsing (fixes garbled history bug)
  - Marketing: JSON mode + httpx webhook + smart trigger (>= 2 user msgs)
  - MAX_TOK 1024→300 (enforces brevity, saves ~70% output tokens)
  - Shadow DOM widget, CORS, CSV killed, await marketing (no more create_task)
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

import gradio as gr
try:
    import sentry_sdk as _sentry_sdk  # опциональная зависимость
    _SENTRY_AVAILABLE = True
except ImportError:
    _sentry_sdk = None          # type: ignore
    _SENTRY_AVAILABLE = False
from dotenv import load_dotenv
from openai import APIConnectionError, AsyncOpenAI, AuthenticationError, RateLimitError

load_dotenv()

# ─── Sentry (инициализируем только если пакет установлен И DSN задан в .env) ──
_sentry_dsn = os.getenv("SENTRY_DSN")
if _SENTRY_AVAILABLE and _sentry_dsn:
    _sentry_sdk.init(dsn=_sentry_dsn, traces_sample_rate=1.0)
    print("[OK] Sentry monitoring aktywny.")
elif not _SENTRY_AVAILABLE:
    print("[INFO] sentry-sdk not installed — monitoring disabled.")

# ─── Константы ────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
KB_PATH       = BASE_DIR / "KnowledgeTopKop.json"
CHAT_LOG_PATH = BASE_DIR / "chat_log.txt"

MODEL            = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
TEMP             = float(os.getenv("LLM_TEMPERATURE", "0.4"))
MAX_TOK          = int(os.getenv("LLM_MAX_TOKENS", "300"))   # 2-3 предложения ≈ 80-120 токенов, 300 = с запасом
MARKETING_MODEL  = "llama-3.1-8b-instant"
MARKETING_MAXTOK = 150   # JSON-формат требует больше токенов чем CSV-строка
WEB_PORT         = 7860
HISTORY_LIMIT    = 6    # ~3 пары — компромисс между памятью и Groq free TPM лимитом


# ─── 1. KNOWLEDGE BASE ────────────────────────────────────────────────────────

def load_knowledge_base() -> dict[str, Any]:
    try:
        with open(KB_PATH, encoding="utf-8") as f:
            data = json.load(f)
        print(f"[OK] Baza wiedzy zaladowana: {KB_PATH.name}")
        return data
    except FileNotFoundError:
        print(f"[BLAD] Nie znaleziono: {KB_PATH}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[BLAD] Uszkodzony JSON: {e}")
        sys.exit(1)


# ─── 2. GROQ CLIENT ───────────────────────────────────────────────────────────

def create_client() -> AsyncOpenAI:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("[BLAD] Brak GROQ_API_KEY w .env")
        sys.exit(1)
    # max_retries=0 — падаем сразу при rate limit, не висим 35 сек
    return AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1", max_retries=0)


# ─── 3. KB OPTIMIZER ─────────────────────────────────────────────────────────

# Поля которые жрут токены, но НЕ помогают боту отвечать клиенту
_STRIP_KEYS = frozenset({
    "source_url", "source_urls", "search_keywords", "client_types",
    "applications", "scope_of_work", "technical_notes", "experience_note",
    "advantages_declared", "visual_examples_on_page", "service_area_specific",
    "service_area_general",  # дублируется в промпте текстом
    "equipment_categories", "example_equipment", "machine_specification",
    "torch_specification", "additional_technologies", "quality_and_certification",
    "network_types", "technology", "company_profile", "knowledge_base_name",
    "language", "last_compiled", "since", "nip", "related_services",
    "products_examples", "materials", "operations", "transport_scope",
    "materials_examples", "pricing_model", "sales_notes",  # дублируются в lead_fields
    "special_notes", "precision_mm",  # delivery_note ОСТАВЛЯЕМ — там +7 zł/km
    "response_style", "mandatory_flow",  # уже в system prompt текстом
    "lead_fields_recommended",  # уже в build_prompt как fields_block
})


def strip_kb_for_prompt(kb: dict) -> dict:
    """Рекурсивно убирает поля-балласт из KB. Оригинал не трогает.
    ~6200 токенов → ~2000 токенов. Цены, контакты и описания остаются."""
    def _clean(obj):
        if isinstance(obj, dict):
            return {k: _clean(v) for k, v in obj.items() if k not in _STRIP_KEYS}
        if isinstance(obj, list):
            return [_clean(item) for item in obj]
        return obj
    return _clean(kb)


# ─── 4. SYSTEM PROMPT ─────────────────────────────────────────────────────────

def build_prompt(kb: dict) -> str:
    rules = kb.get("global_sales_rules_for_gpt", {})
    lead_fields = rules.get("lead_fields_recommended", [])
    companies = kb.get("companies", [])

    company_block = "\n".join(
        f"- {c.get('name', '')}: {c.get('address', '')}, tel: {c.get('phone', '')}, e-mail: {c.get('email', '')}"
        for c in companies
    )

    fields_block = "\n".join(f"- {f}" for f in lead_fields) if lead_fields else "- lokalizacja\n- termin\n- ilość\n- warunki dojazdu"

    # Обрезаем KB: убираем source_urls, search_keywords, technical_notes и прочий балласт
    # ~6200 токенов → ~2000 токенов — в 3 раза дешевле каждый запрос
    kb_lean = strip_kb_for_prompt(kb)
    kb_json = json.dumps(kb_lean, ensure_ascii=False, separators=(',', ':'))

    parts = [
        # ── Anti-hallucination guard — стоит первым, чтобы модель видела до контекста ──
        "WAŻNE: Jesteś asystentem TOP KOP. Odpowiadaj TYLKO i WYŁĄCZNIE na podstawie "
        "dostarczonego poniżej kontekstu (cennik i usługi). Jeśli pytają o cenę, której "
        "NIE MA w tekście poniżej, kategorycznie zabraniam Ci zgadywać. Musisz odpowiedzieć: "
        "'Nie mam dokładnych danych w systemie, muszę to skonsultować z kierownikiem'.",
        "",
        "Jesteś głównym dyspozytorem i asystentem sprzedaży firmy TOP KOP oraz RCM Sp. z o.o. w Gołdapi.",
        "Odpowiadasz zawsze po polsku, nawet jeśli klient pisze w innym języku.",
        "",
        "## CEL",
        "Twoim celem jest zebrać konkretny lead (co, ile, gdzie, dojazd) i doprowadzić do wyceny lub rezerwacji.",
        "",
        "## DANE KONTAKTOWE FIRM",
        company_block or "- Brak danych kontaktowych.",
        "",
        "## STYL ODPOWIEDZI (BARDZO WAŻNE)",
        "- Odpowiadaj BARDZO ZWIĘŹLE. Maksymalnie 2-3 krótkie zdania.",
        "- Zwięzły, profesjonalny, ale LUDZKI.",
        "- Zmieniaj słownictwo. Kategoryczny zakaz ciągłego używania słów 'Rozumiem', 'Dobra', 'Jasne' na początku zdań. Brzmisz wtedy jak robot.",
        "- Jeśli oferujecie daną usługę, zacznij od pozytywnego potwierdzenia (np. 'Zajmujemy się tym', 'Oczywiście, robimy takie rozbiórki').",
        "",
        "## NAJWAŻNIEJSZE ZASADY",
        "1. WYCENA INDYWIDUALNA (MÓW TYLKO RAZ): Jeśli w bazie brakuje ceny lub wymaga ona potwierdzenia, poinformuj o tym klienta (np. 'Wycenę przygotuje kierownik po ustaleniu szczegółów'). Zrób to TYLKO RAZ w całej rozmowie. Kategoryczny zakaz powtarzania tego w każdej kolejnej wiadomości!",
        "2. NIE MĘCZ KLIENTA: Zamiast zadawać 5 pytań po kolei i przedłużać rozmowę, po zebraniu kluczowych danych (co, gdzie, dojazd) zakończ rozmowę prośbą o numer telefonu, aby kierownik mógł oddzwonić z gotową wyceną.",
        "3. FORMAT ODPOWIEDZI: ZAWSZE odpowiadaj czystym tekstem (plain text). Żadnych JSON-ów, Markdowna czy list.",
        "4. TROLLING I WULGARYZMY: Jeśli klient przeklina lub prosi o rzeczy nielegalne (zniszczenie auta sąsiada), odpowiedz krótko: 'Nie świadczymy takich usług. Pracujemy tylko przy legalnych zleceniach budowlanych.'",
        "5. SLANG: Słowa takie jak 'wyjebać/rozjebać' traktuj biznesowo jako wyburzenie lub wywóz gruzu.",
        "6. NUMER SZEFA: Nigdy nie podawaj prywatnego numeru szefa. Podawaj tylko numer biura.",
        "7. Ceny podawaj zawsze jako netto + 23% VAT.",
        "8. Beton licz w m3. Kruszywa, ziemię, piasek licz w tonach.",
        "9. POMPOGRUSZKA: Pompogruszka to cena całkowita za 1m³ betonu z usługą pompowania. NIE SUMUJ jej z ceną gruszki ani z niczym innym!",
        "10. KALKULACJA CEN: Jeśli znasz cenę jednostkową i ilość, MOŻESZ podać orientacyjną kwotę łączną. ALE ZAWSZE dodaj: dopłaty za km powyżej 10 km (+7 zł/m³), ewentualną zimową dopłatę (+15%), przestawienie pompy (+100 zł). Zakończ: 'Dokładną wycenę potwierdzi kierownik'.",
        "",
        "## PRIORYTETY ZBIERANIA LEADA",
        fields_block,
        "",
        "## CZEGO NIE ROBIĆ",
        "- nie wymyślaj cen, terminów ani dostępności",
        "- nie powtarzaj w kółko tych samych fraz",
        "- nie zakładaj lokalizacji na podstawie podobnej nazwy ulicy",
        "",
        "## PEŁNA BAZA WIEDZY (cennik i usługi)",
        "Poniżej znajduje się PEŁNY cennik i lista usług firmy TOP KOP. "
        "Analizuj go elastycznie (np. jeśli klient pisze 'c12' lub '12', zrozum, że chodzi o pozycję 'C12/15'). "
        "Jeśli klient pyta o coś, czego fizycznie nie ma w tym tekście, odpowiedz dokładnie: "
        "'Nie mam dokładnych danych w systemie, muszę to skonsultować z kierownikiem'.",
        "",
        kb_json,
    ]

    return "\n".join(parts).strip()


# ─── 5. LLM ───────────────────────────────────────────────────────────────────

def extract_text(raw: Any) -> str:
    """
    Bulletproof text extractor.
    Strips hallucinated [{'text': '...'}] artifacts produced by 8b models.
    Applied on every streaming chunk AND on final output.
    """
    if raw is None:
        return ""
    if isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            else:
                parts.append(str(block))
        return " ".join(p for p in parts if p).strip()

    if not isinstance(raw, str):
        return str(raw).strip()

    text = raw.strip()

    # Cut leading artifact: [{'text': ' or [{"text": "
    text = re.sub(r"^\[\s*\{\s*['\"]text['\"]\s*:\s*['\"]", "", text)
    # Cut trailing artifact: ', 'type': 'text'}] or "} ]
    text = re.sub(r"['\"]\s*(,\s*['\"]type['\"]\s*:\s*['\"]text['\"])?\s*\}\s*\]$", "", text)
    # Restore escaped chars that 8b sometimes emits
    text = text.replace("\\n", "\n").replace("\\'", "'").replace('\\"', '"')

    return text.strip()


async def call_llm_stream(client: AsyncOpenAI, system: str, messages: list[dict]):
    """Async streaming generator — yields raw content chunks from Groq."""
    try:
        stream = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "system", "content": system}] + messages,
            temperature=TEMP,
            max_tokens=MAX_TOK,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
    except AuthenticationError:
        yield "[BLAD] Nieprawidlowy klucz API."
    except RateLimitError:
        yield "[BLAD] Przekroczono limit API."
    except APIConnectionError:
        yield "[BLAD] Brak polaczenia z Groq."
    except Exception as e:
        yield f"[BLAD] {e}"


# ─── 6. LOGGERS ───────────────────────────────────────────────────────────────

def log_chat(user: str, bot: str) -> None:
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(CHAT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] KLIENT:     {user}\n")
            f.write(f"[{ts}] DYSPOZYTOR: {bot}\n")
            f.write("-" * 72 + "\n")
    except OSError as e:
        print(f"[WARN] chat_log: {e}")


async def _marketing_worker(client: AsyncOpenAI, history_context: str) -> None:
    """
    Async: analyses the FULL conversation history, sends JSON lead to Make.com.
    Called with `await` at the end of respond() — blocking is intentional,
    prevents HF Spaces from freezing the container mid-flight and losing the lead.
    """
    system = (
        "Jesteś analitykiem marketingu B2B. Przeanalizuj CAŁĄ historię rozmowy. "
        "Zwróć JSON z dokładnie tymi kluczami (bez żadnych innych słów): "
        "usluga, miejscowosc, ilosc, intencja, pilnosc, segment, jezyk, telefon. "
        "Dozwolone wartości — intencja: Pytanie o cenę|Zamówienie|Wynajem|Kontynuacja rozmowy|Offtopic; "
        "pilnosc: Pilne|Standardowe|Nieznana; "
        "segment: Klient indywidualny|Firma budowlana|Rolnik|Nieznany; "
        "jezyk: polski|rosyjski|angielski|inny. "
        "Jeśli pole nieznane — wartość 'Brak'. "
        "telefon: numer telefonu klienta jeśli podał, inaczej 'Brak'. "
        "ZASADY KRYTYCZNE: "
        "1. Jeśli klient pisze o dojeździe (asfalt), usługą pozostaje ta z początku rozmowy. "
        "2. Firma działa na Mazurach — 'goldapi'/'Goldap' = 'Gołdap'. Nie wymyślaj innych miast."
    )
    try:
        resp = await client.chat.completions.create(
            model=MARKETING_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": history_context},
            ],
            temperature=0.0,
            max_tokens=MARKETING_MAXTOK,
            response_format={"type": "json_object"},  # гарантируем чистый JSON без мусора
        )

        data = json.loads(resp.choices[0].message.content)

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "timestamp":   ts,
            "usluga":      data.get("usluga",      "Brak"),
            "miejscowosc": data.get("miejscowosc",  "Brak"),
            "ilosc":       data.get("ilosc",        "Brak"),
            "intencja":    data.get("intencja",     "Brak"),
            "pilnosc":     data.get("pilnosc",      "Nieznana"),
            "segment":     data.get("segment",      "Nieznany"),
            "jezyk":       data.get("jezyk",        "polski"),
            "telefon":     data.get("telefon",      "Brak"),
        }
        print(f"[MARKETING] Lead: {payload}")

        webhook_url = os.getenv("MAKE_WEBHOOK_URL")
        if not webhook_url:
            # Webhook ещё не настроен — данные залогированы выше, лид не потерян
            print("[MARKETING] MAKE_WEBHOOK_URL не задан — пропускаю POST (данные выше в логе)")
            return

        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(webhook_url, json=payload)
            r.raise_for_status()
            print(f"[MARKETING] Webhook OK → {r.status_code}")

    except Exception as e:
        print(f"[MARKETING CRITICAL] Lead processing failed: {e}")
        print(f"[MARKETING CRITICAL] Context snippet: {history_context[:300]}")
        if _SENTRY_AVAILABLE and _sentry_dsn:
            _sentry_sdk.capture_message(
                f"Lead lost — error: {e}",
                level="fatal",
                extras={"history": history_context[:1000]},
            )


# ─── 7. CHAT HANDLER (STREAMING) ──────────────────────────────────────────────

def make_respond(kb: dict, client: AsyncOpenAI):
    """
    Factory returning an async streaming respond() generator for gr.Blocks.
    Gradio 6 natively supports async generators — SSE streaming works out of box.
    """
    async def respond(
        message: str,
        history: list[dict],
    ) -> AsyncGenerator[tuple[str, list[dict]], None]:

        if not message or not message.strip():
            yield "", history
            return

        # Build OpenAI-compatible message list from Gradio history
        openai_msgs: list[dict] = []
        for item in history:
            if isinstance(item, dict):
                role = item.get("role", "")
                raw  = item.get("content", "")
                # Gradio 6 может хранить content как [{type:"text", text:"..."}]
                # str([...]) превратит это в мусор → парсим блоки правильно
                if isinstance(raw, list):
                    content = "".join(
                        b.get("text", "") for b in raw if isinstance(b, dict)
                    )
                else:
                    content = str(raw or "")
                if role in ("user", "assistant") and content.strip():
                    openai_msgs.append({"role": role, "content": content})

        if len(openai_msgs) > HISTORY_LIMIT:
            openai_msgs = openai_msgs[-HISTORY_LIMIT:]

        system = build_prompt(kb)  # весь KB в промпт — Context Stuffing

        openai_msgs.append({"role": "user", "content": message})

        # Append user + empty assistant bubble to history
        updated = list(history) + [
            {"role": "user",      "content": message},
            {"role": "assistant", "content": ""},
        ]

        # First yield: show user message immediately, empty assistant bubble
        yield "", updated

        full_answer = ""

        async for chunk in call_llm_stream(client, system, openai_msgs):
            full_answer += chunk
            updated[-1]["content"] = full_answer
            yield "", updated
            await asyncio.sleep(0.01)  # Non-blocking smooth typing effect

        # extract_text только на финале — страховка от редких артефактов 70b
        final_answer = extract_text(full_answer)
        updated[-1]["content"] = final_answer
        yield "", updated

        log_chat(message, final_answer)

        # Маркетинг-анализ — только когда в диалоге есть хоть какое-то содержание.
        # "Cześć" + ответ бота = не лид. Экономит ~50% вызовов 8b.
        user_msg_count = sum(1 for m in openai_msgs if m["role"] == "user")
        if user_msg_count >= 2:
            marketing_msgs = openai_msgs[-3:] if len(openai_msgs) > 3 else openai_msgs
            history_for_marketing = "\n".join(
                f"{m['role']}: {m['content']}" for m in marketing_msgs
            ) + f"\nassistant: {final_answer}"
            await _marketing_worker(client, history_for_marketing)

    return respond


# ─── 8. UI ────────────────────────────────────────────────────────────────────

CSS = """
footer { display: none !important; }
#send-btn { background: #e07b00 !important; color: white !important; }
"""


def build_ui(kb: dict, client: AsyncOpenAI) -> gr.Blocks:
    import gradio as _gr
    print(f"[INFO] Gradio {_gr.__version__}")

    respond = make_respond(kb, client)

    with gr.Blocks(title="TOP KOP - Dyspozytor", css=CSS) as demo:

        gr.Markdown(
            "## TOP KOP - Dyspozytor\n"
            "Witamy w systemie obslugi zamowien **TOP KOP** i **RCM Sp. z o.o.**\n"
            "Napisz czego potrzebujesz - odpowiemy natychmiast."
        )

        chatbot = gr.Chatbot(
            value=[],
            height=480,
            show_label=False,
        )

        with gr.Row():
            msg = gr.Textbox(
                placeholder="Napisz zapytanie...",
                show_label=False,
                scale=9,
                lines=1,
                max_lines=4,
                autofocus=True,
            )
            btn = gr.Button("Wyslij", scale=1, elem_id="send-btn", variant="primary")

        clear = gr.Button("Wyczysc rozmowe", size="sm", variant="secondary")

        # api_name="chat" exposes a stable named endpoint for the widget
        msg.submit(respond, [msg, chatbot], [msg, chatbot], api_name="chat")
        btn.click(respond,  [msg, chatbot], [msg, chatbot], api_name=False)

        clear.click(fn=lambda: ([], ""), outputs=[chatbot, msg])

    return demo


# ─── 9. MAIN ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  TOP KOP RAG Dispatcher - Web UI (async + streaming)")
    print(f"  Model   : {MODEL}  |  Temp: {TEMP}  |  MaxTok: {MAX_TOK}")
    print(f"  ChatLog : {CHAT_LOG_PATH.resolve()}")
    print(f"  Webhook : {os.getenv('MAKE_WEBHOOK_URL', 'NOT SET — leads logged to console only')}")
    print("=" * 60)

    kb     = load_knowledge_base()
    client = create_client()
    demo   = build_ui(kb, client)

    demo.launch(
        server_name="0.0.0.0",
        server_port=WEB_PORT,
        share=True,
        show_error=True,
        strict_cors=False,  # виджет грузится с внешнего домена → ослабляем CORS
    )


if __name__ == "__main__":
    main()
