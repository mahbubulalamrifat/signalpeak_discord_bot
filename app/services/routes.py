"""Keep stored server and channel names current through the SignalPeak API."""

import logging

import discord

from app.constants import EventType
from app.services.activity_log import write_log
from app.services.route_cache import all_routes, refresh_route_cache
from app.services.signalpeak_api import patch_json

logger = logging.getLogger("signalpeak.routes")


async def refresh_route_names(bot: discord.Client) -> int:
    """Update stored server and channel names from Discord only when they changed."""

    updated = 0
    patched = 0
    snapshot_cache: dict[int, tuple[str | None, dict[int, tuple[str, int | None, str | None]]]] = {}
    for route in await all_routes():
        changes: dict[str, str | int | None] = {}
        sides = (
            (
                "source",
                route.source_server_id,
                route.source_channel_id,
                route.source_server_name,
                route.source_channel_name,
                route.source_category_id,
                route.source_category_name,
            ),
            (
                "destination",
                route.destination_server_id,
                route.destination_channel_id,
                route.destination_server_name,
                route.destination_channel_name,
                route.destination_category_id,
                route.destination_category_name,
            ),
        )
        for side, server_id, channel_id, server_name_now, channel_name_now, category_id_now, category_name_now in sides:
            server_name, channels = await _guild_snapshot(bot, int(server_id), snapshot_cache)
            if server_name and server_name_now != server_name:
                changes[f"{side}_server_name"] = server_name[:255]
                updated += 1
            info = channels.get(int(channel_id))
            if info is None:
                info = await _channel_info(bot, int(channel_id))
            if info is None:
                continue
            channel_name, category_id, category_name = info
            category_name = _clip_name(category_name)
            if channel_name and channel_name_now != channel_name:
                changes[f"{side}_channel_name"] = channel_name[:255]
                updated += 1
            if category_id_now != category_id:
                changes[f"{side}_category_id"] = category_id
                updated += 1
            if (category_name_now or None) != (category_name or None):
                changes[f"{side}_category_name"] = category_name
                updated += 1
        if changes:
            await patch_json(f"/discord/routes/{route.id}", changes)
            patched += 1
    if patched:
        await write_log(
            event_type=EventType.NAMES_REFRESHED,
            detail=f"Updated {updated} server or channel name field(s) from Discord",
            extra={"fields": updated, "routes": patched},
        )
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
