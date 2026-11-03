from datetime import date

import pytest

from jambachat.finance.errors import FinanceUnavailableError
from jambachat.finance.provider import NullFinanceProvider


@pytest.mark.asyncio
async def test_null_provider_reports_unavailability() -> None:
    provider = NullFinanceProvider()

    with pytest.raises(FinanceUnavailableError, match="não está disponível"):
        await provider.list_items("item")
    with pytest.raises(FinanceUnavailableError):
        await provider.fetch_transactions("item", date.today(), date.today())
    with pytest.raises(FinanceUnavailableError):
        await provider.delete_item("item")
