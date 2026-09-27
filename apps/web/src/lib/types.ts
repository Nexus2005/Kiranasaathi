export type Priority = {
  id: string;
  type: string;
  severity: string;
  title: string;
  description: string | null;
  created_at: string;
};

export type DashboardSummary = {
  store: { name: string; location: string | null; currency: string };
  user: { name: string; email: string };
  kpis: {
    today_sales: number;
    yesterday_sales: number;
    sales_delta_pct: number;
    today_orders: number;
    today_profit: number;
    inventory_value: number;
    total_products: number;
    low_stock: number;
    out_of_stock: number;
    expiring_soon: number;
    open_alerts: number;
    recommendations: number;
  };
  priorities: Priority[];
  week_sales: { day: string; date: string; total: number }[];
  top_products: {
    name: string;
    category: string;
    units: number;
    revenue: number;
    profit: number;
  }[];
  categories: { name?: string; category: string; revenue: number }[];
  recommendations: {
    id: string;
    type: string;
    title: string;
    description: string;
    evidence: Record<string, unknown>;
    severity: string;
    confidence: number | null;
    status: string;
    proposed_action: string | null;
    created_at: string;
  }[];
  activity: {
    id: string;
    event_type: string;
    message: string | null;
    created_at: string;
    entity_type: string | null;
  }[];
};

export type Product = {
  id: string;
  name: string;
  category: string;
  sku: string | null;
  barcode: string | null;
  unit: string;
  mrp: number;
  selling_price: number;
  purchase_price: number;
  reorder_level: number;
  is_active: boolean;
  quantity: number;
  status: "in_stock" | "low_stock" | "out_of_stock";
  margin: number;
  margin_pct: number;
  brand?: string | null;
  image_url?: string | null;
  description?: string | null;
  source?: string;
  attribution?: string | null;
};

export type Customer = {
  id: string;
  name: string;
  phone: string | null;
  orders: number;
  total_spent: number;
  last_purchase: string | null;
  created_at: string;
};

export type SaleListItem = {
  id: string;
  subtotal: number;
  discount: number;
  total: number;
  payment_method: string;
  status: string;
  created_at: string;
  customer_name: string | null;
  items: { name: string; quantity: number; unit_price: number; line_total: number }[];
};

export function formatINR(n: number): string {
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: n % 1 === 0 ? 0 : 2,
  }).format(n);
}

export function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-IN", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// ---------- Phase 2: inventory intelligence ----------

export type InventoryIntelItem = {
  product_id: string;
  name: string;
  category: string;
  sku: string | null;
  unit: string;
  image_url?: string | null;
  mrp: number;
  selling_price: number;
  purchase_price: number;
  reorder_level: number;
  quantity: number;
  sellable_quantity: number;
  expired_quantity: number;
  margin: number;
  margin_pct: number;
  velocity: number | null;
  days_of_stock: number | null;
  batch_qty: number | null;
  next_expiry: string | null;
  expiry_status: string;
  days_to_expiry: number | null;
  stock_status: string;
  stock_value_cost: number;
  stock_value_sales: number;
  at_risk_value: number;
  reorder_point: number;
  reorder_required: boolean;
  last_purchase_at: string | null;
};

export type InventoryIntel = {
  items: InventoryIntelItem[];
  count: number;
  summary: {
    total_cost_value: number;
    total_sales_value: number;
    potential_gross_margin: number;
    at_risk_value: number;
    expired_value: number;
    dead_stock_value: number;
    status_counts: Record<string, number>;
  };
};

export type BargainResult = {
  decision: "ACCEPT" | "COUNTER" | "REJECT" | "INVALID";
  reason: string;
  product_id: string;
  quantity: number;
  current_price: number;
  offer: number;
  purchase_cost?: number;
  minimum_acceptable_price: number | null;
  counteroffer?: number;
  margin_at_offer_pct?: number | null;
  total_for_quantity?: number;
};

export type PricingInfo = {
  product_id: string;
  name: string;
  purchase_cost: number;
  selling_price: number;
  mrp: number | null;
  gross_profit: number;
  gross_margin_pct: number | null;
  minimum_acceptable_price: number | null;
};

export type PurchaseItem = {
  product_id: string;
  name: string;
  quantity: number;
  unit_cost: number;
  line_total: number;
  expiry_date: string | null;
  batch_no: string | null;
};

export type PurchaseOrder = {
  id: string;
  status: "pending" | "received" | "cancelled";
  total_amount: number;
  invoice_no: string | null;
  notes: string | null;
  created_at: string;
  received_at: string | null;
  supplier_name: string | null;
  line_count: number;
  unit_count: number;
  items: PurchaseItem[];
};

export type Supplier = {
  id: string;
  name: string;
  phone: string | null;
  address: string | null;
  categories: string | null;
  lead_time_days: number | null;
  payment_terms: string | null;
  received_orders: number;
  total_value: number;
  last_purchase_at: string | null;
  pending_orders: number;
  has_history: boolean;
  performance_note?: string;
};

export type Alert = {
  id: string;
  type: string;
  severity: "info" | "warning" | "critical" | "opportunity";
  title: string;
  description: string | null;
  status: "open" | "acknowledged" | "resolved";
  created_at: string;
  is_read: boolean;
};
