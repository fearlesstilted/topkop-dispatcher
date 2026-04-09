---
title: TopKop Dispatcher
emoji: 🚛
colorFrom: yellow
colorTo: gray
sdk: gradio
sdk_version: "6.11.0"
app_file: app.py
pinned: false
license: mit
---

# TOP KOP — AI Dispatcher Widget

AI-powered lead collection chatbot for TOP KOP construction company (Gołdap, Mazury). Embedded as a Shadow DOM widget on the company website — collects service requests 24/7 and forwards leads to management.

## What it does
- Answers questions about services (earthworks, transport, equipment rental, HDD drilling, paving, steel fabrication)
- Collects lead data: service type, location, quantity, phone number
- Sends structured JSON to Make.com → Google Sheets + WhatsApp notification
- Streams responses via SSE (Gradio 6)

## Stack
- **Python 3.10+** — async throughout (`AsyncOpenAI`, `httpx`)
- **Gradio 6** — UI and SSE streaming
- **Groq API** — LLaMA 3.3 70B (main), LLaMA 3.1 8B (lead analysis)
- **Make.com** — webhook → Google Sheets + WhatsApp
- **Shadow DOM** (`widget.js`) — CSS isolation for embedding on any site

## Deploy (HuggingFace Spaces)
Set these secrets in Space Settings:
```
GROQ_API_KEY=...
MAKE_WEBHOOK_URL=...
```
Space auto-rebuilds on push to `main`.

## Embed on website
```html
<script src="widget.js" data-endpoint="https://YOUR_SPACE.hf.space"></script>
```
