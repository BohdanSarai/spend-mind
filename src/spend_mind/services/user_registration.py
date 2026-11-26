from sqlalchemy.ext.asyncio import AsyncSession

from spend_mind.models import User
from spend_mind.repositories import category, user

DEFAULT_CATEGORY_NAMES = (
    "Продукти",
    "Кафе та ресторани",
    "Транспорт",
    "Автомобіль",
    "Житло та комунальні послуги",
    "Здоров'я",
    "Одяг та взуття",
    "Розваги",
    "Освіта",
    "Підписки",
    "Побут",
    "Подарунки",
    "Інше",
)


async def register_user(
    session: AsyncSession, telegram_user_id: int
) -> tuple[User, bool]:
    async with session.begin():
        existing = await user.get_by_telegram_id(session, telegram_user_id)
        if existing is not None:
            return existing, False

        created = await user.insert_if_absent(session, telegram_user_id)
        if created is not None:
            await category.insert_categories(
                session, created.id, DEFAULT_CATEGORY_NAMES
            )
            return created, True

        # READ COMMITTED gives this statement a snapshot after the winning insert.
        existing = await user.get_by_telegram_id(session, telegram_user_id)
        if existing is None:
            raise RuntimeError("Concurrent registration did not leave a user")
        return existing, False
