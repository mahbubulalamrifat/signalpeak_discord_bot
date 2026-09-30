"""In-memory copy of the replace rules. Reloaded only after an API change."""

import asyncio
import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ReplaceRule
from app.services.replacements import ReplacementRule

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
        async with SessionLocal() as session:
            rows = await session.scalars(select(ReplaceRule).order_by(ReplaceRule.id.asc()))
            loaded = tuple(
                ReplacementRule(search_key=row.search_key, replace_value=row.replace_value, rule_id=row.id)
                for row in rows.all()
            )
        _rules = loaded
        logger.info("Replace rules cache now has %s rule(s)", len(_rules))
        return _rules
