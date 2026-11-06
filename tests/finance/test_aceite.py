import csv
import io
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx
import respx
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.config import Settings
from jambachat.container import build_container
from jambachat.conversation.commands import handle_command
from jambachat.expenses.budgets import BUDGET_TOOLS
from jambachat.expenses.models import Budget, EntryKind, EntrySource, Expense
from jambachat.expenses.service import add_entry
from jambachat.expenses.tools import EXPENSE_TOOLS
from jambachat.finance.errors import FinanceAuthError, FinanceError
from jambachat.finance.importer import FinanceImporter
from jambachat.finance.job import FinanceImportJob
from jambachat.finance.models import ExpenseLink, FinanceConnection, FinanceSuppression
from jambachat.finance.pluggy import PluggyProvider, _transaction
from jambachat.finance.provider import ItemInfo, NullFinanceProvider, RawTransaction
from jambachat.finance.service import FinanceConnectionError, FinanceService
from jambachat.finance.tools import build_finance_tools
from jambachat.llm.types import ToolCall
from jambachat.tools.registry import Attachment, ToolContext, ToolRegistry
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient
from tests.fakes import FakeFinanceProvider, make_settings

Maker = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
DAY = date(2026, 11, 5)
ITEMS = {
    "item-1": ItemInfo("Banco Azul", "UPDATED"),
    "item-2": ItemInfo("Banco Verde", "UPDATED"),
}


def tx(
    tx_id: str,
    cents: int = -1200,
    description: str = "Compra",
    *,
    day: date = DAY,
    category: str | None = None,
    kind: str = "expense",
    status: str = "POSTED",
    transfer: bool = False,
) -> RawTransaction:
    return RawTransaction(
        tx_id,
        day,
        cents,
        description,
        category,
        cast(Any, kind),
        cast(Any, status),
        transfer,
    )


class RecordingProvider(FakeFinanceProvider):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fetches: list[tuple[str, date, date]] = []

    async def fetch_transactions(
        self, item_ref: str, since: date, until: date
    ) -> list[RawTransaction]:
        self.fetches.append((item_ref, since, until))
        return await super().fetch_transactions(item_ref, since, until)


class FlakyProvider(RecordingProvider):
    def __init__(self, failing: dict[str, Exception], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.failing = failing

    async def fetch_transactions(
        self, item_ref: str, since: date, until: date
    ) -> list[RawTransaction]:
        if item_ref in self.failing:
            self.fetches.append((item_ref, since, until))
            raise self.failing[item_ref]
        return await super().fetch_transactions(item_ref, since, until)


class Outbox:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, phone: str, body: str) -> None:
        self.messages.append(body)


async def make_user(session: AsyncSession, phone: str = "1") -> User:
    user = User(phone=phone, phone_hash=phone, timezone="America/Sao_Paulo")
    session.add(user)
    await session.flush()
    return user


async def make_connection(
    session: AsyncSession,
    user: User,
    item_ref: str = "item-1",
    institution: str = "Banco Azul",
    **fields: Any,
) -> FinanceConnection:
    connection = FinanceConnection(
        user_id=user.id, item_ref=item_ref, institution=institution, **fields
    )
    session.add(connection)
    await session.flush()
    return connection


async def make_manual(
    session: AsyncSession,
    user: User,
    cents: int,
    when: datetime,
    *,
    description: str = "Almoço",
    category: str = "Alimentação",
    source: EntrySource = EntrySource.MANUAL,
    kind: EntryKind = EntryKind.EXPENSE,
) -> Expense:
    return await add_entry(
        session,
        user,
        kind=kind,
        amount_cents=cents,
        description=description,
        category=category,
        occurred_at=when,
        source=source,
    )


def registry_for(provider: FakeFinanceProvider) -> ToolRegistry:
    return ToolRegistry(
        [
            *build_finance_tools(provider, FinanceService(provider), FinanceImporter(provider)),
            *EXPENSE_TOOLS,
            *BUDGET_TOOLS,
        ]
    )


async def call(
    registry: ToolRegistry,
    session: AsyncSession,
    user: User,
    name: str,
    attachments: list[Attachment] | None = None,
    now: datetime = NOW,
    **arguments: Any,
) -> dict[str, Any]:
    ctx = ToolContext(
        session,
        user,
        make_settings(),
        now,
        attachments if attachments is not None else [],
    )
    result: dict[str, Any] = json.loads(
        await registry.execute(ToolCall("call", name, arguments), ctx)
    )
    return result


def job_for(maker: Maker, provider: FakeFinanceProvider, outbox: Outbox) -> FinanceImportJob:
    return FinanceImportJob(maker, FinanceImporter(provider), cast(WhatsAppClient, outbox))


async def all_expenses(maker: Maker) -> list[Expense]:
    async with maker() as session:
        return list(await session.scalars(select(Expense).order_by(Expense.external_id)))


async def test_criterio_1_1_conectar_pelo_comando_e_pela_ferramenta(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    async with sessionmaker() as session:
        user = await make_user(session)
        reply = await handle_command(session, user, "/banco item-1", finance_provider=provider)
        assert reply == "Banco Banco Azul conectado."
        registry = registry_for(provider)
        result = await call(registry, session, user, "connect_bank", item_ref="item-2")
        assert result == {"connected": "Banco Verde", "status": "ativa"}
        connections = list(await session.scalars(select(FinanceConnection)))
        assert {c.user_id for c in connections} == {user.id}
        assert {c.item_ref for c in connections} == {"item-1", "item-2"}


async def test_criterio_1_2_integracao_desligada_responde_indisponivel(sessionmaker: Maker) -> None:
    async with sessionmaker() as session:
        user = await make_user(session)
        reply = await handle_command(
            session, user, "/banco item-1", finance_provider=NullFinanceProvider()
        )
        assert reply is not None
        assert "não está disponível" in reply
        assert "PLUGGY" not in reply.upper()
        assert "Traceback" not in reply
        assert (await session.scalars(select(FinanceConnection))).all() == []


async def test_criterio_1_3_valida_item_ativo_antes_de_salvar(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(
        items={"ativo": ItemInfo("Banco Azul", "UPDATED"), "vencido": ItemInfo("X", "LOGIN_ERROR")}
    )
    service = FinanceService(provider)
    async with sessionmaker() as session:
        user = await make_user(session)
        for ref in ("vencido", "inexistente"):
            try:
                await service.connect(session, user, ref)
            except FinanceError:
                pass
            else:
                raise AssertionError(ref)
        assert (await session.scalars(select(FinanceConnection))).all() == []
        connection = await service.connect(session, user, "ativo")
        assert connection.institution == "Banco Azul"
        reply = await handle_command(session, user, "/banco ativo", finance_provider=provider)
        assert reply is not None


async def test_criterio_1_4_item_cifrado_e_fora_de_logs_respostas_e_exportacao(
    sessionmaker: Maker,
) -> None:
    provider = FlakyProvider({"segredo-do-item": RuntimeError("falhou segredo-do-item")})
    provider.items["segredo-do-item"] = ItemInfo("Banco Sigiloso", "UPDATED")
    async with sessionmaker() as session:
        user = await make_user(session)
        await make_connection(session, user, "segredo-do-item", "Banco Sigiloso")
        await session.commit()
    async with sessionmaker() as session:
        raw = (
            await session.execute(text("SELECT item_ref, institution FROM finance_connections"))
        ).one()
        assert "segredo-do-item" not in raw.item_ref
        assert "Banco Sigiloso" not in raw.institution
        user = (await session.scalars(select(User))).one()
        registry = registry_for(provider)
        listed = await call(registry, session, user, "list_banks")
        assert "segredo-do-item" not in json.dumps(listed)
        listing = await handle_command(session, user, "/banco", finance_provider=provider)
        assert listing is not None
        assert "segredo-do-item" not in listing
        from jambachat.users.export import export_user_data

        assert b"segredo-do-item" not in await export_user_data(session, user)
    with structlog.testing.capture_logs() as logs:
        await job_for(sessionmaker, provider, Outbox()).run_once(NOW)
    assert logs
    assert "segredo-do-item" not in json.dumps(logs, default=str)


async def test_criterio_1_5_item_de_outro_usuario_e_recusado_sem_revelar(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    service = FinanceService(provider)
    async with sessionmaker() as session:
        owner = await make_user(session, "1")
        other = await make_user(session, "2")
        await service.connect(session, owner, "item-1")
        try:
            await service.connect(session, other, "item-1")
        except FinanceConnectionError as exc:
            message = str(exc).lower()
            assert "outro" not in message
            assert str(owner.id) not in message
        else:
            raise AssertionError
        assert await service.list_connections(session, other) == []
        reply = await handle_command(session, other, "/banco item-1", finance_provider=provider)
        assert reply is not None
        assert "outro" not in reply.lower()
        assert len(await service.list_connections(session, owner)) == 1


async def test_criterio_2_1_busca_so_conexoes_com_mais_de_24h_e_desde_a_ultima_data(
    sessionmaker: Maker,
) -> None:
    provider = RecordingProvider(items=ITEMS)
    async with sessionmaker() as session:
        user = await make_user(session)
        stale = NOW - timedelta(hours=25)
        await make_connection(session, user, "item-1", last_synced_at=stale.replace(tzinfo=None))
        await make_connection(
            session,
            user,
            "item-2",
            "Banco Verde",
            last_synced_at=(NOW - timedelta(hours=23)).replace(tzinfo=None),
        )
        await session.commit()
    await job_for(sessionmaker, provider, Outbox()).run_once(NOW)
    assert [(ref, since) for ref, since, _ in provider.fetches] == [
        ("item-1", stale.date() - timedelta(days=3))
    ]
    assert provider.fetches[0][2] == NOW.date()


async def test_criterio_2_2_primeira_importacao_busca_no_maximo_30_dias(
    sessionmaker: Maker,
) -> None:
    provider = RecordingProvider(items=ITEMS)
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        await FinanceImporter(provider).run(session, user, connection, NOW)
    assert provider.fetches == [("item-1", NOW.date() - timedelta(days=30), NOW.date())]


async def test_criterio_2_3_campos_do_registro_importado(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(
        transactions={
            "item-1": [
                tx("debito", -1234, "Padaria Central", day=DAY),
                tx("credito", 500000, "Salario empresa", kind="income", day=DAY),
            ]
        }
    )
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        assert await FinanceImporter(provider).run(session, user, connection, NOW) == 2
        await session.commit()
    async with sessionmaker() as session:
        raw = (await session.execute(text("SELECT description FROM expenses"))).scalars().all()
        assert all("Padaria" not in value and "Salario" not in value for value in raw)
    entries = {e.external_id: e for e in await all_expenses(sessionmaker)}
    debit, credit = entries["debito"], entries["credito"]
    assert (debit.source, debit.kind, debit.amount_cents) == ("open_finance", "expense", 1234)
    assert (credit.source, credit.kind, credit.amount_cents) == ("open_finance", "income", 500000)
    assert debit.description == "Padaria Central"
    local = debit.occurred_at.replace(tzinfo=UTC).astimezone(ZoneInfo("America/Sao_Paulo"))
    assert local.date() == DAY


async def test_criterio_2_4_categoria_mapeada_deduzida_ou_outros(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(
        transactions={
            "item-1": [
                tx("mapeada", description="xyz", category="Supermercado"),
                tx("deduzida", description="Uber viagem"),
                tx("outros", description="zzzz qqqq", category="Categoria Estranha"),
            ]
        }
    )
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        await FinanceImporter(provider).run(session, user, connection, NOW)
        await session.commit()
    entries = {e.external_id: e.category for e in await all_expenses(sessionmaker)}
    assert entries["mapeada"] == "Mercado"
    assert entries["deduzida"] == "Transporte"
    assert entries["outros"] == "Outros"


async def test_criterio_2_5_falha_so_com_metadados_segue_e_tenta_de_novo(
    sessionmaker: Maker,
) -> None:
    provider = FlakyProvider(
        {"item-1": RuntimeError("rede caiu com dados-sensiveis")},
        items=ITEMS,
        transactions={"item-2": [tx("ok")]},
    )
    async with sessionmaker() as session:
        first = await make_user(session, "1")
        second = await make_user(session, "2")
        await make_connection(session, first, "item-1")
        await make_connection(session, second, "item-2", "Banco Verde")
        await session.commit()
    job = job_for(sessionmaker, provider, Outbox())
    with structlog.testing.capture_logs() as logs:
        assert await job.run_once(NOW) == 1
        assert await job.run_once(NOW + timedelta(minutes=1)) == 0
    assert "dados-sensiveis" not in json.dumps(logs, default=str)
    assert [ref for ref, _, _ in provider.fetches].count("item-1") == 2
    async with sessionmaker() as session:
        failed = (
            await session.scalars(
                select(FinanceConnection).where(FinanceConnection.last_error.is_not(None))
            )
        ).one()
        assert failed.last_error == "RuntimeError"
        assert failed.last_synced_at is None


async def test_criterio_2_6_avisa_reconexao_uma_unica_vez(sessionmaker: Maker) -> None:
    provider = FlakyProvider({"item-1": FinanceAuthError()}, items=ITEMS)
    outbox = Outbox()
    async with sessionmaker() as session:
        user = await make_user(session)
        await make_connection(session, user, "item-1")
        await session.commit()
    job = job_for(sessionmaker, provider, outbox)
    await job.run_once(NOW)
    await job.run_once(NOW + timedelta(days=2))
    assert len(outbox.messages) == 1
    assert "Reconecte" in outbox.messages[0]
    async with sessionmaker() as session:
        connection = (await session.scalars(select(FinanceConnection))).one()
        assert connection.status == "reconnect"


async def test_criterio_2_7_importacao_idempotente(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(transactions={"item-1": [tx("a"), tx("b", -500)]})
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        importer = FinanceImporter(provider)
        assert await importer.run(session, user, connection, NOW) == 2
        assert await importer.run(session, user, connection, NOW) == 0
        assert await importer.run(session, user, connection, NOW + timedelta(days=1)) == 0
        assert len((await session.scalars(select(Expense))).all()) == 2


async def test_criterio_2_8_pendentes_nao_sao_importadas_ate_confirmar(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(transactions={"item-1": [tx("p", status="PENDING")]})
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        importer = FinanceImporter(provider)
        assert await importer.run(session, user, connection, NOW) == 0
        provider.transactions["item-1"] = [tx("p", status="POSTED")]
        assert await importer.run(session, user, connection, NOW) == 1


async def test_criterio_2_9_transferencias_e_fatura_nao_contam(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(
        transactions={
            "item-1": [
                tx("t", transfer=True),
                tx("f1", description="Pagamento de fatura"),
                tx("f2", description="Boleto", category="Pagamento de Fatura"),
                tx("ok"),
            ]
        }
    )
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        assert await FinanceImporter(provider).run(session, user, connection, NOW) == 1
        assert [e.external_id for e in await session.scalars(select(Expense))] == ["ok"]


async def test_criterio_3_1_associa_manual_ou_carteira_e_nao_conta_a_importada(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(
        items=ITEMS,
        transactions={
            "item-1": [
                tx("m", -4500, day=DAY),
                tx("w", -2000, day=DAY),
                tx("longe", -4500, day=DAY - timedelta(days=10)),
            ]
        },
    )
    registry = registry_for(provider)
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        manual = await make_manual(session, user, 4500, datetime(2026, 11, 3, 15, tzinfo=UTC))
        wallet = await make_manual(
            session,
            user,
            2000,
            datetime(2026, 11, 6, 9, tzinfo=UTC),
            source=EntrySource.WALLET,
        )
        await FinanceImporter(provider).run(session, user, connection, NOW)
        links = {link.matched_expense_id for link in await session.scalars(select(ExpenseLink))}
        assert links == {manual.id, wallet.id}
        listed = await call(registry, session, user, "list_entries", start_date="2026-10-01")
        assert listed["count"] == 3
        summary = await call(registry, session, user, "summarize_entries", start_date="2026-10-01")
        assert summary["expenses"] == "R$ 110,00"
        assert summary["entries"] == 3


async def test_criterio_3_2_varios_candidatos_vence_a_data_mais_proxima(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(transactions={"item-1": [tx("x", -3000, day=DAY)]})
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        await make_manual(session, user, 3000, datetime(2026, 11, 2, 15, tzinfo=UTC))
        closest = await make_manual(session, user, 3000, datetime(2026, 11, 4, 15, tzinfo=UTC))
        await make_manual(session, user, 3000, datetime(2026, 11, 3, 15, tzinfo=UTC))
        await FinanceImporter(provider).run(session, user, connection, NOW)
        link = (await session.scalars(select(ExpenseLink))).one()
        assert link.matched_expense_id == closest.id


async def test_criterio_3_3_registro_manual_prevalece(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(
        items=ITEMS,
        transactions={"item-1": [tx("x", -3000, "COMPRA CARTAO 123", category="Shopping")]},
    )
    registry = registry_for(provider)
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        manual = await make_manual(
            session,
            user,
            3000,
            datetime(2026, 11, 5, 15, tzinfo=UTC),
            description="Presente da Ana",
            category="Lazer",
        )
        await FinanceImporter(provider).run(session, user, connection, NOW)
        listed = await call(registry, session, user, "list_entries")
        assert [e["description"] for e in listed["entries"]] == ["Presente da Ana"]
        assert [e["category"] for e in listed["entries"]] == ["Lazer"]
        assert manual.description == "Presente da Ana"
        assert manual.category == "Lazer"


async def test_criterio_4_1_lista_instituicao_ultima_importacao_e_situacao(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    async with sessionmaker() as session:
        user = await make_user(session)
        await make_connection(
            session, user, "item-1", "Banco Azul", last_synced_at=datetime(2026, 11, 4, 12)
        )
        await make_connection(session, user, "item-2", "Banco Verde", status="reconnect")
        await make_connection(
            session, user, "item-3", "Banco Rosa", status="error", last_error="RuntimeError"
        )
        banks = (await call(registry_for(provider), session, user, "list_banks"))["banks"]
        by_name = {b["institution"]: b for b in banks}
        assert by_name["Banco Azul"]["status"] == "ativa"
        assert by_name["Banco Azul"]["last_import"] is not None
        assert by_name["Banco Verde"]["status"] == "precisa reconectar"
        assert by_name["Banco Rosa"]["status"] == "com erro"
        listing = await handle_command(session, user, "/banco", finance_provider=provider)
        assert listing is not None
        assert "Banco Azul — ativa" in listing
        assert "Banco Verde — precisa reconectar" in listing


async def test_criterio_4_2_importa_agora_com_limite_de_15_minutos(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS, transactions={"item-1": [tx("a")]})
    registry = registry_for(provider)
    async with sessionmaker() as session:
        user = await make_user(session)
        await make_connection(session, user)
        assert await call(registry, session, user, "import_now") == {"imported": 1}
        limited = await call(registry, session, user, "import_now")
        assert "aguarde" in limited["error"]
        later = await call(registry, session, user, "import_now", now=NOW + timedelta(minutes=16))
        assert later == {"imported": 0}


async def test_criterio_4_3_desconectar_pede_confirmacao_e_mantem_gastos(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(items=ITEMS, transactions={"item-1": [tx("a")]})
    registry = registry_for(provider)
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        await call(registry, session, user, "import_now")
        pending = await call(
            registry, session, user, "disconnect_bank", connection_id=str(connection.id)
        )
        assert "confirmation_required" in pending
        assert provider.deleted_items == []
        done = await call(
            registry,
            session,
            user,
            "disconnect_bank",
            connection_id=str(connection.id),
            confirm=True,
        )
        assert done == {"disconnected": "Banco Azul"}
        assert provider.deleted_items == ["item-1"]
        assert (await session.scalars(select(FinanceConnection))).all() == []
        assert len((await session.scalars(select(Expense))).all()) == 1


async def test_criterio_4_4_consultas_incluem_importados_e_exportacao_indica_origem(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(
        items=ITEMS,
        transactions={"item-1": [tx("a", -1000, "Padaria", category="Mercado")]},
    )
    registry = registry_for(provider)
    attachments: list[Attachment] = []
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        await make_manual(session, user, 700, datetime(2026, 11, 1, 15, tzinfo=UTC))
        await FinanceImporter(provider).run(session, user, connection, NOW)
        listed = await call(registry, session, user, "list_entries")
        summary = await call(registry, session, user, "summarize_entries")
        await call(registry, session, user, "set_budget", category="Mercado", amount="100")
        budgets = await call(registry, session, user, "list_budgets")
        exported = await call(registry, session, user, "export_entries", attachments)
    assert listed["count"] == 2
    assert summary["expenses"] == "R$ 17,00"
    assert budgets["budgets"][0]["spent_this_month"] == "R$ 10,00"
    assert exported["exported"] == 2
    rows = list(csv.reader(io.StringIO(attachments[0].content.decode("utf-8-sig")), delimiter=";"))
    assert rows[0] == ["Data", "Tipo", "Categoria", "Descrição", "Valor", "Origem"]
    assert sorted(row[-1] for row in rows[1:]) == ["manual", "open_finance"]


async def test_criterio_4_5_corrigir_e_apagar_importado_nao_volta(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS, transactions={"item-1": [tx("a", -1000)]})
    registry = registry_for(provider)
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        importer = FinanceImporter(provider)
        await importer.run(session, user, connection, NOW)
        entry = (await session.scalars(select(Expense))).one()
        updated = await call(
            registry,
            session,
            user,
            "update_entry",
            entry_id=str(entry.id),
            amount="15",
            category="Lazer",
        )
        assert updated["updated"]["amount"] == "R$ 15,00"
        assert updated["updated"]["category"] == "Lazer"
        deleted = await call(registry, session, user, "delete_entry", entry_id=str(entry.id))
        assert "deleted" in deleted
        assert await importer.run(session, user, connection, NOW + timedelta(days=1)) == 0
        assert (await session.scalars(select(Expense))).all() == []
        assert (await session.scalars(select(FinanceSuppression))).one().external_id == "a"


async def test_criterio_4_6_alerta_de_orcamento_agregado_uma_vez_por_importacao(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(
        transactions={
            "item-1": [
                tx("a", -4500, category="Mercado"),
                tx("b", -4000, category="Mercado"),
                tx("c", -6000, category="Lazer"),
            ],
            "item-2": [tx("d", -100, category="Mercado")],
        }
    )
    outbox = Outbox()
    async with sessionmaker() as session:
        user = await make_user(session)
        user.last_inbound_at = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        await make_connection(session, user, "item-1")
        await make_connection(session, user, "item-2", "Banco Verde")
        session.add_all(
            [
                Budget(user_id=user.id, category="Mercado", amount_cents=10000),
                Budget(user_id=user.id, category="Lazer", amount_cents=5000),
            ]
        )
        await session.commit()
    job = job_for(sessionmaker, provider, outbox)
    assert await job.run_once(NOW) == 4
    assert len(outbox.messages) == 1
    assert "Mercado" in outbox.messages[0]
    assert "Lazer" in outbox.messages[0]
    await job.run_once(NOW + timedelta(days=2))
    assert len(outbox.messages) == 1


async def test_criterio_5_1_apagar_meus_dados_remove_conexoes_estado_e_importados(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(items=ITEMS, transactions={"item-1": [tx("a")]})
    async with sessionmaker() as session:
        user = await make_user(session)
        connection = await make_connection(session, user)
        await make_manual(session, user, 1200, datetime(2026, 11, 5, 15, tzinfo=UTC))
        await FinanceImporter(provider).run(session, user, connection, NOW)
        session.add(
            FinanceSuppression(user_id=user.id, source="open_finance", external_id="apagada")
        )
        await session.flush()
        assert (
            await handle_command(
                session, user, "/apagar-meus-dados confirmar", finance_provider=provider
            )
            is not None
        )
        await session.flush()
        assert provider.deleted_items == ["item-1"]
        for model in (FinanceConnection, FinanceSuppression, ExpenseLink, Expense):
            assert (await session.scalars(select(model))).all() == []


async def test_criterio_5_2_exportacao_inclui_conexoes_sem_identificadores(
    sessionmaker: Maker,
) -> None:
    provider = FakeFinanceProvider(items=ITEMS)
    attachments: list[Attachment] = []
    async with sessionmaker() as session:
        user = await make_user(session)
        await make_connection(
            session, user, "identificador-sigiloso", last_synced_at=datetime(2026, 11, 4, 12)
        )
        await handle_command(
            session,
            user,
            "/exportar-meus-dados",
            attachments=attachments,
            finance_provider=provider,
        )
    payload = json.loads(attachments[0].content)
    assert list(payload["finance_connections"][0]) == [
        "institution",
        "status",
        "created_at",
        "last_synced_at",
    ]
    assert payload["finance_connections"][0]["institution"] == "Banco Azul"
    assert "identificador-sigiloso" not in attachments[0].content.decode()


def test_criterio_5_3_credenciais_so_do_ambiente_e_servico_sobe_sem_elas() -> None:
    example = (Path(__file__).parents[2] / ".env.example").read_text(encoding="utf-8")
    lines = {
        line.split("=", 1)[0]: line.split("=", 1)[1] for line in example.splitlines() if "=" in line
    }
    assert lines["PLUGGY_CLIENT_ID"] == ""
    assert lines["PLUGGY_CLIENT_SECRET"] == ""
    settings: Settings = make_settings()
    assert settings.pluggy_client_id.get_secret_value() == ""


async def test_criterio_5_3_container_sobe_com_integracao_desligada() -> None:
    container = build_container(make_settings(scheduler_enabled=True))
    try:
        assert isinstance(container.finance_provider, NullFinanceProvider)
    finally:
        await container.aclose()


def test_criterio_5_4_dados_brutos_do_agregador_nao_sao_persistidos() -> None:
    raw = {
        "id": "t1",
        "date": "2026-11-05T00:00:00.000Z",
        "amount": -12.5,
        "description": "Padaria",
        "type": "DEBIT",
        "status": "POSTED",
        "accountNumber": "12345-6",
        "document": "123.456.789-00",
        "balance": 9999.99,
        "category": "Food",
    }
    parsed = asdict(_transaction(raw))
    flat = json.dumps(parsed, default=str)
    assert "12345-6" not in flat
    assert "123.456.789-00" not in flat
    assert "9999" not in flat
    assert set(parsed) == {
        "id",
        "date",
        "amount_cents",
        "description",
        "category",
        "kind",
        "status",
        "is_transfer",
    }
    forbidden = ("account", "document", "balance", "cpf", "saldo", "conta")
    for model in (Expense, FinanceConnection, ExpenseLink, FinanceSuppression):
        columns = [column.name for column in model.__table__.columns]
        assert not [c for c in columns if any(word in c for word in forbidden)]


async def test_criterio_5_4_ferramentas_expõem_so_campos_de_gastos(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(
        items=ITEMS, transactions={"item-1": [tx("a", -1000, "Padaria")]}
    )
    registry = registry_for(provider)
    async with sessionmaker() as session:
        user = await make_user(session)
        await call(registry, session, user, "connect_bank", item_ref="item-1")
        await call(registry, session, user, "import_now")
        listed = await call(registry, session, user, "list_entries")
        banks = await call(registry, session, user, "list_banks")
    assert set(listed["entries"][0]) == {"id", "kind", "amount", "description", "category", "date"}
    assert set(banks["banks"][0]) == {"id", "institution", "status", "last_import"}
    assert "external_id" not in json.dumps(listed)


async def test_criterio_5_5_chamadas_ao_agregador_tem_timeout() -> None:
    timeouts: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        timeouts.append(request.extensions["timeout"])
        if request.url.path == "/auth":
            return httpx.Response(200, json={"apiKey": "k"})
        return httpx.Response(200, json={"status": "UPDATED", "connector": {"name": "Banco"}})

    provider = PluggyProvider(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        client_id="c",
        client_secret="s",
        base_url="https://pluggy.test",
    )
    await provider.list_items("item-1")
    assert len(timeouts) == 2
    for timeout in timeouts:
        assert timeout["connect"] and timeout["read"] and timeout["write"] and timeout["pool"]
        assert max(timeout.values()) <= 10.0


async def test_criterio_5_6_testes_usam_provedor_falso_sem_rede(sessionmaker: Maker) -> None:
    provider = FakeFinanceProvider(items=ITEMS, transactions={"item-1": [tx("a")]})
    with respx.mock(assert_all_mocked=True) as router:
        async with sessionmaker() as session:
            user = await make_user(session)
            connection = await make_connection(session, user)
            assert await FinanceImporter(provider).run(session, user, connection, NOW) == 1
        assert router.calls.call_count == 0
