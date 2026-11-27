from base64 import b64decode, urlsafe_b64encode
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from hashlib import sha256

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.integrations import nbu
from spend_mind.models import Expense, ExpenseItem
from spend_mind.models.expense import ExpenseStatus
from spend_mind.repositories import category, expense, user
from spend_mind.schemas.currency import SUPPORTED_CURRENCY_CODES
from spend_mind.schemas.expense import (
    ExpenseCreate,
    ExpenseCursor,
    ExpenseDetail,
    ExpenseItemCreate,
    ExpenseListItem,
    ExpenseListQuery,
    ExpensePage,
    ExpensePatch,
)


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
    STALE_ETAG = "expense_etag_mismatch"
    INVALID_CURSOR = "invalid_expense_cursor"


class ExpenseTransitionFailed(Exception):
    def __init__(self, reason: ExpenseTransitionError) -> None:
        self.reason = reason
        super().__init__(reason.value)


def reconcile_item_amounts(payload: ExpenseCreate) -> list[Decimal]:
    return reconcile_amounts(
        [item.amount for item in payload.items],
        payload.total_amount,
        payload.receipt_discount,
    )


def reconcile_amounts(
    amounts: list[Decimal], total_amount: Decimal, receipt_discount: Decimal | None
) -> list[Decimal]:
    discount = receipt_discount or Decimal(0)
    gross_total = sum(amounts, Decimal(0))
    if gross_total - discount != total_amount:
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


def build_expense_items(
    items: list[ExpenseItemCreate], amounts: list[Decimal]
) -> list[ExpenseItem]:
    return [
        ExpenseItem(
            name=item.name,
            quantity=item.quantity,
            unit=item.unit,
            amount=amount,
            category_id=item.category_id,
        )
        for item, amount in zip(items, amounts, strict=True)
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
            items=build_expense_items(payload.items, amounts),
        )
        await expense.insert_expense(session, record)
    return record


async def get_expense_owner_id(session: AsyncSession, telegram_user_id: int) -> int:
    owner = await user.get_by_telegram_id(session, telegram_user_id)
    if owner is None:
        raise ExpenseTransitionFailed(ExpenseTransitionError.USER_NOT_FOUND)
    return owner.id


async def list_expenses(
    session: AsyncSession, telegram_user_id: int, query: ExpenseListQuery
) -> ExpensePage:
    before = None
    if query.cursor is not None:
        try:
            decoded = b64decode(query.cursor, altchars=b"-_", validate=True)
            cursor = ExpenseCursor.model_validate_json(decoded)
        except ValueError as exc:
            raise ExpenseTransitionFailed(
                ExpenseTransitionError.INVALID_CURSOR
            ) from exc
        before = (cursor.expense_date, cursor.id)
    async with session.begin():
        owner_id = await get_expense_owner_id(session, telegram_user_id)
        records = await expense.list_owned_expenses(
            session,
            owner_id,
            status=None if query.status == "all" else query.status,
            date_from=query.date_from,
            date_to=query.date_to,
            limit=query.limit + 1,
            before=before,
        )
        page = records[: query.limit]
        next_cursor = None
        if len(records) > query.limit:
            last = page[-1]
            cursor = ExpenseCursor(expense_date=last.expense_date, id=last.id)
            next_cursor = urlsafe_b64encode(cursor.model_dump_json().encode()).decode()
        return ExpensePage(
            items=[ExpenseListItem.model_validate(record) for record in page],
            next_cursor=next_cursor,
        )


def expense_detail(record: Expense) -> ExpenseDetail:
    detail = ExpenseDetail.model_validate(record)
    detail.items.sort(key=lambda item: item.id)
    return detail


def expense_etag(detail: ExpenseDetail) -> str:
    return f'"{sha256(detail.model_dump_json().encode()).hexdigest()}"'


async def get_expense_detail(
    session: AsyncSession, telegram_user_id: int, expense_id: int
) -> ExpenseDetail:
    async with session.begin():
        owner_id = await get_expense_owner_id(session, telegram_user_id)
        record, _ = await get_owned_expense(session, expense_id, owner_id)
        return expense_detail(record)


async def lock_expense_and_categories(
    session: AsyncSession,
    expense_id: int,
    owner_id: int,
    new_category_ids: set[int] | None = None,
) -> tuple[Expense, set[int]]:
    if not await expense.lock_owned_expense(session, expense_id, owner_id):
        raise ExpenseTransitionFailed(ExpenseTransitionError.EXPENSE_NOT_FOUND)
    record, _ = await get_owned_expense(session, expense_id, owner_id)
    category_ids = {
        item.category_id for item in record.items if item.category_id is not None
    }
    category_ids.update(new_category_ids or ())
    if not category_ids:
        return record, set()
    # Category deletion locks categories before touching items. Read items again
    # after acquiring those locks, before this transaction writes any item rows.
    locked_ids = await category.lock_owned_category_ids_for_reference(
        session, owner_id, category_ids
    )
    record, _ = await get_owned_expense(session, expense_id, owner_id)
    return record, locked_ids


def require_matching_etag(record: Expense, if_match: str) -> None:
    if expense_etag(expense_detail(record)) != if_match.strip():
        raise ExpenseTransitionFailed(ExpenseTransitionError.STALE_ETAG)


async def update_expense(
    session: AsyncSession,
    telegram_user_id: int,
    expense_id: int,
    payload: ExpensePatch,
    if_match: str,
) -> ExpenseDetail:
    async with session.begin():
        owner_id = await get_expense_owner_id(session, telegram_user_id)
        items = payload.items
        new_category_ids = {
            item.category_id for item in items or () if item.category_id is not None
        }
        record, locked_ids = await lock_expense_and_categories(
            session, expense_id, owner_id, new_category_ids
        )
        if record.status != ExpenseStatus.PENDING:
            raise ExpenseTransitionFailed(ExpenseTransitionError.STATUS_CONFLICT)
        require_matching_etag(record, if_match)
        final_items = record.items if items is None else items
        if any(
            item.category_id is not None and item.category_id not in locked_ids
            for item in final_items
        ):
            raise ExpenseCreationFailed(ExpenseCreationError.CATEGORY_NOT_FOUND)
        for field in payload.model_fields_set - {"items", "receipt_discount"}:
            setattr(record, field, getattr(payload, field))
        if record.currency not in SUPPORTED_CURRENCY_CODES:
            raise ExpenseTransitionFailed(ExpenseTransitionError.UNSUPPORTED_CURRENCY)
        if not final_items:
            raise ExpenseCreationFailed(ExpenseCreationError.TOTAL_MISMATCH)
        amounts = reconcile_amounts(
            [item.amount for item in final_items],
            record.total_amount,
            payload.receipt_discount,
        )
        if items is not None:
            record.items = build_expense_items(items, amounts)
        await session.flush()
        stored, _ = await get_owned_expense(session, expense_id, owner_id)
        return expense_detail(stored)


async def delete_expense(
    session: AsyncSession, telegram_user_id: int, expense_id: int, if_match: str
) -> None:
    async with session.begin():
        owner_id = await get_expense_owner_id(session, telegram_user_id)
        record, _ = await lock_expense_and_categories(session, expense_id, owner_id)
        require_matching_etag(record, if_match)
        await expense.delete_owned_expense(session, expense_id, owner_id)


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
        user_id = await get_expense_owner_id(session, telegram_user_id)
        record, kyiv_date = await get_owned_expense(session, expense_id, user_id)
        if is_already_in_target_state(record, target):
            return record
        snapshot = (record.currency, record.total_amount, record.expense_date)
        draft_etag = expense_etag(expense_detail(record))

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
        if target == ExpenseStatus.CONFIRMED:
            current, _ = await lock_expense_and_categories(session, expense_id, user_id)
            if is_already_in_target_state(current, target):
                return current
            if expense_etag(expense_detail(current)) != draft_etag:
                raise ExpenseTransitionFailed(ExpenseTransitionError.EXPENSE_CHANGED)
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
