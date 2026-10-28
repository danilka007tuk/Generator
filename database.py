# database.py
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

BASE_DIR = Path(__file__).parent
DATABASE_URL = f"sqlite+aiosqlite:///{BASE_DIR / 'bookings.db'}"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    """Создаёт таблицы при первом запуске."""
    import models  # гарантирует регистрацию всех моделей в Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    """Возвращает сессию для работы с БД."""
    async with async_session() as session:
        yield session