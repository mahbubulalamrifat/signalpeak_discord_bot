"""One-time permission fixes for the destination server."""

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.services.guild_layout import list_structure, load_guild, lock_all_member_only

router = APIRouter(prefix="/permissions", tags=["discord-permissions"], dependencies=[Depends(require_admin)])


@router.post("/lock-member-only")
async def lock_member_only() -> dict:
    guild = await load_guild(None)
    result = await lock_all_member_only(guild)
    structure = await list_structure(guild)
    return {**structure, "lock": result}
