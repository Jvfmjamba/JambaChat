import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.config import Settings
from jambachat.llm.types import ToolCall, ToolSpec
from jambachat.users.models import User

logger = structlog.get_logger(__name__)


class ToolError(Exception):
    pass


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    user: User
    settings: Settings
    now: datetime


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, simplify_schema(self.args_model))


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"ferramenta duplicada: {tool.name}")
        self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return _dump({"error": f"ferramenta desconhecida: {call.name}"})
        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            details = exc.errors(include_url=False, include_input=False, include_context=False)
            return _dump({"error": "argumentos inválidos", "details": details})
        try:
            result = await tool.handler(ctx, args)
        except ToolError as exc:
            return _dump({"error": str(exc)})
        except Exception:
            logger.exception("tool_failed", tool=call.name)
            return _dump({"error": "falha interna ao executar a ferramenta"})
        return _dump(result)


def simplify_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Gera um JSON Schema sem $ref, títulos ou anyOf com null.

    Vários provedores compatíveis com OpenAI (Gemini, Ollama, Groq) rejeitam ou
    entendem mal esses recursos, então o schema é achatado antes de ser enviado.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = defs[node["$ref"].rsplit("/", 1)[-1]]
            extra = {k: v for k, v in node.items() if k != "$ref"}
            return walk({**target, **extra})
        options = node.get("anyOf")
        if isinstance(options, list):
            concrete = [o for o in options if o != {"type": "null"}]
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            if len(concrete) == 1:
                return walk({**concrete[0], **rest})
            node = {**rest, "anyOf": concrete}
        return {
            k: walk(v)
            for k, v in node.items()
            if k != "title" and not (k == "default" and v is None)
        }

    result: dict[str, Any] = walk(schema)
    return result


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
