"""Approve, kick, and ban members. Each call is stored through the SignalPeak API, then executed."""

import asyncio
import logging
from datetime import datetime, timezone

import discord

from app.config import get_settings
from app.constants import ActionStatus, EventType, MemberActionName
from app.services.activity_log import write_log
from app.services.signalpeak_api import get_json, patch_json, post_json

logger = logging.getLogger("signalpeak.members")
settings = get_settings()
_lock = asyncio.Lock()


class MemberAction:
    def __init__(self, data: dict) -> None:
        self.id = int(data["id"])
        self.action = str(data["action"])
        self.server_id = int(data["server_id"])
        self.server_name = data.get("server_name")
        self.user_id = int(data["user_id"])
        self.username = data.get("username")
        self.reason = data.get("reason")
        self.role_id = int(data["role_id"]) if data.get("role_id") else None
        self.status = data.get("status")
        self.error_message = data.get("error_message")
        self.executed_at: datetime | None = None


async def queue_member_action(
    *,
    action: str,
    server_id: int,
    user_id: int,
    reason: str | None = None,
    role_id: int | None = None,
    username: str | None = None,
    server_name: str | None = None,
) -> MemberAction:
    if action not in {MemberActionName.APPROVE, MemberActionName.KICK, MemberActionName.BAN}:
        raise ValueError(f"Unsupported member action: {action}")
    saved = await post_json(
        "/discord/member-actions",
        {
            "action": action,
            "server_id": server_id,
            "server_name": server_name,
            "user_id": user_id,
            "username": username,
            "reason": reason,
            "role_id": role_id,
            "status": ActionStatus.PENDING,
        },
    )
    row = MemberAction(saved)
    await write_log(
        event_type=EventType.MEMBER_ACTION_QUEUED,
        detail=f"Queued {action} for user {user_id} in server {server_id}",
        actor_id=user_id,
        actor_name=username,
        source_server_id=server_id,
        source_server_name=server_name,
        extra={"member_action_id": row.id, "action": action, "role_id": role_id, "reason": reason},
    )
    return row


async def process_pending_member_actions(client: discord.Client) -> int:
    """Read pending rows from the API and perform each Discord action."""

    if not client.is_ready():
        logger.info("Bot is not connected, so pending member actions stay queued")
        return 0

    async with _lock:
        payload = await get_json("/discord/member-actions?status=pending")
        pending = [MemberAction(item) for item in payload.get("actions", [])]
        for row in pending:
            await _execute(client, row)
        return len(pending)


async def _execute(client: discord.Client, row: MemberAction) -> None:
    try:
        guild = client.get_guild(row.server_id) or await client.fetch_guild(row.server_id)
        row.server_name = guild.name
        if row.action == MemberActionName.APPROVE:
            detail = await _approve(guild, row)
        elif row.action == MemberActionName.KICK:
            detail = await _kick(guild, row)
        elif row.action == MemberActionName.BAN:
            detail = await _ban(guild, row)
        else:
            raise ValueError(f"Unsupported member action: {row.action}")
        row.status = ActionStatus.COMPLETED
        row.error_message = None
        await write_log(
            event_type=EventType.MEMBER_ACTION_COMPLETED,
            detail=detail,
            actor_id=row.user_id,
            actor_name=row.username,
            destination_server_id=row.server_id,
            destination_server_name=row.server_name,
            extra={"member_action_id": row.id, "action": row.action, "role_id": row.role_id},
        )
    except Exception as exc:
        logger.exception("Member action %s failed", row.id)
        row.status = ActionStatus.FAILED
        row.error_message = str(exc)[:2000]
        await write_log(
            event_type=EventType.MEMBER_ACTION_FAILED,
            level="error",
            detail=f"{row.action} failed for user {row.user_id}: {exc}",
            actor_id=row.user_id,
            actor_name=row.username,
            destination_server_id=row.server_id,
            destination_server_name=row.server_name,
            extra={"member_action_id": row.id, "action": row.action, "error": str(exc)},
        )
    await patch_json(
        f"/discord/member-actions/{row.id}",
        {
            "server_name": row.server_name,
            "username": row.username,
            "status": row.status,
            "error_message": row.error_message,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        },
    )


async def _approve(guild: discord.Guild, row: MemberAction) -> str:
    member = guild.get_member(row.user_id) or await guild.fetch_member(row.user_id)
    row.username = str(member)
    notes: list[str] = []
    role_id = row.role_id or settings.approval_role_snowflake
    if role_id is not None:
        role = guild.get_role(role_id)
        if role is None:
            raise ValueError(f"Role {role_id} was not found in {guild.name}")
        await member.add_roles(role, reason=row.reason or "approved")
        notes.append(f"added role {role.name}")
    else:
        notes.append("no role was assigned; pass role_id or set APPROVAL_ROLE_ID")
    if member.is_timed_out():
        await member.timeout(None, reason=row.reason or "approved")
        notes.append("cleared timeout")
    return f"Approved {member} in {guild.name}: {', '.join(notes)}"


async def _kick(guild: discord.Guild, row: MemberAction) -> str:
    member = guild.get_member(row.user_id) or await guild.fetch_member(row.user_id)
    row.username = str(member)
    await member.kick(reason=row.reason or "kicked by signalpeak bot")
    return f"Kicked {member} from {guild.name}"


async def _ban(guild: discord.Guild, row: MemberAction) -> str:
    try:
        member = guild.get_member(row.user_id) or await guild.fetch_member(row.user_id)
        row.username = str(member)
    except discord.NotFound:
        if not row.username:
            row.username = str(row.user_id)
    await guild.ban(
        discord.Object(id=row.user_id),
        reason=row.reason or "banned by signalpeak bot",
        delete_message_seconds=0,
    )
    return f"Banned user {row.user_id} from {guild.name}"
