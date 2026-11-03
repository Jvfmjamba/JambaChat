from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from jambachat import cli
from jambachat.config import get_settings
from jambachat.conversation.commands import HELP_TEXT
from jambachat.db.session import get_engine, get_sessionmaker

LLM_URL = "http://localhost:11434/v1/chat/completions"


def scripted_input(lines: list[str]) -> Iterator[str]:
    yield from lines


@respx.mock
async def test_cli_conversation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    respx.post(LLM_URL).respond(
        json={"choices": [{"message": {"role": "assistant", "content": "Oi, João!"}}]}
    )
    lines = scripted_input(["/ajuda", "", "oi", "/sair"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))

    settings = get_settings().model_copy(update={"allowed_phones": frozenset({cli.CLI_PHONE})})
    try:
        await cli.run(settings, "João")
    finally:
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()

    output = capsys.readouterr().out
    assert HELP_TEXT in output
    assert "jamba> Oi, João!" in output


async def test_console_whatsapp_saves_documents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async with httpx.AsyncClient() as http:
        console = cli.ConsoleWhatsApp(http, tmp_path)
        await console.send_document("x", b"a;b", "text/csv", "../../fora.csv", "planilha")
        await console.send_buttons("x", "Lembrete", ["Adiar", "Feito"])
        await console.send_template("x", "lembrete", ["pagar"])
        await console.mark_read("id", typing=True)

    assert (tmp_path / "fora.csv").read_bytes() == b"a;b"
    output = capsys.readouterr().out
    assert "[Adiar]  [Feito]" in output
    assert "pagar" in output
