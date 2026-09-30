"""API request and response shapes. Discord snowflakes travel as strings so they are not truncated."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


def _require_snowflake(value: str, field_name: str) -> str:
    text = str(value).strip()
    if not text.isdigit():
        raise ValueError(f"{field_name} must be a numeric Discord id")
    return text


class RouteIn(BaseModel):
    source_server_id: str
    source_server_name: str = Field(min_length=1, max_length=255)
    source_category_id: str | None = None
    source_category_name: str | None = Field(default=None, max_length=255)
    source_channel_id: str
    source_channel_name: str = Field(min_length=1, max_length=255)
    destination_server_id: str
    destination_server_name: str = Field(min_length=1, max_length=255)
    destination_category_id: str | None = None
    destination_category_name: str | None = Field(default=None, max_length=255)
    destination_channel_id: str
    destination_channel_name: str = Field(min_length=1, max_length=255)
    is_active: bool = True

    @field_validator(
        "source_server_id",
        "source_channel_id",
        "destination_server_id",
        "destination_channel_id",
    )
    @classmethod
    def snowflake(cls, value: str, info) -> str:
        return _require_snowflake(value, info.field_name)

    @field_validator("source_category_id", "destination_category_id")
    @classmethod
    def optional_category(cls, value: str | None, info) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        return _require_snowflake(value, info.field_name)


class RouteUpdate(BaseModel):
    source_server_id: str | None = None
    source_server_name: str | None = Field(default=None, min_length=1, max_length=255)
    source_channel_id: str | None = None
    source_channel_name: str | None = Field(default=None, min_length=1, max_length=255)
    destination_server_id: str | None = None
    destination_server_name: str | None = Field(default=None, min_length=1, max_length=255)
    destination_channel_id: str | None = None
    destination_channel_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None

    @field_validator(
        "source_server_id",
        "source_channel_id",
        "destination_server_id",
        "destination_channel_id",
    )
    @classmethod
    def optional_snowflake(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _require_snowflake(value, info.field_name)


class RouteOut(BaseModel):
    id: int
    source_server_id: str
    source_server_name: str
    source_category_id: str | None
    source_category_name: str | None
    source_channel_id: str
    source_channel_name: str
    destination_server_id: str
    destination_server_name: str
    destination_category_id: str | None
    destination_category_name: str | None
    destination_channel_id: str
    destination_channel_name: str
    is_active: bool
    last_message_id: str | None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def stringify_ids(cls, value: Any) -> Any:
        if hasattr(value, "source_server_id"):
            return {
                "id": value.id,
                "source_server_id": str(value.source_server_id),
                "source_server_name": value.source_server_name,
                "source_category_id": str(value.source_category_id) if value.source_category_id is not None else None,
                "source_category_name": value.source_category_name,
                "source_channel_id": str(value.source_channel_id),
                "source_channel_name": value.source_channel_name,
                "destination_server_id": str(value.destination_server_id),
                "destination_server_name": value.destination_server_name,
                "destination_category_id": str(value.destination_category_id) if value.destination_category_id is not None else None,
                "destination_category_name": value.destination_category_name,
                "destination_channel_id": str(value.destination_channel_id),
                "destination_channel_name": value.destination_channel_name,
                "is_active": value.is_active,
                "last_message_id": str(value.last_message_id) if value.last_message_id is not None else None,
                "last_message_at": value.last_message_at,
                "created_at": value.created_at,
                "updated_at": value.updated_at,
            }
        return value


class ReplaceRuleIn(BaseModel):
    """key and value are the same fields as search_key and replace_value."""

    search_key: str | None = None
    replace_value: str | None = None
    key: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def resolve_pair(self) -> "ReplaceRuleIn":
        search = self.search_key if self.search_key is not None else self.key
        replacement = self.replace_value if self.replace_value is not None else self.value
        if search is None or search == "":
            raise ValueError("search_key is required")
        if replacement is None:
            replacement = ""
        self.search_key = search
        self.replace_value = replacement
        return self


class ReplaceRuleUpdate(BaseModel):
    search_key: str | None = None
    replace_value: str | None = None
    key: str | None = None
    value: str | None = None


class ReplaceRuleBulk(BaseModel):
    rules: list[ReplaceRuleIn]


class ReplaceRuleOut(BaseModel):
    id: int
    search_key: str
    replace_value: str

    model_config = {"from_attributes": True}


class MemberActionIn(BaseModel):
    server_id: str
    user_id: str
    reason: str | None = None
    role_id: str | None = None
    username: str | None = None
    server_name: str | None = None

    @field_validator("server_id", "user_id")
    @classmethod
    def snowflake(cls, value: str, info) -> str:
        return _require_snowflake(value, info.field_name)

    @field_validator("role_id")
    @classmethod
    def optional_snowflake(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        return _require_snowflake(value, "role_id")


class MemberActionOut(BaseModel):
    id: int
    action: str
    server_id: str
    server_name: str | None
    user_id: str
    username: str | None
    reason: str | None
    role_id: str | None
    status: str
    error_message: str | None
    created_at: datetime
    executed_at: datetime | None

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def stringify_ids(cls, value: Any) -> Any:
        if hasattr(value, "server_id"):
            return {
                "id": value.id,
                "action": value.action,
                "server_id": str(value.server_id),
                "server_name": value.server_name,
                "user_id": str(value.user_id),
                "username": value.username,
                "reason": value.reason,
                "role_id": str(value.role_id) if value.role_id is not None else None,
                "status": value.status,
                "error_message": value.error_message,
                "created_at": value.created_at,
                "executed_at": value.executed_at,
            }
        return value


class LogOut(BaseModel):
    id: int
    event_type: str
    level: str
    actor_id: str | None
    actor_name: str | None
    source_server_id: str | None
    source_server_name: str | None
    source_channel_id: str | None
    source_channel_name: str | None
    destination_server_id: str | None
    destination_server_name: str | None
    destination_channel_id: str | None
    destination_channel_name: str | None
    message_id: str | None
    detail: str
    extra: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def stringify_ids(cls, value: Any) -> Any:
        if not hasattr(value, "event_type"):
            return value

        def text(field: str) -> str | None:
            raw = getattr(value, field)
            return None if raw is None else str(raw)

        return {
            "id": value.id,
            "event_type": value.event_type,
            "level": value.level,
            "actor_id": text("actor_id"),
            "actor_name": value.actor_name,
            "source_server_id": text("source_server_id"),
            "source_server_name": value.source_server_name,
            "source_channel_id": text("source_channel_id"),
            "source_channel_name": value.source_channel_name,
            "destination_server_id": text("destination_server_id"),
            "destination_server_name": value.destination_server_name,
            "destination_channel_id": text("destination_channel_id"),
            "destination_channel_name": value.destination_channel_name,
            "message_id": text("message_id"),
            "detail": value.detail,
            "extra": value.extra,
            "created_at": value.created_at,
        }
