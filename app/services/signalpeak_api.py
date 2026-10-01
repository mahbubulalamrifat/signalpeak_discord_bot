"""Load route pairs and replace rules from the SignalPeak API."""

import logging

import aiohttp

from app.config import get_settings

logger = logging.getLogger("signalpeak.api")


class SignalPeakApiError(Exception):
    pass


def api_configured() -> bool:
    return bool(get_settings().signalpeak_api_url.strip())


async def get_json(path: str) -> dict:
    return await _request("GET", path)


async def post_json(path: str, payload: dict) -> dict:
    return await _request("POST", path, payload)


async def patch_json(path: str, payload: dict) -> dict:
    return await _request("PATCH", path, payload)


async def delete_json(path: str) -> dict:
    return await _request("DELETE", path)


async def _request(method: str, path: str, payload: dict | None = None) -> dict:
    settings = get_settings()
    base = settings.signalpeak_api_url.strip().rstrip("/")
    if not base:
        raise SignalPeakApiError("SIGNALPEAK_API_URL is empty")
    headers = {"Accept": "application/json"}
    token = settings.signalpeak_api_token.strip()
    if token:
        headers["X-API-Key"] = token
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, f"{base}{path}", headers=headers, json=payload) as response:
            if response.status >= 400:
                body = await response.text()
                raise SignalPeakApiError(f"{response.status} {body[:300]}")
            data = await response.json()
    if not isinstance(data, dict):
        raise SignalPeakApiError("SignalPeak API did not return an object")
    return data
