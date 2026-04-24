# services/gemini_service.py - Gemini API wrapper with dual-key fallback + circuit breaker

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import duckdb
from google import genai

logger = logging.getLogger("astrobot.gemini")

_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_OPEN_DURATION_MINUTES = 30


class GeminiService:
    """Wraps Gemini API calls with:
    - Free-key-first, paid-key-fallback dual key strategy
    - Circuit breaker: 3 failures → cache-only for 30 minutes
    """

    def __init__(
        self,
        free_api_key: str | None,
        paid_api_key: str | None,
        db_conn: duckdb.DuckDBPyConnection,
    ) -> None:
        if not free_api_key and not paid_api_key:
            raise ValueError(
                "AstRobot needs at least one Gemini API key. "
                "Set GEMINI_API_KEY_FREE or GEMINI_API_KEY_PAID in .env"
            )

        self._free_client: genai.Client | None = (
            genai.Client(api_key=free_api_key) if free_api_key else None
        )
        self._paid_client: genai.Client | None = (
            genai.Client(api_key=paid_api_key) if paid_api_key else None
        )
        self._db_conn = db_conn

        # Circuit breaker state
        self.failure_count: int = 0
        self.open_until: datetime | None = None
        self.active_key: str = "none"  # "free" | "paid" | "none"
        self.free_only: bool = False

        # Usage counters (lifetime, since last bot restart)
        self.free_calls: int = 0
        self.paid_calls: int = 0
        self.failed_calls: int = 0

        keys_available = []
        if free_api_key:
            keys_available.append("free")
        if paid_api_key:
            keys_available.append("paid")
        logger.info("GeminiService initialised. Available keys: %s", keys_available)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_circuit_open(self) -> bool:
        """True when the circuit breaker has tripped and we're in cache-only mode."""
        if self.open_until is None:
            return False
        if datetime.now() >= self.open_until:
            # Auto-reset
            logger.info("Circuit breaker reset — resuming API calls.")
            self.failure_count = 0
            self.open_until = None
            return False
        return True

    def call(self, prompt: str, system_prompt: str) -> str | None:
        """Attempt a Gemini API call using free key first, then paid key.

        Returns the response text on success, or None on total failure
        (caller should then fall back to the DuckDB cache).
        """
        if self.is_circuit_open:
            remaining = (self.open_until - datetime.now()).seconds // 60  # type: ignore[operator]
            logger.warning(
                "Circuit OPEN — cache-only mode for ~%d more minutes.", remaining
            )
            return None

        # Try free key first
        if self._free_client:
            result = self._try_key(self._free_client, prompt, system_prompt, key_name="free")
            if result is not None:
                self.failure_count = 0  # reset on any success
                self.active_key = "free"
                self.free_calls += 1
                return result

        # Fall back to paid key (only if not in free-only mode)
        if self._paid_client and not self.free_only:
            result = self._try_key(self._paid_client, prompt, system_prompt, key_name="paid")
            if result is not None:
                self.failure_count = 0
                self.active_key = "paid"
                self.paid_calls += 1
                return result

        # All available keys failed — increment circuit breaker
        self.failure_count += 1
        self.failed_calls += 1
        self.active_key = "none"
        logger.error(
            "API keys failed (free_only=%s). Circuit failure count: %d/%d",
            self.free_only,
            self.failure_count,
            _CIRCUIT_FAILURE_THRESHOLD,
        )
        if self.failure_count >= _CIRCUIT_FAILURE_THRESHOLD:
            self.open_until = datetime.now() + timedelta(minutes=_CIRCUIT_OPEN_DURATION_MINUTES)
            logger.error(
                "Circuit OPENED. Cache-only mode until %s.", self.open_until.isoformat()
            )
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _try_key(
        self,
        client: genai.Client,
        prompt: str,
        system_prompt: str,
        key_name: str,
    ) -> str | None:
        """Attempt a single Gemini call. Returns text on success, None on failure."""
        try:
            logger.debug("Trying %s key for prompt: %.80s...", key_name, prompt)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.8,
                },
            )
            text = response.text
            logger.info("Gemini %s key responded OK (%.60s...).", key_name, text)
            return text
        except Exception as exc:
            logger.warning("Gemini %s key failed: %s: %s", key_name, type(exc).__name__, exc)
            return None

    def status_dict(self) -> dict:
        """Return a status snapshot for the /health command."""
        total = self.free_calls + self.paid_calls + self.failed_calls
        free_pct = round(self.free_calls / total * 100) if total else 0
        paid_pct = round(self.paid_calls / total * 100) if total else 0
        return {
            "circuit_open": self.is_circuit_open,
            "failure_count": self.failure_count,
            "open_until": self.open_until.isoformat() if self.open_until else "N/A",
            "active_key": self.active_key,
            "free_key_available": self._free_client is not None,
            "paid_key_available": self._paid_client is not None,
            # Usage counters
            "free_calls": self.free_calls,
            "paid_calls": self.paid_calls,
            "failed_calls": self.failed_calls,
            "total_calls": total,
            "free_pct": free_pct,
            "paid_pct": paid_pct,
        }
