"""WhatsApp integration via Evolution API (direct chats only)."""

import re
from typing import Any

import httpx
from loguru import logger

from caiocore.bus.events import OutboundMessage
from caiocore.bus.queue import MessageBus
from caiocore.channels.base import BaseChannel
from caiocore.config.schema import EvolutionConfig


# Deliberately reject groups, LIDs, device suffixes, formatting and wildcards.
_DIRECT = re.compile(r"([0-9]{8,15})(?:@s\.whatsapp\.net)?\Z", re.ASCII)
_MAX_CONTENT = 4096


def _phone(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = _DIRECT.fullmatch(value)
    return match.group(1) if match else None


class EvolutionChannel(BaseChannel):
    """Evolution channel. Explicit phone allowlist required in both directions."""

    name = "evolution"

    def __init__(self, config: EvolutionConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers={"apikey": config.api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def is_allowed(self, sender_id: str) -> bool:
        phone = _phone(sender_id)
        allowed = getattr(self.config, "allow_from", None)
        return bool(phone and isinstance(allowed, (list, tuple)) and
                    any(_phone(entry) == phone for entry in allowed))

    async def start(self) -> None:
        self._running = True
        if not any(_phone(item) for item in (self.config.allow_from or [])):
            logger.warning("Evolution: no valid allowlist entries; all traffic denied")
        logger.info("Evolution channel initiated")

    async def stop(self) -> None:
        self._running = False
        await self.client.aclose()

    async def send(self, msg: OutboundMessage) -> None:
        if not self.config.base_url or not self.is_allowed(msg.chat_id):
            logger.warning("Evolution: outbound message denied")
            return
        if not isinstance(msg.content, str) or not msg.content.strip() or len(msg.content) > _MAX_CONTENT:
            return
        try:
            response = await self.client.post(
                f"/message/sendText/{self.config.instance_name}",
                json={"number": msg.chat_id, "options": {"delay": 1200, "presence": "composing", "linkPreview": True}, "text": msg.content},
            )
            response.raise_for_status()
        except Exception:
            # HTTP exceptions can contain URLs or secrets; do not log them.
            logger.error("Evolution: outbound request failed")

    async def handle_webhook(self, data: dict[str, Any]) -> None:
        """Validate the message, not the webhook's origin (endpoint must authenticate)."""
        if not isinstance(data, dict) or data.get("event") != "messages.upsert":
            return
        # If provided, the instance must match; origin authentication is still essential.
        if "instance" in data and data["instance"] != self.config.instance_name:
            return
        payload = data.get("data")
        if not isinstance(payload, dict):
            return
        key, message = payload.get("key"), payload.get("message")
        if not isinstance(key, dict) or not isinstance(message, dict):
            return
        # fromMe must be present and literally false, not missing or a string.
        if key.get("fromMe") is not False:
            return
        jid = key.get("remoteJid")
        if not isinstance(jid, str) or not jid.endswith("@s.whatsapp.net"):
            return
        sender = _phone(jid)
        if not sender or not self.is_allowed(sender):
            return
        # A direct JID with conflicting participant fields is ambiguous.
        if key.get("participant") not in (None, "") or payload.get("participant") not in (None, ""):
            return

        content = message.get("conversation")
        if content is None:
            for kind, field in (("extendedTextMessage", "text"), ("imageMessage", "caption"),
                                ("videoMessage", "caption"), ("documentMessage", "caption")):
                if kind in message:
                    item = message[kind]
                    if not isinstance(item, dict):
                        return
                    content = item.get(field)
                    break
            else:
                if isinstance(message.get("audioMessage"), dict):
                    content = "[Voice Message]"
        if not isinstance(content, str) or not content.strip() or len(content) > _MAX_CONTENT:
            return

        await self._handle_message(sender_id=sender, chat_id=jid, content=content,
                                   metadata={"instance": data.get("instance"),
                                             "message_id": key.get("id"), "is_group": False})
