from app.agents.tool_selector import select_tools
from app.agents.tools import TOOLS


def test_ambiguous_short_message_defaults_to_search_tools_only():
    """'Cotton' alone has no keyword match for any category — should fall
    back to the conservative search subset, not the full registry."""
    selected = {t["name"] for t in select_tools(TOOLS, "Cotton", max_tools=5)}
    assert {"search_products", "check_inventory", "update_search_context"} <= selected
    assert "create_order" not in selected
    assert "accept_quote" not in selected


def test_product_search_message_includes_search_products():
    selected = {t["name"] for t in select_tools(TOOLS, "Show me blue cotton shirts under $50", max_tools=5)}
    assert "search_products" in selected


def test_order_intent_includes_order_tools():
    selected = {t["name"] for t in select_tools(TOOLS, "Place an order for this product", max_tools=5)}
    assert "create_order" in selected


def test_returns_full_list_unchanged_when_already_under_the_cap():
    small_registry = TOOLS[:3]
    assert select_tools(small_registry, "anything", max_tools=5) == small_registry


def test_never_selects_more_than_max_tools():
    selected = select_tools(TOOLS, "Place an order for this product", max_tools=3)
    assert len(selected) <= 3
