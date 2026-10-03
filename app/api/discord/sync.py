"""Sync destination server layout with the source server."""

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.services.guild_layout import list_structure, load_guild
from app.services.structure_sync import sync_destination_with_source

router = APIRouter(prefix="/sync", tags=["discord-sync"], dependencies=[Depends(require_admin)])


@router.post("")
async def sync_structure() -> dict:
    summary = await sync_destination_with_source()
    guild = await load_guild(int(summary["server_id"]))
    structure = await list_structure(guild)
    return {**structure, "sync": summary}
