"""In-memory channel pairs used to find a destination. Reloaded after an API change."""

import asyncio
import logging
from dataclasses import dataclass

from app.services.signalpeak_api import api_configured, get_json

logger = logging.getLogger("signalpeak.route_cache")


@dataclass(frozen=True)
class CachedRoute:
    id: int
    source_server_id: int
    source_server_name: str
    source_category_id: int | None
    source_category_name: str | None
    source_channel_id: int
    source_channel_name: str
    destination_server_id: int
    destination_server_name: str
    destination_category_id: int | None
    destination_category_name: str | None
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


async def tracked_server_ids() -> set[int]:
    cache = _cache if _cache is not None else await refresh_route_cache()
    ids: set[int] = set()
    for routes in cache.by_source.values():
        for route in routes:
            ids.add(route.source_server_id)
            ids.add(route.destination_server_id)
    return ids


async def all_routes() -> tuple[CachedRoute, ...]:
    cache = _cache if _cache is not None else await refresh_route_cache()
    return tuple(route for routes in cache.by_source.values() for route in routes)


async def get_channel_map(destination_server_id: int) -> dict[int, int]:
    cache = _cache if _cache is not None else await refresh_route_cache()
    return cache.channel_maps.get(destination_server_id, {})


async def refresh_route_cache() -> _RouteCache:
    global _cache
    async with _lock:
        try:
            loaded = await _load()
        except Exception:
            logger.exception("Could not refresh channel pairs from the API.")
            if _cache is not None:
                return _cache
            return _RouteCache()
        _cache = loaded
        return loaded


async def _load() -> _RouteCache:
    if not api_configured():
        logger.error("SIGNALPEAK_API_URL is empty, so channel pairs were not loaded.")
        return _RouteCache()
    return await _load_from_api()


def _optional_snowflake(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


async def _load_from_api() -> _RouteCache:
    payload = await get_json("/discord/routes")
    cache = _RouteCache()
    grouped: dict[int, list[CachedRoute]] = {}
    for item in payload.get("routes", []):
        if not item.get("is_active", True):
            continue
        route = CachedRoute(
            id=int(item["id"]),
            source_server_id=int(item["source_server_id"]),
            source_server_name=item["source_server_name"],
            source_category_id=_optional_snowflake(item.get("source_category_id")),
            source_category_name=item.get("source_category_name"),
            source_channel_id=int(item["source_channel_id"]),
            source_channel_name=item["source_channel_name"],
            destination_server_id=int(item["destination_server_id"]),
            destination_server_name=item["destination_server_name"],
            destination_category_id=_optional_snowflake(item.get("destination_category_id")),
            destination_category_name=item.get("destination_category_name"),
            destination_channel_id=int(item["destination_channel_id"]),
            destination_channel_name=item["destination_channel_name"],
        )
        grouped.setdefault(route.source_channel_id, []).append(route)
        cache.channel_maps.setdefault(route.destination_server_id, {}).setdefault(
            route.source_channel_id,
            route.destination_channel_id,
        )
    cache.by_source = {channel_id: tuple(items) for channel_id, items in grouped.items()}
    return cache
