from app.agents.search_state import DEFAULT_STATE, merge_search_state, render_search_state


def test_merge_starts_from_default_state_when_current_is_empty():
    state = merge_search_state({}, {"category": "shirt"})
    assert state["category"] == "shirt"
    assert state["filters"] == {}
    assert state["quantity"] is None


def test_merge_accumulates_filters_across_calls_instead_of_replacing():
    state = merge_search_state({}, {"filters": {"material": "cotton"}})
    state = merge_search_state(state, {"filters": {"size": "S"}})
    assert state["filters"] == {"material": "cotton", "size": "S"}


def test_merge_overwrites_a_filter_key_when_given_again():
    state = merge_search_state({}, {"filters": {"size": "S"}})
    state = merge_search_state(state, {"filters": {"size": "M"}})
    assert state["filters"] == {"size": "M"}


def test_clear_filters_drops_previously_tracked_filters():
    state = merge_search_state({}, {"category": "shirt", "filters": {"material": "cotton", "size": "S"}})
    state = merge_search_state(state, {"category": "polo shirt", "clear_filters": True})
    assert state["category"] == "polo shirt"
    assert state["filters"] == {}


def test_merge_updates_selected_product_and_quantity():
    state = merge_search_state({}, {"selected_product_id": "abc-123"})
    # selected_product_id isn't a state field itself — the tool executor
    # resolves it to a product summary before merging as "selected_product".
    state = merge_search_state(state, {"selected_product": {"variant_id": "abc-123", "name": "Urban Polo"}})
    state = merge_search_state(state, {"quantity": 20})
    assert state["selected_product"] == {"variant_id": "abc-123", "name": "Urban Polo"}
    assert state["quantity"] == 20


def test_search_results_are_replaced_wholesale_not_merged():
    state = merge_search_state({}, {"search_results": [{"name": "A"}]})
    state = merge_search_state(state, {"search_results": [{"name": "B"}]})
    assert state["search_results"] == [{"name": "B"}]


def test_render_reports_nothing_tracked_for_default_state():
    assert render_search_state({}) == "Current search state: nothing tracked yet."
    assert render_search_state(DEFAULT_STATE) == "Current search state: nothing tracked yet."


def test_render_shows_populated_fields():
    state = merge_search_state({}, {"category": "shirt", "filters": {"material": "cotton"}, "quantity": 20})
    text = render_search_state(state)
    assert "Category: shirt" in text
    assert "material=cotton" in text
    assert "Quantity: 20" in text
