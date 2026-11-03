import re
from datetime import timedelta
from collections import OrderedDict
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from jambachat.db.base import utcnow
from jambachat.lists.models import ListItem
from jambachat.text import fold
from jambachat.timeutils import as_utc
from jambachat.tools.registry import Tool, ToolContext, ToolError

MAX_ITEMS_PER_LIST = 200
MAX_LISTS = 30
_PREFIX = re.compile(r"^lista (de |do |da |dos |das )?")

ListName = Field(min_length=1, max_length=60, description='Nome da lista, ex.: "compras"')


class AddItemsArgs(BaseModel):
    list_name: str = ListName
    items: list[str] = Field(
        min_length=1, max_length=50, description="Itens a adicionar, um por posição"
    )


class ShowListArgs(BaseModel):
    list_name: str | None = Field(
        default=None, max_length=60, description="Lista a mostrar; omita para ver todas as listas"
    )


class UpdateItemsArgs(BaseModel):
    list_name: str = ListName
    action: Literal["marcar", "desmarcar", "remover", "limpar_marcados", "apagar_lista"] = Field(
        description="marcar/desmarcar/remover itens; limpar_marcados tira os já marcados; "
        "apagar_lista remove a lista inteira"
    )
    items: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Itens pelo texto ou pelo número mostrado em show_list",
    )


def list_key(name: str) -> str:
    return _PREFIX.sub("", fold(name)).strip()


async def _user_items(ctx: ToolContext) -> list[ListItem]:
    rows = await ctx.session.scalars(
        select(ListItem).where(ListItem.user_id == ctx.user.id).order_by(ListItem.created_at)
    )
    return list(rows)


def _group(items: list[ListItem]) -> OrderedDict[str, list[ListItem]]:
    groups: OrderedDict[str, list[ListItem]] = OrderedDict()
    for item in items:
        groups.setdefault(list_key(item.list_name), []).append(item)
    return groups


async def _list(ctx: ToolContext, name: str) -> list[ListItem]:
    key = list_key(name)
    if not key:
        raise ToolError("nome de lista inválido")
    return _group(await _user_items(ctx)).get(key, [])


def _view(name: str, items: list[ListItem]) -> dict[str, Any]:
    display = items[0].list_name if items else name
    return {
        "list": display,
        "pending": sum(not i.done for i in items),
        "items": [
            {"number": n, "text": i.text, "done": i.done} for n, i in enumerate(items, start=1)
        ],
    }


def _match(items: list[ListItem], wanted: str) -> ListItem:
    token = wanted.strip()
    if token.isdigit():
        index = int(token) - 1
        if 0 <= index < len(items):
            return items[index]
        raise ToolError(f"não existe item número {token}")
    needle = fold(token)
    exact = [i for i in items if fold(i.text) == needle]
    if exact:
        return exact[0]
    partial = [i for i in items if needle in fold(i.text)]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ToolError(f"item não encontrado: {wanted}")
    raise ToolError(f"mais de um item parecido com {wanted!r}; use o número")


async def add_items(ctx: ToolContext, args: AddItemsArgs) -> dict[str, Any]:
    texts = [t.strip() for t in args.items if t.strip()]
    if not texts:
        raise ToolError("nenhum item para adicionar")
    if any(len(t) > 200 for t in texts):
        raise ToolError("item longo demais; o limite é de 200 caracteres")
    groups = _group(await _user_items(ctx))
    key = list_key(args.list_name)
    if not key:
        raise ToolError("nome de lista inválido")
    current = groups.get(key, [])
    if not current and len(groups) >= MAX_LISTS:
        raise ToolError(f"limite de {MAX_LISTS} listas atingido; apague alguma antes")
    if len(current) + len(texts) > MAX_ITEMS_PER_LIST:
        raise ToolError(f"a lista passaria de {MAX_ITEMS_PER_LIST} itens")
    name = current[0].list_name if current else args.list_name.strip()
    existing = {fold(i.text): i for i in current}
    skipped: list[str] = []
    for text in texts:
        if fold(text) in existing:
            item = existing[fold(text)]
            if item.done:
                item.done = False
            else:
                skipped.append(text)
            continue
        # garante ordem de inserção mesmo com relógios de baixa resolução
        created_at = utcnow()
        if current:
            created_at = max(created_at, as_utc(current[-1].created_at) + timedelta(microseconds=1))
        new = ListItem(user_id=ctx.user.id, list_name=name, text=text, created_at=created_at)
        ctx.session.add(new)
        await ctx.session.flush()
        current.append(new)
        existing[fold(text)] = new
    await ctx.session.flush()
    result = _view(name, current)
    if skipped:
        result["already_there"] = skipped
    return result


async def show_list(ctx: ToolContext, args: ShowListArgs) -> dict[str, Any]:
    if args.list_name:
        items = await _list(ctx, args.list_name)
        if not items:
            raise ToolError(f"a lista {args.list_name!r} está vazia ou não existe")
        return _view(args.list_name, items)
    groups = _group(await _user_items(ctx))
    return {
        "lists": [
            {
                "list": items[0].list_name,
                "items": len(items),
                "pending": sum(not i.done for i in items),
            }
            for items in groups.values()
        ]
    }


async def update_items(ctx: ToolContext, args: UpdateItemsArgs) -> dict[str, Any]:
    items = await _list(ctx, args.list_name)
    if not items:
        raise ToolError(f"a lista {args.list_name!r} está vazia ou não existe")
    name = items[0].list_name
    if args.action == "apagar_lista":
        for item in items:
            await ctx.session.delete(item)
        await ctx.session.flush()
        return {"deleted_list": name, "removed": len(items)}
    if args.action == "limpar_marcados":
        done = [i for i in items if i.done]
        for item in done:
            await ctx.session.delete(item)
        await ctx.session.flush()
        remaining = [i for i in items if not i.done]
        return {"removed": len(done), **_view(name, remaining)}
    if not args.items:
        raise ToolError("informe quais itens")
    targets = {id(t): t for t in (_match(items, w) for w in args.items)}.values()
    if args.action == "remover":
        for item in targets:
            await ctx.session.delete(item)
        items = [i for i in items if all(i is not t for t in targets)]
    else:
        for item in targets:
            item.done = args.action == "marcar"
    await ctx.session.flush()
    return _view(name, items)


LIST_TOOLS = [
    Tool(
        "list_add",
        "Adiciona itens a uma lista do usuário (compras, tarefas, filmes...). "
        "Cria a lista se ela não existir.",
        AddItemsArgs,
        add_items,
    ),
    Tool(
        "list_show",
        "Mostra os itens de uma lista, ou os nomes de todas as listas.",
        ShowListArgs,
        show_list,
    ),
    Tool(
        "list_update",
        "Marca, desmarca ou remove itens de uma lista, limpa os marcados ou apaga a lista.",
        UpdateItemsArgs,
        update_items,
    ),
]
