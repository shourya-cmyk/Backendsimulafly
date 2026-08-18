"""Async client for Sandbox.co.in PAN and GSTIN verification APIs."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import get_settings


class SandboxConfigurationError(RuntimeError):
    pass


class SandboxAPIError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class SandboxPanResult:
    transaction_id: str | None
    category: str
    status: str
    remarks: str | None
    name_match: bool
    date_of_birth_match: bool


@dataclass(frozen=True)
class SandboxGstinResult:
    transaction_id: str | None
    gstin: str
    legal_name: str
    business_nature: str | None
    state_name: str | None
    state_code: str | None
    pan: str
    registration_start_date: str | None
    registration_status: str
    valid_gstin: bool


class SandboxClient:
    def __init__(self) -> None:
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    def _configuration(self) -> tuple[str, str, str, str]:
        settings = get_settings()
        if bool(getattr(settings, "SANDBOX_MOCK", False)):
            return ("", "", "", "")
        if not settings.SANDBOX_API_KEY or not settings.SANDBOX_API_SECRET:
            raise SandboxConfigurationError(
                "Sandbox credentials are not configured; set SANDBOX_API_KEY and SANDBOX_API_SECRET"
            )
        return (
            settings.SANDBOX_API_BASE_URL.rstrip("/"),
            settings.SANDBOX_API_KEY,
            settings.SANDBOX_API_SECRET,
            settings.SANDBOX_API_VERSION,
        )

    async def _authenticate(self, *, force: bool = False) -> str:
        if (
            not force
            and self._access_token
            and time.monotonic() < self._access_token_expires_at
        ):
            return self._access_token

        async with self._token_lock:
            if (
                not force
                and self._access_token
                and time.monotonic() < self._access_token_expires_at
            ):
                return self._access_token

            base_url, api_key, api_secret, _ = self._configuration()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.post(
                        f"{base_url}/authenticate",
                        headers={
                            "x-api-key": api_key,
                            "x-api-secret": api_secret,
                            "x-api-version": "1.0",
                        },
                    )
            except httpx.HTTPError as exc:
                raise SandboxAPIError(503, "Sandbox authentication is unavailable") from exc
            payload = self._parse_response(response)
            token = (payload.get("data") or {}).get("access_token")
            if not isinstance(token, str) or not token:
                raise SandboxAPIError(502, "Sandbox authentication returned no access token")
            self._access_token = token
            # Sandbox tokens are documented as valid for 24 hours. Refresh a
            # little early so a request never races the provider expiry.
            self._access_token_expires_at = time.monotonic() + (23 * 60 * 60)
            return token

    @staticmethod
    def _parse_response(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SandboxAPIError(response.status_code, "Sandbox returned an invalid response") from exc
        if not response.is_success:
            message = payload.get("message") or payload.get("detail") or "Sandbox request failed"
            raise SandboxAPIError(response.status_code, str(message))
        if not isinstance(payload, dict):
            raise SandboxAPIError(502, "Sandbox returned an invalid response")
        return payload

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        base_url, api_key, _, api_version = self._configuration()
        for attempt in range(2):
            token = await self._authenticate(force=attempt == 1)
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.post(
                        f"{base_url}{path}",
                        headers={
                            "authorization": token,
                            "x-api-key": api_key,
                            "x-api-version": api_version,
                            "x-accept-cache": "false",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
            except httpx.HTTPError as exc:
                raise SandboxAPIError(503, "Sandbox verification is unavailable") from exc
            if response.status_code == 401 and attempt == 0:
                self._access_token = None
                continue
            return self._parse_response(response)
        raise SandboxAPIError(401, "Sandbox authentication failed")

    async def verify_pan(
        self, *, pan: str, name_as_per_pan: str, date_of_birth: str
    ) -> SandboxPanResult:
        settings = get_settings()
        if bool(getattr(settings, "SANDBOX_MOCK", False)):
            return SandboxPanResult(
                transaction_id=f"mock-pan-{uuid.uuid4().hex[:8]}",
                category="Individual",
                status="valid",
                remarks=None,
                name_match=True,
                date_of_birth_match=True,
            )

        payload = await self._post(
            "/kyc/pan/verify",
            {
                "@entity": "in.co.sandbox.kyc.pan_verification.request",
                "pan": pan,
                "name_as_per_pan": name_as_per_pan,
                "date_of_birth": date_of_birth,
                "consent": "Y",
                "reason": "Merchant onboarding and identity verification",
            },
        )
        data = payload.get("data") or {}
        return SandboxPanResult(
            transaction_id=payload.get("transaction_id"),
            category=str(data.get("category") or "unknown"),
            status=str(data.get("status") or "unknown"),
            remarks=data.get("remarks"),
            name_match=data.get("name_as_per_pan_match") is True,
            date_of_birth_match=data.get("date_of_birth_match") is True,
        )

    async def verify_gstin(self, *, gstin: str) -> SandboxGstinResult:
        settings = get_settings()
        if bool(getattr(settings, "SANDBOX_MOCK", False)):
            return SandboxGstinResult(
                transaction_id=f"mock-gst-{uuid.uuid4().hex[:8]}",
                gstin=gstin.upper(),
                legal_name="Verified Merchant Business Pvt Ltd",
                business_nature="Retail Business",
                state_name="Maharashtra",
                state_code=gstin[:2] if len(gstin) >= 2 else "27",
                pan=gstin[2:12].upper() if len(gstin) >= 12 else "",
                registration_start_date="01/07/2017",
                registration_status="Active",
                valid_gstin=True,
            )

        payload = await self._post(
            "/gst/compliance/public/gstin/verify",
            {"gstin": gstin},
        )
        data = ((payload.get("data") or {}).get("data") or {})
        return SandboxGstinResult(
            transaction_id=payload.get("transaction_id"),
            gstin=str(data.get("gstin") or gstin),
            legal_name=str(data.get("legalName") or ""),
            business_nature=data.get("bussNature"),
            state_name=data.get("stateName"),
            state_code=data.get("stateCode"),
            pan=str(data.get("pan") or ""),
            registration_start_date=data.get("regStartDate"),
            registration_status=str(data.get("status") or "unknown"),
            valid_gstin=data.get("validGstin") is True,
        )


@lru_cache
def get_sandbox_client() -> SandboxClient:
    return SandboxClient()
