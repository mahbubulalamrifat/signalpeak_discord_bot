"""Set the visual order of categories and the channels inside them."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.services.guild_layout import load_guild, order_channels

router = APIRouter(prefix="/order", tags=["discord-order"], dependencies=[Depends(require_admin)])


class OrderItem(BaseModel):
    id: str
    position: int = Field(ge=0)
    parent_id: str | None = None


class OrderUpdate(BaseModel):
    items: list[OrderItem] = Field(min_length=1)
    server_id: str | None = None


@router.post("")
async def update(body: OrderUpdate) -> dict:
    guild = await load_guild(_optional_id(body.server_id))
    items = []
    for item in body.items:
        row = {"id": _required_id(item.id, "id"), "position": item.position}
        if "parent_id" in item.model_fields_set:
            row["parent_id"] = None if item.parent_id is None or item.parent_id.strip() == "" else _required_id(item.parent_id, "parent_id")
        items.append(row)
    await order_channels(guild, items)
    return {"updated": len(items)}


def _optional_id(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    return _required_id(value, "server_id")


def _required_id(value: str, field_name: str) -> int:
    text = value.strip()
    if not text.isdigit():
        raise HTTPException(status_code=400, detail=f"{field_name} must be a numeric Discord id")
    return int(text)
