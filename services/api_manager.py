# services/api_manager.py - Fixed Window rate limiter for Gemini API calls

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import duckdb

from schema import get_cached_prediction
from services.gemini_service import GeminiService

logger = logging.getLogger("astrobot.api_manager")

_DEFAULT_RPM_LIMIT = 10
_WINDOW_SECONDS = 60


class ApiManager:
    """Fixed Window rate limiter sitting in front of GeminiService.

    If the per-minute request budget is exhausted, falls back to DuckDB cache
    without touching the Gemini API at all.

    Request flow:
        bot.py → ApiManager → GeminiService (dual-key) → Gemini API
                    ↓ (if rate-limited or circuit open)
                DuckDB cache → FALLBACK_MESSAGE
    """

    def __init__(
        self,
        gemini_service: GeminiService,
        db_conn: duckdb.DuckDBPyConnection,
        rpm_limit: int = _DEFAULT_RPM_LIMIT,
        free_tier_mode: bool = True,
    ) -> None:
        self._gemini = gemini_service
        self._db_conn = db_conn
        self.rpm_limit = rpm_limit
        self.free_tier_mode = free_tier_mode

        self._window_start: datetime = datetime.now()
        self._request_count: int = 0

        logger.info(
            "ApiManager initialised. RPM limit=%d, free_tier_mode=%s",
            rpm_limit,
            free_tier_mode,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def call(
        self,
        prompt: str,
        system_prompt: str,
        cache_type: str,
        name: str,
        curse_used: str | None = None,
        fallback_message: str = "AstRobot-nte lamp went off. KSEB current problem. Try again mone.",
    ) -> tuple[str, bool]:
        """Get a Gemini response, respecting rate limits and circuit state.

        Returns:
            (response_text, is_from_cache): text and whether it came from cache.
        """
        self._refresh_window()

        # Rate limit hit — go straight to cache
        if self._request_count >= self.rpm_limit:
            logger.warning(
                "Rate limit hit (%d/%d RPM). Serving from cache.",
                self._request_count,
                self.rpm_limit,
            )
            cached = self._serve_cache(cache_type, name, curse_used)
            return (cached or fallback_message, True)

        # Circuit open — go to cache
        if self._gemini.is_circuit_open:
            cached = self._serve_cache(cache_type, name, curse_used)
            return (cached or fallback_message, True)

        # Increment window counter before calling
        self._request_count += 1
        logger.debug("Window request %d/%d.", self._request_count, self.rpm_limit)

        result = self._gemini.call(prompt, system_prompt)

        if result is None:
            # GeminiService returned None → both keys failed
            cached = self._serve_cache(cache_type, name, curse_used)
            return (cached or fallback_message, True)

        return (result, False)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _refresh_window(self) -> None:
        """Reset counter if the 60-second window has elapsed."""
        if (datetime.now() - self._window_start).total_seconds() >= _WINDOW_SECONDS:
            logger.debug(
                "Rate limit window reset. Previous count: %d.", self._request_count
            )
            self._window_start = datetime.now()
            self._request_count = 0

    def _serve_cache(
        self,
        cache_type: str,
        name: str,
        curse_used: str | None = None,
    ) -> str | None:
        """Pull a random template from DuckDB cache and personalise it."""
        template = get_cached_prediction(self._db_conn, cache_type, min_count=20)
        if not template:
            return None
        result = template.replace("{user}", name)
        if curse_used:
            result = result.replace("{curse}", curse_used)
        return result

    def status_dict(self) -> dict:
        """Return a status snapshot for /health."""
        self._refresh_window()
        window_resets_in = max(
            0,
            _WINDOW_SECONDS - int((datetime.now() - self._window_start).total_seconds()),
        )
        return {
            "rpm_used": self._request_count,
            "rpm_limit": self.rpm_limit,
            "window_resets_in_seconds": window_resets_in,
            "free_tier_mode": self.free_tier_mode,
        }
