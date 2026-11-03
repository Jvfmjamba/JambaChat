import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jambachat.config import Settings
from jambachat.expenses.budgets import budget_alerts
from jambachat.expenses.categorize import guess_category
from jambachat.expenses.models import EntryKind, EntrySource
from jambachat.expenses.service import add_entry
from jambachat.money import format_brl, parse_amount
from jambachat.security.crypto import get_cipher
from jambachat.security.ratelimit import Decision, RateLimiter
from jambachat.timeutils import as_utc
from jambachat.users.models import User
from jambachat.whatsapp.client import WhatsAppClient, WhatsAppError

logger = structlog.get_logger(__name__)

TOKEN_BYTES = 32
MAX_TOKEN_LENGTH = 128
MAX_MERCHANT_LENGTH = 120
MAX_CARD_LENGTH = 60
SERVICE_WINDOW = timedelta(hours=24)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class WalletError(Exception):
    pass


class WalletAuthError(WalletError):
    pass


class WalletRateLimitError(WalletError):
    pass


class WalletInvalidError(WalletError):
    pass


def token_hash(token: str) -> str:
    return get_cipher().blind_index(f"wallet:{token}")


def issue_token(user: User) -> str:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    user.wallet_token_hash = token_hash(token)
    return token


def revoke_token(user: User) -> None:
    user.wallet_token_hash = None


def _clean(value: object, limit: int, field: str, *, required: bool) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise WalletInvalidError(f"campo obrigatório: {field}")
        return None
    if not isinstance(value, str):
        raise WalletInvalidError(f"campo inválido: {field}")
    text = " ".join(_CONTROL.sub(" ", value).split())
    return text[:limit]


def _alert_text(alert: dict[str, Any]) -> str:
    if alert.get("exceeded_by"):
        return (
            f"Orçamento de {alert['category']} estourado em {alert['exceeded_by']} "
            f"(limite {alert['limit']})."
        )
    return f"Você já usou {alert['used']} do orçamento de {alert['category']}."


class WalletService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        whatsapp: WhatsAppClient,
        settings: Settings,
        limiter: RateLimiter,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._whatsapp = whatsapp
        self._settings = settings
        self._limiter = limiter

    async def record(self, token: str, payload: dict[str, Any]) -> str:
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise WalletAuthError
        async with self._sessionmaker() as session:
            user = await session.scalar(
                select(User).where(User.wallet_token_hash == token_hash(token))
            )
            if user is None or user.phone not in self._settings.allowed_phones:
                raise WalletAuthError
            if self._limiter.hit(("wallet", user.id)) is not Decision.ALLOW:
                raise WalletRateLimitError

            try:
                amount_cents = parse_amount(payload.get("amount"))
            except ValueError as exc:
                raise WalletInvalidError(f"valor inválido: {exc}") from exc
            merchant = _clean(
                payload.get("merchant"), MAX_MERCHANT_LENGTH, "merchant", required=True
            )
            card = _clean(payload.get("card"), MAX_CARD_LENGTH, "card", required=False)
            if merchant is None:
                raise WalletInvalidError("campo obrigatório: merchant")

            category = guess_category(merchant).value
            entry = await add_entry(
                session,
                user,
                kind=EntryKind.EXPENSE,
                amount_cents=amount_cents,
                description=f"{merchant} ({card})" if card else merchant,
                category=category,
                occurred_at=datetime.now(UTC),
            )
            entry.source = EntrySource.WALLET
            alerts = await budget_alerts(
                session, user, datetime.now(ZoneInfo(user.timezone)), category
            )
            phone, last_inbound = user.phone, user.last_inbound_at
            await session.commit()

        message = f"Registrei {format_brl(amount_cents)} em {merchant} ({category})."
        lines = [message, *(_alert_text(a) for a in alerts)]
        if last_inbound is not None and datetime.now(UTC) - as_utc(last_inbound) < SERVICE_WINDOW:
            try:
                await self._whatsapp.send_text(
                    phone, "\n".join([*lines, "Se algo estiver errado, é só me dizer."])
                )
            except WhatsAppError as exc:
                logger.warning("wallet_notify_failed", error=str(exc))
        return "\n".join(lines)
