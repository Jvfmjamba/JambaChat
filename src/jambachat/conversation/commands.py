from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from jambachat import __version__
from jambachat.config import Settings
from jambachat.reminders.models import Reminder, ReminderStatus
from jambachat.tools.registry import Attachment
from jambachat.users.export import export_user_data
from jambachat.users.models import User
from jambachat.users.repository import delete_user_data

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
/apagar-meus-dados — apago tudo que tenho sobre você"""

CLEARED_TEXT = "Pronto, esqueci o assunto. Pode começar do zero."
DELETE_CONFIRM_TEXT = (
    "Isso apaga *todos* os seus lembretes, gastos, listas e conversas, sem volta.\n"
    "Se tiver certeza, mande: /apagar-meus-dados confirmar"
)
DELETED_TEXT = "Seus dados foram apagados. Se mandar outra mensagem, começo do zero."
EXPORTED_TEXT = "Aqui está tudo que eu guardo sobre você."
UNKNOWN_TEXT = "Não conheço esse comando. Mande /ajuda pra ver o que eu faço."


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
) -> str | None:
    if not text.startswith("/"):
        return None
    command, _, argument = text.strip().lower().partition(" ")
    match command:
        case "/ajuda" | "/help" | "/start":
            return HELP_TEXT
        case "/limpar":
            user.context_cleared_at = datetime.now(UTC)
            return CLEARED_TEXT
        # checa ADMIN_PHONES a cada uso: tirar um número da lista revoga na hora
        case "/status" if settings is not None and user.phone in settings.admin_phones:
            return await status_text(session, settings, tools)
        case "/exportar-meus-dados" if attachments is not None:
            content = await export_user_data(session, user)
            attachments.append(Attachment("jambachat-meus-dados.json", "application/json", content))
            return EXPORTED_TEXT
        case "/apagar-meus-dados":
            if argument.strip() != "confirmar":
                return DELETE_CONFIRM_TEXT
            await delete_user_data(session, user)
            return DELETED_TEXT
        case _:
            return UNKNOWN_TEXT
