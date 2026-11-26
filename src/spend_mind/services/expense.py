from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.integrations import nbu
from spend_mind.models import Expense, ExpenseItem
from spend_mind.models.expense import ExpenseStatus
from spend_mind.repositories import category, expense, user
from spend_mind.schemas.currency import SUPPORTED_CURRENCY_CODES
from spend_mind.schemas.expense import ExpenseCreate


class ExpenseCreationError(StrEnum):
    USER_NOT_FOUND = "user_not_found"
    CATEGORY_NOT_FOUND = "category_not_found"
    TOTAL_MISMATCH = "expense_total_mismatch"


class ExpenseCreationFailed(Exception):
    def __init__(self, reason: ExpenseCreationError) -> None:
        self.reason = reason
        super().__init__(reason.value)


class ExpenseTransitionError(StrEnum):
    USER_NOT_FOUND = "user_not_found"
    EXPENSE_NOT_FOUND = "expense_not_found"
    STATUS_CONFLICT = "expense_status_conflict"
    EXPENSE_CHANGED = "expense_changed"
    RATE_UNAVAILABLE = "exchange_rate_unavailable"
    CONVERSION_OUT_OF_RANGE = "expense_conversion_out_of_range"
    UNSUPPORTED_CURRENCY = "unsupported_expense_currency"


class ExpenseTransitionFailed(Exception):
    def __init__(self, reason: ExpenseTransitionError) -> None:
        self.reason = reason
        super().__init__(reason.value)


def reconcile_item_amounts(payload: ExpenseCreate) -> list[Decimal]:
    amounts = [item.amount for item in payload.items]
    discount = payload.receipt_discount or Decimal(0)
    gross_total = sum(amounts, Decimal(0))
    if gross_total - discount != payload.total_amount:
        raise ExpenseCreationFailed(ExpenseCreationError.TOTAL_MISMATCH)
    if not discount:
        return amounts

    # Integer cents preserve exact remainders even for the largest monetary values.
    gross_cents = int(gross_total * 100)
    discount_cents = int(discount * 100)
    item_cents = [int(amount * 100) for amount in amounts]
    shares = [divmod(discount_cents * cents, gross_cents) for cents in item_cents]
    allocated = [whole for whole, _ in shares]
    remaining = discount_cents - sum(allocated)
    # Python's stable sort breaks equal remainders by request order.
    by_remainder = sorted(range(len(shares)), key=lambda index: -shares[index][1])
    for index in by_remainder[:remaining]:
        allocated[index] += 1

    return [
        Decimal(cents - share).scaleb(-2)
        for cents, share in zip(item_cents, allocated, strict=True)
    ]


async def create_expense(
    session: AsyncSession, telegram_user_id: int, payload: ExpenseCreate
) -> Expense:
    amounts = reconcile_item_amounts(payload)
    category_ids = {
        item.category_id for item in payload.items if item.category_id is not None
    }
    async with session.begin():
        owner = await user.get_by_telegram_id(session, telegram_user_id)
        if owner is None:
            raise ExpenseCreationFailed(ExpenseCreationError.USER_NOT_FOUND)
        if category_ids:
            owned_ids = await category.lock_owned_category_ids_for_reference(
                session, owner.id, category_ids
            )
            if owned_ids != category_ids:
                raise ExpenseCreationFailed(ExpenseCreationError.CATEGORY_NOT_FOUND)

        record = Expense(
            user_id=owner.id,
            merchant=payload.merchant,
            total_amount=payload.total_amount,
            currency=payload.currency,
            expense_date=payload.expense_date,
            source=payload.source,
            status=ExpenseStatus.PENDING,
            items=[
                ExpenseItem(
                    name=item.name,
                    amount=amount,
                    quantity=item.quantity,
                    unit=item.unit,
                    category_id=item.category_id,
                )
                for item, amount in zip(payload.items, amounts, strict=True)
            ],
        )
        await expense.insert_expense(session, record)
    return record


def is_already_in_target_state(record: Expense, target: ExpenseStatus) -> bool:
    if record.status == target:
        return True
    if record.status != ExpenseStatus.PENDING:
        raise ExpenseTransitionFailed(ExpenseTransitionError.STATUS_CONFLICT)
    return False


async def get_owned_expense(
    session: AsyncSession, expense_id: int, user_id: int
) -> tuple[Expense, date]:
    result = await expense.find_owned_expense(session, expense_id, user_id)
    if result is None:
        raise ExpenseTransitionFailed(ExpenseTransitionError.EXPENSE_NOT_FOUND)
    return result


async def calculate_conversion(
    currency: str, total_amount: Decimal, kyiv_date: date
) -> tuple[Decimal, Decimal]:
    if currency not in SUPPORTED_CURRENCY_CODES:
        raise ExpenseTransitionFailed(ExpenseTransitionError.UNSUPPORTED_CURRENCY)
    if currency == "UAH":
        return Decimal(1), total_amount

    async with httpx.AsyncClient() as client:
        rate = await nbu.get_historical_rate(client, currency, kyiv_date)
    if rate >= Decimal(10000000000):
        raise ExpenseTransitionFailed(ExpenseTransitionError.CONVERSION_OUT_OF_RANGE)

    with localcontext(prec=34):
        stored_rate = rate.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        amount_uah = (total_amount * stored_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    if not Decimal(0) < stored_rate < Decimal(10000000000) or amount_uah >= Decimal(
        1000000000000
    ):
        raise ExpenseTransitionFailed(ExpenseTransitionError.CONVERSION_OUT_OF_RANGE)
    return stored_rate, amount_uah


async def transition_expense(
    session: AsyncSession,
    telegram_user_id: int,
    expense_id: int,
    target: ExpenseStatus,
) -> Expense:
    if target is not ExpenseStatus.CONFIRMED and target is not ExpenseStatus.REJECTED:
        raise ValueError("Target status must be CONFIRMED or REJECTED")

    async with session.begin():
        owner = await user.get_by_telegram_id(session, telegram_user_id)
        if owner is None:
            raise ExpenseTransitionFailed(ExpenseTransitionError.USER_NOT_FOUND)
        user_id = owner.id
        record, kyiv_date = await get_owned_expense(session, expense_id, user_id)
        if is_already_in_target_state(record, target):
            return record
        snapshot = (record.currency, record.total_amount, record.expense_date)

    expected = rate = amount_uah = None
    if target == ExpenseStatus.CONFIRMED:
        expected = snapshot
        try:
            rate, amount_uah = await calculate_conversion(
                snapshot[0], snapshot[1], kyiv_date
            )
        except nbu.ExchangeRateUnavailable as exc:
            async with session.begin():
                current, _ = await get_owned_expense(session, expense_id, user_id)
                if is_already_in_target_state(current, target):
                    return current
            raise ExpenseTransitionFailed(
                ExpenseTransitionError.RATE_UNAVAILABLE
            ) from exc

    async with session.begin():
        updated = await expense.transition_pending_expense(
            session,
            expense_id,
            user_id,
            target,
            expected=expected,
            rate=rate,
            amount_uah=amount_uah,
        )
        if updated is not None:
            return updated
        current, _ = await get_owned_expense(session, expense_id, user_id)
        if is_already_in_target_state(current, target):
            return current
        raise ExpenseTransitionFailed(ExpenseTransitionError.EXPENSE_CHANGED)
