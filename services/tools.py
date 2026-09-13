"""Real Estate Intelligence Layer - Tool Registry & Dynamic Tool Discovery."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import numpy as np

from services.tool_schemas import (
    COMMERCIAL_TOOLS,
    GENERATE_CONTACT_BUTTONS_TOOL,
    GENERATE_DATA_EXPORT_TOOL,
    REAL_ESTATE_TOOLS,
    SUGGEST_ACTIONS_TOOL,
)

__all__ = [
    "REAL_ESTATE_TOOLS",
    "COMMERCIAL_TOOLS",
    "SUGGEST_ACTIONS_TOOL",
    "GENERATE_DATA_EXPORT_TOOL",
    "GENERATE_CONTACT_BUTTONS_TOOL",
    "TOOL_REGISTRY",
    "discover_tools",
    "select_tools",
    "invalidate_tool_embeddings",
]
TOOL_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "get_real_estate_kpis",
        "category": "MARKET",
        "retrieval_description": "High-level summary cards, aggregate real estate portfolio metrics, active properties count, availability count, 7-day rate changes, scrape health status, overall KPI numbers.",
        "schema": REAL_ESTATE_TOOLS[0],
    },
    {
        "name": "get_market_averages",
        "category": "MARKET",
        "retrieval_description": "Average nightly rate and price baselines for any tracked market region over 7-day, 14-day, or 30-day periods, market benchmark pricing.",
        "schema": REAL_ESTATE_TOOLS[1],
    },
    {
        "name": "get_market_snapshot",
        "category": "MARKET",
        "retrieval_description": "Daily overview snapshot of market performance, market condition in any tracked city or region, nightly rates range, minimum maximum rates, daily summary and availability.",
        "schema": REAL_ESTATE_TOOLS[2],
    },
    {
        "name": "get_market_trend",
        "category": "MARKET",
        "retrieval_description": "Historical pricing trend direction, rising or falling rates over time, week-over-week rate trajectory in any tracked market region.",
        "schema": REAL_ESTATE_TOOLS[3],
    },
    {
        "name": "get_market_rate_changes",
        "category": "ANOMALY",
        "categories": ["ANOMALY", "MARKET", "PROPERTY"],
        "retrieval_description": (
            "Count of properties with any nightly rate increase or decrease in a market, "
            "number of price increases, number of price decreases, properties that changed rates, "
            "rate movements not just spikes, any rate change at all, how many listings adjusted rates, "
            "properties with rate increases or decreases in Nigeria Abuja Lagos, how many had price changes, "
            "nightly rate went up down in last few days."
        ),
        "schema": REAL_ESTATE_TOOLS[4],
    },
    {
        "name": "get_spike_alerts",
        "category": "ANOMALY",
        "retrieval_description": "Sudden sharp price jumps, 25% plus rate increases, price spikes, surge pricing alerts, unseasonal rate surges in any tracked market, ranked slices.",
        "schema": REAL_ESTATE_TOOLS[5],
    },
    {
        "name": "get_rate_anomaly_report",
        "category": "ANOMALY",
        "retrieval_description": "Anomalous pricing patterns, drastic rate drops, price crashes, listings deviating significantly from market baseline.",
        "schema": REAL_ESTATE_TOOLS[6],
    },
    {
        "name": "get_most_volatile_properties",
        "category": "ANOMALY",
        "retrieval_description": "Listings with the most frequent price fluctuations, highest number of rate adjustments, unstable dynamic pricing, ranked slices.",
        "schema": REAL_ESTATE_TOOLS[7],
    },
    {
        "name": "get_property_snapshot",
        "category": "PROPERTY",
        "retrieval_description": "Detailed current status and single listing profile for a specific property UUID, property name, or listing URL.",
        "schema": REAL_ESTATE_TOOLS[8],
    },
    {
        "name": "get_property_detail",
        "category": "PROPERTY",
        "retrieval_description": "Deep single-property exploration, full listing profile, active tracking status, coordinates, URL, and daily-aggregated historical rate revision log.",
        "schema": REAL_ESTATE_TOOLS[9],
    },
    {
        "name": "get_property_rate_changes",
        "category": "PROPERTY",
        "retrieval_description": "Historical log of price changes, chronological rate revisions, daily past rate adjustments for a single property.",
        "schema": REAL_ESTATE_TOOLS[10],
    },
    {
        "name": "compare_properties",
        "category": "PROPERTY",
        "retrieval_description": "Side-by-side head-to-head comparison of multiple property listings, prices, bedrooms, and metrics.",
        "schema": REAL_ESTATE_TOOLS[11],
    },
    {
        "name": "search_properties",
        "category": "PROPERTY",
        "retrieval_description": "Find, list, and filter property listings by bedroom count, price range, market region, availability status, listing name, or ranked slices.",
        "schema": REAL_ESTATE_TOOLS[12],
    },
    {
        "name": "get_availability_rate",
        "category": "PROPERTY",
        "retrieval_description": "Percentage of units booked versus available, occupancy rates, reservation calendar status in any tracked market or city.",
        "schema": REAL_ESTATE_TOOLS[13],
    },
    {
        "name": "geocode_address",
        "category": "GEO",
        "retrieval_description": "Convert street address, landmark, or neighborhood name into latitude and longitude geographic coordinates.",
        "schema": REAL_ESTATE_TOOLS[14],
    },
    {
        "name": "get_nearby_properties",
        "category": "GEO",
        "retrieval_description": "Find listings closest to a specific geographic coordinate, landmark, or neighborhood radius.",
        "schema": REAL_ESTATE_TOOLS[15],
    },
    {
        "name": "get_distance_km",
        "category": "GEO",
        "retrieval_description": "Calculate straight-line distance in kilometers between two properties or locations by UUID.",
        "schema": REAL_ESTATE_TOOLS[16],
    },
    {
        "name": "get_tracked_markets",
        "category": "MARKET",
        "retrieval_description": "List all cities and regions currently supported and monitored in the database, fetch the live market registry.",
        "schema": REAL_ESTATE_TOOLS[17],
    },
    {
        "name": "get_recently_changed_tracking",
        "category": "MARKET",
        "retrieval_description": "Properties recently added to or removed from active monitoring tracking status.",
        "schema": REAL_ESTATE_TOOLS[18],
    },
]

_TOOL_EMBEDDINGS: Optional[np.ndarray] = None


def _get_tool_embeddings() -> np.ndarray:
    """Compute and cache normalized vector embeddings for all retrieval descriptions."""
    global _TOOL_EMBEDDINGS
    if _TOOL_EMBEDDINGS is None:
        from services.embedding_service import get_embedding_model
        embed_model = get_embedding_model()
        descs = [entry["retrieval_description"] for entry in TOOL_REGISTRY]
        _TOOL_EMBEDDINGS = embed_model.encode(descs, normalize_embeddings=True)
    return _TOOL_EMBEDDINGS


def _filter_candidate_indices(categories: Optional[List[str]]) -> List[int]:
    """Filter tool registry indices by category gating if categories specified."""
    if not categories:
        return list(range(len(TOOL_REGISTRY)))

    allowed_cats = {c.upper() for c in categories}
    candidates: List[int] = []
    for i, entry in enumerate(TOOL_REGISTRY):
        entry_cats = entry.get("categories")
        if entry_cats:
            if any(c.upper() in allowed_cats for c in entry_cats):
                candidates.append(i)
        elif entry.get("category", "").upper() in allowed_cats:
            candidates.append(i)

    return candidates or list(range(len(TOOL_REGISTRY)))


def _rank_candidate_tools(
    embed_model: Any,
    tool_vectors: np.ndarray,
    candidate_indices: List[int],
    user_query: str,
    top_k: int,
) -> List[Dict[str, Any]]:
    """Rank candidate tools by vector similarity against the user query."""
    q_vec = embed_model.encode([user_query], normalize_embeddings=True)[0]
    candidate_vecs = tool_vectors[candidate_indices]
    sims = candidate_vecs @ q_vec
    ranked_local_indices = np.argsort(sims)[::-1][:top_k]
    return [
        TOOL_REGISTRY[candidate_indices[local_idx]]["schema"]
        for local_idx in ranked_local_indices
    ]


def _attach_universal_tools(
    selected_tools: List[Dict[str, Any]],
    include_export: bool,
) -> List[Dict[str, Any]]:
    """Deduplicate tools and append universal export tool if requested."""
    results: List[Dict[str, Any]] = []
    seen_names: set[str] = set()

    for tool in selected_tools:
        name = tool["function"]["name"]
        if name not in seen_names:
            seen_names.add(name)
            results.append(tool)

    if include_export and "generate_data_export" not in seen_names:
        results.append(GENERATE_DATA_EXPORT_TOOL)

    return results


def discover_tools(
    user_query: str,
    categories: Optional[List[str]] = None,
    top_k: int = 4,
    include_export: bool = True,
) -> List[Dict[str, Any]]:
    """Hierarchical dynamic tool discovery ranking top-K tools for user query."""
    try:
        from services.embedding_service import get_embedding_model
        embed_model = get_embedding_model()
        tool_vectors = _get_tool_embeddings()
        candidate_indices = _filter_candidate_indices(categories)
        selected_tools = _rank_candidate_tools(
            embed_model, tool_vectors, candidate_indices, user_query, top_k
        )
    except Exception:
        selected_tools = [
            t for t in REAL_ESTATE_TOOLS
            if t["function"]["name"] in ("get_market_averages", "get_market_snapshot", "get_spike_alerts")
        ]

    return _attach_universal_tools(selected_tools, include_export)


def invalidate_tool_embeddings() -> None:
    """Reset the tool embedding cache upon market registry refreshes."""
    global _TOOL_EMBEDDINGS
    _TOOL_EMBEDDINGS = None


def select_tools(user_query: str) -> List[Dict[str, Any]]:
    """Backwards-compatible alias forwarding to discover_tools()."""
    return discover_tools(user_query)
