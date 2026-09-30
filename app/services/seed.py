"""One-time inserts. Existing route and rule rows are left untouched."""

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ROOT_DIR
from app.constants import EventType
from app.models import DiscordRoute, ReplaceRule
from app.services.activity_log import write_log
from app.services.ids import parse_snowflake

logger = logging.getLogger("signalpeak.seed")

ROUTES_FILE = ROOT_DIR / "seed" / "routes.json"
RULES_FILE = ROOT_DIR / "seed" / "replace_rules.json"


async def seed_initial_data(session: AsyncSession) -> None:
    await _seed_routes(session)
    await session.commit()
    try:
        await _seed_replace_rules(session)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("Replace-rule seed failed. Channel routes were still saved.")


async def _seed_routes(session: AsyncSession) -> None:
    existing = await session.scalar(select(func.count()).select_from(DiscordRoute))
    if existing:
        logger.info("signalpeak_discord already has %s row(s); channel mapping was not repeated", existing)
        return
    from app.config import get_settings

    settings = get_settings()
    if settings.source_server_snowflake and settings.destination_server_snowflake:
        logger.info(
            "Channel pairs are created when the bot connects. It loads every text channel from server %s "
            "and matches it to the same channel name on server %s.",
            settings.source_server_id,
            settings.destination_server_id,
        )
        return
    if not ROUTES_FILE.exists():
        logger.warning("No seed file at %s", ROUTES_FILE)
        return

    try:
        payload = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("seed/routes.json is not valid JSON: %s", exc)
        return
    inserted = 0
    skipped = 0
    for item in payload:
        try:
            route = DiscordRoute(
                source_server_id=parse_snowflake(item["source_server_id"]),
                source_server_name=str(item["source_server_name"]).strip(),
                source_channel_id=parse_snowflake(item["source_channel_id"]),
                source_channel_name=str(item["source_channel_name"]).strip(),
                destination_server_id=parse_snowflake(item["destination_server_id"]),
                destination_server_name=str(item["destination_server_name"]).strip(),
                destination_channel_id=parse_snowflake(item["destination_channel_id"]),
                destination_channel_name=str(item["destination_channel_name"]).strip(),
                is_active=bool(item.get("is_active", True)),
            )
        except (KeyError, ValueError) as exc:
            skipped += 1
            logger.warning("Skipped seed route %s: %s", item.get("source_channel_name"), exc)
            continue
        names = (
            route.source_server_name,
            route.source_channel_name,
            route.destination_server_name,
            route.destination_channel_name,
        )
        if not all(names):
            skipped += 1
            logger.warning("Skipped seed route because a name is empty")
            continue
        session.add(route)
        inserted += 1

    if inserted:
        await write_log(
            session,
            event_type=EventType.SEED_INSERTED,
            detail=f"Inserted {inserted} channel route(s) from seed/routes.json",
            extra={"count": inserted},
        )
        logger.info("Inserted %s channel route(s). Later starts read the database only.", inserted)
        if skipped:
            logger.warning(
                "Skipped %s seed route(s). The seed file will not run again now that the table has rows. "
                "Add the skipped pairs with POST /api/routes.",
                skipped,
            )
    else:
        logger.warning(
            "No channel routes were seeded. Put real Discord ids in seed/routes.json before the first start, "
            "or add a route through POST /api/routes."
        )


async def _seed_replace_rules(session: AsyncSession) -> None:
    existing = await session.scalar(select(func.count()).select_from(ReplaceRule))
    if existing:
        logger.info("Replace rules already exist; seed file was not applied")
        return
    if not RULES_FILE.exists():
        logger.info("No %s file. Add rules through the API or copy seed/replace_rules.example.json.", RULES_FILE.name)
        return

    try:
        payload = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("seed/replace_rules.json is not valid JSON: %s", exc)
        return
    inserted = 0
    for item in payload:
        search = item.get("search_key", item.get("key"))
        if not search:
            logger.warning("Skipped replace rule with an empty search key")
            continue
        replacement = item.get("replace_value", item.get("value", ""))
        session.add(
            ReplaceRule(
                search_key=str(search),
                replace_value="" if replacement is None else str(replacement),
            )
        )
        inserted += 1

    if inserted:
        await write_log(
            session,
            event_type=EventType.SEED_INSERTED,
            detail=f"Inserted {inserted} replace rule(s) from seed/replace_rules.json",
            extra={"count": inserted},
        )
