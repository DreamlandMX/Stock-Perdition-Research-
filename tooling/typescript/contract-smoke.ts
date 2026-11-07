/**
 * Generic client-boundary guards only. Future API DTO types must be
 * OpenAPI-generated; do not maintain handwritten copies of Pydantic contracts.
 */

const ISO_UTC_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/u;
const VERSION_PATTERN = /^v[1-9]\d*$/u;

export function isIsoUtcTimestamp(value: string): boolean {
  return ISO_UTC_PATTERN.test(value) && Number.isFinite(Date.parse(value));
}

export function isProbability(value: number): boolean {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

export function isSchemaVersion(value: string): boolean {
  return VERSION_PATTERN.test(value);
}
