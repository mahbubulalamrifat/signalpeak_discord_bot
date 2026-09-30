"""FastAPI app and the Discord gateway running in the same process."""

import asyncio
import logging
from contextlib import asynccontextmanager

import discord
from fastapi import FastAPI

from app.api.discord import router as discord_router
from app.api.http import router
from app.bot import bot
from app.config import get_settings
from app.database import SessionLocal, check_db, init_db
from app.services.replace_cache import refresh_replace_rules
from app.services.route_cache import refresh_route_cache
from app.services.seed import seed_initial_data

logger = logging.getLogger("signalpeak")


def setup_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    if not settings.admin_api_key.strip():
        logger.warning("ADMIN_API_KEY is empty. Management endpoints are open on this host.")
    if not settings.discord_application_id.strip():
        logger.warning("DISCORD_APPLICATION_ID is empty.")
    if not settings.discord_public_key.strip():
        logger.warning("DISCORD_PUBLIC_KEY is empty.")

    await init_db()
    async with SessionLocal() as session:
        await seed_initial_data(session)
    await refresh_replace_rules()
    await refresh_route_cache()

    bot_task: asyncio.Task | None = None
    token = settings.discord_bot_token.strip()
    if token:
        bot_task = asyncio.create_task(_run_bot(token))
    else:
        logger.error("DISCORD_BOT_TOKEN is empty. The API is up, but messages will not be forwarded.")

    yield

    if bot_task is not None and not bot_task.done():
        bot_task.cancel()
    if not bot.is_closed():
        try:
            await bot.close()
        except Exception:
            logger.exception("Discord bot did not close cleanly")
    if bot_task is not None:
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


async def _run_bot(token: str) -> None:
    try:
        await bot.start(token)
    except asyncio.CancelledError:
        raise
    except discord.PrivilegedIntentsRequired:
        logger.error(
            "Discord refused the connection. Open the application in the Developer Portal, "
            "go to Bot, and turn on Message Content Intent and Server Members Intent. Then start the app again."
        )
    except Exception:
        logger.exception("Discord bot stopped")


app = FastAPI(title="SignalPeak Discord Forwarder", lifespan=lifespan)
app.include_router(router)
app.include_router(discord_router)


@app.get("/health")
async def health() -> dict:
    settings = get_settings()
    database_ok = True
    database_error = None
    try:
        await check_db()
    except Exception as exc:
        database_ok = False
        database_error = str(exc)
    return {
        "ok": database_ok and bot.is_ready(),
        "database": database_ok,
        "database_error": database_error,
        "bot_connected": bot.is_ready(),
        "application_id": settings.discord_application_id or None,
        "public_key_configured": bool(settings.discord_public_key.strip()),
    }


@app.get("/")
async def root() -> dict:
    return {"service": "SignalPeak Discord Forwarder", "health": "/health", "docs": "/docs"}
