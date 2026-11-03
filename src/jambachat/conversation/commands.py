from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from jambachat.users.models import User
from jambachat.users.repository import delete_user_data

HELP_TEXT = """*JambaChat* — é só falar comigo normalmente:

• *Lembretes:* "me lembra de pagar a luz dia 10 às 9h"
• *Gastos:* "gastei 42 no ifood", "quanto gastei esse mês?"
• *Listas:* "põe leite e pão na lista de compras"
• *Pesquisa:* "quem ganhou o jogo ontem?"
• *Utilitários:* câmbio, clima, CEP, contas e datas
• *Áudio e foto:* mande um áudio ou a foto de uma nota fiscal

*Comandos*
/ajuda — esta mensagem
/limpar — esqueço o assunto atual
/apagar-meus-dados — apago tudo que tenho sobre você"""

CLEARED_TEXT = "Pronto, esqueci o assunto. Pode começar do zero."
DELETE_CONFIRM_TEXT = (
    "Isso apaga *todos* os seus lembretes, gastos e conversas, sem volta.\n"
    "Se tiver certeza, mande: /apagar-meus-dados confirmar"
)
DELETED_TEXT = "Seus dados foram apagados. Se mandar outra mensagem, começo do zero."
UNKNOWN_TEXT = "Não conheço esse comando. Mande /ajuda pra ver o que eu faço."


async def handle_command(session: AsyncSession, user: User, text: str) -> str | None:
    if not text.startswith("/"):
        return None
    command, _, argument = text.strip().lower().partition(" ")
    match command:
        case "/ajuda" | "/help" | "/start":
            return HELP_TEXT
        case "/limpar":
            user.context_cleared_at = datetime.now(UTC)
            return CLEARED_TEXT
        case "/apagar-meus-dados":
            if argument.strip() != "confirmar":
                return DELETE_CONFIRM_TEXT
            await delete_user_data(session, user)
            return DELETED_TEXT
        case _:
            return UNKNOWN_TEXT
