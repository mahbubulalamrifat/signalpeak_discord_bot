"""Make the destination server match the source server, then rebuild channel pairs."""

import logging

import discord

from app.constants import EventType
from app.services.activity_log import write_log
from app.services.bot_client import bot
from app.services.channel_sync import ChannelSlot, channel_match_key, pair_channels
from app.services.guild_layout import member_only_overwrites
from app.services.route_cache import refresh_route_cache
from app.services.server_cache import refresh_server_pair, server_pair
from app.services.signalpeak_api import post_json

logger = logging.getLogger("signalpeak.structure_sync")


async def sync_destination_with_source() -> dict:
    """Create/delete destination categories and text channels so they match the source, then remap routes."""

    if not bot.is_ready():
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Bot is not connected")

    pair = server_pair() or await refresh_server_pair()
    if pair is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Source and destination server ids are not saved")

    source = bot.get_guild(pair.source_server_id) or await bot.fetch_guild(pair.source_server_id)
    destination = bot.get_guild(pair.destination_server_id) or await bot.fetch_guild(pair.destination_server_id)
    source_channels = await source.fetch_channels()
    destination_channels = await destination.fetch_channels()

    source_categories = {
        _key(category.name): category
        for category in source_channels
        if isinstance(category, discord.CategoryChannel)
    }
    destination_categories = {
        _key(category.name): category
        for category in destination_channels
        if isinstance(category, discord.CategoryChannel)
    }
    source_texts = [channel for channel in source_channels if isinstance(channel, discord.TextChannel)]
    destination_texts = [channel for channel in destination_channels if isinstance(channel, discord.TextChannel)]

    created_categories = 0
    for key, category in source_categories.items():
        if key in destination_categories:
            continue
        created = await destination.create_category(
            category.name,
            overwrites=member_only_overwrites(destination),
            reason="Synced from source server",
        )
        destination_categories[key] = created
        created_categories += 1

    created_channels = 0
    source_keys = {channel_match_key(_category_name(channel), channel.name) for channel in source_texts}
    destination_by_key = {
        channel_match_key(_category_name(channel), channel.name): channel for channel in destination_texts
    }
    private = member_only_overwrites(destination)

    for channel in source_texts:
        key = channel_match_key(_category_name(channel), channel.name)
        if key in destination_by_key:
            continue
        category = None
        if channel.category is not None:
            category = destination_categories.get(_key(channel.category.name))
        created = await destination.create_text_channel(
            channel.name,
            category=category,
            overwrites=private,
            reason="Synced from source server",
        )
        destination_by_key[key] = created
        created_channels += 1

    deleted_channels = 0
    for channel in list(destination_texts):
        key = channel_match_key(_category_name(channel), channel.name)
        if key in source_keys:
            continue
        await channel.delete(reason="Removed while syncing with source server")
        deleted_channels += 1

    destination_channels = await destination.fetch_channels()
    destination_categories = {
        _key(category.name): category
        for category in destination_channels
        if isinstance(category, discord.CategoryChannel)
    }
    source_category_keys = set(source_categories)
    deleted_categories = 0
    for key, category in list(destination_categories.items()):
        if key in source_category_keys:
            continue
        if any(isinstance(child, discord.TextChannel) for child in category.channels):
            continue
        await category.delete(reason="Removed while syncing with source server")
        deleted_categories += 1
        del destination_categories[key]

    # Rebuild after Discord mutations settle.
    source_channels = await source.fetch_channels()
    destination_channels = await destination.fetch_channels()
    source_slots = [_slot(channel) for channel in source_channels if isinstance(channel, discord.TextChannel)]
    destination_slots = [
        _slot(channel) for channel in destination_channels if isinstance(channel, discord.TextChannel)
    ]
    pairs, missing_destination, missing_source = pair_channels(source_slots, destination_slots)

    routes_payload = {
        "routes": [
            {
                "source_server_id": source.id,
                "source_server_name": source.name[:255],
                "source_category_id": item.source.category_id,
                "source_category_name": item.source.category_name,
                "source_channel_id": item.source.channel_id,
                "source_channel_name": item.source.channel_name[:255],
                "destination_server_id": destination.id,
                "destination_server_name": destination.name[:255],
                "destination_category_id": item.destination.category_id,
                "destination_category_name": item.destination.category_name,
                "destination_channel_id": item.destination.channel_id,
                "destination_channel_name": item.destination.channel_name[:255],
                "is_active": True,
            }
            for item in pairs
        ]
    }
    result = await post_json("/discord/routes/replace", routes_payload)
    await refresh_route_cache()

    summary = {
        "created_categories": created_categories,
        "created_channels": created_channels,
        "deleted_categories": deleted_categories,
        "deleted_channels": deleted_channels,
        "routes": result.get("created", len(pairs)),
        "source_only": len(missing_destination),
        "destination_only": len(missing_source),
        "server_id": str(destination.id),
        "server_name": destination.name,
    }
    await write_log(
        event_type=EventType.STRUCTURE_SYNCED,
        detail=(
            f"Synced destination {destination.name} with source {source.name}: "
            f"+{created_categories} categories, +{created_channels} channels, "
            f"-{deleted_categories} categories, -{deleted_channels} channels, "
            f"{summary['routes']} route pair(s)."
        ),
        source_server_id=source.id,
        source_server_name=source.name,
        destination_server_id=destination.id,
        destination_server_name=destination.name,
        extra=summary,
    )
    logger.info("Structure sync finished: %s", summary)
    return summary


def _key(name: str) -> str:
    return name.strip().casefold()


def _category_name(channel: discord.TextChannel) -> str | None:
    return channel.category.name if channel.category is not None else None


def _slot(channel: discord.TextChannel) -> ChannelSlot:
    category = channel.category
    return ChannelSlot(
        channel_id=channel.id,
        channel_name=channel.name,
        category_name=category.name if category is not None else None,
        category_id=category.id if category is not None else None,
    )
