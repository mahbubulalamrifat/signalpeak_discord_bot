"""Forward a source-channel message to each destination stored in signalpeak_discord."""

import logging
from datetime import datetime, timezone

import discord
from sqlalchemy import select, update

from app.config import get_settings
from app.constants import EventType
from app.database import SessionLocal
from app.models import DiscordRoute, MessageLink
from app.services.activity_log import write_log
from app.services.bot_client import bot
from app.services.replacements import apply_replacements, rewrite_channel_mentions
from app.services.replace_cache import get_replace_rules
from app.services.route_cache import CachedRoute, get_channel_map, get_routes_for_source
from app.services.routes import source_guild_ids
from app.services.text import split_discord_content

logger = logging.getLogger("signalpeak.forwarder")
settings = get_settings()

_MENTIONS = discord.AllowedMentions.none()
_FILES_PER_MESSAGE = 10


async def handle_incoming_message(message: discord.Message) -> None:
    """Forward one message when its channel id is an active source in the database."""

    if message.guild is None:
        return
    if bot.user is not None and message.author.id == bot.user.id:
        return

    async with SessionLocal() as session:
        routes = await get_routes_for_source(message.channel.id)
        if not routes:
            await _log_unmatched(session, message)
            return

        await write_log(
            session,
            event_type=EventType.MESSAGE_RECEIVED,
            detail=f"Message {message.id} from {message.author} in #{message.channel}",
            actor_id=message.author.id,
            actor_name=str(message.author),
            source_server_id=message.guild.id,
            source_server_name=message.guild.name,
            source_channel_id=message.channel.id,
            source_channel_name=getattr(message.channel, "name", None),
            message_id=message.id,
            extra=_message_snapshot(message),
        )

        for route in routes:
            try:
                await _forward_one(session, message, route)
            except Exception as exc:
                logger.exception("Forward failed for route %s", route.id)
                await write_log(
                    session,
                    event_type=EventType.FORWARD_FAILED,
                    level="error",
                    detail=f"Route {route.id} failed: {exc}",
                    actor_id=message.author.id,
                    actor_name=str(message.author),
                    message_id=message.id,
                    extra={"error": str(exc)},
                    **_route_fields(route),
                )
        try:
            await session.commit()
        except Exception:
            logger.exception("Could not save forward logs for message %s", message.id)
            await session.rollback()


async def _log_unmatched(session, message: discord.Message) -> None:
    if not settings.log_unmatched_messages or message.guild is None:
        return
    guilds = await source_guild_ids(session)
    if message.guild.id not in guilds:
        return
    await write_log(
        session,
        event_type=EventType.MESSAGE_SKIPPED,
        detail=f"Channel {message.channel.id} is not a source route, so the message was not forwarded",
        actor_id=message.author.id,
        actor_name=str(message.author),
        source_server_id=message.guild.id,
        source_server_name=message.guild.name,
        source_channel_id=message.channel.id,
        source_channel_name=getattr(message.channel, "name", None),
        message_id=message.id,
    )
    await session.commit()


async def _forward_one(session, message: discord.Message, route: CachedRoute) -> None:
    if route.source_channel_id == route.destination_channel_id:
        await write_log(
            session,
            event_type=EventType.FORWARD_FAILED,
            level="error",
            detail="Source and destination channel ids are the same, so the message was not forwarded",
            actor_id=message.author.id,
            actor_name=str(message.author),
            message_id=message.id,
            **_route_fields(route),
        )
        return

    rules = list(await get_replace_rules())
    channel_map = await get_channel_map(route.destination_server_id)
    source_text = rewrite_channel_mentions(message.content or "", channel_map)
    replaced = apply_replacements(source_text, rules)
    if replaced.changes or replaced.errors:
        await write_log(
            session,
            event_type=EventType.REPLACEMENT_APPLIED,
            level="warning" if replaced.errors else "info",
            detail=_replacement_detail(replaced),
            actor_id=message.author.id,
            actor_name=str(message.author),
            message_id=message.id,
            extra={
                "changes": [
                    {
                        "rule_id": change.rule_id,
                        "search_key": change.search_key,
                        "replace_value": change.replace_value,
                        "matches": change.matches,
                    }
                    for change in replaced.changes
                ],
                "errors": replaced.errors,
                "content_before": _snippet(source_text),
                "content_after": _snippet(replaced.text),
            },
            **_route_fields(route),
        )

    cleaned_parts = split_discord_content(replaced.text)
    embeds = _copy_embeds(message, channel_map)
    sticker_names = [sticker.name for sticker in message.stickers]
    if not cleaned_parts and not message.attachments and not embeds:
        detail = "Nothing left to forward after replacement"
        if sticker_names:
            detail = "Message only contained stickers. Stickers cannot be copied to another server."
        await write_log(
            session,
            event_type=EventType.MESSAGE_SKIPPED,
            detail=detail,
            actor_id=message.author.id,
            actor_name=str(message.author),
            message_id=message.id,
            extra={"stickers": sticker_names},
            **_route_fields(route),
        )
        return

    channel = await _destination_channel(route.destination_channel_id)
    reply_to = await _destination_reply(session, message, route, channel_map)
    sent, attachment_errors = await _deliver(channel, cleaned_parts, list(message.attachments), embeds, reply_to)
    if sent:
        session.add(
            MessageLink(
                source_message_id=message.id,
                source_channel_id=message.channel.id,
                destination_message_id=sent[0].id,
                destination_channel_id=route.destination_channel_id,
                destination_server_id=route.destination_server_id,
            )
        )
    now = datetime.now(timezone.utc)
    await session.execute(
        update(DiscordRoute)
        .where(DiscordRoute.id == route.id)
        .values(last_message_id=message.id, last_message_at=message.created_at, updated_at=now)
    )

    detail = f"Forwarded message {message.id} to #{route.destination_channel_name} ({len(sent)} Discord message(s))"
    if attachment_errors:
        detail += f". Attachment upload failed: {attachment_errors[0]}"
    await write_log(
        session,
        event_type=EventType.MESSAGE_FORWARDED,
        level="warning" if attachment_errors else "info",
        detail=detail,
        actor_id=message.author.id,
        actor_name=str(message.author),
        message_id=message.id,
        extra={
            "destination_message_ids": [item.id for item in sent],
            "attachments": [
                {"filename": item.filename, "content_type": item.content_type, "size": item.size}
                for item in message.attachments
            ],
            "attachment_errors": attachment_errors,
            "embed_count": len(embeds),
            "stickers_not_copied": sticker_names,
        },
        **_route_fields(route),
    )
    await write_log(
        session,
        event_type=EventType.ROUTE_UPDATED,
        detail=f"Saved last_message_at {message.created_at.isoformat()} for route {route.id}",
        actor_id=message.author.id,
        actor_name=str(message.author),
        message_id=message.id,
        extra={"last_message_at": message.created_at.isoformat(), "last_message_id": str(message.id)},
        **_route_fields(route),
    )


async def _destination_channel(channel_id: int) -> discord.abc.Messageable:
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise RuntimeError(f"Channel {channel_id} cannot receive messages")
    return channel


async def _deliver(
    channel,
    parts: list[str],
    attachments: list[discord.Attachment],
    embeds: list[discord.Embed],
    reference: discord.MessageReference | None = None,
) -> tuple[list[discord.Message], list[str]]:
    """Send text first, then files. A failed image does not drop text that already went out."""

    sent: list[discord.Message] = []
    errors: list[str] = []
    reply = reference
    for index, part in enumerate(parts):
        message = await _send(channel, part, [], embeds if index == 0 else [], reply)
        reply = None
        if message is not None:
            sent.append(message)

    if not parts and embeds and not attachments:
        message = await _send(channel, None, [], embeds, reply)
        reply = None
        if message is not None:
            sent.append(message)

    batches = [attachments[index : index + _FILES_PER_MESSAGE] for index in range(0, len(attachments), _FILES_PER_MESSAGE)]
    for index, batch in enumerate(batches):
        embed_payload = embeds if not parts and index == 0 else []
        try:
            message = await _send(channel, None, batch, embed_payload, reply)
            reply = None
        except Exception as exc:
            if not sent:
                raise
            errors.append(str(exc))
            logger.exception("Attachment batch failed after text was already forwarded")
            break
        if message is not None:
            sent.append(message)

    if not sent:
        raise RuntimeError("Discord did not accept the forwarded message")
    return sent, errors


async def _send(
    channel,
    content: str | None,
    attachments: list[discord.Attachment],
    embeds: list[discord.Embed],
    reference: discord.MessageReference | None = None,
) -> discord.Message | None:
    if not content and not attachments and not embeds and reference is None:
        return None
    try:
        return await channel.send(**_payload(content, await _files(attachments), embeds, reference))
    except discord.HTTPException:
        if not embeds:
            raise
        logger.warning("Discord rejected the message with embeds; retrying without embeds")
        return await channel.send(**_payload(content, await _files(attachments), [], reference))


def _payload(
    content: str | None,
    files: list[discord.File],
    embeds: list[discord.Embed],
    reference: discord.MessageReference | None = None,
) -> dict:
    payload: dict = {"allowed_mentions": _MENTIONS}
    if content:
        payload["content"] = content
    if files:
        payload["files"] = files
    if embeds:
        payload["embeds"] = embeds[:10]
    if reference is not None:
        payload["reference"] = reference
    return payload


async def _files(attachments: list[discord.Attachment]) -> list[discord.File]:
    return [await attachment.to_file() for attachment in attachments]


def _copy_embeds(message: discord.Message, channel_map: dict[int, int]) -> list[discord.Embed]:
    copies: list[discord.Embed] = []
    for embed in message.embeds[:10]:
        data = embed.to_dict()
        data.pop("type", None)
        for field in ("title", "description", "url"):
            if isinstance(data.get(field), str):
                data[field] = rewrite_channel_mentions(data[field], channel_map)
        for row in data.get("fields") or []:
            for field in ("name", "value"):
                if isinstance(row.get(field), str):
                    row[field] = rewrite_channel_mentions(row[field], channel_map)
        try:
            copies.append(discord.Embed.from_dict(data))
        except (TypeError, ValueError):
            continue
    return copies


async def _destination_reply(session, message: discord.Message, route: CachedRoute, channel_map: dict[int, int]) -> discord.MessageReference | None:
    reference = message.reference
    if reference is None or reference.message_id is None:
        return None
    destination_channel_id = channel_map.get(int(reference.channel_id)) if reference.channel_id else None
    query = select(MessageLink).where(
        MessageLink.source_message_id == int(reference.message_id),
        MessageLink.destination_server_id == route.destination_server_id,
    )
    if destination_channel_id is not None:
        query = query.where(MessageLink.destination_channel_id == destination_channel_id)
    link = await session.scalar(query)
    if link is None:
        return None
    return discord.MessageReference(
        message_id=int(link.destination_message_id),
        channel_id=int(link.destination_channel_id),
        guild_id=int(route.destination_server_id),
        fail_if_not_exists=False,
    )


def _route_fields(route: CachedRoute) -> dict:
    return {
        "source_server_id": route.source_server_id,
        "source_server_name": route.source_server_name,
        "source_channel_id": route.source_channel_id,
        "source_channel_name": route.source_channel_name,
        "destination_server_id": route.destination_server_id,
        "destination_server_name": route.destination_server_name,
        "destination_channel_id": route.destination_channel_id,
        "destination_channel_name": route.destination_channel_name,
    }


def _message_snapshot(message: discord.Message) -> dict:
    return {
        "content": _snippet(message.content or ""),
        "attachment_count": len(message.attachments),
        "embed_count": len(message.embeds),
        "sticker_count": len(message.stickers),
    }


def _replacement_detail(result) -> str:
    parts = [f"{change.matches} match(es) for {change.search_key!r}" for change in result.changes]
    if result.errors:
        parts.extend(result.errors)
    return "; ".join(parts) if parts else "Replacement rules ran"


def _snippet(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
