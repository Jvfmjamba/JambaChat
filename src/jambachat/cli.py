"""Conversa com o JambaChat pelo terminal, sem WhatsApp.

Usa o mesmo modelo, ferramentas e banco configurados no .env. Útil para testar o
provedor de IA antes de configurar a Meta:

    uv run python -m jambachat.cli
"""

import asyncio
import contextlib
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx

from jambachat.config import Settings, get_settings
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.db.models import Base
from jambachat.db.session import get_engine, get_sessionmaker
from jambachat.llm.factory import create_llm_client
from jambachat.logging import configure_logging
from jambachat.reminders.scheduler import ReminderDispatcher, ReminderScheduler
from jambachat.security.crypto import get_cipher
from jambachat.tools.catalog import build_registry
from jambachat.whatsapp.client import Media, WhatsAppClient, WhatsAppError
from jambachat.whatsapp.inbound import InboundMessage

CLI_PHONE = "cli"
EXIT_WORDS = {"/sair", "/exit", "sair"}


class ConsoleWhatsApp(WhatsAppClient):
    """Imita o cliente do WhatsApp escrevendo as mensagens no terminal."""

    def __init__(self, http: httpx.AsyncClient, downloads: Path) -> None:
        super().__init__(http, access_token="", phone_number_id="cli", api_version="cli")
        self._downloads = downloads

    async def send_text(self, to: str, text: str) -> list[str]:
        print(f"\njamba> {text}\n")
        return [str(uuid.uuid4())]

    async def send_buttons(self, to: str, text: str, buttons: Sequence[str]) -> str:
        options = "  ".join(f"[{b}]" for b in buttons)
        print(f"\njamba> {text}\n       {options}\n")
        return str(uuid.uuid4())

    async def send_template(
        self, to: str, name: str, params: Sequence[str] = (), language: str = "pt_BR"
    ) -> str:
        print(f"\njamba (modelo {name})> {' | '.join(params)}\n")
        return str(uuid.uuid4())

    async def send_document(
        self, to: str, content: bytes, mime_type: str, filename: str, caption: str = ""
    ) -> str:
        path = self._downloads / Path(filename).name
        path.write_bytes(content)
        print(f"\njamba> [arquivo salvo em {path}] {caption}\n")
        return str(uuid.uuid4())

    async def mark_read(self, message_id: str, *, typing: bool = False) -> None:
        return None

    async def download_media(self, media_id: str, max_bytes: int) -> Media:
        raise WhatsAppError("mídia não é suportada no terminal")


def _message(text: str, name: str | None) -> InboundMessage:
    return InboundMessage(
        wa_message_id=f"cli.{uuid.uuid4()}",
        phone=CLI_PHONE,
        name=name,
        kind="text",
        text=text,
        timestamp=datetime.now(UTC),
    )


async def _prepare_database(settings: Settings) -> None:
    if settings.database_url.startswith("sqlite"):
        async with get_engine().begin() as connection:
            await connection.run_sync(Base.metadata.create_all)


async def run(settings: Settings, name: str | None) -> None:
    get_cipher()
    await _prepare_database(settings)
    sessionmaker = get_sessionmaker()
    async with httpx.AsyncClient(follow_redirects=False) as http:
        whatsapp = ConsoleWhatsApp(http, Path.cwd())
        orchestrator = Orchestrator(
            create_llm_client(settings, http), build_registry(settings, http)
        )
        service = ConversationService(sessionmaker, whatsapp, orchestrator, settings)
        scheduler = ReminderScheduler(ReminderDispatcher(sessionmaker, whatsapp, settings), 10)
        scheduler.start()
        print("JambaChat no terminal. Escreva /sair para encerrar.\n")
        try:
            while True:
                try:
                    text = (await asyncio.to_thread(input, "você> ")).strip()
                except EOFError:
                    break
                if not text:
                    continue
                if text.lower() in EXIT_WORDS:
                    break
                message = _message(text, name)
                user_id = await service.register_inbound(message)
                if user_id is not None:
                    await service.process(user_id, message)
        finally:
            await scheduler.stop()
            await get_engine().dispose()


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    settings = get_settings().model_copy(
        update={"allowed_phones": frozenset({CLI_PHONE}), "admin_phones": frozenset({CLI_PHONE})}
    )
    configure_logging("WARNING", json=False)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(settings, args[0] if args else None))


if __name__ == "__main__":
    main()
