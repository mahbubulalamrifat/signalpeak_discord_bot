"""Create, rename, move, and order categories and text channels on a Discord server."""

import logging

import discord
from fastapi import HTTPException

from app.config import get_settings
from app.constants import EventType
from app.services.activity_log import write_log
from app.services.bot_client import bot
from app.services.route_cache import all_routes, refresh_route_cache
from app.services.server_cache import server_pair
from app.services.signalpeak_api import delete_json, patch_json

logger = logging.getLogger("signalpeak.layout")


def require_connected_bot() -> None:
    if not bot.is_ready():
        raise HTTPException(status_code=503, detail="Bot is not connected")


def member_only_overwrites(guild: discord.Guild) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    """Deny @everyone. Allow the Member role (APPROVAL_ROLE_ID) and the bot."""

    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    role_id = get_settings().approval_role_snowflake
    if role_id is not None:
        role = guild.get_role(role_id)
        if role is not None:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                read_message_history=True,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                add_reactions=True,
                use_application_commands=True,
            )
        else:
            logger.warning("APPROVAL_ROLE_ID %s was not found in %s", role_id, guild.id)
    else:
        logger.warning("APPROVAL_ROLE_ID is empty, so new channels are hidden from everyone including Member.")

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            manage_channels=True,
            manage_messages=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
        )
    return overwrites


async def load_guild(server_id: int | None) -> discord.Guild:
    require_connected_bot()
    pair = server_pair()
    target = server_id or (pair.destination_server_id if pair else None)
    if target is None:
        raise HTTPException(status_code=400, detail="server_id is required")
    try:
        guild = bot.get_guild(target) or await bot.fetch_guild(target)
    except discord.NotFound as exc:
        raise HTTPException(status_code=404, detail="Server was not found") from exc
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot view that server") from exc
    return guild


def channel_payload(channel: discord.abc.GuildChannel) -> dict:
    category = getattr(channel, "category", None)
    return {
        "id": str(channel.id),
        "name": channel.name,
        "position": channel.position,
        "type": str(getattr(channel.type, "name", channel.type)),
        "category_id": None if category is None else str(category.id),
        "category_name": None if category is None else category.name,
    }


async def lock_all_member_only(guild: discord.Guild) -> dict:
    """One-time: set every category and text channel so only the Member role can view."""

    role_id = get_settings().approval_role_snowflake
    if role_id is None:
        raise HTTPException(status_code=400, detail="Set APPROVAL_ROLE_ID in the bot .env first")
    if guild.get_role(role_id) is None:
        raise HTTPException(status_code=400, detail=f"Member role {role_id} was not found in this server")

    overwrites = member_only_overwrites(guild)
    channels = await guild.fetch_channels()
    updated_categories = 0
    updated_channels = 0
    failed: list[dict] = []

    for channel in channels:
        if not isinstance(channel, (discord.CategoryChannel, discord.TextChannel)):
            continue
        try:
            await channel.edit(overwrites=overwrites, reason="Lock existing channels for Member role only")
        except discord.Forbidden:
            failed.append({"id": str(channel.id), "name": channel.name, "error": "forbidden"})
            continue
        except discord.HTTPException as exc:
            failed.append({"id": str(channel.id), "name": channel.name, "error": str(exc)})
            continue
        if isinstance(channel, discord.CategoryChannel):
            updated_categories += 1
        else:
            updated_channels += 1

    await _log_layout(
        guild,
        EventType.PERMISSIONS_LOCKED,
        f"Locked {updated_categories} categories and {updated_channels} channels for Member only",
    )
    return {
        "updated_categories": updated_categories,
        "updated_channels": updated_channels,
        "failed": failed,
        "member_role_id": str(role_id),
    }


async def list_structure(guild: discord.Guild) -> dict:
    channels = await guild.fetch_channels()
    categories = [channel for channel in channels if isinstance(channel, discord.CategoryChannel)]
    texts = [channel for channel in channels if isinstance(channel, discord.TextChannel)]
    categories.sort(key=lambda item: item.position)
    grouped: dict[int, list[discord.TextChannel]] = {category.id: [] for category in categories}
    loose: list[discord.TextChannel] = []
    for channel in texts:
        if channel.category_id in grouped:
            grouped[channel.category_id].append(channel)
        else:
            loose.append(channel)
    return {
        "server_id": str(guild.id),
        "server_name": guild.name,
        "categories": [
            {
                "id": str(category.id),
                "name": category.name,
                "position": category.position,
                "channels": [channel_payload(channel) for channel in sorted(grouped[category.id], key=lambda item: item.position)],
            }
            for category in categories
        ],
        "uncategorized": [channel_payload(channel) for channel in sorted(loose, key=lambda item: item.position)],
    }


async def create_category(guild: discord.Guild, name: str, position: int | None) -> discord.CategoryChannel:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Name cannot be blank")
    kwargs: dict = {
        "reason": "Created from SignalPeak",
        "overwrites": member_only_overwrites(guild),
    }
    if position is not None:
        kwargs["position"] = position
    try:
        category = await guild.create_category(cleaned, **kwargs)
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot create categories. It needs Manage Channels.") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _log_layout(guild, EventType.CATEGORY_CREATED, f"Created category {category.name}", category_id=category.id, category_name=category.name)
    return category


async def rename_category(guild: discord.Guild, category_id: int, name: str) -> discord.CategoryChannel:
    category = await _category(guild, category_id)
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Name cannot be blank")
    try:
        await category.edit(name=cleaned, reason="Renamed from SignalPeak")
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot rename that category") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _remember_category_name(guild.id, category.id, category.name)
    await refresh_route_cache()
    await _log_layout(guild, EventType.CATEGORY_RENAMED, f"Renamed category to {category.name}", category_id=category.id, category_name=category.name)
    return category


async def create_text_channel(guild: discord.Guild, name: str, category_id: int, position: int | None) -> discord.TextChannel:
    category = await _category(guild, category_id)
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Name cannot be blank")
    kwargs: dict = {
        "category": category,
        "reason": "Created from SignalPeak",
        "overwrites": member_only_overwrites(guild),
    }
    if position is not None:
        kwargs["position"] = position
    try:
        channel = await guild.create_text_channel(cleaned, **kwargs)
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot create channels. It needs Manage Channels.") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _remember_channel(guild.id, channel)
    await refresh_route_cache()
    await _log_layout(
        guild,
        EventType.CHANNEL_CREATED,
        f"Created #{channel.name} in {category.name}",
        channel_id=channel.id,
        channel_name=channel.name,
        category_id=category.id,
        category_name=category.name,
    )
    return channel


async def rename_channel(guild: discord.Guild, channel_id: int, name: str) -> discord.TextChannel:
    channel = await _text_channel(guild, channel_id)
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Name cannot be blank")
    try:
        await channel.edit(name=cleaned, reason="Renamed from SignalPeak")
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot rename that channel") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _remember_channel(guild.id, channel)
    await refresh_route_cache()
    await _log_layout(guild, EventType.CHANNEL_RENAMED, f"Renamed channel to #{channel.name}", channel_id=channel.id, channel_name=channel.name)
    return channel


async def move_channel(guild: discord.Guild, channel_id: int, category_id: int | None, position: int | None) -> discord.TextChannel:
    channel = await _text_channel(guild, channel_id)
    category = None if category_id is None else await _category(guild, category_id)
    kwargs: dict = {"category": category, "reason": "Moved from SignalPeak"}
    if position is not None:
        kwargs["position"] = position
    try:
        await channel.edit(**kwargs)
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot move that channel") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _remember_channel(guild.id, channel)
    await refresh_route_cache()
    place = category.name if category is not None else "no category"
    await _log_layout(guild, EventType.CHANNEL_MOVED, f"Moved #{channel.name} to {place}", channel_id=channel.id, channel_name=channel.name)
    return channel


async def delete_text_channel(guild: discord.Guild, channel_id: int) -> dict:
    channel = await _text_channel(guild, channel_id)
    payload = channel_payload(channel)
    try:
        await channel.delete(reason="Deleted from SignalPeak")
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot delete that channel") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _forget_destination_channel(channel_id)
    await refresh_route_cache()
    await _log_layout(
        guild,
        EventType.CHANNEL_DELETED,
        f"Deleted #{payload['name']}",
        channel_id=channel_id,
        channel_name=payload["name"],
    )
    return {"deleted": True, **payload}


async def delete_category(guild: discord.Guild, category_id: int) -> dict:
    category = await _category(guild, category_id)
    name = category.name
    channels = [channel for channel in category.channels if isinstance(channel, discord.TextChannel)]
    deleted_channels: list[dict] = []
    for channel in channels:
        deleted_channels.append(await delete_text_channel(guild, channel.id))
    try:
        await category.delete(reason="Deleted from SignalPeak")
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot delete that category") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _log_layout(
        guild,
        EventType.CATEGORY_DELETED,
        f"Deleted category {name}",
        category_id=category_id,
        category_name=name,
    )
    return {"deleted": True, "id": str(category_id), "name": name, "channels": deleted_channels}


async def _forget_destination_channel(channel_id: int) -> None:
    for route in await all_routes():
        if route.destination_channel_id == channel_id or route.source_channel_id == channel_id:
            try:
                await delete_json(f"/discord/routes/{route.id}")
            except Exception:
                logger.exception("Could not remove route %s after channel delete", route.id)


async def order_channels(guild: discord.Guild, items: list[dict]) -> None:
    payload = []
    for item in items:
        row: dict = {"id": int(item["id"]), "position": int(item["position"])}
        if "parent_id" in item:
            row["parent_id"] = None if item["parent_id"] is None else int(item["parent_id"])
        payload.append(row)
    try:
        await bot.http.bulk_channel_update(guild.id, payload, reason="Ordered from SignalPeak")
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot reorder channels") from exc
    except discord.HTTPException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    fresh = await guild.fetch_channels()
    by_id = {channel.id: channel for channel in fresh}
    for item in items:
        channel = by_id.get(int(item["id"]))
        if isinstance(channel, discord.TextChannel):
            await _remember_channel(guild.id, channel)
        elif isinstance(channel, discord.CategoryChannel):
            await _remember_category_name(guild.id, channel.id, channel.name)
    await refresh_route_cache()
    await _log_layout(guild, EventType.CHANNELS_ORDERED, f"Updated the order of {len(items)} channel(s) or category(ies)")


async def _category(guild: discord.Guild, category_id: int) -> discord.CategoryChannel:
    channel = guild.get_channel(category_id) or await _fetch(category_id)
    if not isinstance(channel, discord.CategoryChannel) or channel.guild.id != guild.id:
        raise HTTPException(status_code=404, detail="Category was not found on that server")
    return channel


async def _text_channel(guild: discord.Guild, channel_id: int) -> discord.TextChannel:
    channel = guild.get_channel(channel_id) or await _fetch(channel_id)
    if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
        raise HTTPException(status_code=404, detail="Text channel was not found on that server")
    return channel


async def _fetch(channel_id: int):
    try:
        return await bot.fetch_channel(channel_id)
    except discord.NotFound as exc:
        raise HTTPException(status_code=404, detail="Channel was not found") from exc
    except discord.Forbidden as exc:
        raise HTTPException(status_code=403, detail="Bot cannot view that channel") from exc


async def _remember_channel(server_id: int, channel: discord.TextChannel) -> None:
    category = channel.category
    category_id = None if category is None else category.id
    category_name = None if category is None else category.name[:255]
    for route in await all_routes():
        if route.source_server_id == server_id and route.source_channel_id == channel.id:
            await patch_json(
                f"/discord/routes/{route.id}",
                {
                    "source_channel_name": channel.name[:255],
                    "source_category_id": category_id,
                    "source_category_name": category_name,
                },
            )
        if route.destination_server_id == server_id and route.destination_channel_id == channel.id:
            await patch_json(
                f"/discord/routes/{route.id}",
                {
                    "destination_channel_name": channel.name[:255],
                    "destination_category_id": category_id,
                    "destination_category_name": category_name,
                },
            )


async def _remember_category_name(server_id: int, category_id: int, name: str) -> None:
    stored = name[:255]
    for route in await all_routes():
        if route.source_server_id == server_id:
            await patch_json(f"/discord/routes/{route.id}", {"source_category_name": stored})
        if route.destination_server_id == server_id:
            await patch_json(f"/discord/routes/{route.id}", {"destination_category_name": stored})


async def _log_layout(
    guild: discord.Guild,
    event_type: str,
    detail: str,
    *,
    channel_id: int | None = None,
    channel_name: str | None = None,
    category_id: int | None = None,
    category_name: str | None = None,
) -> None:
    pair = server_pair()
    fields: dict = {
        "event_type": event_type,
        "detail": detail,
        "extra": {
            "category_id": None if category_id is None else str(category_id),
            "category_name": category_name,
            "channel_id": None if channel_id is None else str(channel_id),
            "channel_name": channel_name,
        },
    }
    if pair is not None and pair.destination_server_id == guild.id:
        fields["destination_server_id"] = guild.id
        fields["destination_server_name"] = guild.name
        fields["destination_channel_id"] = channel_id
        fields["destination_channel_name"] = channel_name
    else:
        fields["source_server_id"] = guild.id
        fields["source_server_name"] = guild.name
        fields["source_channel_id"] = channel_id
        fields["source_channel_name"] = channel_name
    await write_log(**fields)
