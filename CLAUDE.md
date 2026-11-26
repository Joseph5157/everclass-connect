# CLAUDE.md: Everclass Connect

Everclass's own WhatsApp service on **Meta's official WhatsApp Cloud API** (direct, Everclass as Tech Provider later).
Other Everclass apps (the school ERP `Joseph5157/school-erp`, the leads app) send through its internal API. Read `README.md` first.

## Hard rules
- Official Cloud API only. Never add Baileys, whatsapp-web.js, Evolution's Baileys mode or any WhatsApp Web automation. Never add a BSP SDK.
- No Redis, Celery or extra services. Postgres is the queue (`SELECT … FOR UPDATE SKIP LOCKED` in `whatsapp/worker.py`).
- Never put secrets in code, commits, logs or command arguments. Tokens live encrypted in `WhatsAppAccount.access_token`. Settings come from env vars (see `.env.example`). `add_account` reads the token from a hidden prompt or an env var.
- The webhook view only verifies `X-Hub-Signature-256`, stores the raw `WebhookEvent` and returns 200. All processing happens in the worker.
- Keep the queueing rules in `whatsapp/services.py`: opt-out refused, only APPROVED templates, free text only inside the 24-hour window, idempotency key unique per account.
- Message statuses never move backwards (`Message.STATUS_RANK`).
- Talk to Meta only through `whatsapp/graph.py` (a thin httpx client, no third-party WhatsApp SDK).

## Stack
Python 3.12+, Django 5.2, Postgres on Railway (SQLite locally), httpx, cryptography (Fernet), gunicorn and whitenoise, ruff.

## Commands
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # then fill FIELD_ENCRYPTION_KEY, SECRET_KEY
python manage.py migrate
python manage.py test whatsapp      # must pass before every commit
ruff check .                        # must be clean
python manage.py runserver
python manage.py run_whatsapp_worker
```
Local settings need `DEBUG=1` and `FIELD_ENCRYPTION_KEY` in the environment (settings read `os.environ`; load `.env` yourself or export the variables).

## Code map
- `whatsapp/graph.py`: Graph API client and error classification (`RETRYABLE_CODES`)
- `whatsapp/services.py`: queue_template, queue_text
- `whatsapp/worker.py`: claiming, sending, retries and backoff, webhook processing
- `whatsapp/events.py`: statuses, inbound, STOP and START, template updates
- `whatsapp/views.py`: `/webhooks/meta/` and `/api/v1/messages`
- `whatsapp/models.py`: Organization, WhatsAppAccount, MessageTemplate, Contact, Message, InboundMessage, WebhookEvent, ApiKey

## Working style
- Small commits with tests. Add a test for every behaviour change, and mock Meta with `httpx.MockTransport` or the `FakeClient` in tests.
- Keep the Meta Graph API version in `META_GRAPH_VERSION`. Check Meta's docs before relying on new fields.
- Roadmap: Stage 1 (done: test number) → Stage 2 (real number, health page, media and PDFs, templates from the admin) → Stage 3 (Tech Provider, Embedded Signup v4, multi-tenant schools) → Stage 4 (ERP integration).
