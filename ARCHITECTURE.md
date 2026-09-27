# KiranaSaathi AI — System Architecture

**Status:** Locked for initial build (modular monolith)
**Last updated:** 2026-09-22

---

## 1. Architecture overview

```
                         ┌──────────────────────┐
                         │    KIRANASAATHI UI   │
                         └──────────┬───────────┘
                                    │
                         Next.js / TypeScript
                                    │
                         ┌──────────▼───────────┐
                         │      API LAYER       │
                         │       FastAPI        │
                         └──────────┬───────────┘
                                    │
             ┌──────────────────────┼──────────────────────┐
             │                      │                      │
             ▼                      ▼                      ▼
      BUSINESS ENGINES         AI AGENT             INTEGRATIONS
             │                      │                      │
      Pricing Engine          Tool Router           WhatsApp
      Inventory Engine        LLM                   Payments
      Expiry Engine           Memory                Suppliers
      Demand Engine           Planning              Quick Commerce
      Procurement             Reasoning             Messaging
      Analytics / Alerts         │
             │                      │
             └──────────┬───────────┘
                        ▼
                ┌───────────────┐
                │  PostgreSQL   │
                │   Supabase    │
                └───────┬───────┘
                        │
                 Event / Audit Log
                        │
                ┌───────▼────────┐
                │ External Intel │
                │ Agent Reach    │
                │ APIs / Sources │
                └────────────────┘
```

**Style:** Modular monolith — one deployable backend with clearly separated modules/services. **No unnecessary microservices.**

---

## 2. Core architectural principles

1. **Database is the foundation** — every module (Inventory, Analytics, Reports, AI Assistant, Demand, Festivals, Bargaining, Suppliers, WhatsApp, Quick Commerce, Payments) is a view/query of the same shared data model.
2. **LLM is not the source of truth** — deterministic engines calculate all financial/inventory values; the AI only reasons, explains, and orchestrates.
3. **Event-driven intelligence** — meaningful actions emit business events so the AI reacts instead of constantly scanning the whole DB.
4. **Evidence layer** — every external signal is stored with source metadata and confidence.
5. **Approval before side effects** — financial/inventory actions require merchant approval (Levels 1–4).
6. **Integrations behind abstractions** — `PaymentProvider`, WhatsApp sender, quick-commerce adapters; never hard-code partner-specific logic into core checkout/inventory.
7. **Agent Reach is server-side only** — frontend never calls Agent Reach directly; it is not authoritative for merchant data or exact market prices.

---

## 3. High-level component map

```
MERCHANT BRAIN (what/why/what next/execute)
        │
        ├── STORE DATA: Inventory, Sales, Purchases, Customers, Orders, Payments, Products, Suppliers
        ├── EXTERNAL DATA: Market signals, Festivals/events, Demand signals, Web intelligence (Agent Reach)
        └── ACTIONS: WhatsApp, Campaigns, Reorders, Pricing, Payments, Quick commerce (future)
```

---

## 4. Frontend (Next.js / TypeScript)

**Role:** Paytm-inspired merchant dashboard — one OS with 15 modules, not 15 separate products.

```
Home (AI Priority Feed)
AI Assistant
Smart Counter
Inventory
Purchases & Suppliers
Demand & Trends
Customers
WhatsApp & Marketing
Festival Calendar
Quick Commerce (architecture-ready)
Analytics
Reports
Alerts
Settings
Help & Support
```

**Key UI patterns:**

- **AI Priority Feed** on Home — decision-oriented cards (expiry risk, stockout risk, margin risk, festival opportunity), not just charts
- **Recommendation cards** — situation, evidence, proposed action, expected effect, tradeoffs, timestamps → Review / Approve / Edit / Dismiss
- **Activity timeline** — recent transactions, alerts, AI actions
- **Smart Counter** — search/barcode, cart, margin visibility, bargain flow, customer attach, payment (incl. split if provider supports)

Frontend talks only to the FastAPI API layer (and real-time channels if needed). No direct DB or Agent Reach access from the browser.

---

## 5. Backend API layer (Python / FastAPI)

**Role:** Single API gateway for the monolith. Enforces `user → merchant → store` tenancy on every request.

Suggested module boundaries (logical, same process):

| Module | Responsibility |
|---|---|
| `auth` | Login, signup, sessions, roles |
| `merchants` / `stores` | Profiles, settings |
| `catalog` | Products, variants, prices, categories, barcodes |
| `inventory` | Stock, batches, expiry, in/out/adjust, movements |
| `purchases` | Purchase orders, invoices, items, cost updates |
| `suppliers` | Suppliers, quotes, terms, reliability |
| `sales` | Smart Counter sales, sale items, carts |
| `payments` | Payment records, splits, PaymentProvider abstraction |
| `customers` | Customers, history, segments |
| `engines` | Deterministic business engines (see §6) |
| `alerts` | Alert generation & lifecycle |
| `events` | Business event bus / outbox |
| `agent` | AI agent: tools, orchestration, recommendations |
| `approvals` | Recommendation review → approve/edit/dismiss → execute |
| `campaigns` | Campaigns, WhatsApp workflows (consent-based) |
| `festivals` | Festival calendar + intelligence linkage |
| `analytics` / `reports` | Aggregations, report generation |
| `audit` | Activity/audit logs |
| `external_intel` | Agent Reach client, source registry, evidence |

---

## 6. Deterministic business engines (built BEFORE the LLM)

These services calculate authoritative values. The LLM never invents these numbers.

```
PricingEngine      → margin, discount impact, min allowed price, bargain counter-offer
InventoryEngine    → stock state, velocity, low/overstock/dead/fast/slow, reorder point
ExpiryEngine       → value at risk, clearance options (discount/bundle/campaign), sell-through tracking
DemandEngine       → forecast demand (history + seasonality + festivals + external signals)
ProcurementEngine  → landed cost, supplier comparison (unit price, MOQ, delivery, lead time)
CustomerEngine     → segments (new/repeat/inactive/high-value, affinity)
CampaignEngine     → segment → message → send plan → performance
PaymentEngine      → cart totals, split logic (via provider capabilities)
AnalyticsEngine    → revenue, profit, turnover, top/slow products, stockouts, expiry exposure
AlertEngine        → actionable alerts from events + thresholds
```

### Example: PricingEngine (bargaining)

**Inputs:** purchase_cost, selling_price, mrp, discount, customer_offer, minimum_margin, inventory_age, expiry_date, quantity, market reference (validated), sales velocity.

**Outputs:** current_margin, offer_margin, discount_impact, minimum_allowed_price, suggested_counter_offer, decision (accept/counter/reject) + machine-readable reasons.

AI presents: *"Counter at ₹88 — ₹82 drops margin below your 5% threshold; unit is 54 days old, within clearance strategy."*

### Example: landed-cost supplier comparison

Not "pick cheapest unit price." Compare effective cost for the merchant's actual requirement given MOQ, delivery time, lead time, availability, delivery cost, credit terms.

---

## 7. Business event system

Every meaningful action produces an event. AI and alerts react to events instead of full-DB scans.

```
SALE_CREATED            INVENTORY_UPDATED         PRODUCT_LOW_STOCK
PRODUCT_NEAR_EXPIRY     PURCHASE_CREATED          SUPPLIER_PRICE_CHANGED
ORDER_CREATED           ORDER_PAID                PAYMENT_PARTIALLY_COMPLETED
CAMPAIGN_CREATED        CAMPAIGN_SENT             CUSTOMER_ADDED
FESTIVAL_APPROACHING    MARGIN_DROP               RECOMMENDATION_APPROVED
```

**Example flow:**

```
SALE_CREATED → Inventory decreases → Sales velocity recalculated
             → Reorder threshold checked → Expiry risk checked
             → AI recommendation generated if necessary → Merchant alert
```

**Implementation note:** transactional outbox / queue inside the monolith is enough initially — no separate event-bus microservice required.

---

## 8. AI Agent layer

**Role:** Reasoning + orchestration, not calculation.

```
                  AI AGENT
                     │
          ┌──────────┼──────────┐
          ↓          ↓          ↓
      Inventory   Pricing    Demand   (deterministic tools)
       Tools       Tools      Tools
          ↓          ↓          ↓
      Suppliers   Customers  Marketing
          └──────────┼──────────┘
                     ↓
              ACTION ENGINE
                     ↓
         Merchant Approval (Levels 1–4)
                     ↓
                 Execute
                     ↓
                Audit Log
```

### Tool catalog

```
get_store_summary()            get_sales()
get_inventory()                get_product_details()
get_expiring_inventory()       get_low_stock_products()
calculate_margin()             calculate_bargain()
forecast_demand()              recommend_reorder()
get_supplier_quotes()          compare_supplier_quotes()
get_customer_segments()        get_festival_opportunities()
create_campaign()              create_alert()
prepare_action()               execute_approved_action()
```

### Recommendation contract (evidence-backed)

Every AI recommendation must include:

- **Situation** — what is happening
- **Evidence** — supporting data points + data timestamps; external sources with URL/published/retrieved dates
- **Proposed action** — concrete next step
- **Expected effect** — modeled outcome from engines
- **Tradeoffs**
- **Autonomy level** — observe / recommend / prepare / execute-after-approval

Financial and inventory actions require merchant approval unless explicitly configured low-risk (Level 5 — out of hackathon scope).

### Example merchant query

```
"What should I do today?"
  → agent calls: get_store_summary + get_expiring_inventory
                 + get_low_stock_products + get_sales
                 + forecast_demand + get_festival_opportunities
  → engines return authoritative numbers
  → agent prioritizes and explains
  → recommendation cards with Approve/Edit/Dismiss
```

---

## 9. Autonomy / approval model

| Level | Name | Hackathon |
|---|---|---|
| 1 | Observe | ✅ |
| 2 | Recommend | ✅ |
| 3 | Prepare action | ✅ |
| 4 | Execute after approval | ✅ |
| 5 | Autonomous (explicit low-risk config only) | ❌ Deferred |

Every approval and execution is written to the **audit log**.

---

## 10. Data model (PostgreSQL / Supabase)

```
users
 └── merchants
       └── stores
             │
             ├── products
             │      ├── product_variants
             │      └── product_prices
             │
             ├── inventory
             │      └── inventory_batches
             │
             ├── purchases
             │      └── purchase_items
             │
             ├── sales
             │      └── sale_items
             │
             ├── customers
             │
             ├── orders
             │      └── order_items
             │
             ├── suppliers
             │      └── supplier_quotes
             │
             ├── campaigns
             │
             ├── festivals_events
             │
             ├── payments
             │      └── payment_splits
             │
             ├── alerts
             │
             ├── ai_recommendations
             │      └── recommendation_actions (approval + outcome)
             │
             ├── evidence_records          (external intel)
             │
             ├── source_registry           (external sources)
             │
             ├── business_events           (event outbox)
             │
             └── activity_logs             (audit)
```

**Tenancy rule:** every store-scoped row is reachable only through `store_id` owned by the authenticated merchant; enforced at DB + API layer.

---

## 11. Evidence layer & Source Registry

Every external piece of information becomes an `EvidenceRecord`:

```
id, source, url, title, published_at, retrieved_at,
region, category, content, confidence
```

**Source Registry:**

```
name, type, url, last_checked, reliability,
geographic_scope, data_type, status
```

AI recommendation example:

```
WHY THIS RECOMMENDATION?
Your store sold 42 units during the same festival period last year.
Current stock: 11 units. Current velocity: 4.2/day.
Estimated festival demand: 35–45 units.
External signal: [Source · Retrieved · Confidence]
Recommendation: Review replenishment of 24–34 units.
Reason: Current inventory may not cover expected demand.
```

**Good use of Agent Reach:** *"Find public information about upcoming festival trends."*
**Bad use:** *"Search random websites and tell me the exact wholesale price of Amul milk in Nashik."* — needs trusted/structured sources, never arbitrary web search as authoritative price truth.

---

## 12. Integrations

### Payments

```
PaymentProvider (abstraction)
  └── concrete adapters (actual provider capabilities only)
```

Payment Splitter (cart → multiple payers → all paid → complete order) lives in the **PaymentEngine**, not inside checkout core logic. Only expose split behavior the real provider supports.

### WhatsApp / Marketing

Authorized/official integrations + customer consent only. No scraping, no unsolicited messaging. Flow: Customer segment → catalog/offer → consented message → performance tracking.

### Quick Commerce (future)

```
KiranaSaathi Inventory → Integration Adapter → Partner platform
                       → Order → KiranaSaathi → Inventory reservation → Fulfillment
```

Build the **adapter architecture** first. Do not hard-code Blinkit/Zepto/etc. into core. Do not fake unavailable APIs or partnerships.

### Agent Reach (external intelligence)

```
integrations/
    external_intelligence/
        agent_reach/
            client.py
            sources.py
            normalizer.py
            verifier.py
```

Flow: `Agent → get_external_market_signals() → Agent Reach → raw info → normalization → evidence records → Agent`

Frontend never calls Agent Reach. Server-side only. External layer — not foundation, not required for Milestone 1.

---

## 13. Target repository structure

```
kirana-saathi/
│
├── apps/
│   ├── web/                 # Next.js frontend
│   └── api/                 # FastAPI backend (modular monolith)
│
├── packages/
│   ├── types/               # Shared TS types
│   ├── ui/                  # Shared UI components
│   └── config/              # Shared config
│
├── database/
│   ├── migrations/
│   ├── seed/                # Demo data for judge story
│   └── schema/
│
├── services/                # Logical engine modules (inside api initially)
│   ├── inventory/
│   ├── pricing/
│   ├── demand/
│   ├── procurement/
│   ├── analytics/
│   └── agent/
│
├── integrations/
│   ├── whatsapp/
│   ├── payments/
│   ├── suppliers/
│   └── external_intelligence/
│
├── docs/
│   ├── architecture.md      # this file (or mirror)
│   ├── database.md
│   ├── api.md
│   └── agent-tools.md
│
├── PROJECT.md
├── PROGRESS.md
├── ARCHITECTURE.md
└── README.md
```

Do not make 40 microservices. Engines start as modules inside the FastAPI app; extract only if genuinely needed later.

---

## 14. Strict development order

```
Merchant Data → Business Engine → AI Agent → External Intelligence → Actions → Learning Loop
```

| Phase | Build | Depends on |
|---|---|---|
| 0 | Project foundation (repo, structure, docs) | — |
| 1 | Auth + Merchant + Store + DB tenancy | Phase 0 |
| 2 | Products + Inventory (in/out/adjust, batch, expiry) | Phase 1 |
| 3 | Smart Counter (cart → bargain → payment → sale → inventory deduct) | Phase 2 |
| 4 | Purchases + Suppliers (PO → inventory IN → cost → margin updates) | Phase 2 |
| 5 | Analytics (real transactions exist first) | Phase 3–4 |
| 6 | Demand & Trends (history + seasonality + festivals + external) | Phase 5 |
| 7 | AI Agent (tools over real data) | Phase 6 |
| 8 | Alerts (event engine → actionable alerts → AI prioritization) | Phase 7 |
| 9 | Customers + WhatsApp loop | Phase 7–8 |
| 10 | Festival Calendar + Campaigns | Phase 6, 9 |
| 11 | Quick Commerce adapter architecture | Phase 2–3 |
| 12 | Payment Splitter (via PaymentProvider) | Phase 3 |

**Do NOT start with:** Agent Reach, fancy AI animations, WhatsApp automation, quick-commerce, payment splitter, festival UI, 20 dashboard pages, computer vision, complex forecasting, multi-agent architecture.

---

## 15. Milestone 1 (architecture acceptance test)

> A shopkeeper can run **one full day** of their shop inside KiranaSaathi:

```
Login → See products → Add inventory → Record purchase → Sell product
  → Inventory decreases → Profit updates → Bargain → System calculates offer
  → Payment completes → Sale recorded → AI sees the transaction
  → Ask "What should I pay attention to?" → Real DB-backed recommendations
```

If this works, the foundation is correct. If not, additional pages are just a shell around a broken product.

---

## 16. Locked technology choices

| Layer | Choice |
|---|---|
| Frontend | Next.js / TypeScript |
| Backend | Python / FastAPI (modular monolith) |
| Database | PostgreSQL / Supabase |
| Business logic | Deterministic Python engines |
| AI | LLM agent with tool calling (provider TBD — open item) |
| External intel | Agent Reach (server-side, post-M1) |
| Payments | Provider via abstraction (TBD) |
| WhatsApp | Official/consent-based integration (TBD) |

---

## 17. Open architecture items

- LLM provider for agent layer
- Payment provider capabilities (split payment support?)
- WhatsApp Business API access & consent model
- Agent Reach integration feasibility (after M1)
- Quick commerce partner/API (future — do not fake)
- Real-time channel choice for dashboard updates (polling vs WebSocket/SSE — decide when needed)
