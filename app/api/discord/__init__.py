"""Discord server layout routes used by signalpeak_user."""

from fastapi import APIRouter, Depends

from app.api.deps import require_admin
from app.api.discord.categories import router as categories_router
from app.api.discord.channels import router as channels_router
from app.api.discord.order import router as order_router
from app.api.discord.permissions import router as permissions_router
from app.api.discord.sync import router as sync_router

router = APIRouter(prefix="/api/discord", dependencies=[Depends(require_admin)])
router.include_router(categories_router)
router.include_router(channels_router)
router.include_router(order_router)
router.include_router(sync_router)
router.include_router(permissions_router)
