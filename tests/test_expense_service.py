from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models.expense import ExpenseStatus
from spend_mind.services import expense


@pytest.mark.asyncio
async def test_transition_rejects_pending_target_before_opening_transaction() -> None:
    session = Mock(spec=AsyncSession)

    with pytest.raises(ValueError, match="Target status must be CONFIRMED or REJECTED"):
        await expense.transition_expense(session, 123, 1, ExpenseStatus.PENDING)

    session.begin.assert_not_called()


@pytest.mark.asyncio
async def test_conversion_uses_shared_supported_currency_codes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(expense, "SUPPORTED_CURRENCY_CODES", frozenset({"UAH", "EUR"}))
    fetch_rate = AsyncMock()
    monkeypatch.setattr(expense.nbu, "get_historical_rate", fetch_rate)

    with pytest.raises(expense.ExpenseTransitionFailed) as caught:
        await expense.calculate_conversion("USD", Decimal(10), date(2026, 11, 27))

    assert caught.value.reason is expense.ExpenseTransitionError.UNSUPPORTED_CURRENCY
    fetch_rate.assert_not_called()
