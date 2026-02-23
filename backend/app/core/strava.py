from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any
from urllib import error, parse, request

from app.core.config import settings


class StravaConfigError(RuntimeError):
    pass


class StravaAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


@dataclass
class StravaRateLimits:
    global_limit: str | None = None
    global_usage: str | None = None
    read_limit: str | None = None
    read_usage: str | None = None


class StravaClient:
    def __init__(self) -> None:
        self.client_id = settings.STRAVA_CLIENT_ID
        self.client_secret = settings.STRAVA_CLIENT_SECRET
        self.token_store_path: Path = settings.STRAVA_TOKEN_STORE_PATH
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.expires_at: int | None = None
        self.api_base_url = settings.STRAVA_API_BASE_URL.rstrip("/")
        self.oauth_url = settings.STRAVA_OAUTH_URL
        self._load_tokens_from_store()

    def _load_tokens_from_store(self) -> None:
        if not self.token_store_path.exists():
            return
        raw = self.token_store_path.read_text(encoding="utf-8").strip()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StravaConfigError(
                f"Invalid token store JSON at {self.token_store_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise StravaConfigError(f"Token store at {self.token_store_path} must be a JSON object")

        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        expires_at = payload.get("expires_at")

        self.access_token = str(access) if access else None
        self.refresh_token = str(refresh) if refresh else None
        if expires_at is not None:
            try:
                self.expires_at = int(expires_at)
            except (TypeError, ValueError) as exc:
                raise StravaConfigError(
                    f"expires_at in {self.token_store_path} must be an integer timestamp"
                ) from exc

    def _save_tokens_to_store(self) -> None:
        self.token_store_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }
        self.token_store_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _ensure_basic_config(self) -> None:
        if not self.client_id or not self.client_secret:
            raise StravaConfigError(
                "Missing STRAVA_CLIENT_ID and/or STRAVA_CLIENT_SECRET in environment"
            )
        if not self.access_token:
            raise StravaConfigError(
                f"Missing access_token in token store: {self.token_store_path}"
            )

    def _extract_rate_limits(self, headers: dict[str, str]) -> StravaRateLimits:
        lowered = {k.lower(): v for k, v in headers.items()}
        return StravaRateLimits(
            global_limit=lowered.get("x-ratelimit-limit"),
            global_usage=lowered.get("x-ratelimit-usage"),
            read_limit=lowered.get("x-readratelimit-limit"),
            read_usage=lowered.get("x-readratelimit-usage"),
        )

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[Any, dict[str, str]]:
        req = request.Request(url=url, data=data, method=method, headers=headers or {})
        try:
            with request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
                return parsed, dict(resp.headers.items())
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp else ""
            parsed: Any = raw
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                pass
            message = "Strava API request failed"
            if isinstance(parsed, dict) and parsed.get("message"):
                message = str(parsed["message"])
            raise StravaAPIError(message=message, status_code=exc.code, response_body=parsed) from exc
        except error.URLError as exc:
            raise StravaAPIError(message=f"Could not reach Strava API: {exc}", status_code=502) from exc

    def refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise StravaConfigError(f"Missing refresh_token in token store: {self.token_store_path}")

        payload = parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
        ).encode("utf-8")

        response_body, _ = self._request(
            method="POST",
            url=self.oauth_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
        )

        if not isinstance(response_body, dict) or "access_token" not in response_body:
            raise StravaAPIError("Strava refresh response does not contain access_token")

        self.access_token = str(response_body["access_token"])
        if "refresh_token" in response_body:
            self.refresh_token = str(response_body["refresh_token"])
        if "expires_at" in response_body:
            try:
                self.expires_at = int(response_body["expires_at"])
            except (TypeError, ValueError) as exc:
                raise StravaAPIError("Strava refresh response contains invalid expires_at") from exc
        self._save_tokens_to_store()

    def _is_access_token_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return int(time.time()) >= (self.expires_at - 60)

    def _fetch_recent_activities(self, limit: int) -> tuple[list[dict[str, Any]], StravaRateLimits]:
        url = f"{self.api_base_url}/athlete/activities?per_page={limit}&page=1"
        body, headers = self._request(
            method="GET",
            url=url,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )

        if not isinstance(body, list):
            raise StravaAPIError("Unexpected Strava response for activities list")
        return body, self._extract_rate_limits(headers)

    def _fetch_activities_page(
        self,
        page: int,
        per_page: int,
    ) -> tuple[list[dict[str, Any]], StravaRateLimits]:
        url = f"{self.api_base_url}/athlete/activities?per_page={per_page}&page={page}"
        body, headers = self._request(
            method="GET",
            url=url,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        if not isinstance(body, list):
            raise StravaAPIError("Unexpected Strava response for activities list")
        return body, self._extract_rate_limits(headers)

    def _fetch_activity_by_id(self, activity_id: int) -> tuple[dict[str, Any], StravaRateLimits]:
        url = f"{self.api_base_url}/activities/{activity_id}"
        body, headers = self._request(
            method="GET",
            url=url,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        if not isinstance(body, dict):
            raise StravaAPIError("Unexpected Strava response for activity details")
        return body, self._extract_rate_limits(headers)

    def get_recent_activities(self, limit: int = 2) -> dict[str, Any]:
        self._ensure_basic_config()
        if self._is_access_token_expired():
            self.refresh_access_token()

        normalized_limit = max(1, min(int(limit), 30))
        auto_refreshed = False

        try:
            activities, rate_limits = self._fetch_recent_activities(normalized_limit)
        except StravaAPIError as exc:
            if exc.status_code != 401:
                raise
            self.refresh_access_token()
            auto_refreshed = True
            activities, rate_limits = self._fetch_recent_activities(normalized_limit)

        normalized_activities: list[dict[str, Any]] = []
        for activity in activities:
            distance_m = activity.get("distance")
            distance_km = round(float(distance_m) / 1000, 3) if distance_m is not None else None
            normalized_activities.append(
                {
                    "id": activity.get("id"),
                    "name": activity.get("name"),
                    "sport_type": activity.get("sport_type") or activity.get("type"),
                    "start_date": activity.get("start_date"),
                    "moving_time_seconds": activity.get("moving_time"),
                    "elapsed_time_seconds": activity.get("elapsed_time"),
                    "distance_km": distance_km,
                    "elevation_gain_m": activity.get("total_elevation_gain"),
                }
            )

        return {
            "attempted_limit": normalized_limit,
            "fetched_count": len(normalized_activities),
            "auto_refreshed_token": auto_refreshed,
            "activities": normalized_activities,
            "rate_limits": {
                "global_limit": rate_limits.global_limit,
                "global_usage": rate_limits.global_usage,
                "read_limit": rate_limits.read_limit,
                "read_usage": rate_limits.read_usage,
            },
        }

    def get_activities_page(self, page: int = 1, per_page: int = 30) -> dict[str, Any]:
        self._ensure_basic_config()
        normalized_page = max(1, int(page))
        normalized_per_page = max(1, min(int(per_page), 200))

        auto_refreshed = False
        if self._is_access_token_expired():
            self.refresh_access_token()
            auto_refreshed = True

        try:
            activities, rate_limits = self._fetch_activities_page(
                page=normalized_page,
                per_page=normalized_per_page,
            )
        except StravaAPIError as exc:
            if exc.status_code != 401:
                raise
            self.refresh_access_token()
            auto_refreshed = True
            activities, rate_limits = self._fetch_activities_page(
                page=normalized_page,
                per_page=normalized_per_page,
            )

        return {
            "page": normalized_page,
            "per_page": normalized_per_page,
            "fetched_count": len(activities),
            "auto_refreshed_token": auto_refreshed,
            "activities": activities,
            "rate_limits": {
                "global_limit": rate_limits.global_limit,
                "global_usage": rate_limits.global_usage,
                "read_limit": rate_limits.read_limit,
                "read_usage": rate_limits.read_usage,
            },
        }

    def get_activity_by_id(self, activity_id: int) -> dict[str, Any]:
        self._ensure_basic_config()
        normalized_activity_id = int(activity_id)

        auto_refreshed = False
        if self._is_access_token_expired():
            self.refresh_access_token()
            auto_refreshed = True

        try:
            activity, rate_limits = self._fetch_activity_by_id(normalized_activity_id)
        except StravaAPIError as exc:
            if exc.status_code != 401:
                raise
            self.refresh_access_token()
            auto_refreshed = True
            activity, rate_limits = self._fetch_activity_by_id(normalized_activity_id)

        return {
            "activity": activity,
            "auto_refreshed_token": auto_refreshed,
            "rate_limits": {
                "global_limit": rate_limits.global_limit,
                "global_usage": rate_limits.global_usage,
                "read_limit": rate_limits.read_limit,
                "read_usage": rate_limits.read_usage,
            },
        }
