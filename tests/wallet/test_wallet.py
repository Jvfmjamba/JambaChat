from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.container import Container
from jambachat.conversation.commands import WALLET_OFF_TEXT, handle_command
from jambachat.conversation.orchestrator import Orchestrator
from jambachat.conversation.service import ConversationService
from jambachat.expenses.categorize import guess_category
from jambachat.expenses.models import Category, EntrySource, Expense
from jambachat.main import create_app
from jambachat.security.crypto import get_cipher
from jambachat.security.ratelimit import RateLimiter
from jambachat.tools.registry import ToolRegistry
from jambachat.users.models import User
from jambachat.wallet.service import WalletService
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import GRAPH_MESSAGES_URL, PHONE, ScriptedLLM, graph_ok, make_settings, sent_texts

Maker = async_sessionmaker[AsyncSession]


@pytest.mark.parametrize(
    ("merchant", "category"),
    [
        ("UBER *TRIP", Category.TRANSPORTE),
        ("IFOOD *IFOOD", Category.ALIMENTACAO),
        ("Mercado Livre", Category.COMPRAS),
        ("Supermercado Dia", Category.MERCADO),
        ("NETFLIX.COM", Category.ASSINATURAS),
        ("Drogaria São Paulo", Category.SAUDE),
        ("Posto Ipiranga", Category.TRANSPORTE),
        ("Loja Qualquer", Category.OUTROS),
        ("Bartolomeu Tim Ltda", Category.CONTAS),
        ("Timbó Comércio", Category.OUTROS),
    ],
)
def test_guess_category(merchant: str, category: Category) -> None:
    assert guess_category(merchant) is category


async def seed_user(sessionmaker: Maker, *, last_inbound: datetime | None) -> str:
    async with sessionmaker() as session:
        user = User(
            phone=PHONE, phone_hash=get_cipher().blind_index(PHONE), last_inbound_at=last_inbound
        )
        session.add(user)
        await session.flush()
        reply = await handle_command(session, user, "/atalho", make_settings())
        await session.commit()
    assert reply is not None
    return reply.splitlines()[2]


async def post(
    sessionmaker: Maker,
    requests: list[dict[str, Any]],
    *,
    allowed: str = PHONE,
    limit: int = 10,
) -> tuple[list[httpx.Response], list[str]]:
    settings = make_settings(allowed_phones=allowed)
    responses: list[httpx.Response] = []
    with respx.mock:
        respx.route(host="test").pass_through()
        route = respx.post(GRAPH_MESSAGES_URL).mock(side_effect=graph_ok)
        async with httpx.AsyncClient() as http:
            whatsapp = WhatsAppClient(
                http, access_token="t", phone_number_id="123", api_version="v23.0"
            )
            container = Container(
                settings,
                sessionmaker,
                ConversationService(
                    sessionmaker, whatsapp, Orchestrator(ScriptedLLM(), ToolRegistry()), settings
                ),
                wallet=WalletService(sessionmaker, whatsapp, settings, RateLimiter([(limit, 60)])),
            )
            app = create_app(container)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                for request in requests:
                    responses.append(await c.post("/integrations/wallet", **request))
        return responses, sent_texts(route)


def purchase(token: str, **body: Any) -> dict[str, Any]:
    payload = {"amount": "R$ 42,90", "merchant": "IFOOD *IFOOD", "card": "Nubank"} | body
    return {"json": payload, "headers": {"authorization": f"Bearer {token}"}}


async def test_purchase_is_recorded_and_user_notified(sessionmaker: Maker) -> None:
    token = await seed_user(sessionmaker, last_inbound=datetime.now(UTC) - timedelta(hours=1))
    [response], sent = await post(sessionmaker, [purchase(token)])

    assert response.status_code == 200
    assert response.json()["message"] == "Registrei R$ 42,90 em IFOOD *IFOOD (Alimentação)."
    assert sent[0].startswith("Registrei R$ 42,90")
    async with sessionmaker() as session:
        entry = (await session.scalars(select(Expense))).one()
    assert entry.amount_cents == 4290
    assert entry.source == EntrySource.WALLET
    assert entry.description == "IFOOD *IFOOD (Nubank)"


async def test_no_notification_outside_service_window(sessionmaker: Maker) -> None:
    token = await seed_user(sessionmaker, last_inbound=None)
    [response], sent = await post(sessionmaker, [purchase(token)])

    assert response.status_code == 200
    assert sent == []


async def test_authentication_and_validation(sessionmaker: Maker) -> None:
    token = await seed_user(sessionmaker, last_inbound=None)
    responses, _ = await post(
        sessionmaker,
        [
            purchase("errado"),
            {"json": {"amount": "1", "merchant": "x"}, "headers": {"authorization": token}},
            {"json": {"amount": "1", "merchant": "x"}},
            purchase(token, amount="abc"),
            purchase(token, amount="-5"),
            purchase(token, merchant="   "),
            purchase(token, merchant=123),
            {"content": b"[1]", "headers": {"authorization": f"Bearer {token}"}},
            {"content": b"x" * 20_000, "headers": {"authorization": f"Bearer {token}"}},
        ],
    )

    assert [r.status_code for r in responses] == [401, 401, 401, 422, 422, 422, 422, 400, 413]
    async with sessionmaker() as session:
        assert (await session.scalars(select(Expense))).first() is None


async def test_removed_number_and_revoked_token_are_rejected(sessionmaker: Maker) -> None:
    token = await seed_user(sessionmaker, last_inbound=None)
    [blocked], _ = await post(sessionmaker, [purchase(token)], allowed="5599999999999")
    assert blocked.status_code == 401

    async with sessionmaker() as session:
        user = (await session.scalars(select(User))).one()
        assert await handle_command(session, user, "/atalho desligar") == WALLET_OFF_TEXT
        await session.commit()
    [revoked], _ = await post(sessionmaker, [purchase(token)])
    assert revoked.status_code == 401


async def test_new_token_invalidates_old_one_and_rate_limit(sessionmaker: Maker) -> None:
    old = await seed_user(sessionmaker, last_inbound=None)
    async with sessionmaker() as session:
        user = (await session.scalars(select(User))).one()
        reply = await handle_command(session, user, "/atalho", make_settings())
        await session.commit()
    assert reply is not None
    new = reply.splitlines()[2]

    responses, _ = await post(
        sessionmaker, [purchase(old), purchase(new), purchase(new), purchase(new)], limit=2
    )
    assert [r.status_code for r in responses] == [401, 200, 200, 429]


async def test_token_reply_shows_url_and_is_never_stored_in_clear(sessionmaker: Maker) -> None:
    async with sessionmaker() as session:
        user = User(phone=PHONE, phone_hash="h")
        session.add(user)
        await session.flush()
        reply = await handle_command(
            session, user, "/atalho", make_settings(public_base_url="https://bot.exemplo.com/")
        )
        await session.commit()
        stored = user.wallet_token_hash

    assert reply is not None
    token = reply.splitlines()[2]
    assert "https://bot.exemplo.com/integrations/wallet" in reply
    assert len(token) >= 40
    assert stored is not None and token not in stored
