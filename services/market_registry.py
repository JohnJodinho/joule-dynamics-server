"""
services/market_registry.py
──────────────────────────────
Dynamic Market Registry — the authoritative source of tracked markets.

Fetches the live market list from Supabase at startup (and on demand) and
caches it in-memory. All downstream consumers (prompts, tools, fallback
strings) read from this registry rather than hardcoded strings.

Benefit: Adding a new market (e.g. Dubai) is now a database operation,
not a code change. No restart required — the next cache refresh picks it up.

SQL return shape (from public.get_tracked_markets):
    TABLE(market character varying, active_properties bigint)
"""

import time
from services.observability import setup_logger

logger = setup_logger(__name__)

# Safe bootstrap fallback used only when DB is unreachable during initial load.
# This list is NEVER used as the authoritative market list in normal operation.
_BOOTSTRAP_FALLBACK: list[str] = ["Miami", "NYC/NJ Metro"]


class MarketRegistry:
    """
    Singleton holding the live market list fetched from Supabase.

    Usage (anywhere in the codebase):
        from services.market_registry import market_registry

        markets = market_registry.get_markets()          # sync, always returns list
        market_registry.refresh()                        # sync DB fetch (call at startup)
        desc    = market_registry.get_formatted()        # "'Miami', 'NYC/NJ Metro', 'Abuja', 'Lagos'"
    """

    def __init__(self) -> None:
        self._markets: list[str] = list(_BOOTSTRAP_FALLBACK)
        self._last_fetched: float = 0.0
        self._initialized: bool = False

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_markets(self) -> list[str]:
        """
        Return the cached market list synchronously.
        Never blocks on a network call — always instant.
        Falls back to the bootstrap list before first successful refresh.
        """
        return list(self._markets)

    def get_formatted(self, separator: str = ", ", quote: bool = True) -> str:
        """
        Return market names as a formatted inline string.
        Default: \"'Miami', 'NYC/NJ Metro', 'Abuja', 'Lagos'\"
        """
        markets = self.get_markets()
        if quote:
            return separator.join(f"'{m}'" for m in markets)
        return separator.join(markets)

    def refresh(self) -> list[str]:
        """
        Synchronously fetch the authoritative market list from Supabase and
        update the in-memory cache. Safe to call at startup (sync context) or
        from a background coroutine via asyncio executor.

        Returns the updated market list. On failure, retains the last known
        cache (graceful degradation — never crashes the application).
        """
        try:
            # Import lazily to avoid circular import at module load time
            from services.supabase_service import supabase as _supabase

            res = _supabase.rpc("get_tracked_markets", {}).execute()
            raw: list[dict] = res.data or []

            # SQL returns: TABLE(market character varying, active_properties bigint)
            markets: list[str] = [
                row["market"].strip()
                for row in raw
                if isinstance(row, dict)
                and isinstance(row.get("market"), str)
                and row["market"].strip()
            ]

            if markets:
                self._markets = markets
                self._last_fetched = time.time()
                self._initialized = True
                logger.info(f"[market_registry] Refreshed — active markets: {markets}")
                # Invalidate the tool embedding cache so new market names are
                # reflected in semantic tool discovery on the next query.
                self._invalidate_tool_embeddings()
            else:
                logger.warning(
                    "[market_registry] RPC returned no markets — retaining last known list."
                )
                if not self._initialized:
                    self._markets = list(_BOOTSTRAP_FALLBACK)
                    self._initialized = True

        except Exception as exc:
            logger.error(f"[market_registry] Supabase fetch failed: {exc}")
            if not self._initialized:
                self._markets = list(_BOOTSTRAP_FALLBACK)
                self._initialized = True
                logger.warning(
                    f"[market_registry] Using bootstrap fallback: {self._markets}"
                )

        return list(self._markets)

    def is_tracked(self, market: str) -> bool:
        """Case-insensitive check whether a market name is currently tracked."""
        market_lower = market.strip().lower()
        return any(m.lower() == market_lower for m in self._markets)

    # ── Internal ────────────────────────────────────────────────────────────────

    def _invalidate_tool_embeddings(self) -> None:
        """
        Reset the tool embedding cache in tools.py so that updated market
        descriptions (which now reflect the new market list) are re-embedded
        on the next tool discovery call.
        """
        try:
            from services import tools as _tools
            _tools._TOOL_EMBEDDINGS = None
            logger.info("[market_registry] Tool embedding cache invalidated — will rebuild on next query.")
        except Exception as exc:
            logger.warning(f"[market_registry] Could not invalidate tool embeddings: {exc}")


# ── Module-level singleton ────────────────────────────────────────────────────
# Import this instance everywhere — never instantiate MarketRegistry directly.
market_registry = MarketRegistry()
