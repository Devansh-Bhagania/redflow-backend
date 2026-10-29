"""Context access shared by the Temporal interpreter and the sandbox runner.

Both walkers need to read values out of the run context (SWITCH's `on`,
LOOP's `over`) and substitute them into strings (HTTP_REQUEST urls, SET
values). Keeping that here means the two interpreters resolve a path the same
way instead of drifting.

Deliberately not an expression language: `{{a.b.c}}` looks up a path and
nothing else. No arithmetic, no function calls, no eval. A workflow definition
can be written by an AI agent, so the substitution step is a trust boundary —
the cheapest way to keep it safe is for it to have no capabilities to abuse.
"""

import re

_TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\[\]-]+)\s*\}\}")

# A path like "order.items[0].sku" walks dicts by key and lists by index.
_SEGMENT = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def resolve_path(context: dict, path: str, default=None):
    """Reads a dotted/indexed path out of the context. Missing -> `default`."""
    current = context
    for key, index in _SEGMENT.findall(path):
        if index:
            if not isinstance(current, (list, tuple)):
                return default
            position = int(index)
            if position >= len(current):
                return default
            current = current[position]
        else:
            if not isinstance(current, dict) or key not in current:
                return default
            current = current[key]
    return current


def render_template(value, context: dict):
    """Substitutes {{path}} references inside a string, or recursively inside
    a dict/list. Non-string leaves are returned untouched.

    A string that is *exactly* one placeholder yields the resolved value with
    its original type (so `{"qty": "{{order.count}}"}` sends a JSON number,
    not the string "3"). Mixed text always renders as a string.
    """
    if isinstance(value, dict):
        return {k: render_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [render_template(v, context) for v in value]
    if not isinstance(value, str):
        return value

    whole = _TEMPLATE.fullmatch(value.strip())
    if whole:
        return resolve_path(context, whole.group(1))
    return _TEMPLATE.sub(lambda m: _stringify(resolve_path(context, m.group(1))), value)


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        # "true"/"false" rather than Python's "True"/"False" — these end up in
        # URLs and JSON bodies read by other systems.
        return "true" if value else "false"
    return str(value)
