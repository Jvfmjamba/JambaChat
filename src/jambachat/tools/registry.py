import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, get_args

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.config import Settings
from jambachat.llm.types import ToolCall, ToolSpec
from jambachat.users.models import User

logger = structlog.get_logger(__name__)

MAX_TOOL_CALLS_PER_TURN = 10

IDENTITY_FIELD_HINTS = (
    "user",
    "usuario",
    "owner",
    "dono",
    "phone",
    "telefone",
    "wa_id",
    "recipient",
    "destinat",
    "contact",
    "contato",
)
IDENTITY_FIELD_EXACT = frozenset({"to", "para", "send_to", "enviar_para", "para_quem"})


class ToolError(Exception):
    pass


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Attachment:
    filename: str
    mime_type: str
    content: bytes
    caption: str = ""


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    user: User
    settings: Settings
    now: datetime
    attachments: list[Attachment] = field(default_factory=list)


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


def _is_identity_field(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    if normalized in IDENTITY_FIELD_EXACT:
        return True
    parts = normalized.split("_")
    return any(
        part == hint or (len(hint) >= 4 and part.startswith(hint))
        for part in parts
        for hint in IDENTITY_FIELD_HINTS
    )


def _nested_models(annotation: Any) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _nested_models(arg)]


def _identity_fields(model: type[BaseModel]) -> list[str]:
    found: list[str] = []
    for name, info in model.model_fields.items():
        if _is_identity_field(name) or (info.alias and _is_identity_field(info.alias)):
            found.append(name)
        for nested in _nested_models(info.annotation):
            found.extend(f"{name}.{sub}" for sub in _identity_fields(nested))
    return found


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    reads_untrusted: bool = False
    destructive: bool = False

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
        if not issubclass(tool.args_model, ToolArgs):
            raise ValueError(f"{tool.name}: argumentos precisam herdar de ToolArgs")
        identity = _identity_fields(tool.args_model)
        if identity:
            raise ValueError(
                f"{tool.name}: a IA não pode escolher o usuário afetado (campos {identity}); "
                "use ctx.user"
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> str:
        tool = self._tools.get(call.name)
        if tool is None:
            return dump({"error": f"ferramenta desconhecida: {call.name}"})
        try:
            args = tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            details = exc.errors(include_url=False, include_input=False, include_context=False)
            return dump({"error": "argumentos inválidos", "details": details})
        try:
            async with ctx.session.begin_nested():
                result = await tool.handler(ctx, args)
        except ToolError as exc:
            return dump({"error": str(exc)})
        except Exception:
            logger.exception("tool_failed", tool=call.name)
            return dump({"error": "falha interna ao executar a ferramenta"})
        return dump(result)


def simplify_schema(model: type[BaseModel]) -> dict[str, Any]:
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
            resolved = [_const_to_enum(walk(option)) for option in concrete]
            if all("enum" in o for o in resolved) and len({o.get("type") for o in resolved}) == 1:
                merged_enum = [v for o in resolved for v in o["enum"]]
                return walk({**rest, "type": resolved[0].get("type"), "enum": merged_enum})
            node = {**rest, "anyOf": concrete}
        return {
            k: walk(v)
            for k, v in node.items()
            if k not in {"title", "additionalProperties"} and not (k == "default" and v is None)
        }

    result: dict[str, Any] = walk(schema)
    return result


def _const_to_enum(node: dict[str, Any]) -> dict[str, Any]:
    if "const" not in node:
        return node
    return {**{k: v for k, v in node.items() if k != "const"}, "enum": [node["const"]]}


def dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
