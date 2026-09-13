"""Common helpers for tool schema descriptions."""

def market_desc() -> str:
    """Returns a live, formatted string of currently tracked market names."""
    try:
        from services.market_registry import market_registry
        markets = market_registry.get_markets()
        if markets:
            return ", ".join(f"'{m}'" for m in markets)
    except Exception:
        pass
    return "any tracked market (call get_tracked_markets for the live list)"
