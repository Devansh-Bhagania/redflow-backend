"""Rule-based tool routing for small/local models.

The Sales Agent's full tool registry (search, pricing, quoting, ordering) is
fine for a hosted frontier model but overloads a small local model on short
turns — see the retry logic in runtime.py this supports. Cloud models keep
getting the full registry unchanged; this only kicks in for the small-model
branch of run_agent.

Each category maps to the tool names that belong to it and, via
CATEGORY_PREREQS, the categories a customer typically needs *before* that one
(e.g. you can't sensibly order without having searched and quoted first).
Adding a new tool just means adding its name to a category below — no new
mapping mechanism to keep in sync elsewhere.
"""

CATEGORY_TOOLS: dict[str, tuple[str, ...]] = {
    "search": ("search_products", "check_inventory", "update_search_context"),
    "quote": ("calculate_price", "create_quote", "accept_quote"),
    "order": ("create_order",),
}

CATEGORY_PREREQS: dict[str, tuple[str, ...]] = {
    "quote": ("search",),
    "order": ("search", "quote"),
}

_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "order": (
        "place an order", "place the order", "confirm the order", "checkout",
        "proceed with the order", "convert to order", "buy it now",
    ),
    "quote": ("quote", "how much", "price", "cost", "estimate", "accept", "confirmed", "go ahead", "sounds good"),
    "search": (
        "search", "find", "show me", "looking for", "do you have", "browse",
        "want to buy", "need", "available", "in stock",
    ),
}


def _matched_categories(user_message: str) -> list[str]:
    text = (user_message or "").lower()
    return [cat for cat, keywords in _CATEGORY_KEYWORDS.items() if any(kw in text for kw in keywords)]


def select_tools(tools: list[dict], user_message: str, max_tools: int) -> list[dict]:
    """Pick at most `max_tools` from `tools`, prioritized by what the
    customer's message is actually about. Falls back to the conservative
    "search" category when nothing matches, rather than guessing."""
    if len(tools) <= max_tools:
        return tools

    available = {t["name"]: t for t in tools}
    matched = _matched_categories(user_message) or ["search"]

    ordered_categories = list(matched)
    for cat in matched:
        for prereq in CATEGORY_PREREQS.get(cat, ()):
            if prereq not in ordered_categories:
                ordered_categories.append(prereq)

    selected_names: list[str] = []
    for cat in ordered_categories:
        for name in CATEGORY_TOOLS.get(cat, ()):
            if len(selected_names) >= max_tools:
                break
            if name in available and name not in selected_names:
                selected_names.append(name)

    return [available[n] for n in selected_names] or tools[:max_tools]
