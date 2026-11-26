# Everclass Connect

Everclass's own WhatsApp service on **Meta's official WhatsApp Cloud API**. No BSP, and no unofficial WhatsApp Web libraries.
Other Everclass apps (the school ERP, the leads app) send messages through Connect's small internal API.

Stage 1 (this code): send templates and text, receive statuses and replies, template sync, opt-out, retries.
Later stages (see the plan in the Claude "Whatsapp" project): a real Everclass number, then Tech Provider, then Embedded Signup for schools.

## How it works

```
App (ERP) --POST /api/v1/messages--> Message row (status=queued)
worker (run_whatsapp_worker) --claims rows--> Meta Cloud API --> wamid saved (accepted)
Meta --POST /webhooks/meta/ (signed)--> WebhookEvent row (raw) --worker--> sent/delivered/read/failed,
                                                                          inbound replies, STOP opt-out,
                                                                          template approval status
```

- **Postgres is the queue** (`SELECT … FOR UPDATE SKIP LOCKED`). No Redis or Celery.
- The webhook only checks `X-Hub-Signature-256`, stores the raw body and returns 200. The worker does the processing, so nothing is lost and events can be replayed from the admin.
- Tokens are encrypted at rest (Fernet, `FIELD_ENCRYPTION_KEY`).
- Idempotency: the same `idempotency_key` is never sent twice per number.
- Retries: rate limits and temporary errors back off (30 seconds, 1 minute, 2 minutes … up to 1 hour) until `MESSAGE_MAX_ATTEMPTS`. Permanent errors (bad number, closed window) fail at once.
- Statuses never move backwards (a late "delivered" after "read" is ignored).
- Free text is allowed only inside the 24-hour window. Outside it, use an approved template.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in values (DEBUG=1, SECRET_KEY, FIELD_ENCRYPTION_KEY)
python manage.py migrate
python manage.py createsuperuser
python manage.py test whatsapp
python manage.py runserver      # admin at http://localhost:8000/admin/
python manage.py run_whatsapp_worker   # in a second terminal
```

`manage.py` loads `.env` automatically for local dev when the file exists (python-dotenv). Variables already set in the environment win over `.env`. Production (Railway) has no `.env` and uses real environment variables only.

To receive webhooks locally, expose port 8000 with a tunnel (for example `cloudflared tunnel --url http://localhost:8000`) and use that HTTPS URL in Meta.

## Railway deploy

One Railway project with three services:

| Service | Start command | Notes |
|---|---|---|
| **web** | from `railway.json` (gunicorn) | public domain; migrations run before deploy |
| **worker** | `python manage.py run_whatsapp_worker` | no public domain; same variables as web |
| **Postgres** | Railway plugin | link `DATABASE_URL` to web and worker |

Variables for web and worker: `SECRET_KEY`, `ALLOWED_HOSTS` (your Railway domain), `CSRF_TRUSTED_ORIGINS` (`https://<domain>`), `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, `FIELD_ENCRYPTION_KEY`, `DATABASE_URL`. Back up `FIELD_ENCRYPTION_KEY` in your password manager.

## Connect to Meta (Stage 1, test number)

1. **Meta app:** at developers.facebook.com, create an app with the "Connect with customers through WhatsApp" use case.
2. **API Setup:** note the test **phone number ID** and **WABA ID**, and add your phones as test recipients.
3. **System user token:** in Business Settings, go to System Users, add an admin, assign the app and the WABA, and generate a token with `whatsapp_business_messaging` and `whatsapp_business_management`.
4. **Add the account** on the server (Railway shell or locally). The token is read from a hidden prompt:
   ```bash
   python manage.py add_account --org "Everclass" --waba-id <WABA_ID> --phone-number-id <PHONE_NUMBER_ID> --subscribe
   ```
5. **Webhook:** in the Meta app, go to WhatsApp, then Configuration:
   - Callback URL `https://<your-domain>/webhooks/meta/`
   - Verify token = `META_WEBHOOK_VERIFY_TOKEN`
   - Subscribe the fields `messages` and `message_template_status_update`
6. **Templates:** run `python manage.py sync_templates`.
7. **First message:** run `python manage.py send_test_message 98765XXXXX`. It uses `hello_world`, `en_US`, and the worker sends it.
8. **Reply** from that phone. The reply appears under Inbound messages, and the 24-hour window opens:
   ```bash
   python manage.py send_test_message 98765XXXXX --text "Hi from Everclass Connect"
   ```
9. **Opt-out:** send `STOP` from the phone. The contact is marked opted out and further sends are refused.

**Stage 1 is done when** steps 7 to 9 work and you can see sent, delivered and read statuses on the message in the admin.

## Internal API

Issue a key with `python manage.py create_api_key --org everclass --name school-erp-dev`. The key is shown once.

```bash
curl -X POST https://<domain>/api/v1/messages \
  -H "Authorization: Bearer ecx_…" -H "Content-Type: application/json" \
  -d '{"to":"9876543210","idempotency_key":"absence:123:456:2026-11-26",
       "template":{"name":"absence_alert","language":"en","body_params":["Ravi","26 Nov"]}}'
# 201 = queued, 200 = already queued with this key, 422 = refused (opted out, template not approved, ...)

curl https://<domain>/api/v1/messages/42 -H "Authorization: Bearer ecx_…"
```

Add `"phone_number_id"` when the organization has more than one number.

## Commands

| Command | Purpose |
|---|---|
| `add_account` | add or update a number, check it with Meta, optionally subscribe the webhooks |
| `sync_templates` | pull templates and approval status from Meta |
| `send_test_message` | queue a template or text message |
| `create_api_key` | issue a key for an internal app |
| `run_whatsapp_worker [--once]` | send the queue and process webhooks |

## Code map

- `whatsapp/graph.py`: Graph API client (messages, media, templates, phone info, WABA subscription) and error classification
- `whatsapp/services.py`: queueing rules (opt-out, approved template, 24-hour window, idempotency)
- `whatsapp/worker.py`: queue claiming, sending, retries and backoff, webhook processing
- `whatsapp/events.py`: webhook payload handling (statuses, inbound, STOP and START, template updates)
- `whatsapp/views.py`: webhook endpoint (verify and signature) and the internal API
- `whatsapp/models.py`: Organization, WhatsAppAccount, MessageTemplate, Contact, Message, InboundMessage, WebhookEvent, ApiKey

## Next (Stage 2)

- Real Everclass number and business verification
- Health page (last webhook, queue size, failure rate) and alerts
- Media messages (documents and PDFs), and creating templates from the admin
- Quality rating and messaging-limit monitoring
