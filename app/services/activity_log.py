"""Send one monitoring row to the SignalPeak API and mirror it to the process log."""

import logging
from typing import Any

from app.services.signalpeak_api import post_json

logger = logging.getLogger("signalpeak")

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}


def _clip(value: str | None, limit: int = 4000) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


async def write_log(
    *,
    event_type: str,
    detail: str,
    level: str = "info",
    actor_id: int | None = None,
    actor_name: str | None = None,
    source_server_id: int | None = None,
    source_server_name: str | None = None,
    source_channel_id: int | None = None,
    source_channel_name: str | None = None,
    destination_server_id: int | None = None,
    destination_server_name: str | None = None,
    destination_channel_id: int | None = None,
    destination_channel_name: str | None = None,
    message_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    logger.log(_LEVELS.get(level, logging.INFO), "%s | %s", event_type, detail)
    try:
        await post_json(
            "/discord/logs",
            {
                "event_type": event_type,
                "level": level,
                "actor_id": actor_id,
                "actor_name": _clip(actor_name, 255),
                "source_server_id": source_server_id,
                "source_server_name": _clip(source_server_name, 255),
                "source_channel_id": source_channel_id,
                "source_channel_name": _clip(source_channel_name, 255),
                "destination_server_id": destination_server_id,
                "destination_server_name": _clip(destination_server_name, 255),
                "destination_channel_id": destination_channel_id,
                "destination_channel_name": _clip(destination_channel_name, 255),
                "message_id": message_id,
                "detail": _clip(detail) or "",
                "metadata": extra,
            },
        )
    except Exception:
        return
