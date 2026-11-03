from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from jambachat.finance.errors import FinanceUnavailableError


@dataclass(frozen=True, slots=True)
class ItemInfo:
    institution: str
    status: Literal["UPDATED", "LOGIN_ERROR", "WAITING_USER_INPUT", "OUTDATED"]


@dataclass(frozen=True, slots=True)
class RawTransaction:
    id: str
    date: date
    amount_cents: int
    description: str
    category: str | None
    kind: Literal["expense", "income"]
    status: Literal["POSTED", "PENDING"]
    is_transfer: bool


class FinanceProvider(Protocol):
    async def list_items(self, item_ref: str) -> ItemInfo: ...

    async def fetch_transactions(
        self, item_ref: str, since: date, until: date
    ) -> list[RawTransaction]: ...

    async def delete_item(self, item_ref: str) -> None: ...


class NullFinanceProvider:
    async def list_items(self, item_ref: str) -> ItemInfo:
        raise FinanceUnavailableError("a importação automática não está disponível nesta instância")

    async def fetch_transactions(
        self, item_ref: str, since: date, until: date
    ) -> list[RawTransaction]:
        raise FinanceUnavailableError("a importação automática não está disponível nesta instância")

    async def delete_item(self, item_ref: str) -> None:
        raise FinanceUnavailableError("a importação automática não está disponível nesta instância")
