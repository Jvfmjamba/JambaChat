import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.config import Settings
from jambachat.conversation.commands import handle_command
from jambachat.conversation.history import is_command, load_history, save_turn
from jambachat.conversation.models import Direction, Message, Role
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.llm.types import ChatMessage, Image, LLMError
from jambachat.media.transcriber import Transcriber, TranscriptionError
from jambachat.reminders.buttons import handle_reminder_button
from jambachat.security.ratelimit import Decision, RateLimiter
from jambachat.timeutils import as_utc
from jambachat.tools.registry import Attachment, ToolContext
from jambachat.users.models import User
from jambachat.users.repository import get_or_create_user
from jambachat.whatsapp.client import MediaTooLargeError, WhatsAppClient, WhatsAppError
from jambachat.whatsapp.inbound import IGNORED_KINDS, InboundMessage

logger = structlog.get_logger(__name__)

UNSUPPORTED_TEXT = "Esse tipo de mensagem eu ainda não entendo. Pode mandar em texto?"
AUDIO_DISABLED_TEXT = "Ainda não consigo ouvir áudios por aqui. Pode mandar em texto?"
AUDIO_FAILED_TEXT = "Não consegui entender o áudio. Pode repetir ou mandar em texto?"
IMAGE_DISABLED_TEXT = "Ainda não consigo ver imagens por aqui. Pode me contar em texto?"
IMAGE_FAILED_TEXT = "Não consegui abrir a imagem. Tenta mandar de novo?"
MEDIA_TOO_LARGE_TEXT = "Esse arquivo é grande demais pra mim. Consegue mandar um menor?"
IMAGE_PLACEHOLDER = "(imagem enviada)"
LLM_DOWN_TEXT = "Tive um problema pra pensar agora. Tenta de novo daqui a pouco?"
TOO_LONG_TEXT = "Essa mensagem ficou longa demais pra mim. Pode resumir em até {limit} caracteres?"
RATE_LIMITED_TEXT = (
    "Você mandou muitas mensagens em pouco tempo. Espera um pouquinho e tenta de novo."
)
WELCOME_TEXT = (
    "Oi! Eu sou o *JambaChat* 👋 Anoto gastos, crio lembretes e listas, "
    "pesquiso e faço contas. Mande /ajuda pra ver exemplos."
)
INTERNAL_ERROR_TEXT = "Opa, algo deu errado do meu lado. Tenta de novo daqui a pouco?"
MB = 1024 * 1024


@dataclass
class _UserLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class _EarlyReplyError(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


class ConversationService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        whatsapp: WhatsAppClient,
        orchestrator: Orchestrator,
        settings: Settings,
        transcriber: Transcriber | None = None,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._transcriber = transcriber
        self._sessionmaker = sessionmaker
        self._whatsapp = whatsapp
        self._orchestrator = orchestrator
        self._settings = settings
        self._limiter = limiter or RateLimiter.from_settings(settings)
        self._locks: dict[uuid.UUID, _UserLock] = {}
        self._answered_until: dict[uuid.UUID, datetime] = {}

    def _too_long(self, message: InboundMessage) -> bool:
        return message.text is not None and len(message.text) > self._settings.max_input_chars

    async def register_inbound(self, message: InboundMessage) -> uuid.UUID | None:
        async with self._sessionmaker() as session:
            duplicate = await session.scalar(
                select(Message.id).where(Message.wa_message_id == message.wa_message_id)
            )
            if duplicate is not None:
                return None
            user = await get_or_create_user(session, message.phone, message.name, self._settings)
            user.last_inbound_at = message.timestamp
            session.add(
                Message(
                    user_id=user.id,
                    wa_message_id=message.wa_message_id,
                    direction=Direction.INBOUND,
                    role=Role.USER,
                    content={
                        "text": None if self._too_long(message) else message.text,
                        "kind": message.kind,
                    },
                    created_at=datetime.now(UTC),
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            return user.id

    async def process(self, user_id: uuid.UUID, message: InboundMessage) -> None:
        log = logger.bind(user_id=str(user_id), wa_message_id=message.wa_message_id)
        if message.kind in IGNORED_KINDS:
            log.info("message_ignored", kind=message.kind)
            return
        decision = self._limiter.hit(user_id)
        if decision is not Decision.ALLOW:
            log.warning("rate_limited", notified=decision is Decision.BLOCK_AND_NOTIFY)
            if decision is Decision.BLOCK_AND_NOTIFY:
                await self._send_quietly(user_id, RATE_LIMITED_TEXT, log)
            return
        try:
            await self._whatsapp.mark_read(message.wa_message_id, typing=True)
        except WhatsAppError as exc:
            log.warning("mark_read_failed", error=str(exc))

        # mensagens do mesmo usuário são respondidas em ordem, uma por vez
        async with self._user_lock(user_id):
            try:
                reply_to = await self._prepare_reply(user_id, message, log)
            except Exception:
                log.exception("reply_failed")
                await self._send_failure_notice(message.phone, log)
                return
            if reply_to is None:
                return
            phone, texts, attachments = reply_to
            try:
                for text in texts:
                    await self._whatsapp.send_text(phone, text)
                for attachment in attachments:
                    await self._whatsapp.send_document(
                        phone,
                        attachment.content,
                        attachment.mime_type,
                        attachment.filename,
                        attachment.caption,
                    )
            except WhatsAppError as exc:
                log.error("send_failed", error=str(exc))
                return
        log.info("message_answered")

    @contextlib.asynccontextmanager
    async def _user_lock(self, user_id: uuid.UUID) -> AsyncIterator[None]:
        entry = self._locks.setdefault(user_id, _UserLock())
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0:
                self._locks.pop(user_id, None)

    async def _send_failure_notice(self, phone: str, log: structlog.stdlib.BoundLogger) -> None:
        try:
            await self._whatsapp.send_text(phone, INTERNAL_ERROR_TEXT)
        except WhatsAppError as exc:
            log.error("send_failed", error=str(exc))

    async def _prepare_reply(
        self, user_id: uuid.UUID, message: InboundMessage, log: structlog.stdlib.BoundLogger
    ) -> tuple[str, list[str], list[Attachment]] | None:
        async with self._sessionmaker() as session:
            user = await session.get(User, user_id)
            if user is None:
                return None
            if await self._already_answered(session, user_id, message):
                log.info("message_coalesced")
                return None
            phone = user.phone
            attachments: list[Attachment] = []
            welcome = self._settings.send_welcome and await self._is_first_contact(
                session, user, message
            )
            reply = await self._reply(session, user, message, log, attachments)
            failed = reply in {LLM_DOWN_TEXT, INTERNAL_ERROR_TEXT}
            texts = [WELCOME_TEXT, reply] if welcome and not failed else [reply]
            await session.commit()
        return phone, texts, attachments

    async def _send_quietly(
        self, user_id: uuid.UUID, text: str, log: structlog.stdlib.BoundLogger
    ) -> None:
        async with self._sessionmaker() as session:
            user = await session.get(User, user_id)
            phone = user.phone if user else None
        if phone is None:
            return
        try:
            await self._whatsapp.send_text(phone, text)
        except WhatsAppError as exc:
            log.error("send_failed", error=str(exc))

    async def _reply(
        self,
        session: AsyncSession,
        user: User,
        message: InboundMessage,
        log: structlog.stdlib.BoundLogger,
        attachments: list[Attachment],
    ) -> str:
        try:
            text, images = await self._understand(session, message, log)
        except _EarlyReplyError as early:
            return early.text

        if not message.has_media:
            command_reply = await handle_command(
                session, user, text, self._settings, self._orchestrator.tool_count, attachments
            )
            if command_reply is not None:
                return command_reply

        now = datetime.now(ZoneInfo(user.timezone))
        latest = await self._latest_inbound_at(session, user.id)
        if message.kind in {"interactive", "button"}:
            button_reply = await handle_reminder_button(session, user, text, now, message.reply_id)
            if button_reply is not None:
                await save_turn(
                    session, user.id, [ChatMessage("assistant", button_reply)], after=latest
                )
                return button_reply

        history = await load_history(
            session, user, now, {message.wa_message_id: images} if images else None
        )
        ctx = ToolContext(
            session=session,
            user=user,
            settings=self._settings,
            now=now,
            attachments=attachments,
        )
        try:
            result = await self._orchestrator.run(history, ctx, untrusted_input=bool(images))
        except LLMError as exc:
            log.error("llm_failed", error=str(exc), status_code=exc.status_code)
            # desfaz o que as ferramentas fizeram: o usuário vai repetir o pedido
            await session.rollback()
            attachments.clear()
            return LLM_DOWN_TEXT
        await save_turn(session, user.id, result.messages, after=latest)
        if latest is not None:
            self._answered_until[user.id] = latest
        return result.reply

    async def _understand(
        self, session: AsyncSession, message: InboundMessage, log: structlog.stdlib.BoundLogger
    ) -> tuple[str, tuple[Image, ...]]:
        """Transforma a mensagem recebida em texto (e imagens) para o modelo."""
        if message.kind in {"text", "button", "interactive", "location"} and message.text:
            self._ensure_fits(message.text)
            return message.text, ()
        if message.kind == "audio" and message.media_id:
            transcript = await self._transcribe(message.media_id, log)
            await self._store_inbound_text(session, message, transcript)
            return transcript, ()
        if message.kind == "image" and message.media_id:
            image = await self._download_image(message.media_id, log)
            text = message.text or IMAGE_PLACEHOLDER
            await self._store_inbound_text(session, message, text)
            return text, (image,)
        raise _EarlyReplyError(UNSUPPORTED_TEXT)

    async def _transcribe(self, media_id: str, log: structlog.stdlib.BoundLogger) -> str:
        if self._transcriber is None:
            raise _EarlyReplyError(AUDIO_DISABLED_TEXT)
        try:
            media = await self._whatsapp.download_media(media_id, self._settings.max_audio_mb * MB)
            return await self._transcriber.transcribe(media.content, media.mime_type)
        except MediaTooLargeError as exc:
            raise _EarlyReplyError(MEDIA_TOO_LARGE_TEXT) from exc
        except (WhatsAppError, TranscriptionError) as exc:
            log.warning("audio_failed", error=str(exc))
            raise _EarlyReplyError(AUDIO_FAILED_TEXT) from exc

    async def _download_image(self, media_id: str, log: structlog.stdlib.BoundLogger) -> Image:
        if not self._settings.llm_vision:
            raise _EarlyReplyError(IMAGE_DISABLED_TEXT)
        try:
            media = await self._whatsapp.download_media(media_id, self._settings.max_image_mb * MB)
        except MediaTooLargeError as exc:
            raise _EarlyReplyError(MEDIA_TOO_LARGE_TEXT) from exc
        except WhatsAppError as exc:
            log.warning("image_failed", error=str(exc))
            raise _EarlyReplyError(IMAGE_FAILED_TEXT) from exc
        mime = media.mime_type.split(";")[0].strip().lower()
        if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise _EarlyReplyError(IMAGE_FAILED_TEXT)
        return Image(mime, media.content)

    def _ensure_fits(self, text: str) -> None:
        limit = self._settings.max_input_chars
        if len(text) > limit:
            raise _EarlyReplyError(TOO_LONG_TEXT.format(limit=limit))

    async def _store_inbound_text(
        self, session: AsyncSession, message: InboundMessage, text: str
    ) -> None:
        self._ensure_fits(text)
        row = await session.scalar(
            select(Message).where(Message.wa_message_id == message.wa_message_id)
        )
        if row is not None:
            row.content = {"text": text, "kind": message.kind}
            await session.flush()

    async def _is_first_contact(
        self, session: AsyncSession, user: User, message: InboundMessage
    ) -> bool:
        if message.text and is_command(message.text):
            return False
        # primeiro contato = ainda não respondemos nada; vale também para rajadas
        answered = await session.scalar(
            select(Message.id)
            .where(Message.user_id == user.id, Message.direction == Direction.OUTBOUND)
            .limit(1)
        )
        return answered is None

    async def _latest_inbound_at(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> datetime | None:
        latest: datetime | None = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.user_id == user_id, Message.direction == Direction.INBOUND
            )
        )
        return latest

    async def _already_answered(
        self, session: AsyncSession, user_id: uuid.UUID, message: InboundMessage
    ) -> bool:
        """Mensagens que chegaram enquanto outra era respondida já entraram naquele contexto."""
        answered_until = self._answered_until.get(user_id)
        if (
            answered_until is None
            or message.has_media
            or message.kind in {"interactive", "button"}
            or not message.text
            or is_command(message.text)
        ):
            return False
        received_at = await session.scalar(
            select(Message.created_at).where(Message.wa_message_id == message.wa_message_id)
        )
        return received_at is not None and as_utc(received_at) <= as_utc(answered_until)
