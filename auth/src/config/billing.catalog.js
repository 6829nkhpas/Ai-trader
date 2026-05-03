// ──────────────────────────────────────────────────────────────
// billing.catalog.js — Polar Product Mapping
// Maps Polar price_ids to internal platform plan tiers.
// ──────────────────────────────────────────────────────────────

export const BILLING_CATALOG = {
  // Weekly Plan
  'price_weekly': { // Replace 'price_weekly' with actual Polar Price ID later
    tier: 'Weekly',
    weight: 10,
    name: 'Weekly Pass',
    description: '7-day access to AI-Trade Platform',
    features: ['Real-time trading', 'Basic AI Insights']
  },
  
  // Monthly Plan
  'price_monthly': { // Replace 'price_monthly' with actual Polar Price ID later
    tier: 'Monthly',
    weight: 20,
    name: 'Pro Monthly',
    description: 'Full monthly access to AI-Trade Platform',
    features: ['Real-time trading', 'Advanced AI Insights', 'Priority Support']
  },
  
  // Yearly Plan
  'price_yearly': { // Replace 'price_yearly' with actual Polar Price ID later
    tier: 'Yearly',
    weight: 30,
    name: 'Pro Yearly',
    description: 'Annual access to AI-Trade Platform with a discount',
    features: ['Real-time trading', 'Advanced AI Insights', 'Priority Support', 'Dedicated Account Manager']
  }
};

/**
 * Validates if a Polar price ID is in the catalog.
 */
export function isValidPriceId(priceId) {
  return Object.keys(BILLING_CATALOG).includes(priceId);
}

/**
 * Retrieves plan metadata by Polar price ID.
 */
export function getPlanByPriceId(priceId) {
  return BILLING_CATALOG[priceId] || null;
}
