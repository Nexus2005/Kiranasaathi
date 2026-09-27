"""WhatsApp provider abstraction — Phase 4 foundation.

Hard rules:
- NEVER pretend a message was sent unless a real provider API call succeeded.
- The 'development' provider records what WOULD be sent, clearly labelled,
  and marks recipients 'sent' ONLY in dev mode with provider=development.
- Production providers (meta_cloud_api, gupshup, interakt, wati, aiSensy)
  require credentials in whatsapp_provider_config. Without them, sending
  raises ProviderNotConfigured and the campaign is marked FAILED — no fake
  success, no silent no-op.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from app.database import db


class ProviderNotConfigured(Exception):
    def __init__(self, provider_id: str):
        super().__init__(
            f"WhatsApp provider '{provider_id}' is not configured. "
            "Add credentials in Integrations settings before sending real messages."
        )
        self.provider_id = provider_id


class ProviderSendResult(dict):
    pass


class WhatsAppProvider(ABC):
    """Interface every provider must implement. Phase 5 adds template mgmt."""

    provider_id: str = "abstract"

    @abstractmethod
    async def send_message(
        self, *, to_phone: str, body: str, correlation_id: str
    ) -> ProviderSendResult:
        """Send one text message. Returns provider message id. Raises on failure."""

    @abstractmethod
    async def get_message_status(self, provider_message_id: str) -> dict[str, Any]:
        """Delivery status if the provider supports it."""

    async def send_template(self, *, to_phone: str, template: str, params: dict[str, Any]) -> ProviderSendResult:
        raise NotImplementedError("Templates not supported by this provider yet")

    def supports_delivery_receipts(self) -> bool:
        return False


class DevelopmentProvider(WhatsAppProvider):
    """DEV ONLY: records the exact payload that WOULD be sent.

    Recipients marked 'sent' carry a dev-prefixed message id so the demo flow
    is complete, but every UI surface labels this as development mode.
    """

    provider_id = "development"

    def __init__(self, credentials: dict[str, Any] | None = None):
        self.credentials = credentials or {}

    async def send_message(self, *, to_phone: str, body: str, correlation_id: str) -> ProviderSendResult:
        return ProviderSendResult({
            "provider_message_id": f"dev-{uuid.uuid4().hex[:12]}",
            "status": "sent (development mode — no real message was delivered)",
            "would_send_to": to_phone,
            "body": body,
        })

    async def get_message_status(self, provider_message_id: str) -> dict[str, Any]:
        if not provider_message_id.startswith("dev-"):
            return {"status": "unknown", "note": "Not a development-mode message id."}
        return {
            "status": "delivered (simulated)",
            "note": "Development provider does not contact any network.",
        }


class MetaCloudApiProvider(WhatsAppProvider):
    """Meta WhatsApp Cloud API. Requires credentials; never enabled by default."""

    provider_id = "meta_cloud_api"

    def __init__(self, credentials: dict[str, Any]):
        self.phone_number_id = credentials.get("phone_number_id")
        self.access_token = credentials.get("access_token")
        if not self.phone_number_id or not self.access_token:
            raise ProviderNotConfigured(self.provider_id)

    async def send_message(self, *, to_phone: str, body: str, correlation_id: str) -> ProviderSendResult:
        # Phase 5: actual HTTP call to graph.facebook.com with the stored token.
        # Deliberately not implemented in Phase 4 — raising keeps the honesty contract.
        raise ProviderNotConfigured(self.provider_id)

    async def get_message_status(self, provider_message_id: str) -> dict[str, Any]:
        raise ProviderNotConfigured(self.provider_id)

    def supports_delivery_receipts(self) -> bool:
        return True  # webhooks supported once Phase 5 wires the HTTP client


PROVIDERS: dict[str, type[WhatsAppProvider]] = {
    "development": DevelopmentProvider,
    "meta_cloud_api": MetaCloudApiProvider,
    # gupshup / interakt / wati / aiSensy: registered in Phase 5 with real clients.
}


async def get_provider_for_store(store_id: str) -> tuple[WhatsAppProvider, dict[str, Any]]:
    """Resolve the store's configured provider. Returns (provider, config)."""
    cfg = await db.fetchrow(
        "select * from whatsapp_provider_config where store_id = $1", store_id
    )
    if not cfg:
        raise ProviderNotConfigured("none")
    provider_id = cfg["provider_id"]
    if provider_id not in PROVIDERS:
        raise ProviderNotConfigured(provider_id)
    if provider_id != "development" and not cfg["configured"]:
        raise ProviderNotConfigured(provider_id)
    credentials = cfg["credentials"] if isinstance(cfg["credentials"], dict) else {}
    provider = PROVIDERS[provider_id](credentials)
    return provider, dict(cfg)


async def provider_status(store_id: str) -> dict[str, Any]:
    cfg = await db.fetchrow(
        "select provider_id, display_name, configured from whatsapp_provider_config where store_id = $1",
        store_id,
    )
    if not cfg:
        return {"provider_id": "development", "configured": False,
                "mode": "development", "note": "No provider row; development mode applies."}
    mode = "development" if cfg["provider_id"] == "development" else "production"
    return {
        "provider_id": cfg["provider_id"],
        "display_name": cfg["display_name"],
        "configured": bool(cfg["configured"]),
        "mode": mode,
        "note": (
            "Development mode: messages are recorded locally and NOT delivered. "
            "No real customer receives anything."
            if mode == "development" else
            "Production provider selected. Real messages will be sent to consented customers once credentials are verified."
        ),
    }
