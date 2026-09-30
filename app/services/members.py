"""Approve, kick, and ban members. Each call is stored first, then executed from that row."""

import asyncio
import logging
from datetime import datetime, timezone

import discord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.constants import ActionStatus, EventType, MemberActionName
from app.database import SessionLocal
from app.models import MemberAction
from app.services.activity_log import write_log

logger = logging.getLogger("signalpeak.members")
settings = get_settings()
_lock = asyncio.Lock()


async def queue_member_action(
    session: AsyncSession,
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

    row = MemberAction(
        action=action,
        server_id=server_id,
        server_name=server_name,
        user_id=user_id,
        username=username,
        reason=reason,
        role_id=role_id,
        status=ActionStatus.PENDING,
    )
    session.add(row)
    await session.flush()
    await write_log(
        session,
        event_type=EventType.MEMBER_ACTION_QUEUED,
        detail=f"Queued {action} for user {user_id} in server {server_id}",
        actor_id=user_id,
        actor_name=username,
        source_server_id=server_id,
        source_server_name=server_name,
        extra={"member_action_id": row.id, "action": action, "role_id": role_id, "reason": reason},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def process_pending_member_actions(client: discord.Client) -> int:
    """Read pending rows and perform the Discord action described by each row."""

    if not client.is_ready():
        logger.info("Bot is not connected, so pending member actions stay queued")
        return 0

    async with _lock:
        async with SessionLocal() as session:
            pending = list(
                (
                    await session.scalars(
                        select(MemberAction).where(MemberAction.status == ActionStatus.PENDING).order_by(MemberAction.id.asc())
                    )
                ).all()
            )
            for row in pending:
                await _execute(client, session, row)
            await session.commit()
            return len(pending)


async def _execute(client: discord.Client, session: AsyncSession, row: MemberAction) -> None:
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
        row.executed_at = datetime.now(timezone.utc)
        await write_log(
            session,
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
        row.executed_at = datetime.now(timezone.utc)
        await write_log(
            session,
            event_type=EventType.MEMBER_ACTION_FAILED,
            level="error",
            detail=f"{row.action} failed for user {row.user_id}: {exc}",
            actor_id=row.user_id,
            actor_name=row.username,
            destination_server_id=row.server_id,
            destination_server_name=row.server_name,
            extra={"member_action_id": row.id, "action": row.action, "error": str(exc)},
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
