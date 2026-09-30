"""Read channel pairs from signalpeak_discord and keep their names current."""

import logging

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import EventType
from app.database import SessionLocal
from app.models import DiscordRoute
from app.services.activity_log import write_log
from app.services.route_cache import refresh_route_cache

logger = logging.getLogger("signalpeak.routes")


async def tracked_guild_ids(session: AsyncSession) -> set[int]:
    rows = await session.execute(select(DiscordRoute.source_server_id, DiscordRoute.destination_server_id))
    ids: set[int] = set()
    for source_id, destination_id in rows.all():
        ids.add(int(source_id))
        ids.add(int(destination_id))
    return ids


async def source_guild_ids(session: AsyncSession) -> set[int]:
    rows = await session.scalars(select(DiscordRoute.source_server_id).where(DiscordRoute.is_active.is_(True)))
    return {int(value) for value in rows.all()}


async def refresh_route_names(bot: discord.Client) -> int:
    """Update stored server and channel names from Discord for the ids already in the database."""

    updated = 0
    async with SessionLocal() as session:
        routes = list((await session.scalars(select(DiscordRoute))).all())
        cache: dict[int, tuple[str | None, dict[int, tuple[str, int | None, str | None]]]] = {}
        for route in routes:
            pairs = (
                ("source", route.source_server_id, route.source_channel_id),
                ("destination", route.destination_server_id, route.destination_channel_id),
            )
            for side, server_id, channel_id in pairs:
                server_name, channels = await _guild_snapshot(bot, int(server_id), cache)
                if server_name and getattr(route, f"{side}_server_name") != server_name:
                    setattr(route, f"{side}_server_name", server_name[:255])
                    updated += 1
                info = channels.get(int(channel_id))
                if info is None:
                    fetched = await _channel_info(bot, int(channel_id))
                    if fetched is not None:
                        info = fetched
                if info is None:
                    continue
                channel_name, category_id, category_name = info
                if channel_name and getattr(route, f"{side}_channel_name") != channel_name:
                    setattr(route, f"{side}_channel_name", channel_name[:255])
                    updated += 1
                if getattr(route, f"{side}_category_id") != category_id:
                    setattr(route, f"{side}_category_id", category_id)
                    updated += 1
                stored_category = _clip_name(category_name)
                if getattr(route, f"{side}_category_name") != stored_category:
                    setattr(route, f"{side}_category_name", stored_category)
                    updated += 1
        if updated:
            await write_log(
                session,
                event_type=EventType.NAMES_REFRESHED,
                detail=f"Updated {updated} server or channel name field(s) from Discord",
                extra={"fields": updated},
            )
        await session.commit()
    await refresh_route_cache()
    return updated


async def _guild_snapshot(
    bot: discord.Client,
    server_id: int,
    cache: dict[int, tuple[str | None, dict[int, tuple[str, int | None, str | None]]]],
) -> tuple[str | None, dict[int, tuple[str, int | None, str | None]]]:
    cached = cache.get(server_id)
    if cached is not None:
        return cached
    try:
        guild = bot.get_guild(server_id) or await bot.fetch_guild(server_id)
        channels = await guild.fetch_channels()
        snapshot = (guild.name, {channel.id: _channel_parts(channel) for channel in channels})
    except discord.HTTPException as exc:
        logger.warning("Could not refresh names for server %s: %s", server_id, exc)
        snapshot = (None, {})
    cache[server_id] = snapshot
    return snapshot


def _channel_parts(channel) -> tuple[str, int | None, str | None]:
    category = getattr(channel, "category", None)
    category_id = category.id if category is not None else None
    category_name = category.name if category is not None else None
    return str(channel.name), category_id, category_name


async def _channel_info(bot: discord.Client, channel_id: int) -> tuple[str, int | None, str | None] | None:
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    except discord.HTTPException:
        return None
    name = getattr(channel, "name", None)
    if not name:
        return None
    return _channel_parts(channel)


def _clip_name(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:255]
