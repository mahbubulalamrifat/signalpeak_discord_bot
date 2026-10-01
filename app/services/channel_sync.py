"""Pair every source text channel with the destination channel of the same name.

The destination server is a template copy of the source, so the category name plus the
channel name is the match key. This runs once, while signalpeak_discord is empty.
"""

from dataclasses import dataclass
import logging

import discord

from app.config import get_settings
from app.constants import EventType
from app.services.activity_log import write_log
from app.services.route_cache import refresh_route_cache
from app.services.signalpeak_api import api_configured, get_json, post_json

logger = logging.getLogger("signalpeak.channels")


@dataclass(frozen=True)
class ChannelSlot:
    channel_id: int
    channel_name: str
    category_name: str | None
    category_id: int | None = None


@dataclass(frozen=True)
class ChannelPair:
    source: ChannelSlot
    destination: ChannelSlot


def channel_match_key(category_name: str | None, channel_name: str) -> str:
    category = (category_name or "").strip().casefold()
    name = channel_name.strip().casefold()
    return f"{category}\n{name}"


def pair_channels(source: list[ChannelSlot], destination: list[ChannelSlot]) -> tuple[list[ChannelPair], list[ChannelSlot], list[ChannelSlot]]:
    """Match channels that share a category name and a channel name."""

    destination_by_key: dict[str, ChannelSlot] = {}
    for slot in destination:
        destination_by_key.setdefault(channel_match_key(slot.category_name, slot.channel_name), slot)

    pairs: list[ChannelPair] = []
    unmatched_source: list[ChannelSlot] = []
    used: set[str] = set()
    for slot in source:
        key = channel_match_key(slot.category_name, slot.channel_name)
        match = destination_by_key.get(key)
        if match is None:
            unmatched_source.append(slot)
            continue
        pairs.append(ChannelPair(source=slot, destination=match))
        used.add(key)

    unmatched_destination = [
        slot
        for slot in destination
        if channel_match_key(slot.category_name, slot.channel_name) not in used
    ]
    return pairs, unmatched_source, unmatched_destination


async def sync_channels_if_empty(client: discord.Client) -> int:
    """Load both servers and insert one route per matching text channel. Skip when rows already exist."""

    settings = get_settings()
    source_id = settings.source_server_snowflake
    destination_id = settings.destination_server_snowflake
    if source_id is None or destination_id is None:
        logger.error("Set SOURCE_SERVER_ID and DESTINATION_SERVER_ID before the first channel mapping.")
        return 0

    if await _pairs_already_saved():
        return 0

    try:
        source_guild, source_slots = await _text_channels(client, source_id)
        destination_guild, destination_slots = await _text_channels(client, destination_id)
    except discord.HTTPException as exc:
        detail = (
            f"Could not read channels for source {source_id} or destination {destination_id}. "
            f"The bot must be a member of both servers. Discord said: {exc}"
        )
        logger.error(detail)
        await write_log(event_type=EventType.CHANNELS_MAPPED, level="error", detail=detail)
        return 0

    pairs, missing_on_destination, missing_on_source = pair_channels(source_slots, destination_slots)
    saved_through_api = await _save_pairs_through_api(source_guild, destination_guild, pairs)
    if not saved_through_api:
        logger.error("Channel pairs were not saved. SIGNALPEAK_API_URL must point at the SignalPeak API.")
        return 0

    detail = (
        f"Mapped {len(pairs)} text channel(s) from {source_guild.name} to {destination_guild.name}. "
        f"{len(missing_on_destination)} source channel(s) had no destination match. "
        f"{len(missing_on_source)} destination channel(s) had no source match."
    )
    await write_log(
            event_type=EventType.CHANNELS_MAPPED,
            detail=detail,
            source_server_id=source_guild.id,
            source_server_name=source_guild.name,
            destination_server_id=destination_guild.id,
            destination_server_name=destination_guild.name,
            extra={
                "mapped": len(pairs),
                "saved_through_api": saved_through_api,
                "source_only": [_label(slot) for slot in missing_on_destination[:50]],
                "destination_only": [_label(slot) for slot in missing_on_source[:50]],
            },
        )
    await refresh_route_cache()
    logger.info(detail)
    return len(pairs)


async def _pairs_already_saved() -> bool:
    if api_configured():
        try:
            payload = await get_json("/discord/routes")
            count = len(payload.get("routes", []))
            if count:
                logger.info("The API already has %s channel pair(s). Mapping was not repeated.", count)
                return True
            return False
        except Exception:
            logger.exception("Could not ask the API for existing channel pairs.")
            return False
    logger.error("SIGNALPEAK_API_URL is empty, so existing channel pairs could not be checked.")
    return False


async def _save_pairs_through_api(source_guild: discord.Guild, destination_guild: discord.Guild, pairs: list[ChannelPair]) -> bool:
    if not api_configured():
        return False
    payload = {
        "routes": [
            {
                "source_server_id": source_guild.id,
                "source_server_name": source_guild.name[:255],
                "source_category_id": pair.source.category_id,
                "source_category_name": _clip(pair.source.category_name),
                "source_channel_id": pair.source.channel_id,
                "source_channel_name": pair.source.channel_name[:255],
                "destination_server_id": destination_guild.id,
                "destination_server_name": destination_guild.name[:255],
                "destination_category_id": pair.destination.category_id,
                "destination_category_name": _clip(pair.destination.category_name),
                "destination_channel_id": pair.destination.channel_id,
                "destination_channel_name": pair.destination.channel_name[:255],
                "is_active": True,
            }
            for pair in pairs
        ]
    }
    try:
        result = await post_json("/discord/routes/sync", payload)
    except Exception:
        return False
    logger.info("API stored %s new channel pair(s)", result.get("created", 0))
    return True


async def _text_channels(client: discord.Client, server_id: int) -> tuple[discord.Guild, list[ChannelSlot]]:
    guild = client.get_guild(server_id) or await client.fetch_guild(server_id)
    channels = await guild.fetch_channels()
    slots = [_slot(channel) for channel in channels]
    return guild, [slot for slot in slots if slot is not None]


def _slot(channel: discord.abc.GuildChannel) -> ChannelSlot | None:
    if not isinstance(channel, discord.TextChannel):
        return None
    category = channel.category
    return ChannelSlot(
        channel_id=channel.id,
        channel_name=channel.name,
        category_name=category.name if category is not None else None,
        category_id=category.id if category is not None else None,
    )


def _clip(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:255]


def _label(slot: ChannelSlot) -> str:
    if slot.category_name:
        return f"{slot.category_name} / {slot.channel_name}"
    return slot.channel_name
