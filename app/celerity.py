from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from .config import Config


class CelerityError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CelerityClient:
    def __init__(self, config: Config):
        self.base_url = config.celerity_base_url.rstrip("/")
        self.api_key = config.celerity_api_key
        self.auth_mode = config.celerity_auth_mode
        self.timeout = config.celerity_timeout_seconds

    def _headers(self) -> dict[str, str]:
        if self.auth_mode == "x-api-key":
            return {
                "X-API-Key": self.api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        retry_get: bool = False,
    ) -> Any:
        attempts = 3 if retry_get else 1
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout, follow_redirects=True
                ) as client:
                    response = await client.request(
                        method,
                        f"{self.base_url}{path}",
                        headers=self._headers(),
                        json=body,
                    )
                if (
                    retry_get
                    and response.status_code in {502, 503, 504}
                    and attempt < attempts - 1
                ):
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                if response.status_code >= 400:
                    detail = response.text[:300].replace("\n", " ")
                    raise CelerityError(
                        f"HTTP {response.status_code}: {detail}",
                        response.status_code,
                    )
                if not response.text.strip():
                    return {"ok": True}
                try:
                    return response.json()
                except ValueError as exc:
                    raise CelerityError("Celerity returned invalid JSON") from exc
            except CelerityError:
                raise
            except (httpx.HTTPError, TimeoutError) as exc:
                if retry_get and attempt < attempts - 1:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise CelerityError(f"Request failed: {exc}") from exc
        raise CelerityError("Request failed after retries")

    async def list_users(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/users", retry_get=True)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            users = data.get("users", [])
            if isinstance(users, list):
                return [item for item in users if isinstance(item, dict)]
        raise CelerityError("Unexpected response from GET /api/users")

    async def find_user(self, stable_id: str) -> dict[str, Any] | None:
        users = await self.list_users()
        for user in users:
            user_id = str(user.get("userId") or user.get("username") or "")
            if user_id.lower() == stable_id.lower():
                return user
        return None

    async def create_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._request("POST", "/api/users", body=payload)
        if not isinstance(data, dict):
            raise CelerityError("Unexpected response from POST /api/users")
        return data

    async def update_user(
        self, user_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        data = await self._request(
            "PUT", f"/api/users/{quote(user_id, safe='')}", body=payload
        )
        if not isinstance(data, dict):
            raise CelerityError("Unexpected response from PUT /api/users/:id")
        return data

    async def enable_user(self, user_id: str) -> dict[str, Any]:
        data = await self._request(
            "POST", f"/api/users/{quote(user_id, safe='')}/enable"
        )
        if not isinstance(data, dict):
            raise CelerityError("Unexpected response from enable user")
        return data

    def subscription_url(self, token: str) -> str:
        return f"{self.base_url}/api/files/{quote(token, safe='')}"