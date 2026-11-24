"""Telegram channel implementation using python-telegram-bot."""

from __future__ import annotations

import asyncio
import logging
import re
from loguru import logger
from telegram import BotCommand, Update, ReplyParameters
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

from caiocore.bus.events import OutboundMessage
from caiocore.bus.queue import MessageBus
from caiocore.channels.base import BaseChannel
from caiocore.config.schema import TelegramConfig


def _markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram-safe HTML.
    Handles **, *, __, _, ~~ and protects existing <b>, <i>, <code>, <a> tags.
    """
    if not text:
        return ""

    # 1. Protect existing HTML tags that we want to keep
    # We'll use the same placeholder technique as code blocks
    protected_tags: list[str] = []
    def save_tag(m: re.Match) -> str:
        protected_tags.append(m.group(0))
        return f"\x00TAG{len(protected_tags) - 1}\x00"
    
    # Matches common tags like <b>, </b>, <a>, <a href="...">, etc.
    text = re.sub(r'<(/?(?:b|i|u|s|code|pre|a)(?:\s+[^>]+)?)>', save_tag, text)

    # 2. Extract and protect code blocks
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"
    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)

    # 3. Extract and protect inline code
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"
    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    # 4. Escape remaining HTML special characters
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # 5. Bold **text** or __text__ or *text* (common markdown)
    # We start with the strongest (double) and then single
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    # Single asterisk bold (Telegram standard)
    text = re.sub(r'(?<!\*)\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)', r'<b>\1</b>', text)

    # 6. Italic _text_ (avoid matching inside words)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)

    # 7. Strikethrough ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 8. Links [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 9. Bullet lists
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)

    # 10. Restore protected parts in reverse order
    # Restore tags first so they can be nested or contain other things if they were already HTML
    for i, tag in enumerate(protected_tags):
        text = text.replace(f"\x00TAG{i}\x00", tag)

    for i, code in enumerate(inline_codes):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    for i, code in enumerate(code_blocks):
        escaped = code.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le", errors="surrogatepass")) // 2


def _split_message(content: str, max_len: int = 4096) -> list[str]:
    """Split losslessly, respecting Telegram's UTF-16 message limit."""
    if max_len < 2:
        raise ValueError("max_len must be at least 2")
    if not content:
        return [""]
    chunks: list[str] = []
    start = 0
    units = 0
    for i, char in enumerate(content):
        char_units = 2 if ord(char) > 0xFFFF else 1
        if units + char_units > max_len:
            chunks.append(content[start:i])
            start, units = i, 0
        units += char_units
    chunks.append(content[start:])
    return chunks


class _TelegramTokenLogFilter(logging.Filter):
    """Drop PTB/HTTPX records, including tracebacks and private updates."""
    def __init__(self, token: str):
        super().__init__()

    def filter(self, record: logging.LogRecord) -> bool:
        # Tracebacks, stack info and formatting may contain secrets or messages.
        # Drop the entire record; inspecting only getMessage is not sufficient.
        return False


# Python logger filters do not propagate to descendants: attach to each PTB
# logger used by this channel (and HTTPX/httpcore), before constructing the bot.
_PRIVATE_LOGGERS = (
    "telegram", "telegram.Bot", "telegram.ext", "telegram.ext.ExtBot",
    "telegram.ext.Application", "telegram.ext.Updater", "telegram.ext.JobQueue",
    "telegram.ext.ConversationHandler", "telegram.ext.AIORateLimiter",
    "telegram.ext._utils.stack", "telegram.ext._utils.networkloop",
    "telegram.request.BaseRequest", "telegram.request.HTTPXRequest", "httpx",
    "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2",
    "httpcore.proxy", "httpcore.socks_proxy", "httpcore.socks",
)


class TelegramChannel(BaseChannel):
    """
    Telegram channel using long polling.
    
    Simple and reliable - no webhook/public IP needed.
    """
    
    name = "telegram"
    
    # Commands registered with Telegram's command menu
    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("new", "Start a new conversation"),
        BotCommand("help", "Show available commands"),
    ]
    
    def __init__(
        self,
        config: TelegramConfig,
        bus: MessageBus,
        groq_api_key: str = "",
    ):
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self.groq_api_key = groq_api_key
        self._app: Application | None = None
        self._starting = False
        self._stop_requested = False
        self._http_log_filter: _TelegramTokenLogFilter | None = None
        self._private_loggers: list[logging.Logger] = []
        self._typing_tasks: dict[str, asyncio.Task] = {}  # chat_id -> typing loop task
    
    @staticmethod
    def _numeric_id(value: object) -> str | None:
        # Canonical ASCII positive IDs only: no usernames, signs, whitespace or aliases.
        if type(value) is int:
            value = str(value)
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]*", value):
            return None
        return value

    def is_allowed(self, sender_id: str) -> bool:
        numeric = self._numeric_id(sender_id)
        allow = getattr(self.config, "allow_from", None)
        return bool(numeric and isinstance(allow, (list, tuple)) and numeric in allow)

    def _authorized_update(self, update: Update) -> bool:
        """Validate sender AND private peer before touching message content."""
        message = getattr(update, "message", None)
        user = getattr(update, "effective_user", None)
        if message is None or user is None:
            return False
        user_id = self._numeric_id(getattr(user, "id", None))
        chat = getattr(message, "chat", None)
        chat_id = self._numeric_id(getattr(message, "chat_id", None))
        chat_object_id = self._numeric_id(getattr(chat, "id", None))
        message_sender_id = self._numeric_id(getattr(getattr(message, "from_user", None), "id", None))
        return bool(user_id and self.is_allowed(user_id)
                    and getattr(chat, "type", None) == "private"
                    and chat_id == user_id == chat_object_id == message_sender_id
                    and not getattr(user, "is_bot", False)
                    and getattr(message, "sender_chat", None) is None
                    and not getattr(message, "business_connection_id", None))

    async def _handle_message(self, sender_id, chat_id, content, media=None, metadata=None):
        # Defense in depth even if called directly by another handler.
        if not self.is_allowed(sender_id) or self._numeric_id(chat_id) != self._numeric_id(sender_id):
            return
        if media:
            raise ValueError("Telegram: recepção de anexos desativada")
        await super()._handle_message(sender_id, chat_id, content, media, metadata)

    async def start(self) -> None:
        """Start polling only with a usable explicit allowlist; never log exceptions."""
        allow = getattr(self.config, "allow_from", None)
        if not isinstance(allow, (list, tuple)) or not any(
            isinstance(entry, str) and self._numeric_id(entry) for entry in allow
        ):
            logger.error("Telegram: configure pelo menos um ID numérico autorizado")
            return
        token = self.config.token
        if not token or len(token) < 40 or len(token.split(":")) != 2 or not token.split(":")[0].isascii() or not token.split(":")[0].isdigit():
            logger.error("Telegram: token inválido ou ausente")
            return
        if self._running or self._starting:
            logger.warning("Telegram: inicialização já ativa")
            return
        self._starting = True
        self._stop_requested = False
        initialized = started = polling = False
        try:
            # Install before bot construction: PTB can log tokens and updates.
            self._http_log_filter = _TelegramTokenLogFilter(token)
            names = set(_PRIVATE_LOGGERS)
            names.update(name for name in logging.root.manager.loggerDict
                         if name == "httpx" or name.startswith(("httpx.", "httpcore.", "telegram.")))
            self._private_loggers = [logging.getLogger(name) for name in names]
            for private_logger in self._private_loggers:
                private_logger.addFilter(self._http_log_filter)
            # PTB 22.x forbids builder proxy after explicit requests. Put it on
            # both requests; invalid configuration fails startup, never direct.
            req = HTTPXRequest(connection_pool_size=16, pool_timeout=5.0, connect_timeout=30.0, read_timeout=30.0, proxy=self.config.proxy or None)
            updates_req = HTTPXRequest(connection_pool_size=2, pool_timeout=5.0, connect_timeout=30.0, read_timeout=30.0, proxy=self.config.proxy or None)
            builder = Application.builder().token(token).request(req).get_updates_request(updates_req)
            self._app = builder.build()
            self._app.add_error_handler(self._on_error)
            self._app.add_handler(CommandHandler("start", self._on_start))
            self._app.add_handler(CommandHandler("new", self._forward_command))
            self._app.add_handler(CommandHandler("help", self._on_help))
            self._app.add_handler(MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL) & ~filters.COMMAND,
                self._on_message,
            ))
            await self._app.initialize()
            initialized = True
            if self._stop_requested:
                return
            await self._app.start()
            started = True
            if self._stop_requested:
                return
            try:
                await self._app.bot.set_my_commands(self.BOT_COMMANDS)
            except Exception:
                logger.warning("Telegram: não foi possível registrar comandos")
            if self._stop_requested or not any(self.is_allowed(entry) for entry in (self.config.allow_from or [])):
                return
            await self._app.updater.start_polling(allowed_updates=["message"], drop_pending_updates=True)
            polling = True
            self._running = not self._stop_requested
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Telegram: falha na inicialização ou polling")
        finally:
            self._running = False
            self._starting = False
            for chat_id in list(self._typing_tasks):
                self._stop_typing(chat_id)
            if self._app:
                for active, action in ((polling, self._app.updater.stop),
                                       (started, self._app.stop),
                                       (initialized, self._app.shutdown)):
                    if active:
                        try:
                            await action()
                        except Exception:
                            logger.warning("Telegram: falha ao encerrar componente")
                self._app = None
            if self._http_log_filter:
                for private_logger in self._private_loggers:
                    private_logger.removeFilter(self._http_log_filter)
                self._private_loggers = []
                self._http_log_filter = None

    async def stop(self) -> None:
        """Signal the polling loop; start owns lifecycle cleanup."""
        self._stop_requested = True
        self._running = False
        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)

    async def send(self, msg: OutboundMessage) -> None:
        """Only send to an explicitly authorized private-user ID."""
        target = self._numeric_id(msg.chat_id)
        if not target or not self.is_allowed(target) or not self._app:
            return
        # Reject the whole message, including any text claiming success.
        if msg.media:
            raise ValueError("Telegram: envio de anexos desativado")
        self._stop_typing(target)
        chat_id = int(target)
        reply_params = None
        if self.config.reply_to_message:
            reply_id = (msg.metadata or {}).get("message_id")
            if type(reply_id) is int and reply_id > 0:
                reply_params = ReplyParameters(message_id=reply_id, allow_sending_without_reply=True)

        if msg.content and msg.content != "[empty message]":
            for chunk in _split_message(msg.content):
                if not self.is_allowed(target) or not self._app:
                    return
                html = _markdown_to_telegram_html(chunk)
                # Conversion may expand the text beyond Telegram's 4096 UTF-16 units.
                if _utf16_len(html) > 4096:
                    html = None
                try:
                    kwargs = {"chat_id": chat_id, "text": html if html is not None else chunk,
                              "reply_parameters": reply_params}
                    if html is not None:
                        kwargs["parse_mode"] = "HTML"
                    await self._app.bot.send_message(**kwargs)
                except Exception:
                    logger.warning("Telegram: falha ao enviar texto formatado")
                    if html is None or not self.is_allowed(target) or not self._app:
                        return
                    try:
                        await self._app.bot.send_message(chat_id=chat_id, text=chunk, reply_parameters=reply_params)
                    except Exception:
                        logger.error("Telegram: falha ao enviar texto")

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized_update(update):
            return
        await update.message.reply_text(
            "👋 Hi! I'm caiocore.\n\nSend me a message and I'll respond!\n"
            "Type /help to see available commands."
        )

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized_update(update):
            return
        await update.message.reply_text(
            "🐈 caio commands:\n/new — Start a new conversation\n/help — Show available commands"
        )

    @staticmethod
    def _sender_id(user) -> str:
        return str(user.id)

    async def _forward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized_update(update):
            return
        message = update.message
        await self._handle_message(sender_id=self._sender_id(update.effective_user),
                                   chat_id=str(message.chat_id), content=message.text,
                                   metadata={"message_id": message.message_id})

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Authorize before reading text; attachment downloads are disabled."""
        if not self._authorized_update(update):
            return
        message = update.message
        user = update.effective_user
        sender_id = self._sender_id(user)
        chat_id = str(message.chat_id)
        content_parts = []
        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)

        if message.photo or message.voice or message.audio or message.document:
            content_parts.append("[Anexo não recebido nem analisado: anexos do Telegram estão desativados.]")
        if not self._authorized_update(update):
            return
        self._start_typing(chat_id)
        await self._handle_message(
            sender_id=sender_id, chat_id=chat_id,
            content="\n".join(content_parts) if content_parts else "[empty message]",
            media=[],
            metadata={"message_id": message.message_id, "user_id": user.id,
                      "username": user.username, "first_name": user.first_name, "is_group": False},
        )

    def _start_typing(self, chat_id: str) -> None:
        """Start sending 'typing...' indicator for a chat."""
        # Cancel any existing typing task for this chat
        if not self.is_allowed(chat_id) or not self._app:
            return
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))
    
    def _stop_typing(self, chat_id: str) -> None:
        """Stop the typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()
    
    async def _typing_loop(self, chat_id: str) -> None:
        """Repeatedly send 'typing' action until cancelled."""
        try:
            while self._app and self.is_allowed(chat_id):
                await self._app.bot.send_chat_action(chat_id=int(chat_id), action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("Telegram: indicador de digitação interrompido")
    
    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log polling / handler errors instead of silently swallowing them."""
        logger.error("Telegram: erro no processamento ou polling")
