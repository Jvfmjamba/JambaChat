from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat import __version__
from jambachat.config import Settings
from jambachat.finance.errors import FinanceError
from jambachat.finance.provider import FinanceProvider, NullFinanceProvider
from jambachat.finance.service import FinanceConnectionError, FinanceService
from jambachat.finance.tools import _view
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.tools.registry import Attachment
from jambachat.users.export import export_user_data
from jambachat.users.models import User
from jambachat.users.repository import delete_user_data
from jambachat.wallet.service import issue_token, revoke_token

HELP_TEXT = """*JambaChat* — é só falar comigo normalmente:

• *Lembretes:* "me lembra de pagar a luz dia 10 às 9h"
• *Gastos:* "gastei 42 no ifood", "quanto gastei esse mês?"
• *Orçamento:* "limite de 800 pra mercado", "aluguel de 1500 todo dia 5"
• *Listas:* "põe leite e pão na lista de compras"
• *Pesquisa:* "quem ganhou o jogo ontem?"
• *Utilitários:* câmbio, clima, CEP, contas e datas
• *Perfil:* "me chama de Jão", "mudei pra Manaus"
• *Áudio e foto:* mande um áudio ou a foto de uma nota fiscal

*Comandos*
/ajuda — esta mensagem
/limpar — esqueço o assunto atual
/exportar-meus-dados — mando um arquivo com tudo que tenho sobre você
/atalho — registro sozinho as compras feitas com o iPhone
/banco [id] — listo bancos conectados ou conecto um banco pelo identificador
/apagar-meus-dados — apago tudo que tenho sobre você"""

CLEARED_TEXT = "Pronto, esqueci o assunto. Pode começar do zero."
DELETE_CONFIRM_TEXT = (
    "Isso apaga *todos* os seus lembretes, gastos, listas e conversas, sem volta.\n"
    "Se tiver certeza, mande: /apagar-meus-dados confirmar"
)
DELETED_TEXT = "Seus dados foram apagados. Se mandar outra mensagem, começo do zero."
EXPORTED_TEXT = "Aqui está tudo que eu guardo sobre você."
UNKNOWN_TEXT = "Não conheço esse comando. Mande /ajuda pra ver o que eu faço."
WALLET_OFF_TEXT = "Pronto, o atalho da Carteira foi desligado. O código antigo não funciona mais."
BANK_EMPTY_TEXT = (
    "Nenhum banco conectado. Conecte no Meu Pluggy e mande /banco seguido do identificador."
)


def wallet_text(token: str, base_url: str) -> str:
    url = f"{(base_url or 'https://SEU-DOMINIO').rstrip('/')}/integrations/wallet"
    return "\n".join(
        [
            "*Atalho da Carteira*",
            "Seu código pessoal (não compartilhe, ele só aparece agora):",
            token,
            "",
            "No iPhone: Atalhos → Automação → Nova → Transação → escolha os cartões →",
            "ação *Obter Conteúdo de URL*:",
            f"• URL: {url}",
            "• Método: POST",
            "• Cabeçalho Authorization: Bearer + espaço + o código",
            "• Corpo JSON: amount = Valor, merchant = Comerciante, card = Cartão",
            "",
            "Mandar /atalho de novo gera outro código e invalida este.",
            "Para desligar: /atalho desligar",
        ]
    )


async def status_text(session: AsyncSession, settings: Settings, tools: int) -> str:
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    pending = (
        await session.scalar(
            select(func.count())
            .select_from(Reminder)
            .where(Reminder.status == ReminderStatus.PENDING)
        )
        or 0
    )

    def flag(enabled: bool) -> str:
        return "ligado" if enabled else "desligado"

    return "\n".join(
        [
            f"*JambaChat {__version__}*",
            f"• Modelo: {settings.llm_provider} / {settings.llm_model}",
            f"• Ferramentas: {tools}",
            f"• Pesquisa: {flag(bool(settings.tavily_api_key.get_secret_value()))}",
            f"• Áudio: {flag(bool(settings.stt_base_url))}",
            f"• Visão: {flag(settings.llm_vision)}",
            f"• Agendador: {flag(settings.scheduler_enabled)}",
            f"• Usuários: {users} · lembretes pendentes: {pending}",
        ]
    )


async def handle_command(
    session: AsyncSession,
    user: User,
    text: str,
    settings: Settings | None = None,
    tools: int = 0,
    attachments: list[Attachment] | None = None,
    finance_provider: FinanceProvider | None = None,
) -> str | None:
    if not text.startswith("/"):
        return None
    command, _, argument = text.strip().partition(" ")
    command = command.lower()
    match command:
        case "/ajuda" | "/help" | "/start":
            return HELP_TEXT
        case "/limpar":
            user.context_cleared_at = datetime.now(UTC)
            return CLEARED_TEXT
        case "/status" if settings is not None and user.phone in settings.admin_phones:
            return await status_text(session, settings, tools)
        case "/exportar-meus-dados" if attachments is not None:
            content = await export_user_data(session, user)
            attachments.append(Attachment("jambachat-meus-dados.json", "application/json", content))
            return EXPORTED_TEXT
        case "/atalho":
            if argument.strip() == "desligar":
                revoke_token(user)
                return WALLET_OFF_TEXT
            return wallet_text(issue_token(user), settings.public_base_url if settings else "")
        case "/banco":
            service = FinanceService(finance_provider or NullFinanceProvider())
            parts = argument.strip().split()
            if not parts:
                connections = await service.list_connections(session, user)
                if not connections:
                    return BANK_EMPTY_TEXT
                return "\n".join(
                    [
                        "*Bancos conectados*",
                        *(
                            f"• {view['institution']} — {view['status']}"
                            + (
                                f"; última importação: {view['last_import']}"
                                if view["last_import"]
                                else ""
                            )
                            + f"\n  Id: {view['id']}"
                            for view in (
                                _view(connection, user.timezone) for connection in connections
                            )
                        ),
                    ]
                )
            if parts[0].casefold() == "remover":
                if len(parts) != 3 or parts[2].casefold() != "confirmar":
                    return "Para desconectar, mande: /banco remover <id> confirmar"
                connection = next(
                    (
                        current
                        for current in await service.list_connections(session, user)
                        if str(current.id) == parts[1]
                    ),
                    None,
                )
                if connection is None:
                    return "conexão não encontrada"
                with suppress(FinanceError):
                    await (finance_provider or NullFinanceProvider()).delete_item(
                        connection.item_ref
                    )
                await session.delete(connection)
                return "Banco desconectado. Seus gastos importados foram mantidos."
            if len(parts) != 1:
                return "Use /banco <identificador> para conectar um banco."
            try:
                connection = await service.connect(session, user, parts[0])
            except (FinanceError, FinanceConnectionError) as exc:
                return str(exc)
            return f"Banco {connection.institution} conectado."
        case "/apagar-meus-dados":
            if argument.strip() != "confirmar":
                return DELETE_CONFIRM_TEXT
            await delete_user_data(session, user, finance_provider or NullFinanceProvider())
            return DELETED_TEXT
        case _:
            return UNKNOWN_TEXT
