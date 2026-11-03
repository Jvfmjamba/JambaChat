import asyncio
import uuid
from collections import defaultdict
from dataclasses import replace
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
from jambachat.timeutils import as_utc
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from jambachat.users.repository import get_or_create_user
from jambachat.whatsapp.client import MediaTooLargeError, WhatsAppClient, WhatsAppError
from jambachat.whatsapp.inbound import InboundMessage

logger = structlog.get_logger(__name__)

UNSUPPORTED_TEXT = "Esse tipo de mensagem eu ainda não entendo. Pode mandar em texto?"
AUDIO_DISABLED_TEXT = "Ainda não consigo ouvir áudios por aqui. Pode mandar em texto?"
AUDIO_FAILED_TEXT = "Não consegui entender o áudio. Pode repetir ou mandar em texto?"
IMAGE_DISABLED_TEXT = "Ainda não consigo ver imagens por aqui. Pode me contar em texto?"
IMAGE_FAILED_TEXT = "Não consegui abrir a imagem. Tenta mandar de novo?"
MEDIA_TOO_LARGE_TEXT = "Esse arquivo é grande demais pra mim. Consegue mandar um menor?"
IMAGE_PLACEHOLDER = "(imagem enviada)"
LLM_DOWN_TEXT = "Tive um problema pra pensar agora. Tenta de novo daqui a pouco?"
MB = 1024 * 1024


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
    ) -> None:
        self._transcriber = transcriber
        self._sessionmaker = sessionmaker
        self._whatsapp = whatsapp
        self._orchestrator = orchestrator
        self._settings = settings
        self._locks: defaultdict[uuid.UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._answered_until: dict[uuid.UUID, datetime] = {}

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
                    content={"text": message.text, "kind": message.kind},
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
        try:
            await self._whatsapp.mark_read(message.wa_message_id, typing=True)
        except WhatsAppError as exc:
            log.warning("mark_read_failed", error=str(exc))

        # mensagens do mesmo usuário são respondidas em ordem, uma por vez
        async with self._locks[user_id]:
            reply_to = await self._prepare_reply(user_id, message, log)
            if reply_to is None:
                return
            phone, reply = reply_to
            try:
                await self._whatsapp.send_text(phone, reply)
            except WhatsAppError as exc:
                log.error("send_failed", error=str(exc))
                return
        log.info("message_answered")

    async def _prepare_reply(
        self, user_id: uuid.UUID, message: InboundMessage, log: structlog.stdlib.BoundLogger
    ) -> tuple[str, str] | None:
        async with self._sessionmaker() as session:
            user = await session.get(User, user_id)
            if user is None:
                return None
            if await self._already_answered(session, user_id, message):
                log.info("message_coalesced")
                return None
            phone = user.phone
            reply = await self._reply(session, user, message, log)
            await session.commit()
        return phone, reply

    async def _reply(
        self,
        session: AsyncSession,
        user: User,
        message: InboundMessage,
        log: structlog.stdlib.BoundLogger,
    ) -> str:
        try:
            text, images = await self._understand(session, message, log)
        except _EarlyReplyError as early:
            return early.text

        if not message.has_media:
            command_reply = await handle_command(session, user, text)
            if command_reply is not None:
                return command_reply

        now = datetime.now(ZoneInfo(user.timezone))
        latest = await self._latest_inbound_at(session, user.id)
        history = await load_history(session, user, now)
        if images:
            history = _attach_images(history, images)
        ctx = ToolContext(session=session, user=user, settings=self._settings, now=now)
        try:
            result = await self._orchestrator.run(history, ctx)
        except LLMError as exc:
            log.error("llm_failed", error=str(exc), status_code=exc.status_code)
            return LLM_DOWN_TEXT
        await save_turn(session, user.id, result.messages)
        if latest is not None:
            self._answered_until[user.id] = latest
        return result.reply

    async def _understand(
        self, session: AsyncSession, message: InboundMessage, log: structlog.stdlib.BoundLogger
    ) -> tuple[str, tuple[Image, ...]]:
        """Transforma a mensagem recebida em texto (e imagens) para o modelo."""
        if message.kind in {"text", "button", "interactive"} and message.text:
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

    async def _store_inbound_text(
        self, session: AsyncSession, message: InboundMessage, text: str
    ) -> None:
        row = await session.scalar(
            select(Message).where(Message.wa_message_id == message.wa_message_id)
        )
        if row is not None:
            row.content = {"text": text, "kind": message.kind}
            await session.flush()

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
            or not message.text
            or is_command(message.text)
        ):
            return False
        received_at = await session.scalar(
            select(Message.created_at).where(Message.wa_message_id == message.wa_message_id)
        )
        return received_at is not None and as_utc(received_at) <= as_utc(answered_until)


def _attach_images(history: list[ChatMessage], images: tuple[Image, ...]) -> list[ChatMessage]:
    for index in range(len(history) - 1, -1, -1):
        if history[index].role == "user":
            history[index] = replace(history[index], images=images)
            break
    return history
