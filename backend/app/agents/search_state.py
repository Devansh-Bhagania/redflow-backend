"""Persisted shopping-session state for the Sales/Reordering agents.

Stored on Conversation.agent_state (JSON) and merged, not replaced, on every
update — the agent updates it explicitly via the update_search_context tool
(see tools.py/tool_executor.py) instead of the model having to re-derive
"what does the customer currently want" by re-reading the whole transcript
every turn.
"""

DEFAULT_STATE = {
    "category": None,
    "filters": {},
    "search_results": [],
    "selected_product": None,
    "quantity": None,
}


def merge_search_state(current: dict, updates: dict) -> dict:
    state = {**DEFAULT_STATE, **current}

    if updates.get("clear_filters"):
        state["filters"] = {}
    if "category" in updates and updates["category"] is not None:
        state["category"] = updates["category"]
    if "filters" in updates and updates["filters"]:
        merged_filters = dict(state["filters"])
        for key, value in updates["filters"].items():
            if value is None:
                merged_filters.pop(key, None)
            else:
                merged_filters[key] = value
        state["filters"] = merged_filters
    if "search_results" in updates:
        state["search_results"] = updates["search_results"]
    if "selected_product" in updates and updates["selected_product"] is not None:
        state["selected_product"] = updates["selected_product"]
    if "quantity" in updates and updates["quantity"] is not None:
        state["quantity"] = updates["quantity"]

    return state


def render_search_state(state: dict) -> str:
    if not state or state == DEFAULT_STATE:
        return "Current search state: nothing tracked yet."

    lines = ["Current search state (persisted — treat this as authoritative over guessing from the transcript):"]
    lines.append(f"- Category: {state.get('category') or 'none'}")
    filters = state.get("filters") or {}
    lines.append(f"- Filters: {', '.join(f'{k}={v}' for k, v in filters.items()) or 'none'}")
    lines.append(f"- Selected product: {state.get('selected_product') or 'none'}")
    lines.append(f"- Quantity: {state.get('quantity') if state.get('quantity') is not None else 'none'}")
    return "\n".join(lines)
