import asyncio
import uuid
from collections import defaultdict
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
from jambachat.llm.types import LLMError
from jambachat.timeutils import as_utc
from jambachat.tools.registry import ToolContext
from jambachat.users.models import User
from jambachat.users.repository import get_or_create_user
from jambachat.whatsapp.client import WhatsAppClient, WhatsAppError
from jambachat.whatsapp.inbound import InboundMessage

logger = structlog.get_logger(__name__)

UNSUPPORTED_TEXT = "Por enquanto eu só entendo mensagens de texto. Áudio e imagem chegam em breve!"
LLM_DOWN_TEXT = "Tive um problema pra pensar agora. Tenta de novo daqui a pouco?"


class ConversationService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        whatsapp: WhatsAppClient,
        orchestrator: Orchestrator,
        settings: Settings,
    ) -> None:
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
        if message.kind not in {"text", "button", "interactive"} or not message.text:
            return UNSUPPORTED_TEXT

        command_reply = await handle_command(session, user, message.text)
        if command_reply is not None:
            return command_reply

        now = datetime.now(ZoneInfo(user.timezone))
        latest = await self._latest_inbound_at(session, user.id)
        history = await load_history(session, user, now)
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
        if answered_until is None or not message.text or is_command(message.text):
            return False
        received_at = await session.scalar(
            select(Message.created_at).where(Message.wa_message_id == message.wa_message_id)
        )
        return received_at is not None and as_utc(received_at) <= as_utc(answered_until)
