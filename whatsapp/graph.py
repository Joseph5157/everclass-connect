"""Thin client for Meta's Graph API (WhatsApp Cloud API). No third-party SDK on purpose."""

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

# Meta error codes worth retrying later (rate limits, temporary outages).
RETRYABLE_CODES = {
    "1", "2", "4", "80007", "130429", "131000", "131016", "131048", "131056", "133004",
}


class GraphAPIError(Exception):
    def __init__(self, message, code="", status=None, retryable=False, details=None):
        super().__init__(message)
        self.code = str(code or "")
        self.status = status
        self.retryable = retryable
        self.details = details or {}

    def __str__(self):
        return f"[{self.code or self.status}] {self.args[0]}"


class GraphClient:
    def __init__(self, access_token, version=None, base_url=None, timeout=None, transport=None):
        self.access_token = access_token
        self.version = version or settings.META_GRAPH_VERSION
        base = (base_url or settings.META_GRAPH_BASE_URL).rstrip("/")
        self._http = httpx.Client(
            base_url=f"{base}/{self.version}",
            timeout=timeout or settings.META_HTTP_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {access_token}"},
            transport=transport,
        )

    @classmethod
    def for_account(cls, account, **kwargs):
        return cls(account.access_token, **kwargs)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- low level -------------------------------------------------------
    def _request(self, method, path, **kwargs):
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise GraphAPIError(f"Network error: {exc}", retryable=True) from exc

        if response.status_code < 400:
            return response.json() if response.content else {}

        try:
            error = response.json().get("error", {})
        except ValueError:
            error = {}
        code = str(error.get("code", ""))
        message = error.get("error_user_msg") or error.get("message") or response.text[:300]
        retryable = response.status_code >= 500 or code in RETRYABLE_CODES
        raise GraphAPIError(message, code=code, status=response.status_code, retryable=retryable, details=error)

    # --- messages ----------------------------------------------------------
    def send_message(self, phone_number_id, payload):
        body = {"messaging_product": "whatsapp", "recipient_type": "individual", **payload}
        return self._request("POST", f"/{phone_number_id}/messages", json=body)

    def send_text(self, phone_number_id, to, text, preview_url=False):
        return self.send_message(
            phone_number_id, {"to": to, "type": "text", "text": {"body": text, "preview_url": preview_url}}
        )

    def send_template(self, phone_number_id, to, name, language, body_params=None, components=None):
        template = {"name": name, "language": {"code": language}}
        if components is None and body_params:
            components = [
                {"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in body_params]}
            ]
        if components:
            template["components"] = components
        return self.send_message(phone_number_id, {"to": to, "type": "template", "template": template})

    def mark_read(self, phone_number_id, wamid):
        return self._request(
            "POST",
            f"/{phone_number_id}/messages",
            json={"messaging_product": "whatsapp", "status": "read", "message_id": wamid},
        )

    # --- media ---------------------------------------------------------------
    def upload_media(self, phone_number_id, filename, content, mime_type):
        files = {"file": (filename, content, mime_type)}
        data = {"messaging_product": "whatsapp", "type": mime_type}
        return self._request("POST", f"/{phone_number_id}/media", data=data, files=files)

    def get_media_url(self, media_id):
        return self._request("GET", f"/{media_id}")

    def download_media(self, url):
        """Media URLs expire within minutes: download as soon as the webhook arrives."""
        response = self._http.get(url)
        if response.status_code >= 400:
            raise GraphAPIError("Media download failed", status=response.status_code,
                                retryable=response.status_code >= 500)
        return response.content

    # --- templates -----------------------------------------------------------
    def list_templates(self, waba_id, limit=100):
        templates, params = [], {"limit": limit,
                                 "fields": "id,name,language,status,category,components,rejected_reason"}
        path = f"/{waba_id}/message_templates"
        while True:
            data = self._request("GET", path, params=params)
            templates.extend(data.get("data", []))
            after = data.get("paging", {}).get("cursors", {}).get("after")
            if not after or not data.get("paging", {}).get("next"):
                return templates
            params = {**params, "after": after}

    def create_template(self, waba_id, name, language, category, components):
        body = {"name": name, "language": language, "category": category, "components": components}
        return self._request("POST", f"/{waba_id}/message_templates", json=body)

    # --- account -------------------------------------------------------------
    def get_phone_number(self, phone_number_id):
        fields = "display_phone_number,verified_name,quality_rating,messaging_limit_tier"
        return self._request("GET", f"/{phone_number_id}", params={"fields": fields})

    def subscribe_app(self, waba_id):
        """Subscribe our app to a WABA's webhooks (needed for each connected WABA)."""
        return self._request("POST", f"/{waba_id}/subscribed_apps")
