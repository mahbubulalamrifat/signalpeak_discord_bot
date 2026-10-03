"""Source and destination server ids, loaded from the API and kept in memory."""

from dataclasses import dataclass
import logging

from app.services.signalpeak_api import get_json

logger = logging.getLogger("signalpeak.servers")


@dataclass(frozen=True)
class ServerPair:
    source_server_id: int
    destination_server_id: int
    source_server_name: str | None
    destination_server_name: str | None


_pair: ServerPair | None = None


def server_pair() -> ServerPair | None:
    return _pair


async def refresh_server_pair() -> ServerPair | None:
    global _pair
    try:
        payload = await get_json("/discord/servers")
    except Exception:
        logger.exception("Could not load source and destination server ids from the API.")
        return _pair
    source_id = _snowflake(payload.get("source_server_id"))
    destination_id = _snowflake(payload.get("destination_server_id"))
    if source_id is None or destination_id is None:
        logger.error("The server row is missing a source or destination server id.")
        _pair = None
        return None
    _pair = ServerPair(
        source_server_id=source_id,
        destination_server_id=destination_id,
        source_server_name=payload.get("source_server_name"),
        destination_server_name=payload.get("destination_server_name"),
    )
    return _pair


def _snowflake(value: object) -> int | None:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    return None
