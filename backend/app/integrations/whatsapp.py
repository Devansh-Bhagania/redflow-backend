import hashlib
import hmac

import httpx

from app.integrations.base import MessagingProvider

GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


class WhatsAppProvider(MessagingProvider):
    async def send(self, *, to: str, text: str, credentials: dict) -> str:
        phone_number_id = credentials["phone_number_id"]
        access_token = credentials["access_token"]
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{GRAPH_API_BASE}/{phone_number_id}/messages",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"messaging_product": "whatsapp", "to": to, "text": {"body": text}},
            )
            resp.raise_for_status()
            return resp.json()["messages"][0]["id"]

    @staticmethod
    def verify_signature(raw_body: bytes, signature_header: str | None, app_secret: str) -> bool:
        if not signature_header or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))

    @staticmethod
    def extract_phone_number_id(payload: dict) -> str | None:
        try:
            return payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]
        except (KeyError, IndexError):
            return None

    @staticmethod
    def parse_inbound(payload: dict) -> list[dict]:
        """Normalize a WhatsApp Cloud API webhook body into a flat list of
        inbound text messages. Non-text messages (media, status updates,
        reactions) are skipped for now."""
        out = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                contacts = {c["wa_id"]: c.get("profile", {}).get("name") for c in value.get("contacts", [])}
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        continue
                    out.append(
                        {
                            "from": msg["from"],
                            "text": msg["text"]["body"],
                            "external_id": msg["id"],
                            "profile_name": contacts.get(msg["from"]),
                        }
                    )
        return out
