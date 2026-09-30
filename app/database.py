"""Async Postgres sessions. Tables are created on startup if they do not exist."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.models import Base

settings = get_settings()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("ALTER TABLE signalpeak_discord ADD COLUMN IF NOT EXISTS source_category_id BIGINT"))
        await connection.execute(text("ALTER TABLE signalpeak_discord ADD COLUMN IF NOT EXISTS source_category_name VARCHAR(255)"))
        await connection.execute(text("ALTER TABLE signalpeak_discord ADD COLUMN IF NOT EXISTS destination_category_id BIGINT"))
        await connection.execute(text("ALTER TABLE signalpeak_discord ADD COLUMN IF NOT EXISTS destination_category_name VARCHAR(255)"))
        await connection.execute(text("DROP INDEX IF EXISTS ix_signalpeak_discord_replace_rules_active"))
        await connection.execute(text("ALTER TABLE signalpeak_discord_replace_rules DROP COLUMN IF EXISTS route_id CASCADE"))
        await connection.execute(text("ALTER TABLE signalpeak_discord_replace_rules DROP COLUMN IF EXISTS is_regex"))
        await connection.execute(text("ALTER TABLE signalpeak_discord_replace_rules DROP COLUMN IF EXISTS ignore_case"))
        await connection.execute(text("ALTER TABLE signalpeak_discord_replace_rules DROP COLUMN IF EXISTS is_active"))
        await connection.execute(text("ALTER TABLE signalpeak_discord_replace_rules DROP COLUMN IF EXISTS position"))
        await connection.execute(text("ALTER TABLE signalpeak_discord_replace_rules DROP COLUMN IF EXISTS created_at"))
        await connection.execute(text("ALTER TABLE signalpeak_discord_replace_rules DROP COLUMN IF EXISTS updated_at"))


async def check_db() -> None:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
