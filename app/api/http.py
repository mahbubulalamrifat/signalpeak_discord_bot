"""Management API. Stored data is read and written through the SignalPeak API."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import require_admin
from app.constants import MemberActionName
from app.schemas import MemberActionIn, ReplaceRuleBulk, ReplaceRuleIn, ReplaceRuleUpdate, RouteIn, RouteUpdate
from app.services.bot_client import bot
from app.services.members import process_pending_member_actions, queue_member_action
from app.services.replace_cache import refresh_replace_rules
from app.services.route_cache import refresh_route_cache
from app.services.server_cache import refresh_server_pair
from app.services.routes import refresh_route_names
from app.services.signalpeak_api import SignalPeakApiError, delete_json, get_json, patch_json, post_json

router = APIRouter(prefix="/api", tags=["signalpeak"], dependencies=[Depends(require_admin)])


@router.get("/routes")
async def list_routes() -> dict:
    return await get_json("/discord/routes")


@router.post("/routes", status_code=201)
async def create_route(body: RouteIn) -> dict:
    if body.source_channel_id == body.destination_channel_id:
        raise HTTPException(status_code=400, detail="Source and destination channel must be different")
    try:
        saved = await post_json("/discord/routes", body.model_dump())
    except SignalPeakApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await refresh_route_cache()
    return saved


@router.patch("/routes/{route_id}")
async def update_route(route_id: int, body: RouteUpdate) -> dict:
    try:
        saved = await patch_json(f"/discord/routes/{route_id}", body.model_dump(exclude_unset=True))
    except SignalPeakApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await refresh_route_cache()
    return saved


@router.delete("/routes/{route_id}", status_code=204)
async def delete_route(route_id: int) -> None:
    try:
        await delete_json(f"/discord/routes/{route_id}")
    except SignalPeakApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await refresh_route_cache()


@router.post("/routes/refresh-names")
async def refresh_names() -> dict:
    if not bot.is_ready():
        raise HTTPException(status_code=503, detail="Bot is not connected")
    updated = await refresh_route_names(bot)
    return {"updated_fields": updated}


@router.post("/cache/refresh")
async def refresh_cache() -> dict:
    await refresh_replace_rules()
    await refresh_route_cache()
    await refresh_server_pair()
    return {"ok": True}


@router.get("/replace-rules")
async def list_replace_rules() -> dict:
    return await get_json("/discord/replace-rules")


@router.post("/replace-rules", status_code=201)
async def create_replace_rule(body: ReplaceRuleIn) -> dict:
    saved = await post_json(
        "/discord/replace-rules",
        {"search_key": body.search_key, "replace_value": body.replace_value or ""},
    )
    await refresh_replace_rules()
    return saved


@router.post("/replace-rules/bulk", status_code=201)
async def create_replace_rules(body: ReplaceRuleBulk) -> list[dict]:
    created = []
    for item in body.rules:
        created.append(
            await post_json(
                "/discord/replace-rules",
                {"search_key": item.search_key, "replace_value": item.replace_value or ""},
            )
        )
    await refresh_replace_rules()
    return created


@router.patch("/replace-rules/{rule_id}")
async def update_replace_rule(rule_id: int, body: ReplaceRuleUpdate) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "search_key" not in changes and "key" in changes:
        changes["search_key"] = changes.pop("key")
    else:
        changes.pop("key", None)
    if "replace_value" not in changes and "value" in changes:
        changes["replace_value"] = changes.pop("value")
    else:
        changes.pop("value", None)
    saved = await patch_json(f"/discord/replace-rules/{rule_id}", changes)
    await refresh_replace_rules()
    return saved


@router.delete("/replace-rules/{rule_id}", status_code=204)
async def delete_replace_rule(rule_id: int) -> None:
    await delete_json(f"/discord/replace-rules/{rule_id}")
    await refresh_replace_rules()


@router.get("/logs")
async def list_logs(
    event_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    return {"message": "Read logs from the SignalPeak API database."}


@router.get("/members/actions")
async def list_member_actions(status: str | None = None) -> dict:
    path = "/discord/member-actions"
    if status:
        path += f"?status={status}"
    return await get_json(path)


@router.post("/members/approve")
async def approve_member(body: MemberActionIn) -> dict:
    return await _queue_and_run(MemberActionName.APPROVE, body)


@router.post("/members/kick")
async def kick_member(body: MemberActionIn) -> dict:
    return await _queue_and_run(MemberActionName.KICK, body)


@router.post("/members/ban")
async def ban_member(body: MemberActionIn) -> dict:
    return await _queue_and_run(MemberActionName.BAN, body)


async def _queue_and_run(action: str, body: MemberActionIn) -> dict:
    row = await queue_member_action(
        action=action,
        server_id=int(body.server_id),
        user_id=int(body.user_id),
        reason=body.reason,
        role_id=int(body.role_id) if body.role_id else None,
        username=body.username,
        server_name=body.server_name,
    )
    await process_pending_member_actions(bot)
    return {
        "id": row.id,
        "action": row.action,
        "server_id": str(row.server_id),
        "user_id": str(row.user_id),
        "status": row.status,
        "username": row.username,
        "server_name": row.server_name,
        "error_message": row.error_message,
    }
