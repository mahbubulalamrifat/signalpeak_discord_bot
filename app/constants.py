"""Stable names for rows written to signalpeak_discord_logs."""


class EventType:
    BOT_READY = "bot_ready"
    BOT_STOPPED = "bot_stopped"
    SEED_INSERTED = "seed_inserted"
    CHANNELS_MAPPED = "channels_mapped"
    CATEGORY_CREATED = "category_created"
    CATEGORY_RENAMED = "category_renamed"
    CHANNEL_CREATED = "channel_created"
    CHANNEL_RENAMED = "channel_renamed"
    CHANNEL_MOVED = "channel_moved"
    CHANNELS_ORDERED = "channels_ordered"
    NAMES_REFRESHED = "names_refreshed"
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SKIPPED = "message_skipped"
    REPLACEMENT_APPLIED = "replacement_applied"
    MESSAGE_FORWARDED = "message_forwarded"
    FORWARD_FAILED = "forward_failed"
    ROUTE_UPDATED = "route_updated"
    MESSAGE_DELETED = "message_deleted"
    MEMBER_JOINED = "member_joined"
    MEMBER_REMOVED = "member_removed"
    MEMBER_BANNED = "member_banned"
    MEMBER_UNBANNED = "member_unbanned"
    MEMBER_ACTION_QUEUED = "member_action_queued"
    MEMBER_ACTION_COMPLETED = "member_action_completed"
    MEMBER_ACTION_FAILED = "member_action_failed"


class MemberActionName:
    APPROVE = "approve"
    KICK = "kick"
    BAN = "ban"


class ActionStatus:
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
