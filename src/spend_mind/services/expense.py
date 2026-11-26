from decimal import Decimal
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import Expense, ExpenseItem
from spend_mind.models.expense import ExpenseStatus
from spend_mind.repositories import category, expense, user
from spend_mind.schemas.expense import ExpenseCreate


class ExpenseCreationError(StrEnum):
    USER_NOT_FOUND = "user_not_found"
    CATEGORY_NOT_FOUND = "category_not_found"
    TOTAL_MISMATCH = "expense_total_mismatch"


class ExpenseCreationFailed(Exception):
    def __init__(self, reason: ExpenseCreationError) -> None:
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
