# Redflow

AI Operating System for B2B commerce — AI employees that handle wholesale/distributor
sales, support, and operations across WhatsApp, email, and other channels.

This repo is being built incrementally, phase by phase (see the phase plan below).
**Phases 1–9 are done**: foundation (auth, orgs, RBAC), the B2B core (customers,
products/variants, inventory, pricing, quotations, orders), communication (unified
inbox, a real WhatsApp Cloud API webhook, plus outbound email), AI (a tool-calling Sales
Agent on Claude Opus 5, plus Support/Reordering agents backed by a keyword-search
knowledge base), a durable workflow engine on real Temporal that automatically follows
up on quotes that go quiet, and an analytics summary endpoint with AI cost tracking.

## Stack

- **Frontend**: `frontend/` — Next.js (App Router), TypeScript, Tailwind.
- **Backend**: `backend/` — FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL, Argon2 + JWT auth.
- **Infra**: `docker-compose.yml` — Postgres, Redis, Temporal, api, `worker` (ARQ — AI
  turns), `temporal-worker` (the workflow engine), web.

## Running locally

```bash
cp .env.example .env
docker compose up -d
docker compose exec api alembic upgrade head
```

- API: http://localhost:8000 (docs at `/docs`)
- Web: http://localhost:3000

Note: the compose file maps Postgres to host port `5433` and Redis to `6380` to avoid
clashing with any local Postgres/Redis already running on the default ports. Containers
still talk to each other over the default `5432`/`6379` via the Docker network.

## Backend dev (without Docker)

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
pytest
```

Tests run against an in-memory SQLite DB and don't require Postgres to be running.

## What exists today

- `POST /api/v1/auth/register` — creates a user + organization, first member becomes OWNER.
- `POST /api/v1/auth/login`, `/refresh` (rotating refresh tokens), `/logout`.
- `GET /api/v1/auth/me` — current user, organization, role, and permissions.
- `GET/POST /api/v1/organizations/members` — list/invite team members (RBAC-gated).
- 8 system roles (OWNER..VIEWER) seeded with a default permission matrix
  (`app/core/permissions.py`), enforced via `require_permission()` on every route.
- Tenant isolation: `organization_id` is always derived from the verified JWT, never
  from a client-supplied parameter.
- **Frontend** (`frontend/app/`): `/register`, `/login`, and a protected `/app/*` shell
  (`app/app/layout.tsx`) that redirects to `/login` when unauthenticated and hides nav
  items the current role lacks permission for. Full pages, each backed by the matching
  API, not stubs: dashboard (analytics stats), customers, products, inventory
  (warehouses + stock receiving), quotes (create/send/accept/convert-to-order),
  orders, conversations (the inbox — thread view, send a reply, toggle `ai_enabled`,
  reassign `agent_type`), workflows (list/publish/executions), knowledge base
  (create/publish articles), invoices (status/amount due/paid), integrations (connect
  WhatsApp/email credentials), and team (list/invite members).
- `POST /api/v1/customers`, `/products` (with nested variants), `/inventory/warehouses`,
  `/inventory/receive`, `/quotes` (+ `/send`, `/accept`), `/orders`.
- Pricing engine (`app/services/pricing_service.py`): resolves a customer's unit price
  from their assigned price list's volume tiers, falling back to the variant/product
  base price — the AI layer (Phase 4) will call this, never invent a number.
- Inventory reservation (`app/services/inventory_service.py`): row-locked (`SELECT ...
  FOR UPDATE`), so two concurrent orders can't oversell the same stock.
- Order creation is idempotent: retrying `POST /orders` with the same `idempotency_key`
  returns the original order instead of double-reserving stock or double-charging.
- Verified end-to-end against a real dockerized Postgres: register → create product →
  receive stock → quote → send → accept → convert to order → inventory reserved.
- `POST /api/v1/integrations/whatsapp/connect` — per-org WhatsApp credentials (phone
  number id, access token, app secret), encrypted at rest with Fernet
  (`app/core/crypto.py`) before hitting the DB.
- `GET/POST /api/v1/webhooks/whatsapp` — Meta's callback verification challenge, plus
  the real inbound webhook: HMAC-SHA256 signature check against the connected org's app
  secret, then an unknown WhatsApp number auto-creates a lead `Customer` and an `OPEN`
  `Conversation`. Delivery is idempotent — `messages.external_message_id` is unique, so a
  retried webhook (Meta redelivers on timeout) never double-records a message.
- `GET/PATCH /api/v1/conversations`, `POST /api/v1/conversations/{id}/messages` — the
  staff-facing inbox: list/view threads, send an outbound WhatsApp reply through the
  provider abstraction, toggle `ai_enabled` or reassign a conversation.
- Verified live: a webhook payload matching the spec's example ("Hi, I need 200 black
  T-shirts, 100 M and 100 L.") signed with a real HMAC, POSTed to the running container,
  correctly created the lead customer + conversation + message; a forged signature got
  401'd; a replayed delivery stayed at one message.
- **AI Sales Agent** (`app/agents/`): a tool-calling loop against Claude Opus 5
  (`app/agents/runtime.py`). The model never touches the database or invents a number —
  it can only call the six tools in `app/agents/tools.py` (`search_products`,
  `check_inventory`, `calculate_price`, `create_quote`, `accept_quote`, `create_order`),
  each a thin wrapper (`app/agents/tool_executor.py`) around the same domain services the
  REST API uses, scoped to the conversation's `organization_id` — never a value the AI
  supplies. `create_order` is tagged `SENSITIVE` in the tool registry (Section 7's
  three-tier model exists as a field today; the approval-request workflow itself is
  Phase 4b — see skips below).
- Every run writes an `ai_executions` row (model, status, latency, token counts) and one
  `ai_tool_calls` row per tool call (arguments, result, status, risk level) — real
  execution logs, not a placeholder table.
- Webhook handling stays fast: the webhook only persists the inbound message and enqueues
  a job (`app/core/queue.py`, ARQ over Redis); the actual AI turn — and the outbound
  WhatsApp reply — runs in a separate `worker` container (`app/workers/`), matching the
  "never run expensive AI ops in the webhook handler" rule from the spec.
- Verified live end-to-end: a signed webhook reproducing the spec's own example ("Hi, do
  you have black t-shirts in stock?") was enqueued, picked up by the worker container,
  ran the agent loop, and correctly wrote a `FAILED` `ai_executions` row with a graceful
  fallback reply (no `AI_API_KEY` configured in this environment) — proving the
  queue → worker → agent → logging path end to end, including the failure path.
- **Workflow engine** (`app/workflows/`): a small generic graph interpreter —
  `TRIGGER`/`ACTION`/`CONDITION`/`DELAY`/`END` nodes — running as an actual durable
  Temporal workflow (`GraphWorkflow`), not a cron job. `DELAY` nodes can be days long;
  Temporal persists the workflow's position so a worker restart mid-wait resumes exactly
  where it left off. Every AI/user-submitted graph is validated by
  `app/workflows/schema.py` (`WorkflowDefinition`) before it's stored or run — unique
  node ids, exactly one `TRIGGER`, no dangling edges, required config per node type —
  never execute unvalidated JSON.
- `POST /api/v1/workflows` (validated create), `/publish`, `GET /workflows`,
  `/workflows/{id}`, `/workflows/executions` — full CRUD, RBAC-gated
  (`workflows.read`/`workflows.manage`, already seeded in Phase 1's permission matrix).
- **Built-in quote follow-up workflow**: sending a quotation (staff API or the AI's
  `create_quote` tool) fires `quote.sent`, which starts the org's own `PUBLISHED`
  `quote.sent` workflow if it has one, else a built-in default matching the spec's example
  exactly: wait 24h → check for a response → if none, send a WhatsApp nudge → wait 48h →
  check again → if still nothing, notify the team (`notifications` table) and end. A
  Temporal outage never blocks sending the quote — starting the workflow is wrapped and
  logged, not fatal.
- Every node execution writes a `workflow_execution_steps` row (audit trail), and the
  workflow's own final activity marks `workflow_executions.status = COMPLETED`.
- Verified live against a real dockerized Temporal server (not mocked): published a test
  workflow with 3-second delays via the API, sent a quote against it with no WhatsApp
  connected, and watched the *entire* graph run for real — trigger → delay → condition
  (false) → follow-up action → delay → condition (false) → escalate action → end — with
  all 8 steps logged, the execution correctly marked `COMPLETED`, and a real row landing
  in `notifications` ("Quote QCFE73B73 ... has had no customer response after two
  follow-ups").

- **Support and Reordering agents** (`app/agents/runtime.py`): `AGENTS` now maps
  `agent_type` → (system prompt, tool list), and `run_agent()` replaces the old
  hardcoded Sales-only loop (`run_sales_agent` is now a one-line wrapper around it, kept
  for existing callers). Each `Conversation` carries its own `agent_type` (defaults to
  `SALES`, `PATCH /api/v1/conversations/{id}` reassigns it), and the inbound-message
  worker (`app/workers/tasks.py`) runs whichever agent the conversation is assigned to.
  - **Support**: `search_products`, `check_inventory`, and a new `get_order_status` tool
    (`app/services/order_service.get_order_by_number_for_customer`, scoped to the
    calling customer). Cannot quote or order.
  - **Reordering**: a new `get_purchase_history` tool
    (`order_service.list_orders_for_customer`) plus the Sales Agent's quoting tools, so
    it can look at what a customer bought before and requote it.

- **Follow-up, Inventory, and Collections agents** (`app/agents/runtime.py`,
  `agent_type` now also accepts `FOLLOWUP`/`INVENTORY`/`COLLECTIONS`):
  - **Follow-up**: handles a customer's reply to the Phase 6 Temporal nudge on a quiet
    quote — `list_quotes_for_customer` (new: `quotation_service.list_quotations_for_customer`)
    to find the quote in question, then the Sales Agent's `accept_quote`/`create_order` if
    the customer confirms. Cannot create a new quote itself.
  - **Inventory**: `search_products`, `check_inventory`, and a new `check_low_stock` tool
    (`inventory_service.list_low_stock` — variants at or below their `reorder_level`).
    Cannot quote or order.
  - **Collections**: a new `invoices` table (migration `0009`, `app/models/invoice.py`) —
    every order now gets an `Invoice` (net-30, `amount_due` = order total) via
    `invoice_service.create_invoice_for_order`, called from
    `order_service.create_order_from_quotation`. The agent gets `get_outstanding_balance`
    (unpaid/partial invoices) and `record_payment` — tagged `SENSITIVE` like `create_order`,
    same three-tier risk model. Staff can see them too: `GET /api/v1/invoices`(`/{id}`),
    gated by a new `invoices.read` permission (migration `0010`), and an `/app/invoices`
    frontend page listing invoice #, status, amount due/paid, and due date.

- **Knowledge base** (`app/models/knowledge.py`, `app/services/knowledge_service.py`):
  org-scoped `knowledge_base_articles` (title, content, category, `is_published`), full
  CRUD at `/api/v1/knowledge-base` (`knowledge_base.read`/`.manage`, seeded in migration
  `0007`). A new `search_knowledge_base` tool wired into the Support Agent's tool list
  searches published articles by keyword (`ILIKE` on title/content).
- **Email integration** (`app/integrations/email.py`): a real, if thin, second
  `MessagingProvider` — sends outbound mail over stdlib `smtplib` using an org's own SMTP
  credentials (`POST /api/v1/integrations/email/connect`, encrypted at rest same as
  WhatsApp). `POST /conversations/{id}/messages` now dispatches by `conversation.channel`
  (`WHATSAPP` → `WhatsAppProvider`, `EMAIL` → `EmailProvider`) instead of hardcoding
  WhatsApp.
- **Analytics** (`app/services/analytics_service.py`): `GET /api/v1/analytics/summary`
  (gated by the `analytics.read` permission that already existed in the role matrix) —
  customer/product/order counts, total revenue, quote conversion rate, and AI usage
  (executions, tokens, `estimated_cost`).
- **AI cost tracking**: `ai_executions.estimated_cost` (migration `0008`), computed from
  `input_tokens`/`output_tokens` × configurable `AI_INPUT_PRICE_PER_MILLION` /
  `AI_OUTPUT_PRICE_PER_MILLION` (both default to `0` — no invented pricing; set them to
  your actual contract rates to get a real number). Closes the "no cost tracking in
  dollars" gap noted under Phase 4.

## Deliberately skipped in Phase 9

- **No payment gateway / subscription billing** — no Stripe/Razorpay integration, plan
  tiers, or invoices. That needs a provider decision and live credentials this repo
  doesn't have; what's built instead is internal AI usage-cost tracking (see above),
  which is the metering half any usage-based plan would need regardless of provider.
- **No SMS provider** — stdlib can't send SMS without a paid gateway (Twilio, etc.); add
  a `SMSProvider(MessagingProvider)` once one is chosen.
- **Email is outbound-only** — no inbound webhook/mail-receiving service, so a customer
  can't start an `EMAIL` conversation by emailing in (only WhatsApp can today). Add an
  inbound email parser (SES/Mailgun webhook or IMAP polling) to close that.
- **Analytics is one summary endpoint** — no time-series/trends, no charts, no per-agent
  or per-channel breakdown. Add those once there's a dashboard UI that needs them.

## Deliberately skipped in Phase 8

- **Keyword search, not pgvector** — `knowledge_service.search_articles` is `ILIKE`, not
  semantic/embedding search. No embedding provider is configured in this repo, and
  pgvector would make Postgres a hard test-suite dependency (tests run on SQLite). Swap
  in a `vector` column + a real embedding API key once semantic recall matters more than
  keyword match.
- **No knowledge base tool on the Sales/Reordering agents** — only Support has
  `search_knowledge_base` today; add it to the others if they start needing policy/FAQ
  answers mid-quote.
- **No article versioning, attachments, or admin UI** — articles are flat
  title/content/category rows edited via the API only.

## Deliberately skipped in Phase 7

- **No proactive trigger for Inventory** — the Inventory agent (added in Phase 9, see
  above) is reactive-only: it answers when a customer asks, it doesn't push a low-stock
  alert on its own. There's still no outbound trigger channel besides an inbound WhatsApp
  message — add one (e.g. a scheduled Temporal check) to make it proactive.
- **No routing/classification** — nothing auto-picks an agent for a new conversation
  based on message content; every new conversation starts on `SALES` until staff (or a
  future workflow) reassigns it.

## Deliberately skipped in Phase 6

- **No visual builder** — the spec's React Flow drag-and-drop editor and
  prompt-to-workflow generation (sections 29–30) aren't built; workflows are created by
  POSTing validated JSON today.
- **Node types**: only `TRIGGER`/`ACTION`/`CONDITION`/`DELAY`/`END` are implemented.
  `AI_AGENT`, `WEBHOOK`, and `HUMAN_APPROVAL` node types aren't — the Sales Agent isn't
  callable as a workflow node yet, and there's no way to pause a workflow for human
  sign-off (ties into the approval-workflow gap from Phase 4).
- **No versioning UI / rollback** — `workflow_versions` exists and every `create_workflow`
  writes version 1, but there's no `POST` to publish a *new* version of an existing
  workflow; each `Workflow` currently only ever has one version.
- **Only one built-in trigger** (`quote.sent`) — `lead.created`, `order.created`, etc.
  from the spec's example flows aren't wired to anything yet.

## Deliberately skipped in Phase 4

- **Approval workflow** (`approval_requests`/`approval_actions`) isn't built — tools
  carry a `risk_level` (`SAFE`/`SENSITIVE`) today, but nothing yet blocks a `SENSITIVE`
  tool call pending human sign-off. `create_order` runs immediately; add the gate before
  giving the agent any tool that changes money (discounts, refunds, credit limits).
- **Context builder is minimal**: the system prompt is a static template plus the raw
  conversation history — no retrieved order/quote history, no knowledge-base lookup, no
  customer purchase-pattern summary (those come with the Reordering/Support agents in
  Phase 7 and the knowledge base in Phase 8).
- **Only one agent** (Sales) and no agent-configuration UI — `agents`/`agent_tools`
  tables and the Agent Builder are still just a plan, not code.
- **Human handoff is a flag, not a flow**: `conversations.ai_enabled` can be toggled off
  by staff, but the AI itself never generates an escalation summary or auto-hands-off on
  low confidence.
- **No cost tracking in dollars** — `ai_executions` records token counts, not
  `estimated_cost`; add pricing math once there's a dashboard that needs it.

## Deliberately skipped in Phase 3

- `conversation_participants` / `conversation_events` tables — a conversation's single
  `assigned_user_id` covers assignment for now; add participants for group threads,
  and events once there's an audit-log system (Phase 4+) to feed.
- Only text messages are parsed from inbound webhooks (`WhatsAppProvider.parse_inbound`
  skips media/status/reaction payloads) — add handling per message type as needed.
- Email/SMS `MessagingProvider` implementations — the abstract base exists
  (`app/integrations/base.py`) but only `WhatsAppProvider` is implemented.

## Deliberately skipped in Phase 2 (see spec section 11 for the full table list)

- `customer_contacts` / `customer_addresses` — single contact + no shipping addresses
  yet; add when a customer needs more than one contact or delivery address.
- `product_images` / `product_attributes` — variant attributes live in a JSONB column
  instead; add a dedicated table if attributes need their own validation/search.
- `customer_price_rules`, `volume_discounts`, `promotions` as separate tables — volume
  discounts are just multiple `price_list_items` rows per variant with different
  `min_quantity`; add the dedicated tables when per-customer overrides or promo codes
  are needed.
- `quotation_versions` / `quotation_approvals` — versioning and the approval workflow
  land with the AI approval system in Phase 4.
- Inventory reservations pick the first single warehouse with enough stock rather than
  splitting across warehouses — add splitting if that turns out to matter.

## Phase plan

1. **Foundation** — done.
2. **B2B core** — done: customers, products, inventory, pricing, quotations, orders.
3. **Communication** — done: unified inbox, WhatsApp provider, webhooks.
4. **AI** — done: tool registry, agent runtime, Sales Agent, background worker, execution logging.
5. First full vertical slice: WhatsApp → quote → order (functionally complete as of Phase 4 — worth a dedicated end-to-end pass with a real `AI_API_KEY` before calling it stable).
6. **Workflow engine** — done (backend + Temporal): graph interpreter, quote follow-up. Visual builder still to do.
7. **Additional AI agents** — done: Support and Reordering agents alongside Sales, sharing one runtime. Follow-up/Collections/Inventory skipped (see notes above).
8. **Knowledge base** — done: CRUD + keyword search, wired into the Support Agent. pgvector/semantic search skipped (see notes above).
9. **Analytics, billing, remaining integrations** — done: an analytics summary endpoint, AI usage-cost tracking, and an outbound email provider. Payment-gateway billing, SMS, and inbound email skipped (see notes above).

Each phase adds its own migration, models, services, routes, tests, and UI — nothing
in this repo is a mock or a stub standing in for real functionality.
