'use strict';
const { LIMIT_PROVIDER_IDS } = require('./limitProviders');
const { DEFAULT_LIMITS_REFRESH_MS } = require('./limits');
const LIMIT_REFRESH_VALUES = new Set([60_000, 120_000, 300_000, 900_000, 1_800_000]);

function parseBoolean(value, fallback = true) {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'boolean') return value;
  return !['0', 'false', 'no', 'off'].includes(String(value).trim().toLowerCase());
}

function parseLimitProviders(value) {
  // Omission keeps the historical default; an explicitly empty setting means
  // that no provider is enabled and must survive persistence/reload.
  const source = value === undefined || value === null ? LIMIT_PROVIDER_IDS : value;
  const raw = Array.isArray(source) ? source : String(source).split(',');
  const seen = new Set();
  const providers = [];
  for (const item of raw) {
    const provider = String(item || '').trim().toLowerCase();
    if (!LIMIT_PROVIDER_IDS.includes(provider) || seen.has(provider)) continue;
    seen.add(provider);
    providers.push(provider);
  }
  return providers;
}

function normalizeLimitsRefreshMs(value) {
  const parsed = Number(value);
  if (LIMIT_REFRESH_VALUES.has(parsed)) return parsed;
  return DEFAULT_LIMITS_REFRESH_MS;
}

// A scheduling policy, kept separate from limitsRefreshMs so that switching to
// adaptive and back restores the interval the user had chosen, and so that no
// consumer doing arithmetic on limitsRefreshMs has to handle a sentinel value.
function normalizeLimitsRefreshMode(value) {
  return String(value ?? '').trim().toLowerCase() === 'adaptive' ? 'adaptive' : 'fixed';
}

module.exports = { parseBoolean, parseLimitProviders, normalizeLimitsRefreshMs, normalizeLimitsRefreshMode };
