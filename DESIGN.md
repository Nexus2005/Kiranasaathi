# KiranaSaathi AI — Design System (from UI mockups)

**Source:** 19 ChatGPT UI mockup images in project folder (analyzed 2026-09-22)
**Status:** Captured for implementation — do not deviate without updating this file
**Rule:** Every future UI iteration must follow this document. Update this file at every design iteration.

---

## 1. Screens analyzed (inventory)

| # | File | Screen |
|---|---|---|
| 1 | `04_23_23 PM` | Checkout — Split Payment with UPI |
| 2 | `04_23_26 PM` | Product detail + Make an Offer (bargain) + Checkout |
| 3 | `04_23_28 PM` | Create Campaign (WhatsApp) — 5-step wizard |
| 4 | `04_23_30 PM` | Help & Support |
| 5 | `04_23_33 PM` | Settings (General / Display / Theme Light-Dark-System) |
| 6 | `04_24_28 PM` | Reports |
| 7 | `04_24_31 PM` | WhatsApp & Marketing |
| 8 | `04_24_34 PM` | Demand & Trends |
| 9 | `04_24_36 PM` | Marketing landing page (public site) |
| 10 | `04_24_38 PM` | Analytics |
| 11 | `04_24_40 PM` | Customers |
| 12 | `04_24_42 PM` | Inventory |
| 13 | `04_24_45 PM` | Smart Counter (camera scan + bill) |
| 14 | `04_24_48 PM` | Home dashboard (AI Priority Feed) |
| 15 | `04_24_50 PM` | Alerts |
| 16 | `04_24_52 PM` | Festival Calendar |
| 17 | `04_24_54 PM` | Quick Commerce |
| 18 | `04_24_56 PM` | Purchases & Suppliers |
| 19 | `04_24_59 PM` | AI Assistant (chat) |

**Supporting assets in folder:** Paytm, WhatsApp, UPI, GPay, PhonePe, BHIM, Razorpay, Visa, Mastercard, RuPay, Blinkit, Zepto, Instamart, bigbasket, ONDC logos (SVG/PNG).

---

## 2. Overall UI style

**Style name:** Paytm-inspired light merchant OS dashboard

- Clean, airy, light theme (primary)
- Dense but readable data UI for Indian SMB merchants
- Card-based layout on a soft blue-gray canvas
- Friendly + professional; rounded corners; soft borders; minimal heavy shadows
- Decision-oriented: KPIs + tables + AI insight side rails, not raw chart walls
- Indian context: ₹ amounts, lakh-style figures (₹1,24,560), Hindi-English touches (“Namaste Ramesh ji! 👋”, “Ramesh ji”)
- Dark mode: Settings shows Light / Dark / System — light is default and must match mockups first

---

## 3. Color theme

### 3.1 Core brand colors

| Token | Hex (approx) | Usage |
|---|---|---|
| `brand-blue` (primary) | `#2080F0` | Primary buttons, links, active nav, chart primary, focus |
| `brand-blue-hover` | `#1668D6` | Button hover |
| `brand-blue-deep` | `#0B5FCC` | Pressed / strong accents |
| `paytm-cyan` | `#00BAF2` | Paytm logo / brand mark accent (sparingly) |
| `navy-900` (heading) | `#0B1F3A` / `#0A1F44` | Page titles, metric numbers, dark headings |
| `navy-800` | `#102A56` | Card titles, strong text |
| `beta-blue` | `#2080F0` on `#E8F2FF` | BETA pill |

### 3.2 Backgrounds & surfaces

| Token | Hex (approx) | Usage |
|---|---|---|
| `page-bg` | `#F5F7FA` / `#F3F6FB` | Main canvas behind cards |
| `surface` | `#FFFFFF` | Cards, tables, sidebar, topbar |
| `surface-muted` | `#F8FAFC` | Table headers, subtle panels, chat bubbles (AI) |
| `surface-blue-tint` | `#EFF6FF` / `#E8F2FF` | AI callouts, active nav bg, tip boxes, priority panels |
| `surface-green-tint` | `#ECFDF5` / `#F0FDF4` | Safe/secure panels, success tips |
| `surface-orange-tint` | `#FFF7ED` | Warning / festivity accents |
| `surface-red-tint` | `#FEF2F2` | Danger / expiry / delete panels |
| `surface-purple-tint` | `#F5F3FF` | AI / campaign type accents |
| `surface-pink-tint` | `#FDF2F8` | Promo / festival gradient hints |

### 3.3 Text colors

| Token | Hex (approx) | Usage |
|---|---|---|
| `text-primary` | `#0B1F3A` / `#111827` | Headings, main values |
| `text-secondary` | `#475569` / `#64748B` | Body, descriptions |
| `text-muted` | `#94A3B8` | Placeholders, meta, timestamps |
| `text-on-primary` | `#FFFFFF` | Text on blue buttons |
| `text-link` | `#2080F0` | Inline links, “View All →” |

### 3.4 Semantic / status colors

| Token | Hex (approx) | Badge bg (approx) | Usage |
|---|---|---|---|
| `success` | `#16A34A` | `#DCFCE7` | In Stock, Paid, Active, Delivered, Operational, +deltas |
| `danger` | `#DC2626` / `#EF4444` | `#FEE2E2` | Low Stock, Expiring Soon, Out of Stock, Cancelled, overdue |
| `warning` | `#F59E0B` / `#F97316` | `#FFEDD5` | Pending, Expiring within window, Preparing |
| `info` | `#2080F0` | `#E8F2FF` | Info, system updates |
| `gold` | `#EAB308` / `#F59E0B` | `#FEF9C3` | VIP, star ratings, loyalty crown |
| `whatsapp` | `#25D366` | `#DCFCE7` | WhatsApp actions, connected status |
| `purple` | `#8B5CF6` | `#EDE9FE` | AI sparkles, personalized, marketing icons |
| `pink` | `#EC4899` | `#FCE7F3` | Festival / promo icon accents |
| `teal` | `#14B8A6` | `#CCFBF1` | Chart secondary |

### 3.5 Chart palette (order observed)

1. `#2080F0` blue (primary series / largest donut slice)
2. `#2DD4BF` / `#22D3EE` teal-cyan
3. `#22C55E` green
4. `#FACC15` / `#FBBF24` yellow
5. `#F97316` orange
6. `#EC4899` pink
7. `#8B5CF6` purple
8. `#94A3B8` gray (“Others”)

- Line charts: blue stroke + soft blue gradient fill; light gridlines `#E2E8F0`
- Bar charts: blue primary; paired series may use blue + light blue; grouped expense bars use red/blue/yellow/green
- Donut center: large ₹ total + “Total Sales” caption

### 3.6 Alert priority colors (AI Priority Feed)

| Level | Color | Meaning |
|---|---|---|
| 🔴 | Red `#DC2626` | Urgent (expiry risk) |
| 🟠 | Orange `#F97316` | High (stockout risk) |
| 🟡 | Yellow/amber `#F59E0B` | Medium (supplier/margin) |
| 🟢 | Green `#16A34A` | Opportunity (festival) |
| 🔵 | Blue `#2080F0` | Info / system |

### 3.7 Gradients (used sparingly)

- Sidebar promo / soft callouts: very light blue → white (`#EFF6FF` → `#FFFFFF`)
- Landing hero: white/light overlay on kirana-store photo
- WhatsApp campaign preview header: WhatsApp green header inside phone frame
- Festival banner thumbs: warm orange/purple festive art (image assets, not CSS-critical)

---

## 4. Typography

**Font family:** Modern geometric/humanist sans — matches **Inter** (or Paytm All / system UI fallback).

```css
font-family: Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
```

| Role | Weight | Size (approx) | Color | Notes |
|---|---|---|---|---|
| Brand product title (`KiranaSaathi AI`) | 700 | 18–20px | `#0B1F3A` | Next to Paytm logo |
| Tagline under brand | 400 | 11–12px | `#64748B` | “Your Kirana Shop’s AI Business Partner” |
| Page title (H1) | 700 | 26–28px | `#0B1F3A` | e.g. “Inventory”, “Analytics” |
| Page subtitle | 400–500 | 14–15px | `#64748B` | One-line purpose under H1 |
| Section title (H2) | 600–700 | 16–18px | `#0B1F3A` | Card headers |
| KPI metric value | 700 | 26–32px | `#0B1F3A` | e.g. ₹12,430, 18.5% |
| KPI label | 500 | 12–13px | `#64748B` | “Today’s Sales” |
| KPI delta | 600 | 12–13px | green/red | “↑ +8% vs yesterday” |
| Body / table | 400–500 | 13–14px | `#334155` | Rows, paragraphs |
| Small / meta | 400–500 | 11–12px | `#94A3B8` | Timestamps, hints, “vs last month” |
| Button | 600 | 14px | white/blue | Sentence case labels |
| Badge / pill | 600 | 11–12px | semantic | UPPERCASE rare; mostly sentence case |
| Nav item | 500–600 | 14px | `#334155` / blue active | |
| Chat message | 400–500 | 14px | `#1E293B` | AI bubbles on `#F8FAFC` |

**Numerals:** tabular where possible for tables; ₹ prefix always; Indian grouping (`₹1,24,560`); L suffix for large inventory value (`₹2.48L`).

---

## 5. Spacing, radius, elevation

| Token | Value | Usage |
|---|---|---|
| Page padding | 24px | Main content inset |
| Card padding | 20–24px | Inside cards |
| Grid gap | 16–20px | Between cards |
| Section gap | 24px | Between major blocks |
| `radius-sm` | 6–8px | Inputs, small buttons, chips |
| `radius-md` | 10–12px | Buttons, tabs, standard cards |
| `radius-lg` | 14–16px | KPI cards, large panels, modals |
| `radius-pill` | 9999px | Badges, avatars, BETA, filter chips |
| Border | 1px `#E2E8F0` | Card/table/input borders |
| Shadow-sm | `0 1px 2px rgba(15,23,42,.04)` | Subtle lift |
| Shadow-md | `0 4px 16px rgba(15,23,42,.06)` | Cards, dropdowns |
| Modal/overlay | `rgba(15,23,42,.45)` | Backdrop (e.g. Make an Offer sheet) |

---

## 6. Layout system

### 6.1 App shell (authenticated)

```
┌────────────────────────────────────────────────────────────┐
│ TOPBAR (h≈64px, white, bottom border)                      │
│ [Paytm logo] [KiranaSaathi AI BETA] … [search Ctrl+K] …    │
│ [location] [bell] [avatar + Ramesh Verma / Sharma Kirana]  │
├──────────┬─────────────────────────────────────────────────┤
│ SIDEBAR  │ MAIN                                            │
│ w≈220px  │ page header → KPI row → content grid           │
│ white    │ often: 2/3 main column + 1/3 right rail         │
│          │                                                 │
│ bottom:  │                                                 │
│ Settings │                                                 │
│ Help     │                                                 │
└──────────┴─────────────────────────────────────────────────┘
```

### 6.2 Topbar details

- Left: Paytm wordmark (cyan-blue) + divider + **KiranaSaathi AI** (navy bold) + **BETA** pill + tiny gray tagline
- Center: rounded search field, placeholder e.g. `Search products, customers, or ask AI anything...`, right-aligned `Ctrl K` kbd chip; light blue-gray fill `#F1F5F9` or white + border
- Right: location pill (“Nashik, Maharashtra” / “Noida, UP”) with pin icon; notification bell (red dot); avatar circle initials `RV`; name + “Merchant since 2023”; store chip (“Sharma Kirana Store”)
- Variant (Home): also `+ Add Sale` and `Scan Product` buttons in topbar

### 6.3 Sidebar navigation (order is fixed)

1. Home  
2. AI Assistant  
3. Smart Counter  
4. Inventory  
5. Purchases & Suppliers  
6. Demand & Trends  
7. Customers  
8. WhatsApp & Marketing  
9. Quick Commerce — **`New` badge (blue pill)**  
10. Analytics  
11. Reports  

**Bottom:** Settings · Help & Support  

**Active item:** light blue bg `#E8F2FF`, blue icon + text `#2080F0`, rounded 8–10px, full-width highlight.

**Sidebar promo card** (above Settings, always present pattern):

- Soft blue gradient card, rounded 12–14px
- Bold navy heading e.g. “Grow Faster with KiranaSaathi AI” / context-specific per page
- 3–5 green check bullets (12px)
- White/blue outline button with play icon: **“Watch Demo”**
- Optional small illustration (shop, truck, phone) bottom-right

### 6.4 Page header pattern

```
[Icon-in-blue-circle]  Page Title (28px bold)     [optional actions…]
                       Subtitle (one line, gray)
```

Actions right-aligned: primary button(s) + secondary outline + kebab menu.

### 6.5 Standard page composition

1. **Page header** (title + subtitle + CTAs)
2. **KPI row** — 4–6 equal cards (sometimes 4 + wider AI card)
3. **Content band** — tables, charts, wizards
4. **Right rail** (common) — AI insights, summaries, quick actions, promos
5. **Bottom** — secondary tables / recommendations / AI assistant teaser

Grid: CSS grid, main ~ `1fr` + rail ~ `340–380px` on large screens; stack rail below on smaller widths.

### 6.6 Landing (public) shell

- Sticky white nav: logo + brand, links (Features, Solutions, Pricing, Success Stories, Resources), location, **Login** (outline), **Get Started** (primary)
- Hero: left copy + dual CTA (`Get Started Free`, `Watch Demo`), trust row (avatars + 4.8/5 stars), right phone mockup + merchant photo
- Stats band: 4 icons + big numbers (1 Lakh+, 30%+, 10+ Hours, 4.8/5)
- Feature grid: 6 icon tiles (Smart Inventory, WhatsApp Marketing, Sales Analytics, Festival Calendar, Quick Commerce, AI Assistant)

---

## 7. Components

### 7.1 Buttons

| Type | Style |
|---|---|
| Primary | Solid `#2080F0`, white text, radius 8–10px, px 16–20, py 10–12, weight 600; hover `#1668D6` |
| Secondary / outline | White bg, 1px `#2080F0` or `#CBD5E1`, blue or navy text |
| Ghost / text | Blue text only (“View All →”, “Edit Cart”) |
| Danger outline | White, red border/text (“Delete Account” panel is red-tinted row) |
| Icon button | 36×36 square, radius 8, border `#E2E8F0` |
| WhatsApp green | `#25D366` solid when WhatsApp-branded |

Disabled primary (e.g. Place Order before split complete): gray `#CBD5E1` bg, white text, lock icon.

### 7.2 KPI / stat card

```
┌─────────────────────────────┐
│ (icon in tinted circle)  Label │
│                      Big ₹Number │
│                      ↑ +8%       │
│                      vs yesterday│
└─────────────────────────────┘
```

- White, radius 14–16, border `#E2E8F0` or none + shadow-sm
- Icon tile: 40–44px circle, soft tint (blue/green/orange/red/purple) + colored icon
- Delta green up / red down; tiny gray comparison line
- Critical sub-badge possible: red pill “3 critical”, “Within 7 days”

### 7.3 AI Priority / insight row

```
(soft colored icon tile)  Title (bold 14px)     [Action button]
                          One-line evidence/description
```

- Colored left icon: red clock, orange box, blue chart, green ₹, purple sparkle
- Right: outline or solid small button — “View Products”, “Review Stock”, “Compare Now”
- Optional right chevron for drill-in cards (AI Insights list)

### 7.4 Tables

- Header row: 12–13px semibold `#64748B`, bg white or `#F8FAFC`, bottom border
- Rows: 13–14px, py 12–14, bottom border `#F1F5F9`, hover `#F8FAFC`
- Product cells: 32px thumbnail + name (+ category subtext)
- Status: pill badges (see §3.4)
- Actions: outline blue “View” buttons + vertical kebab
- Footer: “Show [10] per page” + pagination pills (active = solid blue circle)
- Selection: left checkbox column on list pages (Inventory, Customers)

### 7.5 Badges / pills

- Status pills: radius full, px 10 py 4, 11–12px semibold, tinted bg + dark semantic text
- Filter tabs (segmented pills): active = solid blue or blue underline tab style (both appear: tabs with blue underline on Customers/Inventory; pill filters on Alerts/Festival)
- Category chips: white + border, active solid blue (Festival filter: All, Religious, Seasonal…)
- BETA / New: small blue pills

### 7.6 Inputs & forms

- Input: white, border `#CBD5E1`/`#E2E8F0`, radius 8–10, py 10–12, focus ring blue
- Search: same + search icon left; optional `Ctrl K` right
- Labels: 13–14px medium navy; required `*` in red
- Helper: 12px gray; char counts `20/50` right-aligned
- Select: custom chevron, same as input
- Toggle: iOS-style; on = blue `#2080F0`
- Radio cards (payment method / campaign type): bordered tiles; selected = blue border + blue check corner
- Stepper (checkout / campaign): numbered circles — done = blue check, current = solid blue, upcoming = gray outline; connector lines

### 7.7 Cards & panels

- Standard card: white, radius 14–16, p 20–24, border or shadow-sm
- Card header: title 16–17px bold + optional right link “View All →” or dropdown
- Tinted callout: bg tint + icon + text (success tips, AI suggestions, “Cart value exceeds ₹2,000”)
- Right-rail AI card: title “AI Inventory Insights” + sparkle icon + list rows + chevrons

### 7.8 Chat / AI Assistant

- Page: centered column max ~720–760px + right rail (Quick Actions, Today’s AI Insights)
- Category chips above chat: Stock & Inventory, Pricing & Margin, Suppliers, Festival Demand, Marketing, Reports
- AI bubble: `#F8FAFC` (or white), radius 12–16, left avatar robot icon in blue circle
- User bubble: solid blue `#2080F0`, white text, right-aligned; avatar `RV` right
- Suggestion chips: white pills, border `#E2E8F0`, blue text
- Result cards inside chat: horizontal product cards (image, name, ↑ demand, Suggested Qty)
- Composer: rounded full-width input, attach 📎, mic, blue circular send button
- Footer hint: `Try: "Show expiring products" …` muted 12px

### 7.9 Smart Counter specifics

- Mode toggle buttons: **By Camera** (primary solid), By Barcode, Search Manually (outline)
- Camera viewport: rounded 16, dark overlay chips; detection boxes cyan/blue stroke; floating price labels dark navy pills
- “Live • Detecting products…” green dot chip
- Detected product mini-cards + blue “Add” outline buttons
- Quick Add: category chips + product grid
- Bill panel (right rail): line items with qty stepper (− 1 +), price, remove ×; “Add Discount”; totals block; **Create Bill ₹180.00 →** full-width blue; green confirmation “Stock will be updated automatically after billing”
- AI Suggestion card: blue-tinted, product thumb, cross-sell prompt, “+ Add”

### 7.10 Bargain (“Make an Offer”) modal

- Centered modal / sheet over dimmed page
- Product row (thumb, name, current price)
- Offer input large with ₹ prefix; “Min. offer price: ₹130”; “Suggest Price” link with sparkle
- Optional message textarea with counter `48/200`
- Primary “Send Offer”
- Success state: big green check circle, “Offer Accepted!”, Offer Price, You Save ₹X (Y%), Add to Cart
- Negotiation thread: chat bubbles, timestamps, “You” vs “Sharma Kirana Store”, green “Accepted ₹138”

### 7.11 Split payment

- Left: Order Summary list + totals (Item Total, Shop Discount in green, FREE green pill, Cart Value large)
- Center: blue-tint icon + “Split Payment with UPI”; green check feature list; tabs **QR Code Split** | Share Payment Link (active underline blue)
- Two payer cards: Payer 1 blue-tint, Payer 2 green-tint; huge ₹1,053; QR; UPI logo row (GPay, PhonePe, Paytm, BHIM, Others)
- Expiry countdown bar + Refresh; outline “+ Add More People”
- Right: promo illustration card, numbered How-it-works, Safe & Secure green panel, AI Assistant mini-card

### 7.12 Charts (patterns to reuse)

| Chart | Where | Style |
|---|---|---|
| Line / area | Sales Overview, Customer Insights | Blue line, gradient fill, tooltip white card w/ date + value + % delta |
| Bars | Sales Trend, Time of Day, Message Performance | Solid blue; labels ₹K on axis; optional This/Last month toggle |
| Grouped bars | Profit & Expense | Blue / red / green series + legend dots |
| Donut | Category sales, Inventory value | Palette §3.7, center total, legend with % |
| Progress bars | Segments, Demand Forecast, Top Categories | Blue rounded track+fill + right % or ↑ |
| Heatmap | Hyperlocal Demand Map | Green→red demand dots on light map |
| Sparklines / growth score | Analytics “Growth Score 82/100” | Green progress bar |

### 7.13 Icons

- Style: outline/stroke, 20–24px in nav; 16–18px inline; occasional filled for active
- Families: home, sparkle (AI), barcode/scan, box (inventory), truck (suppliers), chart, users, WhatsApp glyph, bolt (quick commerce), chart-bar (analytics), file (reports), bell, settings, help circle
- Enclosed in soft tinted squares/circles on cards (40px, radius 10–12)

### 7.14 Imagery & empty/promo art

- Friendly 3D/flat illustrations: robot AI mascot (blue), shopkeepers, people with phones (split pay), delivery scooter, shop storefront
- Product photos: real FMCG pack shots (Maggi, Parle-G, Amul, Fortune, Lay’s…) with white bg thumbs
- WhatsApp preview: phone frame with green header, business name + verified check, festive banner image, CTA buttons Shop Now / Contact Us
- Landing: photo-real kirana aisle + smiling merchant; script-style handwritten slogan on chalkboard (“Badi Soch Har Kirana Store ke Liye”)

---

## 8. Module-specific design notes (from mockups)

| Module | Signature elements |
|---|---|
| **Home** | Greeting “Good morning, Ramesh ji! 👋”, weather + location, festive banner CTA, 6 KPIs, Today’s Priorities list, Sales Overview mini-chart, Talk to KiranaSaathi chat teaser + chips, Inventory at a Glance table, Top Categories progress, Quick Commerce + WhatsApp store cards |
| **AI Assistant** | See §7.8; right rail Quick Actions grid (6 tinted tiles) + Today’s AI Insights |
| **Smart Counter** | See §7.9; right bill always visible |
| **Inventory** | 5 KPIs, filter tabs (All/Low/Expiring/Out), rich table (SKU, prices, expiry red text), AI Inventory Insights rail, donut Inventory Value, CSV upload CTA |
| **Purchases & Suppliers** | Purchase table w/ status Paid/Pending, Supplier Comparison cards (“Best Price” green badge + Order Now), AI Recommended Purchases list, Pending & Due Pay Now, AI Procurement Assistant blue CTA |
| **Demand & Trends** | Location/date/Festival Calendar buttons, Diwali 2025 orange festivity card, Sales Trend compare, Emerging Trends ranked list, Hyperlocal map, AI Insights, Recommended Actions with action buttons (Reorder, Create Offer, Send Offers) |
| **Customers** | Segment filter tabs, table with VIP/Frequent/Regular/At Risk/New pills, segments horizontal bars, Customer Actions icon grid, Loyalty Program gold crown card, AI Recommendations list |
| **WhatsApp & Marketing** | Green WhatsApp accent, campaign table w/ type pills + Active/Completed, message performance bars, Customer Groups + Send Message, Quick Campaign Templates, AI Message Assistant “Try Now” |
| **Festival Calendar** | Month grid with event dots, upcoming festival rows (date block, emoji/icon art, category chips, expected sales ↑%, Create Campaign), AI Recommendations checklist + Generate Marketing Plan |
| **Quick Commerce** | Platform connection cards (brand-colored logos, Connected green), recent orders table, Fast-Moving Products Online, Inventory Sync Status green |
| **Analytics** | 4 KPIs (incl. Gross Profit Margin), Sales Overview line, Top Selling table, Time-of-Day bars, Profit & Expense grouped bars, AI Insights, Customer Insights, Growth Score |
| **Reports** | Date/store filters, Download Report primary, report type list with PDF/XLS chips + Download buttons, GST & Compliance block, Store Performance multi-location table |
| **Alerts** | Category tabs w/ counts, alert list with severity dot + icon tile + title/desc + time + action button, Alert Preferences toggles rail, Alerts Summary tinted 2×2 grid |
| **Settings** | Tab bar (General, Store & Profile, Users & Team, Notifications, Integrations, Billing & Plan, Security, Data & Privacy), setting rows with icon + label + right control, theme segmented Light/Dark/System, right rail profile + plan green Active card + Quick Settings |
| **Help & Support** | Search + popular chips, 4 tinted category cards, FAQ accordion, contact channel rows w/ Available badge, Support Status operational list, AI help blue card |
| **Checkout / payments** | Progress stepper, order summary, UPI split, payment method radio cards, Pay Securly full-width blue |
| **Campaign wizard** | 5-step stepper, campaign type icon cards (selected = blue border + check), WhatsApp phone preview rail |

---

## 9. Motion & interaction (lightweight)

- Hover: buttons darken; table rows `#F8FAFC`; cards subtle shadow increase
- Focus: 2px blue ring `rgba(32,128,240,.35)`
- Transitions: 150–200ms ease on color/shadow/transform
- No heavy parallax or bounce — professional SMB tool
- Live states: pulsing green dot on “Live • Detecting”; skeleton loaders acceptable for tables (gray shimmer `#F1F5F9`)
- Modal: fade backdrop + slight scale-in of sheet

---

## 10. Accessibility & content rules

- Body text ≥ 4.5:1 contrast on white (navy/slate on white is safe)
- Never rely on color alone for status — pair with text pills
- Numbers always with ₹ and Indian digit grouping in UI copy
- Dates shown as `DD Mon YYYY` (e.g. 15 Jan 2026) in tables; Settings allows DD/MM/YYYY
- Buttons: verb-first (“Create Campaign”, “Restock Now”, “Order Now”)
- AI copy: polite, named merchant (“Namaste Ramesh ji!”), evidence + action, no fake precision
- Microcopy voice: encouraging, growth-oriented (“Grow Faster…”, “Never Miss What Matters”, “Smarter Stock Bigger Profits”)

---

## 11. Locked design tokens (implementation cheat sheet)

```css
/* Colors */
--color-primary: #2080F0;
--color-primary-hover: #1668D6;
--color-paytm-cyan: #00BAF2;
--color-navy: #0B1F3A;
--color-page-bg: #F5F7FA;
--color-surface: #FFFFFF;
--color-border: #E2E8F0;
--color-text: #0B1F3A;
--color-text-secondary: #475569;
--color-text-muted: #94A3B8;
--color-success: #16A34A;
--color-danger: #DC2626;
--color-warning: #F59E0B;
--color-whatsapp: #25D366;
--color-purple: #8B5CF6;

/* Chart series */
--chart-1: #2080F0;
--chart-2: #22D3EE;
--chart-3: #22C55E;
--chart-4: #FACC15;
--chart-5: #F97316;
--chart-6: #EC4899;
--chart-7: #8B5CF6;
--chart-8: #94A3B8;

/* Typography */
--font-sans: Inter, "Segoe UI", Roboto, sans-serif;

/* Radius */
--radius-sm: 8px;
--radius-md: 12px;
--radius-lg: 16px;
--radius-pill: 9999px;

/* Layout */
--sidebar-width: 220px;
--topbar-height: 64px;
--content-max-pad: 24px;
--rail-width: 360px;
```

---

## 12. Do / Don’t

**Do**

- Keep light Paytm-blue merchant OS look consistent across all 15 modules
- Put AI evidence next to every recommendation (numbers from engines, not vibes)
- Use right-rail AI insight panels on data-heavy pages
- Use actionable buttons on every alert/priority row
- Match nav order and “New” badge exactly as §6.3

**Don’t**

- No dark neon, no heavy glassmorphism, no purple-dominated theme
- No unlabeled color-only status
- No chart without a business caption (“Daily sales trend…”)
- No fake brand-green WhatsApp full-page takeover except WhatsApp modules
- Don’t invent new primary blues — always `#2080F0`

---

## 13. Iteration log (design)

| Iteration | Date | Changes |
|---|---|---|
| D0 | 2026-09-22 | Initial full analysis of 19 mockups → this file created. No code. |
