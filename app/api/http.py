"""Management API. Forwarding itself reads the database; these endpoints maintain that data."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.constants import MemberActionName
from app.database import get_session
from app.models import ActivityLog, DiscordRoute, MemberAction, ReplaceRule
from app.schemas import (
    LogOut,
    MemberActionIn,
    MemberActionOut,
    ReplaceRuleBulk,
    ReplaceRuleIn,
    ReplaceRuleOut,
    ReplaceRuleUpdate,
    RouteIn,
    RouteOut,
    RouteUpdate,
)
from app.services.bot_client import bot
from app.services.members import process_pending_member_actions, queue_member_action
from app.services.replace_cache import refresh_replace_rules
from app.services.route_cache import refresh_route_cache
from app.services.routes import refresh_route_names

router = APIRouter(prefix="/api", tags=["signalpeak"], dependencies=[Depends(require_admin)])


@router.get("/routes", response_model=list[RouteOut])
async def list_routes(session: AsyncSession = Depends(get_session)) -> list[DiscordRoute]:
    rows = await session.scalars(select(DiscordRoute).order_by(DiscordRoute.id.asc()))
    return list(rows.all())


@router.post("/routes", response_model=RouteOut, status_code=201)
async def create_route(body: RouteIn, session: AsyncSession = Depends(get_session)) -> DiscordRoute:
    if body.source_channel_id == body.destination_channel_id:
        raise HTTPException(status_code=400, detail="Source and destination channel must be different")
    names = (
        body.source_server_name,
        body.source_channel_name,
        body.destination_server_name,
        body.destination_channel_name,
    )
    if any(not name.strip() for name in names):
        raise HTTPException(status_code=400, detail="Server and channel names cannot be blank")
    route = DiscordRoute(
        source_server_id=int(body.source_server_id),
        source_server_name=body.source_server_name.strip(),
        source_category_id=int(body.source_category_id) if body.source_category_id else None,
        source_category_name=body.source_category_name.strip() if body.source_category_name else None,
        source_channel_id=int(body.source_channel_id),
        source_channel_name=body.source_channel_name.strip(),
        destination_server_id=int(body.destination_server_id),
        destination_server_name=body.destination_server_name.strip(),
        destination_category_id=int(body.destination_category_id) if body.destination_category_id else None,
        destination_category_name=body.destination_category_name.strip() if body.destination_category_name else None,
        destination_channel_id=int(body.destination_channel_id),
        destination_channel_name=body.destination_channel_name.strip(),
        is_active=body.is_active,
    )
    session.add(route)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="That source and destination channel pair already exists") from None
    await session.refresh(route)
    await refresh_route_cache()
    return route


@router.patch("/routes/{route_id}", response_model=RouteOut)
async def update_route(route_id: int, body: RouteUpdate, session: AsyncSession = Depends(get_session)) -> DiscordRoute:
    route = await session.get(DiscordRoute, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")
    id_fields = {
        "source_server_id",
        "source_channel_id",
        "destination_server_id",
        "destination_channel_id",
    }
    for field, value in body.model_dump(exclude_unset=True).items():
        if field in id_fields and value is not None:
            value = int(value)
        setattr(route, field, value)
    if route.source_channel_id == route.destination_channel_id:
        raise HTTPException(status_code=400, detail="Source and destination channel must be different")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail="That source and destination channel pair already exists") from None
    await session.refresh(route)
    await refresh_route_cache()
    return route


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(route_id: int, session: AsyncSession = Depends(get_session)) -> None:
    route = await session.get(DiscordRoute, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Route not found")
    await session.delete(route)
    await session.commit()
    await refresh_route_cache()


@router.post("/routes/refresh-names")
async def refresh_names() -> dict:
    if not bot.is_ready():
        raise HTTPException(status_code=503, detail="Bot is not connected")
    updated = await refresh_route_names(bot)
    return {"updated_fields": updated}


@router.get("/replace-rules", response_model=list[ReplaceRuleOut])
async def list_replace_rules(session: AsyncSession = Depends(get_session)) -> list[ReplaceRule]:
    rows = await session.scalars(select(ReplaceRule).order_by(ReplaceRule.id.asc()))
    return list(rows.all())


@router.post("/replace-rules", response_model=ReplaceRuleOut, status_code=201)
async def create_replace_rule(body: ReplaceRuleIn, session: AsyncSession = Depends(get_session)) -> ReplaceRule:
    rule = _rule_from_input(body)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await refresh_replace_rules()
    return rule


@router.post("/replace-rules/bulk", response_model=list[ReplaceRuleOut], status_code=201)
async def create_replace_rules(body: ReplaceRuleBulk, session: AsyncSession = Depends(get_session)) -> list[ReplaceRule]:
    created: list[ReplaceRule] = []
    for item in body.rules:
        rule = _rule_from_input(item)
        session.add(rule)
        created.append(rule)
    await session.commit()
    for rule in created:
        await session.refresh(rule)
    await refresh_replace_rules()
    return created


@router.patch("/replace-rules/{rule_id}", response_model=ReplaceRuleOut)
async def update_replace_rule(
    rule_id: int,
    body: ReplaceRuleUpdate,
    session: AsyncSession = Depends(get_session),
) -> ReplaceRule:
    rule = await session.get(ReplaceRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Replace rule not found")
    changes = body.model_dump(exclude_unset=True)
    if "search_key" not in changes and "key" in changes:
        changes["search_key"] = changes["key"]
    if "replace_value" not in changes and "value" in changes:
        changes["replace_value"] = changes["value"]
    changes.pop("key", None)
    changes.pop("value", None)
    if "search_key" in changes and not str(changes["search_key"]).strip():
        raise HTTPException(status_code=400, detail="search_key cannot be empty")
    for field, value in changes.items():
        setattr(rule, field, value)
    await session.commit()
    await session.refresh(rule)
    await refresh_replace_rules()
    return rule


@router.delete("/replace-rules/{rule_id}", status_code=204)
async def delete_replace_rule(rule_id: int, session: AsyncSession = Depends(get_session)) -> None:
    rule = await session.get(ReplaceRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Replace rule not found")
    await session.delete(rule)
    await session.commit()
    await refresh_replace_rules()


@router.get("/logs", response_model=list[LogOut])
async def list_logs(
    event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> list[ActivityLog]:
    query = select(ActivityLog).order_by(ActivityLog.id.desc()).offset(offset).limit(limit)
    if event_type:
        query = query.where(ActivityLog.event_type == event_type)
    rows = await session.scalars(query)
    return list(rows.all())


@router.get("/members/actions", response_model=list[MemberActionOut])
async def list_member_actions(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[MemberAction]:
    query = select(MemberAction).order_by(MemberAction.id.desc()).limit(limit)
    if status:
        query = query.where(MemberAction.status == status)
    rows = await session.scalars(query)
    return list(rows.all())


@router.post("/members/approve", response_model=MemberActionOut)
async def approve_member(body: MemberActionIn, session: AsyncSession = Depends(get_session)) -> MemberAction:
    return await _queue_and_run(session, MemberActionName.APPROVE, body)


@router.post("/members/kick", response_model=MemberActionOut)
async def kick_member(body: MemberActionIn, session: AsyncSession = Depends(get_session)) -> MemberAction:
    return await _queue_and_run(session, MemberActionName.KICK, body)


@router.post("/members/ban", response_model=MemberActionOut)
async def ban_member(body: MemberActionIn, session: AsyncSession = Depends(get_session)) -> MemberAction:
    return await _queue_and_run(session, MemberActionName.BAN, body)


async def _queue_and_run(session: AsyncSession, action: str, body: MemberActionIn) -> MemberAction:
    row = await queue_member_action(
        session,
        action=action,
        server_id=int(body.server_id),
        user_id=int(body.user_id),
        reason=body.reason,
        role_id=int(body.role_id) if body.role_id else None,
        username=body.username,
        server_name=body.server_name,
    )
    await process_pending_member_actions(bot)
    await session.refresh(row)
    return row


def _rule_from_input(body: ReplaceRuleIn) -> ReplaceRule:
    return ReplaceRule(
        search_key=str(body.search_key),
        replace_value="" if body.replace_value is None else body.replace_value,
    )
