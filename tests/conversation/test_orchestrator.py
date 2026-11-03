import json
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.conversation.orchestrator import LIMIT_REPLY, Orchestrator
from jambachat.llm.types import ChatMessage, LLMResponse, ToolCall
from jambachat.tools.registry import Tool, ToolContext, ToolError, ToolRegistry
from jambachat.users.models import User
from tests.fakes import ScriptedLLM, make_settings


class EchoArgs(BaseModel):
    value: int


async def echo(ctx: ToolContext, args: EchoArgs) -> dict[str, Any]:
    if args.value < 0:
        raise ToolError("valor negativo")
    return {"double": args.value * 2}


async def boom(ctx: ToolContext, args: EchoArgs) -> dict[str, Any]:
    raise RuntimeError("bug")


def context() -> ToolContext:
    user = User(phone="1", phone_hash="h", name="João", timezone="America/Sao_Paulo")
    return ToolContext(
        session=cast(AsyncSession, None),
        user=user,
        settings=make_settings(),
        now=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
    )


def registry() -> ToolRegistry:
    return ToolRegistry(
        [Tool("echo", "dobra", EchoArgs, echo), Tool("boom", "falha", EchoArgs, boom)]
    )


async def test_plain_reply_and_system_prompt() -> None:
    llm = ScriptedLLM(LLMResponse("Oi, João!"))
    result = await Orchestrator(llm, registry()).run([ChatMessage("user", "oi")], context())

    assert result.reply == "Oi, João!"
    assert [m.role for m in result.messages] == ["assistant"]
    system, _, tools = llm.calls[0]
    assert "terça-feira, 03/11/2026 12:00" in system
    assert "de João" in system
    assert {t.name for t in tools} == {"echo", "boom"}
    assert "title" not in tools[0].parameters


async def test_tool_loop_feeds_results_back() -> None:
    llm = ScriptedLLM(
        LLMResponse(
            "",
            (
                ToolCall("1", "echo", {"value": 21}),
                ToolCall("2", "echo", {"value": "x"}),
                ToolCall("3", "echo", {"value": -1}),
                ToolCall("4", "boom", {"value": 1}),
                ToolCall("5", "nada", {}),
            ),
        ),
        LLMResponse("Deu 42."),
    )
    result = await Orchestrator(llm, registry()).run([ChatMessage("user", "x")], context())

    tool_outputs = [json.loads(m.content) for m in result.messages if m.role == "tool"]
    assert result.reply == "Deu 42."
    assert tool_outputs[0] == {"double": 42}
    assert tool_outputs[1]["error"] == "argumentos inválidos"
    assert tool_outputs[2] == {"error": "valor negativo"}
    assert tool_outputs[3] == {"error": "falha interna ao executar a ferramenta"}
    assert "desconhecida" in tool_outputs[4]["error"]
    second_call_messages = llm.calls[1][1]
    assert [m.role for m in second_call_messages] == ["user", "assistant"] + ["tool"] * 5


async def test_stops_after_max_iterations() -> None:
    loop = LLMResponse("", (ToolCall("1", "echo", {"value": 1}),))
    llm = ScriptedLLM(loop, loop)
    result = await Orchestrator(llm, registry(), max_iterations=2).run(
        [ChatMessage("user", "x")], context()
    )

    assert result.reply == LIMIT_REPLY
    assert result.messages[-1] == ChatMessage("assistant", LIMIT_REPLY)
