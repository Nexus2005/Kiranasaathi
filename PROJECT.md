# KiranaSaathi AI — Project Definition

**One-line explanation:** KiranaSaathi AI is an AI business partner for kirana merchants that connects every sale, stock movement, purchase and customer interaction into one intelligence layer that tells the merchant what needs attention, why it matters, what action to take, and helps execute it.

---

## 1. What are we building?

KiranaSaathi AI is an AI-powered operating system and business partner for neighborhood kirana merchants.

It connects the merchant's:

sales + billing + inventory + purchases + suppliers + customers + pricing + demand + festivals + marketing + payments

into one continuously updating system.

The AI watches the business data, identifies what needs attention, explains why, recommends what the merchant should do, prepares the action, and—where the merchant has authorized it—executes the action.

In simple terms: **The merchant runs the shop. KiranaSaathi AI continuously figures out how the shop can run smarter and grow.**

This fits the direction Paytm itself describes around AI-powered merchant insights, customer engagement and AI-led workflows.

**This is NOT:** "AI chatbot + inventory + analytics + marketing."
**This IS:** One intelligent merchant system that continuously converts business data into decisions and actions.

---

## 2. The core problem

A typical kirana owner has information scattered across:

- Handwritten records
- Billing/POS
- Supplier bills
- WhatsApp
- Memory
- Paytm/payment records
- Inventory shelves
- Customer conversations
- Market prices
- Seasonal demand

The merchant has to manually answer questions such as:

| Question |
|---|---|
| What should I reorder? |
| Which products are going to expire? |
| Which products are actually making me money? |
| What should I stock for Diwali? |
| Why did sales fall this week? |
| Which supplier should I buy from? |
| Can I accept this customer's bargain? |
| Which customers should I contact? |
| What products are becoming popular? |
| What should I do today? |

KiranaSaathi AI turns these questions into an intelligent operating loop.

---

## 3. The central intelligence loop

This is the heart of the entire product.

```
                 MERCHANT DATA
                      │
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
      Sales        Inventory     Purchases
        ↓             ↓             ↓
    Customers      Expiry       Suppliers
        └─────────────┼─────────────┘
                      ↓
              KIRANASAATHI AI
                      │
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
    Detect Risk    Find Opportunity  Understand Demand
        │             │             │
        └─────────────┼─────────────┘
                      ↓
              AI RECOMMENDATION
                      ↓
          Evidence + Explanation
                      ↓
            Merchant Approval
                      ↓
                Action
                      ↓
             Result / Outcome
                      ↓
               Learn & Improve
```

The product is **one intelligent merchant system** that continuously converts business data into decisions and actions.

---

## 4. What the AI actually does

The AI behaves like a business partner, not just a chatbot.

**Example:** The merchant opens the app and asks: *"What should I focus on today?"*

KiranaSaathi responds:

> **4 things need attention today**
>
> 1. **Expiry risk** — 18 packets of Product X expire in 12 days. Current stock value: ₹1,260.
>    Recommended action: 8% promotional discount + bundle with Product Y.
> 2. **Reorder risk** — Maggi 70g is selling 2.4× faster than its recent average. Current stock may last 2 days.
> 3. **Margin risk** — Supplier A increased the purchase price by ₹2. Your current selling price leaves only 4.1% margin.
> 4. **Festival opportunity** — Diwali is approaching. Historical sales suggest higher demand for snacks, dry fruits and gift packs.
>
> **Actions: Review → Approve → Execute**

That is the product.

---

## 5. Complete system modules

### A. Home / AI Business Overview

Answers: *"How is my shop doing and what should I do next?"*

Contains: Today's sales, today's profit, orders, inventory value, low-stock items, near-expiry stock, pending purchases, customer activity, AI recommendations, alerts, today's opportunities, recent transactions.

**Most important component: AI Priority Feed**

```
TODAY'S PRIORITIES

🔴 3 products nearing expiry — ₹4,850 stock at risk
🟠 5 products may stock out — Estimated 2.3 days remaining
🟡 Supplier ABC increased prices — 4 products affected
🟢 Festival opportunity detected — Expected demand increase for snack category
```

The dashboard is decision-oriented, not just a collection of charts.

### B. AI Assistant

Conversational interface to the entire system. The merchant can ask:

- "What should I reorder?"
- "Why are my profits down?"
- "Which products are selling fastest?"
- "Can I give this customer ₹10 discount?"
- "What should I stock for Diwali?"
- "Find cheaper suppliers."
- "Show products expiring this month."
- "Create a WhatsApp campaign for customers who bought biscuits."
- "How much did I make yesterday?"

The AI does not simply generate text — it calls actual backend tools:

```
User: "What should I reorder?"
  ↓ get_sales_history()
  ↓ get_inventory()
  ↓ forecast_demand()
  ↓ get_supplier_prices()
  ↓ calculate_reorder_quantity()
  ↓ generate recommendation
```

### C. Smart Counter

Real-time shop transaction layer: search product, scan barcode, recognize product, add to cart, check price/stock/margin, apply discount, bargain, add customer, generate bill, accept payment, split payment, complete transaction.

Every transaction updates the rest of the system:

```
SALE → Inventory decreases → Revenue increases → Profit calculated
    → Customer purchase history updated → Demand data updated
    → Reorder forecast updated → AI recommendations updated
```

### D. Intelligent Bargaining

Sophisticated multi-factor pricing decision:

**Inputs:** purchase cost, current selling price, minimum acceptable margin, current inventory, inventory age, expiry date, current market reference, recent sales velocity, customer/order context, quantity being purchased.

```
Customer offer → Pricing Engine → Cost + Margin + Inventory + Expiry + Market Signals
              → Accept / Counter / Reject
```

**Example:** MRP ₹100, current price ₹95, purchase cost ₹72, customer offer ₹82.
AI: *Counter at ₹88* — because ₹82 would reduce margin below configured threshold; product has been in inventory 54 days, so ₹88 is acceptable within clearance strategy.

**Key principle: The LLM does NOT calculate this itself.** A deterministic Pricing Engine calculates the economics. AI explains the result.

### E. Inventory Intelligence

Tracks: current stock, stock movement, product batches, purchase cost, selling price, stock age, expiry, sales velocity, minimum stock, reorder point, dead stock, fast-moving/slow-moving products.

AI continuously identifies:

- **Low stock:** "Rice 5kg may stock out within 3 days."
- **Overstock:** "You have 46 units but average weekly sales are only 4."
- **Dead stock:** "This product has had no sale in 28 days."
- **Expiry risk:** "₹3,200 inventory may expire within 15 days."
- **High-demand:** "Product X sales increased 41% over the previous period."

### F. Expiry Rescue Engine

Answers: *"How do I prevent this stock from becoming a loss?"*

```
Near expiry → Calculate stock value at risk → Analyze sales velocity
            → Check permissible discount → Find bundle opportunities
            → Suggest promotion → Merchant approves
            → Campaign / pricing action → Track sell-through
```

Example: 32 units expire in 18 days → Option A: 7% discount / Option B: Buy 2 bundle / Option C: WhatsApp promotion — then track which action worked.

### G. Demand & Trend Intelligence

Combines:
- **Internal:** historical sales, product velocity, category trends, day-of-week patterns, seasonal patterns, customer behavior
- **External:** festivals, public market information, relevant web signals, regional events, product trends

Produces: *What is likely to sell, when, and why?*

Example: **DIWALI — 14 DAYS AWAY** — Dry Fruits ↑, Gift Packs ↑, Namkeen ↑, Sweets ↑, Decorations ↑ with recommended preparation (increase stock, prepare bundles, contact repeat customers, create campaign).

**External intelligence must be source-backed and time-stamped, not invented by the AI.**

### H. Procurement & Supplier Intelligence

Compares: supplier, product, purchase price, quantity, MOQ, delivery time, availability, credit terms, delivery cost, historical reliability.

The AI calculates **landed cost**, not blindly the cheapest quote.

Example: Supplier B has lowest unit price (₹116) but Supplier A has lower effective procurement cost for current requirement due to MOQ and delivery constraints. Calculation comes from backend logic.

### I. Customer Intelligence

Identifies: new customers, repeat customers, inactive customers, frequent customers, high-value customers, product preferences, purchase frequency, category affinity.

Recommendations: "12 customers who regularly purchase baby products haven't purchased in 30 days." / "These customers purchased Diwali items last year."

### J. WhatsApp & Marketing

Create campaigns, select customer segment, generate message, personalize, share catalog/offers, send festival greetings, track performance.

**Actual messaging must use authorized/official integrations and customer consent — not scraping or unsolicited messaging.**

### K. Festival Intelligence

Not merely a calendar. Connects: **Festival → Demand → Inventory → Customers → Marketing → Procurement**

```
DIWALI → Demand forecast → Recommended products → Current inventory
       → Stock gap → Supplier recommendations → Customer segments
       → Campaign → Sales tracking
```

### L. Alerts

Alert types: Inventory (low/overstock/dead stock), Expiry (near expiry, high stock at risk), Pricing (margin deterioration, supplier price increase), Sales (demand spike, sales decline), Suppliers (price changes, delayed supply), Customers (inactivity, campaign opportunity), System (failed integration, payment issue).

**Alerts must be actionable:**
- ❌ "Stock is low."
- ✅ "Maggi 70g may stock out in ~2 days. Recommended reorder: 48 units from Supplier A. Estimated landed cost: ₹X."

### M. Analytics

Sales (daily/weekly/monthly/category/product), Profit (revenue/gross profit/margin/product contribution), Inventory (value/turnover/dead stock/expiry loss), Customers (repeat rate/purchase frequency/value), Marketing (campaigns/response/conversions), AI (recommendations accepted/rejected, actions executed, outcomes).

### N. Reports

Daily/weekly/monthly business reports, inventory, profit, expiry, supplier, customer, campaign reports. AI can summarize: *"This week revenue increased, but gross margin declined because three high-volume products had supplier price increases."*

### O. Payment System

Supports payment methods actually available through the integration. Payment Splitter concept (Cart ₹2,106 → Person A ₹1,053 + Person B ₹1,053 → both paid → order completed) can be included, but implementation depends on actual payment provider/API capabilities. **Do not present unsupported multi-party settlement behavior as already available.**

### P. Quick Commerce

Future integration layer — not faked in MVP:

```
KiranaSaathi Inventory → Merchant catalog → Approved commerce integration
                       → Online orders → Store fulfillment → Inventory synchronization
```

Architecture ready, but do not pretend Blinkit/Zepto/etc. integrations exist without partner/API access.

---

## 6. AI Agent Architecture

The part that makes the product genuinely agentic.

**Tools:**

```
get_store_summary()           get_sales()
get_inventory()               get_product_details()
get_expiring_inventory()      get_low_stock_products()
calculate_margin()            calculate_bargain()
forecast_demand()             recommend_reorder()
get_supplier_quotes()         compare_suppliers()
get_customer_segments()       get_festival_opportunities()
create_campaign()             create_alert()
prepare_action()              execute_approved_action()
```

**Architecture:**

```
                  AI AGENT
                     │
          ┌──────────┼──────────┐
          ↓          ↓          ↓
      Inventory   Pricing    Demand
       Tools       Tools      Tools
          ↓          ↓          ↓
      Suppliers   Customers  Marketing
          └──────────┼──────────┘
                     ↓
              ACTION ENGINE
                     ↓
              Merchant Approval
                     ↓
                 Execute
                     ↓
                Audit Log
```

---

## 7. Agent Reach's place

Agent Reach is **NOT** the foundation of KiranaSaathi. It is the **external intelligence layer**.

```
KiranaSaathi → External Intelligence → Agent Reach → Web / GitHub / RSS
             → Evidence → Verification → KiranaSaathi AI
```

**Use it for:** external market research, public information, festival/event information, trend discovery, web research.

**Do NOT use** arbitrary web search as the authoritative source for exact wholesale prices.

**Preserve for every external result:** Source, URL, Title, Published Date, Retrieved Date, Region, Category, Content, Confidence.

**Verdict:** Useful? Yes. Core dependency? No. Day 1 requirement? No. Best role: external intelligence/research layer for the AI agent.

---

## 8. Database / data model

```
Merchant
   └── Store
        ├── Products → Product Variants
        ├── Inventory → Inventory Batches
        ├── Sales → Sale Items
        ├── Purchases → Purchase Items
        ├── Suppliers → Supplier Quotes
        ├── Customers
        ├── Orders → Order Items
        ├── Payments → Payment Splits
        ├── Campaigns
        ├── Festivals / Events
        ├── Alerts
        ├── AI Recommendations
        └── Activity / Audit Logs
```

Everything should be connected. **The database is the actual foundation** — every module is a different view of the same data.

---

## 9. The most important technical principle

**The LLM is NOT the source of truth.**

| ❌ Bad | ✅ Correct |
|---|---|
| LLM: "I think your profit is ₹8,400." | Database → Profit calculation function → ₹8,417.50 → AI explains: "Your gross profit increased 8.2%..." |

Same for: inventory, prices, margins, expiry, payments, reorder quantities, supplier comparisons, sales.

**The AI is the reasoning/orchestration layer. The backend is the source of truth.**

---

## 10. Human approval model (autonomy levels)

| Level | Name | Behavior |
|---|---|---|
| 1 | Observe | "Something is wrong." |
| 2 | Recommend | "You should reorder 50 units." |
| 3 | Prepare | "I prepared the purchase order." |
| 4 | Execute after approval | "Approve to send it." |
| 5 | Autonomous | Only for explicitly configured, low-risk actions |

**For the hackathon: build Levels 1–4.** This gives an actual agentic system without giving an LLM uncontrolled access to financial or inventory operations.

---

## 11. Complete user journey

```
MERCHANT LOGIN → STORE SETUP → PRODUCT CATALOG → INVENTORY → PURCHASES
  → SMART Counter → SALE → PAYMENT → INVENTORY UPDATED → CUSTOMER UPDATED
  → SALES ANALYTICS UPDATED → AI ANALYZES STORE
  → AI DETECTS (low stock / expiry risk / margin problem / demand opportunity /
     supplier opportunity / customer opportunity / festival opportunity)
  → AI RECOMMENDATION → MERCHANT REVIEWS → APPROVE / EDIT / DISMISS
  → ACTION EXECUTED → RESULT TRACKED → AI LEARNS FROM OUTCOME
```

---

## 12. Navigation structure

```
KiranaSaathi AI
├── Home
├── AI Assistant
├── Smart Counter
├── Inventory
├── Purchases & Suppliers
├── Demand & Trends
├── Customers
├── WhatsApp & Marketing
├── Festival Calendar
├── Quick Commerce
├── Analytics
├── Reports
├── Alerts
├── Settings
└── Help & Support
```

These are modules of **one operating system**, not separate products.

---

## 13. Build phases (hackathon scope)

| Phase | Focus | Deliverables |
|---|---|---|
| **1 — Foundation** | Auth & data core | Authentication, Merchant, Store, Database, Product catalog, Inventory, Suppliers, Customers |
| **2 — Transaction engine** | Smart Counter | Cart, Billing, Payment, Sales, Inventory deduction, Customer history |
| **3 — Intelligence** | Deterministic engines | Margin engine, Expiry engine, Demand engine, Reorder engine, Supplier comparison, Alerts |
| **4 — AI Agent** | Agentic layer | AI Assistant, Tool calling, Store context, Recommendation generation, Evidence, Approval workflow, Action execution, Audit log |
| **5 — Growth** | Customer engagement | Customers, Campaigns, WhatsApp, Festival intelligence |
| **6 — External intelligence** | Outside world | Agent Reach, External evidence, Trend research, Market signals |
| **7 — Demo polish** | Presentation | Paytm-style UI, Dashboard, charts, recommendation cards, activity timeline, demo data, realistic merchant scenarios |

### What NOT to build first

❌ Agent Reach, fancy AI animations, WhatsApp automation, quick-commerce, payment splitter, festival UI, 20 dashboard pages, computer vision, complex forecasting model, multi-agent architecture.

### Start with

**Database → APIs → Inventory → Purchases → Sales → Pricing → Events → AI tools**

---

## 14. The one demo that proves the entire system

Judge enters the application and sees:

1. **"Good morning. Here's what needs your attention."**
2. **₹4,850 inventory at expiry risk** → explains products → recommends discount/bundle → merchant approves
3. **Maggi stock may run out in 2 days** → AI calculates reorder quantity → compares suppliers → recommends supplier → merchant approves
4. **Customer offers ₹82 for a ₹95 product** → pricing engine checks cost/margin/inventory age → AI recommends ₹88 counteroffer → merchant accepts
5. **Diwali demand opportunity** → AI identifies expected categories → checks inventory → identifies stock gaps → recommends procurement
6. **Customer campaign** → identifies relevant customers → generates campaign → merchant approves
7. **Analytics** → shows the resulting business impact

That single story demonstrates: **transaction → data → intelligence → recommendation → action → outcome.**

---

## 15. Milestone 1 definition

**"A shopkeeper can actually run one day of their shop inside KiranaSaathi."**

They can: Login → See products → Add inventory → Record purchase → Sell product → Inventory decreases → Profit updates → Customer bargains → System calculates offer → Payment completes → Sale recorded → AI sees the transaction.

Then ask the AI: **"What should I pay attention to?"** — and it returns recommendations based on the actual database.

**If this works, you have the foundation. If it doesn't, adding 15 more pages is just building a beautiful shell around a broken product.**

---

## 16. Key product principles

1. **Every transaction becomes a better business decision.**
2. **The LLM is not the source of truth** — backend engines calculate, AI explains.
3. **One integrated system**, not 15 independent features.
4. **Evidence-backed recommendations** — situation, evidence, proposed action, expected effect, tradeoffs, source/data timestamps.
5. **Human approval for financial/inventory actions** (Levels 1–4 for hackathon).
6. **External intelligence is source-backed and time-stamped**, never fabricated.
7. **Modular monolith first**, avoid unnecessary microservices.
8. **Agent Reach is external intelligence, not the foundation.**
9. **Don't fake unavailable integrations** (quick commerce, multi-party settlement).
10. **The goal is an AI operating system for a kirana store** — not another POS or analytics dashboard.

---

## 17. Tech stack (locked)

| Layer | Technology |
|---|---|
| Frontend | Next.js / TypeScript |
| Backend | Python / FastAPI |
| Database | PostgreSQL / Supabase |
| Business logic | Deterministic business engines (Python) |
| AI layer | LLM agent with tool calling |
| External intelligence | Agent Reach (server-side only) |

---

## Success criteria

The final application must feel like one AI business partner where the merchant can ask **"What should I do today?"** and the system can inspect actual store state, identify the highest-value risks and opportunities, explain the evidence, recommend actions, obtain approval and execute supported actions.

**Ultimate goal:** An AI operating system for a kirana store that continuously turns business data into actionable decisions and measurable outcomes.

**Development order:** Merchant Data → Business Engine → AI Agent → External Intelligence → Actions → Learning Loop
