"""Text channel endpoints. Channels are created inside a category."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.services.guild_layout import channel_payload, create_text_channel, load_guild, move_channel, rename_channel

router = APIRouter(prefix="/channels", tags=["discord-channels"], dependencies=[Depends(require_admin)])


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category_id: str
    server_id: str | None = None
    position: int | None = Field(default=None, ge=0)


class ChannelRename(BaseModel):
    channel_id: str
    name: str = Field(min_length=1, max_length=100)
    server_id: str | None = None


class ChannelMove(BaseModel):
    channel_id: str
    category_id: str | None = None
    server_id: str | None = None
    position: int | None = Field(default=None, ge=0)


@router.post("", status_code=201)
async def store(body: ChannelCreate) -> dict:
    guild = await load_guild(_optional_id(body.server_id))
    channel = await create_text_channel(guild, body.name, _required_id(body.category_id, "category_id"), body.position)
    return channel_payload(channel)


@router.post("/rename")
async def rename(body: ChannelRename) -> dict:
    guild = await load_guild(_optional_id(body.server_id))
    channel = await rename_channel(guild, _required_id(body.channel_id, "channel_id"), body.name)
    return channel_payload(channel)


@router.post("/move")
async def move(body: ChannelMove) -> dict:
    guild = await load_guild(_optional_id(body.server_id))
    category_id = None if body.category_id is None or body.category_id.strip() == "" else _required_id(body.category_id, "category_id")
    channel = await move_channel(guild, _required_id(body.channel_id, "channel_id"), category_id, body.position)
    return channel_payload(channel)


def _optional_id(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return _required_id(value, "server_id")


def _required_id(value: str, field_name: str) -> int:
    text = value.strip()
    if not text.isdigit():
        raise HTTPException(status_code=400, detail=f"{field_name} must be a numeric Discord id")
    return int(text)
