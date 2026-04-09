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

# B2B Lead Dispatcher — TOP KOP

Intent-parsing chatbot for a construction and heavy machinery company. Qualifies inbound service requests in natural language, extracts structured lead data and routes it to the sales team via webhooks.

## Tech Stack
- **Backend:** Python 3.10+, async (`AsyncOpenAI`, `httpx`)
- **LLM:** Groq API — LLaMA 3.3 70B (chat), LLaMA 3.1 8B (lead analysis)
- **UI:** Gradio 6 (SSE streaming)
- **Integration:** Make.com webhook → Google Sheets + WhatsApp
- **Embed:** Shadow DOM widget (`widget.js`) — CSS-isolated, drops into any site with one `<script>` tag

## Core Logic
1. User describes a service need in free text
2. Bot collects structured lead: service type, location, quantity, phone
3. After 2+ messages: 8B model extracts JSON lead data
4. Payload sent via POST to Make.com → Google Sheets row + WhatsApp notification to manager

## Local Setup
```bash
git clone https://github.com/fearlesstilted/topkop-dispatcher
pip install -r requirements.txt
cp .env.example .env  # add GROQ_API_KEY and MAKE_WEBHOOK_URL
python app_web.py
```

## Embed on any website
```html
<script src="widget.js" data-endpoint="https://YOUR_SPACE.hf.space"></script>
```
