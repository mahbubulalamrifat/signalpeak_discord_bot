"""In-memory channel pairs used to find a destination. Reloaded after an API change."""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.database import SessionLocal
from app.models import DiscordRoute

logger = logging.getLogger("signalpeak.route_cache")


@dataclass(frozen=True)
class CachedRoute:
    id: int
    source_server_id: int
    source_server_name: str
    source_channel_id: int
    source_channel_name: str
    destination_server_id: int
    destination_server_name: str
    destination_channel_id: int
    destination_channel_name: str


class _RouteCache:
    def __init__(self) -> None:
        self.by_source: dict[int, tuple[CachedRoute, ...]] = {}
        self.channel_maps: dict[int, dict[int, int]] = {}


_cache: _RouteCache | None = None
_lock = asyncio.Lock()


async def get_routes_for_source(source_channel_id: int) -> tuple[CachedRoute, ...]:
    cache = _cache if _cache is not None else await refresh_route_cache()
    return cache.by_source.get(source_channel_id, ())


async def get_channel_map(destination_server_id: int) -> dict[int, int]:
    cache = _cache if _cache is not None else await refresh_route_cache()
    return cache.channel_maps.get(destination_server_id, {})


async def refresh_route_cache() -> _RouteCache:
    global _cache
    async with _lock:
        loaded = await _load()
        _cache = loaded
        logger.info("Destination cache now has %s active channel pair(s)", sum(len(rows) for rows in loaded.by_source.values()))
        return loaded


async def _load() -> _RouteCache:
    async with SessionLocal() as session:
        rows = await session.scalars(
            select(DiscordRoute).where(DiscordRoute.is_active.is_(True)).order_by(DiscordRoute.id.asc())
        )
        cache = _RouteCache()
        grouped: dict[int, list[CachedRoute]] = {}
        for row in rows.all():
            route = CachedRoute(
                id=row.id,
                source_server_id=row.source_server_id,
                source_server_name=row.source_server_name,
                source_channel_id=row.source_channel_id,
                source_channel_name=row.source_channel_name,
                destination_server_id=row.destination_server_id,
                destination_server_name=row.destination_server_name,
                destination_channel_id=row.destination_channel_id,
                destination_channel_name=row.destination_channel_name,
            )
            grouped.setdefault(route.source_channel_id, []).append(route)
            cache.channel_maps.setdefault(route.destination_server_id, {}).setdefault(
                route.source_channel_id,
                route.destination_channel_id,
            )
        cache.by_source = {channel_id: tuple(items) for channel_id, items in grouped.items()}
        return cache
