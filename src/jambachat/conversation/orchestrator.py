from collections.abc import Sequence
from dataclasses import dataclass, field

from jambachat.conversation.prompt import build_system_prompt
from jambachat.llm.types import ChatMessage, LLMClient
from jambachat.tools.registry import ToolContext, ToolRegistry

MAX_ITERATIONS = 5
EMPTY_REPLY = "Hmm, não consegui formular uma resposta. Pode reformular?"
LIMIT_REPLY = "Esse pedido ficou complexo demais pra mim de uma vez. Pode dividir em partes?"


@dataclass
class TurnResult:
    reply: str
    messages: list[ChatMessage] = field(default_factory=list)


class Orchestrator:
    def __init__(
        self, llm: LLMClient, registry: ToolRegistry, max_iterations: int = MAX_ITERATIONS
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._max_iterations = max_iterations

    async def run(self, history: Sequence[ChatMessage], ctx: ToolContext) -> TurnResult:
        system = build_system_prompt(ctx.now, ctx.user.timezone, ctx.user.name)
        specs = self._registry.specs()
        conversation = list(history)
        produced: list[ChatMessage] = []

        for _ in range(self._max_iterations):
            response = await self._llm.complete(system, conversation, specs)
            if not response.tool_calls:
                reply = ChatMessage("assistant", response.text or EMPTY_REPLY)
                produced.append(reply)
                return TurnResult(reply.content, produced)

            step = ChatMessage("assistant", response.text, response.tool_calls)
            conversation.append(step)
            produced.append(step)
            for call in response.tool_calls:
                result = ChatMessage(
                    "tool", await self._registry.execute(call, ctx), tool_call_id=call.id
                )
                conversation.append(result)
                produced.append(result)

        produced.append(ChatMessage("assistant", LIMIT_REPLY))
        return TurnResult(LIMIT_REPLY, produced)
