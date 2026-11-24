"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from caiocore.bus.events import OutboundMessage
from caiocore.bus.queue import MessageBus
from caiocore.channels.base import BaseChannel
from caiocore.channels.evolution import _phone
from caiocore.config.schema import WhatsAppConfig


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.
    
    The bridge uses @whiskeysockets/baileys to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """
    
    name = "whatsapp"
    
    def __init__(self, config: WhatsAppConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._ws = None
        self._connected = False

    def is_allowed(self, sender_id: str) -> bool:
        phone = _phone(sender_id)
        allowed = getattr(self.config, "allow_from", None)
        return bool(phone and isinstance(allowed, (list, tuple)) and
                    any(_phone(entry) == phone for entry in allowed))
    
    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets
        
        bridge_url = self.config.bridge_url
        token = self.config.bridge_token
        try:
            target = urlsplit(bridge_url)
            if (target.scheme != "ws" or target.hostname not in ("127.0.0.1", "localhost")
                    or target.username is not None or target.password is not None):
                raise ValueError("Non-local bridge")
        except (ValueError, TypeError):
            logger.error("WhatsApp bridge must use a local ws URL")
            return
        if not isinstance(token, str) or not token.strip():
            logger.error("WhatsApp bridge token is required; channel disabled")
            return
        logger.info("Connecting to WhatsApp bridge")
        self._running = True
        while self._running:
            try:
                async with websockets.connect(bridge_url, max_size=65536) as ws:
                    await ws.send(json.dumps({"type": "auth", "token": token}))
                    reply = await asyncio.wait_for(ws.recv(), timeout=5)
                    if json.loads(reply) != {"type": "auth_ok"}:
                        raise ValueError("Bridge authentication failed")
                    self._ws = ws
                    self._connected = True
                    logger.info("Connected to authenticated WhatsApp bridge")
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception:
                            logger.error("Error handling bridge message")
            except asyncio.CancelledError:
                break
            except Exception:
                # Exceptions can contain URL credentials or peer-controlled text.
                logger.warning("WhatsApp bridge connection or authentication failed")
            finally:
                self._connected = False
                self._ws = None
            if self._running:
                await asyncio.sleep(5)
    
    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False
        
        if self._ws:
            await self._ws.close()
            self._ws = None
    
    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
        if not self._ws or not self._connected or not self.is_allowed(msg.chat_id):
            logger.warning("WhatsApp bridge not connected or outbound recipient denied")
            return
        if not isinstance(msg.content, str) or not msg.content.strip() or len(msg.content) > 4096:
            return
        
        try:
            payload = {
                "type": "send",
                "to": msg.chat_id,
                "text": msg.content
            }
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception:
            logger.error("Error sending WhatsApp message")
    
    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge")
            return
        
        if not isinstance(data, dict):
            return
        msg_type = data.get("type")
        
        if msg_type == "message":
            pn = data.get("pn", "")
            sender = data.get("sender", "")
            content = data.get("content", "")
            # Only unambiguous direct phone JIDs: group and LID identities are
            # not independently verifiable against a phone allowlist.
            if (data.get("isGroup") is not False or not isinstance(sender, str)
                    or not sender.endswith("@s.whatsapp.net") or not self.is_allowed(sender)
                    or (pn not in (None, "") and _phone(pn) != _phone(sender))
                    or not isinstance(content, str) or not content.strip() or len(content) > 4096):
                return
            sender_id = _phone(sender)
            
            # Handle voice transcription if it's a voice message
            if content == "[Voice Message]":
                logger.info("Voice message received from {}, but direct download from bridge is not yet supported.", sender_id)
                content = "[Voice Message: Transcription not available for WhatsApp yet]"
            
            await self._handle_message(
                sender_id=sender_id,
                chat_id=sender,  # Use full LID for replies
                content=content,
                metadata={
                    "message_id": data.get("id"),
                    "timestamp": data.get("timestamp"),
                    "is_group": data.get("isGroup", False)
                }
            )
        
        elif msg_type == "status":
            # Connection status update
            status = data.get("status")
            logger.info("WhatsApp status: {}", status)
            
            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False
        
        elif msg_type == "qr":
            # QR code for authentication
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")
        
        elif msg_type == "error":
            logger.error("WhatsApp bridge reported an error")
