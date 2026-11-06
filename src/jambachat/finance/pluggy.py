from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from jambachat.finance.errors import FinanceAuthError, FinanceError
from jambachat.finance.provider import ItemInfo, RawTransaction

_AUTH_PATH = "/auth"


class PluggyProvider:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        client_id: str | SecretStr,
        client_secret: str | SecretStr,
        base_url: str = "https://api.pluggy.ai",
    ) -> None:
        self._http = http
        self._client_id = _secret_value(client_id)
        self._client_secret = _secret_value(client_secret)
        self._base_url = base_url.rstrip("/")
        self._api_key: str | None = None

    async def list_items(self, item_ref: str) -> ItemInfo:
        data = await self._request("GET", f"/items/{item_ref}")
        if not isinstance(data, dict):
            raise FinanceError("resposta inválida do agregador")
        status = data.get("status")
        if status not in {"UPDATED", "LOGIN_ERROR", "WAITING_USER_INPUT", "OUTDATED"}:
            raise FinanceError("resposta inválida do agregador")
        connector = data.get("connector")
        institution = connector.get("name") if isinstance(connector, dict) else None
        if not isinstance(institution, str) or not institution:
            raise FinanceError("resposta inválida do agregador")
        return ItemInfo(institution=institution, status=status)

    async def fetch_transactions(
        self, item_ref: str, since: date, until: date
    ) -> list[RawTransaction]:
        data = await self._request(
            "GET",
            "/transactions",
            params={"itemId": item_ref, "from": since.isoformat(), "to": until.isoformat()},
        )
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise FinanceError("resposta inválida do agregador")
        try:
            return [_transaction(row) for row in rows]
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            raise FinanceError("resposta inválida do agregador") from exc

    async def delete_item(self, item_ref: str) -> None:
        await self._request("DELETE", f"/items/{item_ref}")

    async def _request(
        self, method: str, path: str, *, params: dict[str, str] | None = None
    ) -> Any:
        for attempt in range(2):
            api_key = await self._get_api_key()
            try:
                return await self._send(
                    method, path, params=params, headers={"X-API-KEY": api_key}
                )
            except FinanceAuthError:
                if attempt == 1:
                    raise
                self._api_key = None
        raise FinanceAuthError("a autorização do agregador precisa ser renovada")

    async def _get_api_key(self) -> str:
        if self._api_key is not None:
            return self._api_key
        data = await self._send(
            "POST",
            _AUTH_PATH,
            json={"clientId": self._client_id, "clientSecret": self._client_secret},
        )
        api_key = data.get("apiKey") if isinstance(data, dict) else None
        if not isinstance(api_key, str) or not api_key:
            raise FinanceError("resposta inválida do agregador")
        self._api_key = api_key
        return api_key

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        for attempt in range(2):
            try:
                response = await self._http.request(
                    method, url, params=params, json=json, headers=headers, timeout=10.0
                )
            except httpx.HTTPError as exc:
                if attempt == 1:
                    raise FinanceError("o agregador não respondeu") from exc
                continue
            if response.status_code in {401, 403}:
                raise FinanceAuthError("a autorização do agregador precisa ser renovada")
            if response.status_code >= 500:
                if attempt == 1:
                    raise FinanceError("o agregador está indisponível")
                continue
            if response.status_code >= 400:
                raise FinanceError("o agregador recusou a solicitação")
            try:
                return response.json() if response.content else None
            except ValueError as exc:
                raise FinanceError("resposta inválida do agregador") from exc
        raise FinanceError("o agregador está indisponível")


def _secret_value(value: str | SecretStr) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _transaction(raw: object) -> RawTransaction:
    if not isinstance(raw, dict):
        raise TypeError
    amount = _amount_cents(raw["amount"])
    transaction_type = str(raw.get("type", "")).upper()
    kind: Literal["expense", "income"] = "expense" if amount < 0 else "income"
    if transaction_type in {"DEBIT", "EXPENSE"}:
        kind = "expense"
    elif transaction_type in {"CREDIT", "INCOME"}:
        kind = "income"
    status: Literal["POSTED", "PENDING"] = (
        "PENDING" if str(raw.get("status", "POSTED")).upper() == "PENDING" else "POSTED"
    )
    category = raw.get("category")
    category_name = category.get("name") if isinstance(category, dict) else category
    return RawTransaction(
        id=str(raw["id"]),
        date=date.fromisoformat(str(raw["date"])[:10]),
        amount_cents=abs(amount),
        description=str(raw["description"]),
        category=str(category_name) if category_name else None,
        kind=kind,
        status=status,
        is_transfer=transaction_type == "TRANSFER" or str(category_name).upper() == "TRANSFER",
    )


def _amount_cents(value: object) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
