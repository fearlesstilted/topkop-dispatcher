"""
TOPKOP RAG Dispatcher — Web UI
================================
v4: async LLM + Sentry monitoring + anti-hallucination guard.
Changes vs v3:
  - OpenAI → AsyncOpenAI, all LLM calls are non-blocking
  - threading replaced by asyncio.create_task for marketing logger
  - sentry_sdk init (only when SENTRY_DSN is set in .env)
  - build_prompt: strict anti-hallucination rule injected at top
"""

import asyncio
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

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
BASE_DIR         = Path(__file__).parent
KB_PATH          = BASE_DIR / "KnowledgeTopKop.json"
CHAT_LOG_PATH    = BASE_DIR / "chat_log.txt"
MARKETING_PATH   = BASE_DIR / "marketing_leads.csv"

MODEL            = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
TEMP             = float(os.getenv("LLM_TEMPERATURE", "0.4"))
MAX_TOK          = int(os.getenv("LLM_MAX_TOKENS", "1024"))
MARKETING_MODEL  = "llama-3.1-8b-instant"
MARKETING_MAXTOK = 80
WEB_PORT         = 7860
HISTORY_LIMIT    = 4    # 2 пары вопрос-ответ — больше не нужно, экономим TPM


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


# ─── 3. KB FORMATTER ─────────────────────────────────────────────────────────

def format_kb_to_markdown(kb: dict) -> str:
    """Converts KB dict to readable Markdown — lighter than JSON, better for LLM."""
    lines = []

    # Usługi
    lines.append("## USŁUGI")
    for s in kb.get("services", []):
        lines.append(f"\n### {s.get('name', '')} [{s.get('company_id', '').upper()}]")
        if s.get("description"):
            lines.append(s["description"])
        pricing = s.get("pricing", {})
        if pricing.get("unit_price"):
            lines.append(f"Cena: {pricing['unit_price']} {pricing.get('currency', 'PLN')}/{pricing.get('unit', '')}")
        elif pricing.get("type") == "wycena_indywidualna":
            lines.append("Cena: wycena indywidualna")
        if s.get("notes"):
            lines.append(f"Uwagi: {s['notes']}")

    # Cennik materiałów i prefabrykatów
    lines.append("\n## CENNIK MATERIAŁÓW (NETTO)")
    for group in kb.get("global_sales_rules_for_gpt", {}).get("materials_pricing", []):
        title = group.get("location") or group.get("name") or group.get("group_id", "")
        lines.append(f"\n### {title}")
        if group.get("delivery_note"):
            lines.append(group["delivery_note"])
        for item in group.get("items", []):
            if "price_per_ton" in item:          # materiały sypkie (piasek, żwir itd.)
                note = " ⚠ do weryfikacji" if item.get("to_verify") else ""
                if item.get("transport"):
                    note += f", transport {item['transport']} zł"
                if item.get("notes"):
                    note += f" ({item['notes']})"
                lines.append(f"- {item['name']}: {item['price_per_ton']} zł/t{note}")
            elif "gruszka_pln_m3" in item:       # beton towarowy — dwie osobne linie, zero niejednoznaczności
                lines.append(f"- Beton {item['class']} (dostawa zwykłą gruszką, bez pompy): "
                              f"{item['gruszka_pln_m3']} zł/m³")
                lines.append(f"- Beton {item['class']} (dostawa pompogruszką - cena zawiera beton i pompowanie): "
                              f"{item['pompogruszka_pln_m3']} zł/m³")
            elif "cena_pln_szt" in item:         # prefabrykaty
                lines.append(f"- {item['category']} {item.get('wymiary_cm', '')}: "
                              f"{item['cena_pln_szt']} zł/szt")
        sur = group.get("surcharges", {})        # dopłaty do betonu
        if sur:
            if sur.get("extra_km_pln_m3"):
                lines.append(f"  + każdy kolejny km: +{sur['extra_km_pln_m3']} zł/m³")
            if sur.get("pump_repositioning_pln"):
                lines.append(f"  + przestawienie pompy: {sur['pump_repositioning_pln']} zł")
            if sur.get("polypropylene_fiber_pln_m3"):
                lines.append(f"  + włókno polipropylenowe: {sur['polypropylene_fiber_pln_m3']} zł/m³")
            if sur.get("winter_heating_surcharge_pct"):
                lines.append(f"  + grzanie w zimę: +{sur['winter_heating_surcharge_pct']}%")
        for p in group.get("podsypki", []):      # podsypki cementowe
            note = f" ({p['notes']})" if p.get("notes") else ""
            lines.append(f"  - {p['name']}: {p['price_pln_m3']} zł/m³{note}")

    return "\n".join(lines)


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

    # Markdown вместо JSON — читабельно для модели, ~50% меньше токенов
    kb_markdown = format_kb_to_markdown(kb)

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
        kb_markdown,
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
    Async: analyses the FULL conversation history.
    Fired as a background task — non-blocking for the main chat handler.
    """
    system = (
        "Jesteś analitykiem marketingu B2B. Przeanalizuj CAŁĄ poniższą historię rozmowy. "
        "Zwróć TYLKO JEDNĄ LINIJKĘ CSV rozdzieloną średnikami z polami: "
        "Usługa; Miejscowość (Brak jeśli nie podano); Ilość (Brak jeśli nie podano); "
        "Intencja (Pytanie o cenę/Zamówienie/Wynajem/Kontynuacja rozmowy/Offtopic); "
        "Pilność (Pilne/Standardowe/Nieznana); "
        "Segment (Klient indywidualny/Firma budowlana/Rolnik/Nieznany); "
        "Język (polski/rosyjski/angielski/inny). "
        "ZASADY KRYTYCZNE (ZAKAZ HALUCYNACJI): "
        "1. Jeśli klient pisze o dojeździe (np. 'asfalt'), usługą pozostaje ta z początku rozmowy (np. 'Rozbiórka'), a nie 'Asfaltowanie'. "
        "2. Firma działa na Mazurach. Jeśli klient pisze 'goldapi' lub podobnie, wpisz 'Gołdap'. Nie wymyślaj 'Gdańska' czy innych miast! "
        "Zero innych słów. Zero komentarzy."
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
        )

        raw_text = extract_text(resp.choices[0].message.content)

        lines = [l.strip() for l in raw_text.split('\n') if l.strip()]
        line = lines[-1] if lines else "Błąd;Brak;Brak;Brak;Nieznana;Nieznany;polski"

        # Strip header if model hallucinated it
        line = line.replace("Usługa;Miejscowość;Ilość;Intencja;Pilność;Segment;Język", "").strip()
        line = line.replace("Usługa; Miejscowość; Ilość; Intencja; Pilność; Segment; Język", "").strip()
        if line.startswith(";"):
            line = line[1:].strip()

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        file_exists = MARKETING_PATH.exists()

        with open(MARKETING_PATH, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";")
            if not file_exists:
                w.writerow(["Timestamp", "Usluga", "Miejscowosc", "Ilosc",
                            "Intencja", "Pilnosc", "Segment", "Jezyk"])
            fields = [x.strip() for x in line.split(";")]
            w.writerow([ts] + fields)

        print(f"[MARKETING] Zapisano: {line}")
    except Exception as e:
        print(f"[MARKETING WARN] {e}")


def log_marketing(client: AsyncOpenAI, history_context: str) -> None:
    """Schedules marketing analysis as a fire-and-forget async task."""
    asyncio.create_task(_marketing_worker(client, history_context))


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
                role    = item.get("role", "")
                content = str(item.get("content", "") or "")
                if role in ("user", "assistant") and content:
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
            # Clean accumulated text on every chunk (catches 8b artifacts mid-stream)
            clean_answer = extract_text(full_answer)
            updated[-1]["content"] = clean_answer
            yield "", updated
            await asyncio.sleep(0.01)  # Non-blocking smooth typing effect

        # Final yield with guaranteed clean text
        final_answer = extract_text(full_answer)
        updated[-1]["content"] = final_answer
        yield "", updated

        log_chat(message, final_answer)

        history_for_marketing = "\n".join(
            f"{m['role']}: {m['content']}" for m in openai_msgs
        ) + f"\nassistant: {final_answer}"
        log_marketing(client, history_for_marketing)

    return respond


# ─── 8. UI ────────────────────────────────────────────────────────────────────

QUICK_REPLIES = [
    "Ile kosztuje beton C20/25?",
    "Wynajem koparki na 2 dni",
    "Transport 15 ton piasku do Goldapi",
    "Przecisk pod droga",
    "Chce zamowic piasek plukany",
]

CSS = """
footer { display: none !important; }
#send-btn { background: #e07b00 !important; color: white !important; }
.quick { font-size: 12px !important; border: 1px solid #e07b00 !important;
         color: #e07b00 !important; background: transparent !important;
         border-radius: 20px !important; }
.quick:hover { background: #e07b00 !important; color: white !important; }
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

        gr.Markdown("**Szybkie zapytania:**")
        with gr.Row():
            qbtns = [gr.Button(r, elem_classes=["quick"], size="sm") for r in QUICK_REPLIES]

        clear = gr.Button("Wyczysc rozmowe", size="sm", variant="secondary")

        # api_name="chat" exposes a stable named endpoint for the widget
        msg.submit(respond, [msg, chatbot], [msg, chatbot], api_name="chat")
        btn.click(respond,  [msg, chatbot], [msg, chatbot], api_name=False)

        def _make_quick_handler(r: str):
            async def handler(h: list[dict]):
                async for result in respond(r, h):
                    yield result
            return handler

        for qbtn, reply in zip(qbtns, QUICK_REPLIES):
            qbtn.click(
                fn=_make_quick_handler(reply),
                inputs=[chatbot],
                outputs=[msg, chatbot],
                api_name=False,
            )

        clear.click(fn=lambda: ([], ""), outputs=[chatbot, msg])

    return demo


# ─── 9. MAIN ──────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  TOP KOP RAG Dispatcher - Web UI (async + streaming)")
    print(f"  Model   : {MODEL}  |  Temp: {TEMP}  |  MaxTok: {MAX_TOK}")
    print(f"  ChatLog : {CHAT_LOG_PATH.resolve()}")
    print(f"  Leads   : {MARKETING_PATH.resolve()}")
    print("=" * 60)

    kb     = load_knowledge_base()
    client = create_client()
    demo   = build_ui(kb, client)

    demo.launch(
        server_name="0.0.0.0",
        server_port=WEB_PORT,
        share=True,
        # auth REMOVED — external widget cannot pass Basic-Auth via EventSource (SSE)
        show_error=True,
    )


if __name__ == "__main__":
    main()
