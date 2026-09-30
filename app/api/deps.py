"""Optional API key for the management endpoints."""

from fastapi import Header, HTTPException

from app.config import get_settings

settings = get_settings()


async def require_admin(x_api_key: str | None = Header(default=None)) -> None:
    expected = settings.admin_api_key.strip()
    if not expected:
        return
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
