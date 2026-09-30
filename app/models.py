"""Postgres tables. The route table name is signalpeak_discord, as requested."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DiscordRoute(Base):
    """One source channel to one destination channel.

    A message is forwarded only when its channel id matches source_channel_id.
    """

    __tablename__ = "signalpeak_discord"
    __table_args__ = (
        UniqueConstraint("source_channel_id", "destination_channel_id", name="uq_signalpeak_discord_route"),
        Index("ix_signalpeak_discord_source_channel", "source_channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_category_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_category_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    destination_category_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destination_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    last_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MessageLink(Base):
    """Remember which destination message was copied from a source message.

    A later reply uses that destination message, not the source server.
    """

    __tablename__ = "signalpeak_discord_message_links"
    __table_args__ = (
        UniqueConstraint("source_message_id", "destination_channel_id", name="uq_signalpeak_discord_message_link"),
        Index("ix_signalpeak_discord_message_links_source", "source_message_id", "destination_server_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    destination_server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReplaceRule(Base):
    """One search key and the text that replaces it. An empty replace_value removes the key."""

    __tablename__ = "signalpeak_discord_replace_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    search_key: Mapped[str] = mapped_column(Text, nullable=False)
    replace_value: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")


class MemberAction(Base):
    """Queued approve, kick, or ban. The bot executes pending rows."""

    __tablename__ = "signalpeak_discord_member_actions"
    __table_args__ = (
        CheckConstraint("action IN ('approve', 'kick', 'ban')", name="ck_signalpeak_member_action"),
        CheckConstraint("status IN ('pending', 'completed', 'failed')", name="ck_signalpeak_member_status"),
        Index("ix_signalpeak_discord_member_actions_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    server_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    server_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActivityLog(Base):
    """One row per step: receive, replace, forward, join, remove, kick, ban, and failures."""

    __tablename__ = "signalpeak_discord_logs"
    __table_args__ = (
        Index("ix_signalpeak_discord_logs_created_at", "created_at"),
        Index("ix_signalpeak_discord_logs_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info", server_default="info")
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_server_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_server_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destination_server_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    destination_server_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destination_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    destination_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
