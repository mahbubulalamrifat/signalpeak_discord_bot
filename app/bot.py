"""Discord gateway events. Importing this module registers the handlers."""

import logging

import discord

from app.constants import EventType
from app.database import SessionLocal
from app.services.activity_log import write_log
from app.services.bot_client import bot
from app.services.channel_sync import sync_channels_if_empty
from app.services.forwarder import handle_incoming_message
from app.services.members import process_pending_member_actions
from app.services.routes import refresh_route_names, tracked_guild_ids

logger = logging.getLogger("signalpeak.bot")


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")
    async with SessionLocal() as session:
        await write_log(
            session,
            event_type=EventType.BOT_READY,
            detail=f"Bot connected as {bot.user}",
            actor_id=bot.user.id if bot.user else None,
            actor_name=str(bot.user) if bot.user else None,
        )
        await session.commit()
    try:
        mapped = await sync_channels_if_empty(bot)
        logger.info("Initial channel mapping saved %s route(s)", mapped)
    except Exception:
        logger.exception("Could not map source channels to destination channels")
    try:
        updated = await refresh_route_names(bot)
        logger.info("Refreshed %s server/channel name field(s)", updated)
    except Exception:
        logger.exception("Could not refresh server and channel names")
    try:
        processed = await process_pending_member_actions(bot)
        logger.info("Processed %s pending member action(s)", processed)
    except Exception:
        logger.exception("Could not process pending member actions")


@bot.event
async def on_message(message: discord.Message) -> None:
    await handle_incoming_message(message)


@bot.event
async def on_member_join(member: discord.Member) -> None:
    if bot.user and member.id == bot.user.id:
        return
    await _log_guild_member(
        member.guild,
        EventType.MEMBER_JOINED,
        member.id,
        str(member),
        f"{member} joined {member.guild.name}",
    )


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    if bot.user and member.id == bot.user.id:
        return
    moderator_id, moderator_name = await _moderator(member.guild, discord.AuditLogAction.kick, member.id)
    detail = f"{member} left or was removed from {member.guild.name}"
    if moderator_name:
        detail = f"{member} was kicked from {member.guild.name} by {moderator_name}"
    await _log_guild_member(
        member.guild,
        EventType.MEMBER_REMOVED,
        moderator_id or member.id,
        moderator_name or str(member),
        detail,
        extra={"member_id": str(member.id), "member_name": str(member), "moderator_id": moderator_id, "moderator_name": moderator_name},
    )


@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User) -> None:
    moderator_id, moderator_name = await _moderator(guild, discord.AuditLogAction.ban, user.id)
    detail = f"{user} was banned from {guild.name}"
    if moderator_name:
        detail += f" by {moderator_name}"
    await _log_guild_member(
        guild,
        EventType.MEMBER_BANNED,
        moderator_id or user.id,
        moderator_name or str(user),
        detail,
        extra={"member_id": str(user.id), "member_name": str(user), "moderator_id": moderator_id, "moderator_name": moderator_name},
    )


@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    moderator_id, moderator_name = await _moderator(guild, discord.AuditLogAction.unban, user.id)
    detail = f"{user} was unbanned from {guild.name}"
    if moderator_name:
        detail += f" by {moderator_name}"
    await _log_guild_member(
        guild,
        EventType.MEMBER_UNBANNED,
        moderator_id or user.id,
        moderator_name or str(user),
        detail,
        extra={"member_id": str(user.id), "member_name": str(user), "moderator_id": moderator_id, "moderator_name": moderator_name},
    )


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    if payload.guild_id is None or not await _is_tracked(payload.guild_id):
        return

    cached = payload.cached_message
    author_id = cached.author.id if cached is not None else None
    author_name = str(cached.author) if cached is not None else None
    channel_name = getattr(cached.channel, "name", None) if cached is not None else None
    guild = bot.get_guild(payload.guild_id)
    deleter_id, deleter_name = (None, None)
    if guild is not None and author_id is not None:
        deleter_id, deleter_name = await _message_deleter(guild, payload.channel_id, author_id)

    detail = f"Message {payload.message_id} was deleted in channel {payload.channel_id}"
    if author_name:
        detail += f" (author {author_name})"
    if deleter_name:
        detail += f" by {deleter_name}"
    else:
        detail += ". Deleter was not available in the audit log."

    async with SessionLocal() as session:
        await write_log(
            session,
            event_type=EventType.MESSAGE_DELETED,
            detail=detail,
            actor_id=deleter_id or author_id,
            actor_name=deleter_name or author_name,
            source_server_id=payload.guild_id,
            source_server_name=guild.name if guild is not None else None,
            source_channel_id=payload.channel_id,
            source_channel_name=channel_name,
            message_id=payload.message_id,
            extra={
                "author_id": None if author_id is None else str(author_id),
                "author_name": author_name,
                "deleter_id": None if deleter_id is None else str(deleter_id),
                "deleter_name": deleter_name,
            },
        )
        await session.commit()


async def _log_guild_member(
    guild: discord.Guild,
    event_type: str,
    actor_id: int | None,
    actor_name: str | None,
    detail: str,
    extra: dict | None = None,
) -> None:
    if not await _is_tracked(guild.id):
        return
    async with SessionLocal() as session:
        await write_log(
            session,
            event_type=event_type,
            detail=detail,
            actor_id=actor_id,
            actor_name=actor_name,
            source_server_id=guild.id,
            source_server_name=guild.name,
            extra=extra,
        )
        await session.commit()


async def _is_tracked(guild_id: int) -> bool:
    async with SessionLocal() as session:
        return guild_id in await tracked_guild_ids(session)


async def _moderator(guild: discord.Guild, action: discord.AuditLogAction, target_id: int) -> tuple[int | None, str | None]:
    me = guild.me
    if me is None or not me.guild_permissions.view_audit_log:
        return None, None
    try:
        async for entry in guild.audit_logs(limit=6, action=action):
            target = entry.target
            if target is not None and getattr(target, "id", None) == target_id and entry.user is not None:
                return entry.user.id, str(entry.user)
    except (discord.Forbidden, discord.HTTPException):
        return None, None
    return None, None


async def _message_deleter(guild: discord.Guild, channel_id: int, author_id: int) -> tuple[int | None, str | None]:
    me = guild.me
    if me is None or not me.guild_permissions.view_audit_log:
        return None, None
    try:
        async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.message_delete):
            target = entry.target
            channel = getattr(entry.extra, "channel", None)
            same_author = target is not None and getattr(target, "id", None) == author_id
            same_channel = channel is not None and getattr(channel, "id", None) == channel_id
            if same_author and same_channel and entry.user is not None:
                return entry.user.id, str(entry.user)
    except (discord.Forbidden, discord.HTTPException):
        return None, None
    return None, None
