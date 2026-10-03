"""Category endpoints. A category is the folder that holds channels."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.services.guild_layout import (
    channel_payload,
    create_category,
    delete_category,
    list_structure,
    load_guild,
    rename_category,
)

router = APIRouter(prefix="/categories", tags=["discord-categories"], dependencies=[Depends(require_admin)])


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    server_id: str | None = None
    position: int | None = Field(default=None, ge=0)


class CategoryRename(BaseModel):
    category_id: str
    name: str = Field(min_length=1, max_length=100)
    server_id: str | None = None


class CategoryDelete(BaseModel):
    category_id: str
    server_id: str | None = None


@router.get("")
async def index(server_id: str | None = Query(default=None)) -> dict:
    guild = await load_guild(_optional_id(server_id))
    return await list_structure(guild)


@router.post("", status_code=201)
async def store(body: CategoryCreate) -> dict:
    guild = await load_guild(_optional_id(body.server_id))
    category = await create_category(guild, body.name, body.position)
    return channel_payload(category)


@router.post("/rename")
async def rename(body: CategoryRename) -> dict:
    guild = await load_guild(_optional_id(body.server_id))
    category = await rename_category(guild, int(body.category_id), body.name)
    return channel_payload(category)


@router.post("/delete")
async def destroy(body: CategoryDelete) -> dict:
    guild = await load_guild(_optional_id(body.server_id))
    return await delete_category(guild, int(body.category_id))


def _optional_id(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    if not value.strip().isdigit():
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="server_id must be a numeric Discord id")
    return int(value.strip())
