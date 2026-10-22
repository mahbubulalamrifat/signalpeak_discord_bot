"""Forward a source-channel message to each destination stored in signalpeak_discord."""

import asyncio
import logging
import mimetypes
import os
from pathlib import Path
import tempfile

import discord

from app.config import get_settings
from app.constants import EventType
from app.services.activity_log import write_log
from app.services.bot_client import bot
from app.services.image_process import manipulate_image
from app.services.replacements import apply_replacements, rewrite_channel_mentions
from app.services.replace_cache import get_replace_rules
from app.services.route_cache import CachedRoute, get_channel_map, get_routes_for_source, tracked_server_ids
from app.services.signalpeak_api import get_json, patch_json, post_json
from app.services.text import split_discord_content

logger = logging.getLogger("signalpeak.forwarder")
settings = get_settings()

_MENTIONS = discord.AllowedMentions.none()
_FILES_PER_MESSAGE = 10
_thread_locks: dict[int, asyncio.Lock] = {}


async def handle_incoming_message(message: discord.Message) -> None:
    """Forward one message when its channel id is an active source in the database."""

    if message.guild is None:
        return
    if bot.user is not None and message.author.id == bot.user.id:
        return

    route_channel_id = message.channel.id
    if isinstance(message.channel, discord.Thread):
        parent = await _forum_parent(message.channel)
        if parent is None:
            return
        route_channel_id = parent.id
        try:
            if message.channel.me is None:
                await message.channel.join()
        except discord.HTTPException:
            logger.warning("Could not join forum thread %s", message.channel.id)

    routes = await get_routes_for_source(route_channel_id)
    if not routes:
        await _log_unmatched(message)
        return

    await write_log(
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
            await _forward_one(message, route)
        except Exception as exc:
            logger.exception("Forward failed for route %s", route.id)
            await write_log(
                event_type=EventType.FORWARD_FAILED,
                level="error",
                detail=f"Route {route.id} failed: {exc}",
                actor_id=message.author.id,
                actor_name=str(message.author),
                message_id=message.id,
                extra={"error": str(exc)},
                **_route_fields(route),
            )


async def _log_unmatched(message: discord.Message) -> None:
    if not settings.log_unmatched_messages or message.guild is None:
        return
    if message.guild.id not in await tracked_server_ids():
        return
    await write_log(
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


async def _forward_one(message: discord.Message, route: CachedRoute) -> None:
    if route.source_channel_id == route.destination_channel_id:
        await write_log(
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
            event_type=EventType.MESSAGE_SKIPPED,
            detail=detail,
            actor_id=message.author.id,
            actor_name=str(message.author),
            message_id=message.id,
            extra={"stickers": sticker_names},
            **_route_fields(route),
        )
        return

    if isinstance(message.channel, discord.Thread):
        sent, attachment_errors = await _deliver_forum_message(
            message,
            route,
            cleaned_parts,
            list(message.attachments),
            embeds,
            channel_map,
        )
    else:
        channel = await _destination_channel(route.destination_channel_id)
        reply_to = await _destination_reply(message, route, channel_map)
        sent, attachment_errors = await _deliver(channel, cleaned_parts, list(message.attachments), embeds, reply_to)
    if not sent:
        await write_log(
            event_type=EventType.MESSAGE_SKIPPED,
            level="warning",
            detail="Image processing failed, so the image was not forwarded",
            actor_id=message.author.id,
            actor_name=str(message.author),
            message_id=message.id,
            extra={"attachment_errors": attachment_errors},
            **_route_fields(route),
        )
        return
    if sent:
        try:
            await post_json(
                "/discord/message-links",
                {
                    "source_message_id": str(message.id),
                    "source_channel_id": str(message.channel.id),
                    "destination_message_id": str(sent[0].id),
                    "destination_channel_id": str(sent[0].channel.id if isinstance(message.channel, discord.Thread) else route.destination_channel_id),
                    "destination_server_id": str(route.destination_server_id),
                },
            )
        except Exception:
            logger.exception(
                "Could not save message link for source %s -> destination %s",
                message.id,
                sent[0].id,
            )
    try:
        await patch_json(
            f"/discord/routes/{route.id}/last-message",
            {"last_message_id": message.id, "last_message_at": message.created_at.isoformat()},
        )
    except Exception:
        pass

    detail = f"Forwarded message {message.id} to #{route.destination_channel_name} ({len(sent)} Discord message(s))"
    if attachment_errors:
        detail += f". Attachment upload failed: {attachment_errors[0]}"
    await write_log(
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
        event_type=EventType.ROUTE_UPDATED,
        detail=f"Saved last_message_at {message.created_at.isoformat()} for route {route.id}",
        actor_id=message.author.id,
        actor_name=str(message.author),
        message_id=message.id,
        extra={"last_message_at": message.created_at.isoformat(), "last_message_id": str(message.id)},
        **_route_fields(route),
    )


async def _deliver_forum_message(
    message: discord.Message,
    route: CachedRoute,
    parts: list[str],
    attachments: list[discord.Attachment],
    embeds: list[discord.Embed],
    channel_map: dict[int, int],
) -> tuple[list[discord.Message], list[str]]:
    """Create a destination forum post for a new thread, or send a reply into the linked post."""

    source_thread = message.channel
    if not isinstance(source_thread, discord.Thread):
        raise RuntimeError("Forum forwarding expected a thread")

    async with _lock_for(source_thread.id):
        destination_thread = await _linked_thread(source_thread.id, route.destination_server_id)
        if destination_thread is None:
            forum = await _destination_channel(route.destination_channel_id)
            if not isinstance(forum, discord.ForumChannel):
                raise RuntimeError(f"Destination channel {route.destination_channel_id} is not a forum")
            await _ensure_bot_can_post(forum)
            destination_thread, sent, errors = await _open_forum_post(
                forum,
                source_thread.name,
                parts,
                attachments,
                embeds,
            )
            await _save_message_link(
                source_thread.id,
                source_thread.id,
                destination_thread.id,
                destination_thread.id,
                route.destination_server_id,
            )
            return sent, errors

    reply_to = await _destination_reply(message, route, channel_map)
    return await _deliver(destination_thread, parts, attachments, embeds, reply_to)


async def _open_forum_post(
    forum: discord.ForumChannel,
    title: str,
    parts: list[str],
    attachments: list[discord.Attachment],
    embeds: list[discord.Embed],
) -> tuple[discord.Thread, list[discord.Message], list[str]]:
    name = (title or "post").strip()[:100] or "post"
    first_attachments = attachments[:_FILES_PER_MESSAGE]
    files: list[discord.File] = []
    paths: list[str] = []
    errors: list[str] = []
    try:
        if first_attachments:
            files, paths = await _files(first_attachments)
        kwargs: dict = {"name": name, "allowed_mentions": _MENTIONS}
        if parts:
            kwargs["content"] = parts[0]
        if files:
            kwargs["files"] = files
        if embeds:
            kwargs["embeds"] = embeds[:10]
        created = await forum.create_thread(**kwargs)
        sent = [created.message]
        extra_parts = parts[1:]
        extra_attachments = attachments[_FILES_PER_MESSAGE:]
        if extra_parts or extra_attachments:
            more, more_errors = await _deliver(created.thread, extra_parts, extra_attachments, [], None)
            sent.extend(more)
            errors.extend(more_errors)
        return created.thread, sent, errors
    finally:
        for path in paths:
            if os.path.exists(path):
                os.remove(path)


async def _forum_parent(thread: discord.Thread) -> discord.ForumChannel | None:
    parent = thread.parent
    if isinstance(parent, discord.ForumChannel):
        return parent
    if thread.parent_id is None:
        return None
    try:
        fetched = bot.get_channel(thread.parent_id) or await bot.fetch_channel(thread.parent_id)
    except discord.HTTPException:
        return None
    return fetched if isinstance(fetched, discord.ForumChannel) else None


async def _linked_thread(source_thread_id: int, destination_server_id: int) -> discord.Thread | None:
    link = await _lookup_message_link(source_thread_id, destination_server_id, None)
    if link is None:
        return None
    for key in ("destination_channel_id", "destination_message_id"):
        try:
            channel = await _destination_channel(int(link[key]))
        except (TypeError, ValueError, RuntimeError, discord.HTTPException):
            continue
        if isinstance(channel, discord.Thread):
            return channel
    return None


async def _ensure_bot_can_post(forum: discord.ForumChannel) -> None:
    me = forum.guild.me
    if me is None:
        return
    overwrite = forum.overwrites_for(me)
    if overwrite.send_messages and overwrite.send_messages_in_threads and overwrite.create_public_threads:
        return
    overwrite.view_channel = True
    overwrite.send_messages = True
    overwrite.send_messages_in_threads = True
    overwrite.create_public_threads = True
    overwrite.read_message_history = True
    try:
        await forum.set_permissions(me, overwrite=overwrite, reason="Allow the bot to forward forum posts")
    except discord.HTTPException:
        logger.warning("Could not update forum permissions for the bot in %s", forum.id)


def _lock_for(thread_id: int) -> asyncio.Lock:
    lock = _thread_locks.get(thread_id)
    if lock is None:
        lock = asyncio.Lock()
        _thread_locks[thread_id] = lock
    return lock


async def _save_message_link(
    source_message_id: int,
    source_channel_id: int,
    destination_message_id: int,
    destination_channel_id: int,
    destination_server_id: int,
) -> None:
    try:
        await post_json(
            "/discord/message-links",
            {
                "source_message_id": str(source_message_id),
                "source_channel_id": str(source_channel_id),
                "destination_message_id": str(destination_message_id),
                "destination_channel_id": str(destination_channel_id),
                "destination_server_id": str(destination_server_id),
            },
        )
    except Exception:
        logger.exception(
            "Could not save message link for source %s -> destination %s",
            source_message_id,
            destination_message_id,
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
    cleanup_paths: list[str] = []
    try:
        for index, batch in enumerate(batches):
            embed_payload = embeds if not parts and index == 0 else []
            try:
                files, paths = await _files(batch)
                cleanup_paths.extend(paths)
                if not files and not embed_payload:
                    errors.append("Image was not forwarded because processing failed")
                    continue
                message = await _send(channel, None, files, embed_payload, reply)
                reply = None
            except Exception as exc:
                if not sent:
                    raise
                errors.append(str(exc))
                logger.exception("Attachment batch failed after text was already forwarded")
                break
            if message is not None:
                sent.append(message)
    finally:
        for path in cleanup_paths:
            if os.path.exists(path):
                os.remove(path)

    if not sent:
        if errors and not parts and not embeds:
            return [], errors
        raise RuntimeError("Discord did not accept the forwarded message")
    return sent, errors


async def _send(
    channel,
    content: str | None,
    files: list[discord.File],
    embeds: list[discord.Embed],
    reference: discord.MessageReference | None = None,
) -> discord.Message | None:
    if not content and not files and not embeds and reference is None:
        return None
    try:
        return await channel.send(**_payload(content, files, embeds, reference))
    except discord.HTTPException:
        if not embeds:
            raise
        logger.warning("Discord rejected the message with embeds; retrying without embeds")
        return await channel.send(**_payload(content, files, [], reference))


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


async def _files(attachments: list[discord.Attachment]) -> tuple[list[discord.File], list[str]]:
    files: list[discord.File] = []
    cleanup: list[str] = []
    for attachment in attachments:
        if not _is_image(attachment):
            files.append(await attachment.to_file())
            continue
        downloaded = await _download_attachment(attachment)
        if downloaded is None:
            logger.warning("Could not download image %s; it will not be forwarded", attachment.filename)
            continue
        cleanup.append(downloaded)
        processed = await manipulate_image(downloaded)
        if processed is None:
            logger.warning("Image processing failed for %s; original image will not be sent", attachment.filename)
            continue
        if os.path.abspath(processed) != os.path.abspath(downloaded):
            cleanup.append(processed)
            filename = f"{Path(attachment.filename).stem}.png"
        else:
            filename = attachment.filename or "image"
        files.append(discord.File(processed, filename=filename))
    return files, cleanup


async def _download_attachment(attachment: discord.Attachment) -> str | None:
    suffix = Path(attachment.filename or "").suffix
    if not suffix:
        content_type = (attachment.content_type or "").split(";", 1)[0].strip()
        suffix = mimetypes.guess_extension(content_type) or ".img"
    handle, path = tempfile.mkstemp(suffix=suffix)
    os.close(handle)
    try:
        await attachment.save(path)
    except Exception:
        logger.exception("Could not save attachment %s", attachment.filename)
        if os.path.exists(path):
            os.remove(path)
        return None
    return path


def _is_image(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    if content_type.startswith("image/"):
        return True
    guessed = mimetypes.guess_type(attachment.filename or "")[0] or ""
    return guessed.startswith("image/")


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


async def _destination_reply(
    message: discord.Message,
    route: CachedRoute,
    channel_map: dict[int, int],
) -> discord.MessageReference | None:
    """Map a source reply onto the matching destination message. Never reuse the source reference."""

    reference = message.reference
    if reference is None or reference.message_id is None:
        return None
    # Discord forward snapshots are not normal replies.
    ref_type = getattr(reference, "type", None)
    if ref_type is not None and int(getattr(ref_type, "value", ref_type)) != 0:
        return None

    source_message_id = int(reference.message_id)
    source_channel_id = int(reference.channel_id) if reference.channel_id else None
    link = await _lookup_message_link(
        source_message_id,
        route.destination_server_id,
        channel_map.get(source_channel_id) if source_channel_id is not None else None,
    )
    if link is None:
        return None

    destination_message_id = int(link["destination_message_id"])
    destination_channel_id = int(link["destination_channel_id"])
    if destination_message_id == source_message_id or (
        source_channel_id is not None and destination_channel_id == source_channel_id
    ):
        logger.error(
            "Refusing source-looking message link for reply %s (channel %s)",
            source_message_id,
            source_channel_id,
        )
        return None

    try:
        dest_channel = await _destination_channel(destination_channel_id)
        dest_message = await dest_channel.fetch_message(destination_message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, RuntimeError):
        logger.warning(
            "Destination reply target %s/%s missing for source reply %s",
            destination_channel_id,
            destination_message_id,
            source_message_id,
        )
        return None

    return dest_message.to_reference(fail_if_not_exists=False)


async def _lookup_message_link(
    source_message_id: int,
    destination_server_id: int,
    destination_channel_id: int | None,
) -> dict | None:
    queries = [
        f"/discord/message-links?source_message_id={source_message_id}&destination_server_id={destination_server_id}",
    ]
    if destination_channel_id is not None:
        queries.insert(
            0,
            (
                f"/discord/message-links?source_message_id={source_message_id}"
                f"&destination_server_id={destination_server_id}"
                f"&destination_channel_id={destination_channel_id}"
            ),
        )
    for query in queries:
        try:
            payload = await get_json(query)
        except Exception:
            logger.exception("Message-link lookup failed for source %s", source_message_id)
            continue
        link = payload.get("link")
        if link:
            return link
    return None


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
