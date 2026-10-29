# AI Agent System — How It Works

Backend: Python 3.12 / FastAPI (`app/main.py`), SQLAlchemy 2.0 async + Postgres, arq (Redis) for fast jobs, Temporal for durable workflows. All agent code lives in `app/agents/`.

## Design in one sentence

There is **no agent class hierarchy and no LLM router**. Six personas share **one** tool-calling loop (`run_agent()` in `app/agents/runtime.py`); which persona runs is decided by data (`Conversation.agent_type`, a workflow node's config, or a sandbox API param), not by a model deciding to hand off.

## The six agents

Defined as a registry in `runtime.py`:

```python
AGENTS = {
  "SALES":       (_sales_prompt,      TOOLS),
  "SUPPORT":     (_support_prompt,    SUPPORT_TOOLS),
  "REORDERING":  (_reordering_prompt, REORDERING_TOOLS),
  "FOLLOWUP":    (_followup_prompt,   FOLLOWUP_TOOLS),
  "INVENTORY":   (_inventory_prompt,  INVENTORY_TOOLS),
  "COLLECTIONS": (_collections_prompt, COLLECTIONS_TOOLS),
}
```

Each entry is just a **(system-prompt builder, tool list)** pair — a persona, not a separate code path. `GET /agents` exposes `AGENT_DESCRIPTIONS` for a human-readable summary of each.

| Agent | Can do | Cannot do |
|---|---|---|
| SALES | search products, quote, order | — |
| SUPPORT | order status, purchase history, knowledge base | quote or order |
| REORDERING | repeat-purchase search/quote/order | — |
| FOLLOWUP | accept + convert an existing quote | create new quotes |
| INVENTORY | check/low-stock queries | customer-facing sales actions |
| COLLECTIONS | check balance, record payment | mention amounts without checking balance first |

These rules are enforced **structurally**, not just by prompt wording (see Authorization below).

## Files

| File | Role |
|---|---|
| `app/agents/runtime.py` | The orchestrator: prompts, provider selection, the tool-use loop |
| `app/agents/tools.py` | Tool schemas (name, description, JSON Schema, `risk_level`), sliced per agent |
| `app/agents/tool_executor.py` | Tool name → async handler; each handler calls a `services/*` module |
| `app/agents/search_state.py` | Persisted shopping-session memory (`Conversation.agent_state`) |
| `app/agents/tool_selector.py` | Rule-based tool pruning, small local models only |
| `app/agents/openai_compatible_client.py` | Shim giving Groq/LM Studio the same `.messages.create()` shape as the Anthropic SDK |

## Providers

`settings.ai_provider` picks the client in `_client_and_model()`:

- **`anthropic`** (default): official `anthropic` SDK, `AsyncAnthropic`, model `claude-opus-5` unless `ai_model` overrides it.
- **`groq`**: `OpenAICompatibleClient` against Groq's OpenAI-compatible endpoint.
- **`lmstudio`**: `OpenAICompatibleClient` against a local server; `ai_model` is required (no sane default for local models).

`OpenAICompatibleClient` converts Anthropic-shaped requests/responses to/from OpenAI chat-completions format so `runtime.py` never branches on provider. It also strips `<think>...</think>` blocks some local models emit, so chain-of-thought never reaches the customer.

**Small-model handling** (`ai_provider in {groq, lmstudio}`): tool list is pruned per-turn by `tool_selector.select_tools()` (keyword-matches the last user message against `search`/`quote`/`order` categories, respects prerequisites — e.g. you need a search before a quote) then compacted (`compact_tools()` drops verbose descriptions), capped at 5 tools/turn. This exists because an overloaded small model given ~15 tool schemas on a short turn was observed to return 0 output tokens.

## The tool-calling loop — `run_agent()`

```
1. Look up (prompt_builder, tools) for agent_type
2. Build system prompt (persona + rules + injected search state for SALES/REORDERING)
3. Insert AiExecution row (status=RUNNING), commit immediately — audit row survives a crash
4. If small model: prune/compact tools for this turn
5. loop up to 6 iterations:
     call LLM with tools=active_tools
     if stop_reason != "tool_use":
         if answer is substantive and not a no-op repeat -> done
         else -> shrink tools once (small models) / retry -> eventually NO_ANSWER_REPLY
     if stop_reason == "tool_use":
         for each tool_use block: execute it, append tool_result, loop again
6. On success: status=SUCCESS. On error: status=FAILED, reply=FALLBACK_REPLY
7. finally: record latency_ms, input/output tokens, estimated_cost onto AiExecution
```

This is a standard bounded ReAct loop (think → call tool → observe → repeat), not a novel architecture.

## Authorization boundary

`_run_tool()` looks up the requested tool name in **the current agent's own tool list**, not the global registry. If the model names a tool outside its persona's list (hallucinated or bled in from context), it raises `PermissionError` and the handler is never called. This is why SUPPORT genuinely cannot place an order even if the model tries — it's not just prompt instruction.

Every tool call (success or failure) is logged as an `AiToolCall` row: `tool_name`, `arguments`, `result_summary`, `status`, `risk_level`, `latency_ms`. A failed handler triggers `db.rollback()` so one bad call doesn't poison the rest of the DB session.

## Tools → services → database

Tool handlers in `tool_executor.py` are thin wrappers — no business logic lives in the agent layer:

```
LLM calls tool → tool_executor handler → services/*.py (product, inventory,
pricing, quotation, order, invoice, knowledge, workflow) → Postgres
```

Two handlers are worth calling out:
- `search_products` persists its results into `Conversation.agent_state` so a later message like "the cotton one" resolves against exactly what was shown, not a re-guess from the transcript.
- `create_quote` → `quotation_service.create_quotation` → `send_quotation` → `workflow_service.trigger_quote_sent_workflow`, which **starts a Temporal workflow** (quote-sent → wait → check response → nudge or notify team). This is the one place an agent's own action reaches into the workflow system.

## Memory: `agent_state`

`Conversation.agent_state` (JSON column) holds `{category, filters, search_results, selected_product, quantity}`. `merge_search_state()` merges rather than replaces on each update; `render_search_state()` renders it into the SALES/REORDERING system prompt as authoritative state the model should trust over re-deriving it from the transcript. Conversation history itself (`Conversation.messages`) is the only other memory — there's no vector store or long-term memory beyond this.

## How agents get triggered — 3 entry points

**1. Live WhatsApp inbound (async, via arq/Redis)**
`POST /webhooks/whatsapp` verifies the signature, resolves/creates customer + conversation, persists the inbound message, commits, then enqueues `process_inbound_message` on arq — keeping the webhook response fast. The arq worker (`app/workers/tasks.py`) re-fetches the conversation, checks `ai_enabled`, calls `run_agent()`, persists the outbound message **before** attempting delivery (so a WhatsApp outage doesn't silently eat a reply that already cost tokens), then sends it and patches in the provider's message id.

**2. Agent Sandbox (synchronous REST, for testing/demoing)**
`app/api/v1/agents.py`:
- `GET /agents` — list personas + tools
- `GET /agents/sandbox` — get/create an isolated `WEB`-channel sandbox conversation
- `POST /agents/sandbox/messages` — calls `run_agent()` synchronously in-request; real tools/services/DB, isolated conversation
- `POST /agents/sandbox/reset` — clears sandbox messages + `agent_state`

**3. Temporal workflows (proactive automation)**
`app/workflows/schema.py` defines a node-graph DSL (`TRIGGER`, `ACTION`, `CONDITION`, `DELAY`, `AI_AGENT`, `END`). `GraphWorkflow._walk()` interprets it; an `AI_AGENT` node runs the `run_ai_agent_node` Temporal activity (90s timeout, 3 retries), which resolves org/customer/conversation and calls `run_agent()`, then sends the reply via WhatsApp/Email. Example chain: quote sent → `create_quote` tool triggers this workflow → Temporal DELAY → CONDITION checks `quote_responded` → branches to a FOLLOWUP `AI_AGENT` node or a `notify_team_quote_stalled` ACTION.

## Data model

- **`Conversation`**: `channel`, `status`, `ai_enabled` (per-conversation kill switch), `agent_type`, `agent_state` (JSON memory), `messages`.
- **`Message`**: `direction`, `content`, `ai_generated`, `external_message_id` (unique — webhook idempotency).
- **`AiExecution`**: one row per `run_agent()` call — model, status, latency, tokens, estimated cost. The audit/analytics trail.
- **`AiToolCall`**: one row per tool invocation — name, arguments, result, status, risk level, latency.

## Config (`app/core/config.py`, from `.env`)

`ai_provider`, `ai_api_key`, `ai_model`, `ai_base_url`, `ai_input_price_per_million`, `ai_output_price_per_million`, plus general infra vars (`database_url`, `redis_url`, `temporal_url`, `whatsapp_verify_token`, JWT/encryption keys used elsewhere).

## What this system deliberately does NOT have

- No LLM-driven router/supervisor that picks or hands off between agents mid-conversation — persona selection is external (human, config, or workflow designer).
- No cross-agent handoff execution — prompts tell the model to *say* "connecting you with sales," but nothing in code performs that handoff (`NO_ANSWER_REPLY`'s docstring says so explicitly).
- No vector DB / long-term memory beyond `agent_state` and conversation history.

## End-to-end example (WhatsApp)

```
WhatsApp message
  → POST /webhooks/whatsapp (signature verified, message persisted)
  → arq.enqueue_job("process_inbound_message")
  → workers/tasks.py: process_inbound_message
      → agents/runtime.py: run_agent(agent_type=conversation.agent_type)
          → builds system prompt (persona + agent_state)
          → LLM call → tool_use? → tool_executor handler → services/* → Postgres
            (create_quote also kicks off a Temporal follow-up workflow)
          → tool result fed back to LLM → repeat until final text or 6 iterations
          → AiExecution + AiToolCall rows logged throughout
  → outbound Message persisted (ai_generated=True)
  → sent via WhatsAppProvider, external_message_id patched in
```
