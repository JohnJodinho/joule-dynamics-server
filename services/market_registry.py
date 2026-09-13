"""
Dynamic Market Registry — the authoritative source of tracked markets.

Fetches the live market list from Supabase at startup (and on demand) and
caches it in-memory. All downstream consumers (prompts, tools, fallback
strings) read from this registry rather than hardcoded strings.
"""

import time
from services.observability import setup_logger

logger = setup_logger(__name__)

_BOOTSTRAP_FALLBACK: list[str] = ["Abuja", "Lagos", "Miami", "NYC/NJ Metro"]


class MarketRegistry:
    """
    Singleton holding the live market list fetched from Supabase.

    Usage:
        from services.market_registry import market_registry

        markets = market_registry.get_markets()
        market_registry.refresh()
        desc = market_registry.get_formatted()
    """

    def __init__(self) -> None:
        self._markets: list[str] = list(_BOOTSTRAP_FALLBACK)
        self._last_fetched: float = 0.0
        self._initialized: bool = False

    def get_markets(self) -> list[str]:
        """
        Return the cached market list synchronously.
        Never blocks on a network call — always instant.
        Falls back to the bootstrap list before first successful refresh.
        """
        if not self._initialized:
            self.refresh()
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
        Synchronously fetch authoritative market list from Supabase and update cache.
        Returns the updated market list. On failure, retains the last known cache.
        """
        try:
            from services.supabase_service import supabase as _supabase

            res = _supabase.rpc("get_tracked_markets", {}).execute()
            raw: list[dict] = res.data or []

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

    def _invalidate_tool_embeddings(self) -> None:
        """
        Reset the tool embedding cache in tools.py so that updated market
        descriptions are re-embedded on the next tool discovery call.
        """
        try:
            from services import tools as _tools
            _tools._TOOL_EMBEDDINGS = None
            logger.info("[market_registry] Tool embedding cache invalidated — will rebuild on next query.")
        except Exception as exc:
            logger.warning(f"[market_registry] Could not invalidate tool embeddings: {exc}")


market_registry = MarketRegistry()
