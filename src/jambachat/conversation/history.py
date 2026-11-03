import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.conversation.models import Direction, Message, Role
from jambachat.llm.types import ChatMessage, Image, ToolCall
from jambachat.timeutils import as_utc
from jambachat.users.models import User

HISTORY_LIMIT = 20
HISTORY_TTL = timedelta(hours=6)
MAX_OLD_TOOL_RESULT = 1500


def is_command(text: str) -> bool:
    return text.lstrip().startswith("/")


def to_content(message: ChatMessage) -> dict[str, Any]:
    content: dict[str, Any] = {"text": message.content}
    if message.tool_calls:
        content["tool_calls"] = [
            {"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls
        ]
    if message.tool_call_id:
        content["tool_call_id"] = message.tool_call_id
    return content


def from_row(row: Message) -> ChatMessage | None:
    content = row.content
    if row.role == Role.USER:
        text = content.get("text")
        return ChatMessage("user", text) if text and not is_command(text) else None
    if row.role == Role.TOOL:
        # resultados de turnos anteriores só servem de contexto; corta para poupar tokens
        text = str(content.get("text", ""))
        if len(text) > MAX_OLD_TOOL_RESULT:
            text = text[:MAX_OLD_TOOL_RESULT] + "… [resultado truncado]"
        return ChatMessage("tool", text, tool_call_id=content.get("tool_call_id"))
    calls = tuple(
        ToolCall(c["id"], c["name"], c.get("arguments") or {})
        for c in content.get("tool_calls") or []
    )
    return ChatMessage("assistant", content.get("text", ""), calls)


async def save_turn(
    session: AsyncSession,
    user_id: uuid.UUID,
    messages: Sequence[ChatMessage],
    after: datetime | None = None,
) -> None:
    """Grava as mensagens do turno logo depois de `after` (a última mensagem respondida).

    Assim, uma mensagem que chegou enquanto o modelo pensava continua depois da
    resposta no histórico, e não antes dela.
    """
    base = as_utc(after) if after is not None else datetime.now(UTC)
    for offset, message in enumerate(messages):
        session.add(
            Message(
                user_id=user_id,
                direction=Direction.OUTBOUND,
                role=Role(message.role),
                content=to_content(message),
                created_at=base + timedelta(microseconds=offset + 1),
            )
        )


async def load_history(
    session: AsyncSession,
    user: User,
    now: datetime,
    images: dict[str, tuple[Image, ...]] | None = None,
) -> list[ChatMessage]:
    since = now.astimezone(UTC) - HISTORY_TTL
    if user.context_cleared_at is not None:
        since = max(since, as_utc(user.context_cleared_at))
    rows = await session.scalars(
        select(Message)
        .where(Message.user_id == user.id, Message.created_at > since)
        .order_by(Message.created_at.desc())
        .limit(HISTORY_LIMIT)
    )
    history: list[ChatMessage] = []
    for row in reversed(list(rows)):
        message = from_row(row)
        if message is None:
            continue
        if images and row.wa_message_id in images:
            message = replace(message, images=images[row.wa_message_id])
        history.append(message)
    while history and history[0].role != "user":
        history.pop(0)
    return history
