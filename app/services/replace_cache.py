"""In-memory copy of the replace rules. Reloaded from the API, or the local database when the API is unset."""

import asyncio
import logging

from app.services.replacements import ReplacementRule
from app.services.signalpeak_api import api_configured, get_json

logger = logging.getLogger("signalpeak.replace_cache")

_rules: tuple[ReplacementRule, ...] | None = None
_lock = asyncio.Lock()


async def get_replace_rules() -> tuple[ReplacementRule, ...]:
    if _rules is not None:
        return _rules
    return await refresh_replace_rules()


async def refresh_replace_rules() -> tuple[ReplacementRule, ...]:
    global _rules
    async with _lock:
        if api_configured():
            try:
                payload = await get_json("/discord/replace-rules")
                _rules = tuple(
                    ReplacementRule(
                        search_key=item["search_key"],
                        replace_value=item.get("replace_value") or "",
                        rule_id=int(item["id"]),
                    )
                    for item in payload.get("replace_rules", [])
                )
                logger.info("Replace rules cache now has %s rule(s) from the API", len(_rules))
                return _rules
            except Exception:
                logger.exception("Could not load replace rules from the SignalPeak API.")
                _rules = tuple()
                return _rules
        logger.error("SIGNALPEAK_API_URL is empty, so replace rules were not loaded.")
        _rules = tuple()
        return _rules
