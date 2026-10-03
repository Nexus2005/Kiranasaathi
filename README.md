# KiranaSaathi AI — Your Kirana Shop's AI Business Partner

> An AI-powered operating system for neighborhood kirana merchants. Every sale, stock movement, purchase and customer interaction flows into one intelligence layer that tells the merchant **what needs attention, why it matters, what to do — and helps execute it.**

[![Next.js](https://img.shields.io/badge/Frontend-Next.js_16-black?logo=next.js)](https://nextjs.org/)
[![React](https://img.shields.io/badge/React-19-61DAFB?logo=react)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Supabase](https://img.shields.io/badge/Database-Supabase_Postgres-3FCF8E?logo=supabase)](https://supabase.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)](https://www.python.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript)](https://www.typescriptlang.org/)
[![Tailwind](https://img.shields.io/badge/Tailwind_CSS-4-06B6D4?logo=tailwindcss)](https://tailwindcss.com/)
[![License](https://img.shields.io/badge/License-Proprietary-red)]()

---

![KiranaSaathi AI — Home dashboard with AI Priority Feed](docs/images/01-home-dashboard.png)

<p align="center">
  <b>Home · AI Priority Feed</b> — today's sales, profit, low stock, expiry risk and AI recommendations in one decision-oriented view.<br/>
  <sub>Namaste Ramesh ji 👋 · Sharma Kirana Store, Nashik</sub>
</p>

---

## ✨ What is KiranaSaathi AI?

A typical kirana owner juggles handwritten records, billing/POS, supplier bills, WhatsApp, memory, payment records and shelf stock — all disconnected.

**KiranaSaathi AI connects it all:**

```
sales + billing + inventory + purchases + suppliers + customers
      + pricing + demand + festivals + marketing + payments
                          │
                          ▼
                   KIRANASAATHI AI
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   Detect Risk     Find Opportunity   Understand Demand
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ▼
                  AI RECOMMENDATION
                  Evidence + Why
                          ▼
                  Merchant Approval
                          ▼
                   Action → Outcome
                          ▼
                   Learn & Improve
```

The merchant runs the shop. **KiranaSaathi continuously figures out how the shop can run smarter and grow.** This is not "chatbot + dashboard" — it is one intelligent merchant system that converts business data into decisions and actions.

### The one demo that proves it

1. 👋 *"Good morning. Here's what needs your attention."*
2. ⏰ **₹4,850 inventory at expiry risk** → explains products → recommends discount/bundle → merchant approves
3. 📦 **Maggi stock runs out in ~2 days** → AI calculates reorder qty → compares suppliers → recommends supplier → merchant approves
4. 🤝 **Customer offers ₹82 for a ₹95 product** → pricing engine checks cost/margin/age → AI recommends ₹88 counter → merchant accepts
5. 🪔 **Diwali demand opportunity** → expected categories → inventory gaps → procurement plan
6. 💬 **Customer campaign** → relevant segment → message → merchant approves
7. 📈 **Analytics** → business impact of every action

---

## 🚀 Key Features

| Module | What it does |
|---|---|
| 🏠 **Home / AI Priority Feed** | Today's sales, profit, orders, inventory value, low-stock, near-expiry, pending purchases + prioritized AI cards (🔴 expiry · 🟠 stockout · 🟡 margin · 🟢 festival) |
| 💬 **AI Assistant** | Conversational interface to the whole store: *"What should I reorder?" · "Why are profits down?" · "What should I stock for Diwali?"* — backed by real DB tools, not hallucinations |
| 🧾 **Smart Counter** | Camera-first barcode scan, visual product recognition (DINOv2 + OCR), cart, margin visibility, bargaining flow, split payments, atomic checkout → inventory + profit + customer history update instantly |
| 🤝 **Intelligent Bargaining** | Deterministic pricing engine (cost + margin + age + expiry + velocity) decides Accept / Counter / Reject — the LLM only explains |
| 📦 **Inventory Intelligence** | Low / over / dead / fast / slow stock, expiry risk with ₹ value-at-risk, reorder points, batch tracking |
| ⏰ **Expiry Rescue Engine** | Discount vs bundle vs WhatsApp campaign options with sell-through tracking |
| 📊 **Demand & Trends** | Internal velocity + seasonality + festivals + external signals → *"what will sell, when, and why?"* with hyperlocal demand map |
| 🚚 **Purchases & Suppliers** | Landed-cost comparison (price + MOQ + delivery + lead time), not naive cheapest-price picking |
| 👥 **Customers** | New / repeat / at-risk / VIP segments, purchase frequency, affinity, loyalty + re-engagement offers |
| 💚 **WhatsApp & Marketing** | Consent-based 5-step campaign wizard, catalog sharing, festival greetings, performance tracking |
| 🪔 **Festival Calendar** | Festival → demand forecast → inventory gap → suppliers → customer segments → campaign → sales tracking |
| ⚡ **Quick Commerce** | Adapter architecture ready for Blinkit / Zepto / Instamart / ONDC (no fake integrations) |
| 📈 **Analytics & Reports** | Revenue, margin, turnover, dead stock, repeat rate, campaign ROI + AI-narrated weekly summaries |
| 🔔 **Alerts** | Actionable alerts only — *"Maggi 70g lasts ~2 days. Reorder 48 units from Supplier A"* (never bare *"Stock is low"*) |
| 💳 **Payments** | UPI / cards / cash via provider abstraction, incl. split-payment engine where the provider supports it |

---

## 📸 Screenshots

### 💬 AI Assistant — ask anything about your business

![AI Assistant chat](docs/images/02-ai-assistant.png)

> Tool-calling agent over real store data: `get_store_summary · get_inventory · forecast_demand · compare_suppliers · create_campaign …`

### 🧾 Smart Counter — scan, recognize, bill in seconds

![Smart Counter](docs/images/03-smart-counter.png)

> Camera-first barcode + DINOv2 visual recognition + RapidOCR evidence → 0.95 auto-add / 0.70 review thresholds, atomic sale, store-isolated.

### 📦 Inventory — every unit, batch and expiry tracked

![Inventory](docs/images/04-inventory.png)

### 📊 Demand & Trends — know what will sell next

![Demand and Trends](docs/images/05-demand-trends.png)

### 👥 Customers — turn walk-ins into regulars

![Customers](docs/images/06-customers.png)

### 📈 Analytics — profit truth, not vanity charts

![Analytics](docs/images/07-analytics.png)

### 💚 WhatsApp & Marketing — consent-based campaigns that convert

![WhatsApp and Marketing](docs/images/08-whatsapp-marketing.png)

### 🚚 Purchases & Suppliers — landed cost wins

![Purchases and Suppliers](docs/images/09-purchases-suppliers.png)

### 🪔 Festival Calendar — Diwali-ready, automatically

![Festival Calendar](docs/images/10-festival-calendar.png)

---

## 🧠 How the intelligence works

**Golden rule: the LLM is NEVER the source of truth.** Deterministic Python engines calculate every number; the AI reasons, explains and orchestrates.

| Engine | Authority |
|---|---|
| `PricingEngine` | margin, min price, bargain counter-offer |
| `InventoryEngine` | velocity, low/over/dead/fast/slow, reorder point |
| `ExpiryEngine` | value-at-risk, clearance options, sell-through |
| `DemandEngine` | forecast (history + seasonality + festivals + external) |
| `ProcurementEngine` | landed cost, supplier comparison |
| `CustomerEngine` | segments, affinity, churn risk |
| `CampaignEngine` | segment → message → plan → performance |
| `AnalyticsEngine` | revenue, profit, turnover, top/slow products |
| `AlertEngine` | actionable alerts from business events |

**Autonomy model (hackathon scope: Levels 1–4):**

| Level | Name | Behavior |
|---|---|---|
| 1 | Observe | "Something is wrong." |
| 2 | Recommend | "Reorder 50 units." |
| 3 | Prepare | "Purchase order drafted." |
| 4 | Execute after approval | "Approve to send." |
| 5 | Autonomous | Deferred — low-risk explicit config only |

Every approval and execution is written to an **audit log**. External web signals carry **source · URL · published · retrieved · confidence** — never invented prices.

---

## 🏗️ Architecture

```
                 ┌──────────────────────┐
                 │    KIRANASAATHI UI   │
                 │   Next.js 16 / TS    │
                 └──────────┬───────────┘
                            │
                 ┌──────────▼───────────┐
                 │      API LAYER       │
                 │  FastAPI (monolith)  │
                 └──────────┬───────────┘
                            │
       ┌────────────────────┼────────────────────┐
       ▼                    ▼                    ▼
 BUSINESS ENGINES        AI AGENT          INTEGRATIONS
 Pricing / Inventory    Tool Router         WhatsApp
 Expiry / Demand        LLM + Memory        Payments
 Procurement / Alerts   Planning            Quick Commerce
       │                    │                    │
       └──────────┬─────────┴────────────────────┘
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
          └────────────────┘
```

> Modular monolith — one deployable backend, cleanly separated modules. No premature microservices. Full spec: [`ARCHITECTURE.md`](ARCHITECTURE.md) · Product definition: [`PROJECT.md`](PROJECT.md) · Design system: [`DESIGN.md`](DESIGN.md) · Progress log: [`PROGRESS.md`](PROGRESS.md)

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, lucide-react, ZXing (camera scan) |
| Backend | Python, FastAPI, Pydantic v2, asyncpg, python-jose (JWT) |
| Database | PostgreSQL via Supabase (RLS, pgvector for visual embeddings) |
| Vision (Smart Counter) | DINOv2 (timm) embeddings · RapidOCR / PaddleOCR · RT-DETR detection · zxing-cpp barcodes — all optional adapters with honest degradation |
| External data | Open Food Facts (ODbL, attributed) product enrichment · Agent Reach research layer (server-side only) |
| Payments / messaging | Provider abstractions (UPI, GPay, PhonePe, BHIM, Razorpay, Visa/MC/RuPay) · official WhatsApp API only |

---

## 📁 Project Structure

```
.
├── apps/
│   ├── web/                  # Next.js merchant OS (15 modules)
│   │   ├── src/app/          # home, ai-assistant, smart-counter, inventory,
│   │   │                     # purchases, demand, customers, whatsapp,
│   │   │                     # festivals, quick-commerce, analytics,
│   │   │                     # reports, alerts, checkout, orders …
│   │   ├── src/components/   # shell, metric-card, recommendation-card …
│   │   └── public/brand/     # Paytm, UPI, GPay, PhonePe, BHIM, Razorpay,
│   │                         # Visa, Mastercard, RuPay, Blinkit, Zepto,
│   │                         # Instamart, bigbasket, ONDC, WhatsApp
│   └── api/                  # FastAPI modular monolith
│       └── app/
│           ├── routers/      # auth, dashboard, counter, inventory, sales,
│           │                 # purchases, suppliers, customers, marketing,
│           │                 # demand, festivals, payments, alerts, agent …
│           ├── services/     # pricing, inventory, expiry, demand,
│           │                 # procurement, analytics + vision pipeline
│           └── agent/        # tool router, orchestration, approvals
├── database/
│   ├── migrations/           # versioned SQL (RLS-first, store tenancy)
│   └── seed/                 # demo merchant scenario for judges
├── docs/
│   ├── images/               # ✅ screenshots used by this README
│   └── smart-counter-*.md    # vision pipeline docs
├── scripts/                  # OFF seeder, e2e verification suites
├── ARCHITECTURE.md
├── DESIGN.md
├── PROJECT.md
├── PROGRESS.md
└── README.md                 # you are here
```

---

## ⚡ Quick Start

### Prerequisites

- Node.js 20+ · Python 3.11+ · a Supabase project (Postgres + pgvector)

### 1️⃣ Clone & configure

```bash
git clone https://github.com/Nexus2005/Kiranasaathi.git
cd Kiranasaathi
cp .env.example .env   # fill in Supabase + JWT values
```

Required vars (`apps/web/.env` + root `.env` — see [`.env.example`](.env.example)):

```ini
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@db.YOUR_PROJECT.supabase.co:5432/postgres
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
JWT_SECRET=change-me
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 2️⃣ Backend (FastAPI)

```bash
cd apps/api
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 3️⃣ Frontend (Next.js)

```bash
cd apps/web
npm install
npm run dev        # → http://localhost:3000
```

### 4️⃣ Seed the demo store (optional, recommended)

```bash
python scripts/seed_openfoodfacts.py   # real Indian grocery catalog (ODbL, attributed)
```

Run migrations from [`database/migrations/`](database/migrations/) in order, then open the app and ask the AI: **"What should I pay attention to?"**

---

## 🔌 API Overview

| Router | Prefix | Purpose |
|---|---|---|
| `auth` | `/api/auth` | login, signup, sessions, roles |
| `dashboard` | `/api/dashboard` | home KPIs + priority feed |
| `counter` | `/api/counter` | scan → recognize → cart → atomic checkout |
| `inventory` / `products` | `/api/inventory`, `/api/products` | stock, batches, expiry, catalog, image upload |
| `sales` / `orders` | `/api/sales`, `/api/orders` | transactions, order lifecycle |
| `purchases` / `suppliers` | `/api/purchases`, `/api/suppliers` | POs, quotes, landed-cost compare |
| `customers` | `/api/customers` | segments, history, loyalty |
| `marketing` | `/api/marketing` | WhatsApp campaigns |
| `demand` / `festivals` | `/api/demand`, `/api/festivals` | forecasts, festival intelligence |
| `payments` | `/api/payments` | provider-abstracted checkout + splits |
| `alerts` | `/api/alerts` | actionable alert lifecycle |
| `agent` | `/api/agent` | tool-calling AI + approvals + audit |
| `enrichment` / `retail` / `external` | … | OFF catalog, store ops, Agent Reach evidence |

Interactive docs when the backend runs: `http://localhost:8000/docs`

---

## 📷 Smart Counter vision pipeline

Real OCR + frozen DINOv2 "Global Product Brain" + merchant-feedback ledger (enrollment ≠ training):

```
camera frame → detector → crop → barcode + embedding + OCR
     → multi-signal matcher (visual + OCR + brand + pack)
     → ≥0.95 auto-add · 0.70–0.95 merchant review · <0.70 no-add
     → atomic checkout → inventory ↓ → profit + history updated
```

Verification suites (all live, real inference): `real-vision` · `learning` · `multiproduct` · `inventory-loop` · `phase1` · `security` — see [`docs/`](docs/) and [`PROGRESS.md`](PROGRESS.md). Details: `docs/smart-counter-*.md`.

---

## 🗺️ Roadmap

- [x] Auth + store tenancy + product/inventory core
- [x] Smart Counter (camera barcode + visual recognition + atomic billing)
- [x] Deterministic engines (pricing, expiry, demand, procurement)
- [x] AI agent with tool calling + approval workflow + audit
- [x] Customers + WhatsApp + festival intelligence
- [x] Paytm-style merchant OS UI (light theme, ₹-native, Hindi-English warmth)
- [ ] Real-time dashboard channel (SSE/WebSocket)
- [ ] Admin UI for product-edit policy switch
- [ ] Self-hosted product images (ODbL-safe)
- [ ] Licensed retail-domain detector training run
- [ ] Quick-commerce partner adapters (live APIs only)

---

## 🤝 Contributing

PRs welcome! Please:

1. Follow [`ARCHITECTURE.md`](ARCHITECTURE.md) module boundaries and [`DESIGN.md`](DESIGN.md) UI tokens
2. Keep the LLM out of calculations — engines compute, AI explains
3. Add/extend verification scripts under `scripts/` for behavior changes
4. Never commit `.env`, credentials, datasets or heavy artifacts (see [`.gitignore`](.gitignore))

---

## 🙏 Acknowledgements

- Product imagery + catalog metadata: [Open Food Facts](https://world.openfoodfacts.org/) (ODbL — attributed in-app)
- Brand marks belong to their owners (Paytm, UPI, GPay, PhonePe, BHIM, Razorpay, Visa, Mastercard, RuPay, Blinkit, Zepto, Instamart, bigbasket, ONDC, WhatsApp) — used for merchant familiarity
- UI language: Paytm-inspired light merchant aesthetic with Indian SMB context (₹, lakh formatting, festival awareness)

---

<p align="center">
  <b>KiranaSaathi AI</b> — <i>The merchant runs the shop. KiranaSaathi figures out how it runs smarter.</i><br/>
  Built for Paytm Merchant ecosystem · Modular monolith · Evidence-backed AI
</p>
