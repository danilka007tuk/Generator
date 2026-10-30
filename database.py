import logging
from pathlib import Path
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "bookings.db"

DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH.as_posix()}"

SQLITE_BUSY_TIMEOUT_MS = 30_000


class Base(DeclarativeBase):
    """
    Базовый класс для всех SQLAlchemy-моделей.
    """
    pass


async_engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={
        "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000,
    },
    pool_pre_ping=True,
)


@event.listens_for(async_engine.sync_engine, "connect")
def configure_sqlite_connection(dbapi_connection, connection_record):
    """
    Настройка каждого нового SQLite-соединения.

    Важно: PRAGMA применяются к каждому соединению отдельно.
    Однократный вызов PRAGMA при старте приложения недостаточен,
    потому что SQLAlchemy может создать несколько соединений.
    """
    cursor = dbapi_connection.cursor()

    try:
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute(
            f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}"
        )

        # WAL позволяет читать БД во время записи.
        # Если база находится на сетевой файловой системе,
        # WAL использовать не рекомендуется.
        cursor.execute("PRAGMA journal_mode = WAL")

        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA temp_store = MEMORY")

    finally:
        cursor.close()


async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def init_db() -> None:
    """
    Создаёт отсутствующие таблицы.

    Метод НЕ удаляет существующие таблицы и НЕ изменяет их структуру.
    Для изменения уже существующих таблиц нужны миграции Alembic
    либо отдельный ручной ALTER TABLE.
    """
    # Импорт нужен здесь, чтобы модели были зарегистрированы в metadata
    # до вызова Base.metadata.create_all().
    import models  # noqa: F401

    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger.info(
        "Инициализация SQLite: %s",
        DATABASE_PATH,
    )

    async with async_engine.begin() as connection:
        await connection.run_sync(
            Base.metadata.create_all,
            checkfirst=True,
        )

    logger.info("Инициализация базы данных завершена")


async def get_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency для получения AsyncSession.

    Использование:

        @app.get("/")
        async def endpoint(
            session: AsyncSession = Depends(get_session),
        ):
            ...
    """
    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """
    Корректно закрывает пул соединений.

    Можно вызвать в lifespan FastAPI перед завершением приложения.
    """
    await async_engine.dispose()
    logger.info("Соединения с базой данных закрыты")