import { apiClient } from "./client";
import type {
  RuntimeEventKind,
  RuntimeEventLevel,
  RuntimeEventSource,
  RuntimeEventSummary,
} from "../types/runtime";

const RUNTIME_EVENT_LEVELS = new Set<RuntimeEventLevel>([
  "INFO",
  "WARNING",
  "ERROR",
]);
const RUNTIME_EVENT_SOURCES = new Set<RuntimeEventSource>([
  "PERSISTENCE",
  "LEASE",
  "DISPATCH",
  "WORKER",
  "RUNTIME",
  "AGENT",
  "VERIFICATION",
  "REVIEW",
  "REPAIR",
  "INTEGRATION",
]);
const RUNTIME_EVENT_KINDS = new Set<RuntimeEventKind>([
  "RUN_STARTED",
  "RUN_FINALIZED",
  "LEASE_ACQUIRED",
  "LEASE_TAKEN_OVER",
  "LEASE_HEARTBEAT",
  "LEASE_RELEASED",
  "EVIDENCE_RECORDED",
]);

const SENSITIVE_ATTRIBUTE_KEYS = new Set([
  "access_token",
  "api_key",
  "authorization",
  "bearer_token",
  "password",
  "refresh_token",
  "run_token",
  "secret",
  "siliconflow_api_key",
]);
const MAX_EVENT_DATA_CHARACTERS = 65_536;

type JsonRecord = Record<string, unknown>;

export function runtimeEventStreamUrl(
  runId: string,
  afterSequence = 0,
): string {
  const normalizedRunId = runId.trim();
  if (!normalizedRunId) {
    throw new Error("runId is required for the runtime event stream.");
  }
  if (!Number.isSafeInteger(afterSequence) || afterSequence < 0) {
    throw new Error("afterSequence must be a non-negative safe integer.");
  }

  const query =
    afterSequence > 0 ? `?after_sequence=${String(afterSequence)}` : "";
  return `${apiClient.baseUrl}/api/v1/runs/${encodeURIComponent(normalizedRunId)}/events${query}`;
}

export function parseRuntimeEventSummary(data: string): RuntimeEventSummary {
  if (data.length > MAX_EVENT_DATA_CHARACTERS) {
    throw new Error("Runtime event payload exceeds the browser bound.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    throw new Error("Runtime event payload is not valid JSON.");
  }
  if (!isRecord(parsed)) {
    throw new Error("Runtime event payload must be an object.");
  }

  const level = requiredString(parsed, "level");
  const source = requiredString(parsed, "source");
  const kind = requiredString(parsed, "kind");
  if (!RUNTIME_EVENT_LEVELS.has(level as RuntimeEventLevel)) {
    throw new Error(`Unknown runtime event level: ${level}`);
  }
  if (!RUNTIME_EVENT_SOURCES.has(source as RuntimeEventSource)) {
    throw new Error(`Unknown runtime event source: ${source}`);
  }
  if (!RUNTIME_EVENT_KINDS.has(kind as RuntimeEventKind)) {
    throw new Error(`Unknown runtime event kind: ${kind}`);
  }

  const attributes = parsed.attributes;
  if (!isRecord(attributes)) {
    throw new Error("Runtime event attributes must be an object.");
  }
  assertBrowserSafeAttributes(attributes);

  const attributesSha256 = requiredString(parsed, "attributes_sha256");
  if (!/^[0-9a-f]{64}$/.test(attributesSha256)) {
    throw new Error("Runtime event attributes_sha256 is invalid.");
  }

  return {
    id: positiveInteger(parsed, "id"),
    event_id: requiredString(parsed, "event_id"),
    run_id: requiredString(parsed, "run_id"),
    sequence: positiveInteger(parsed, "sequence"),
    event_key: requiredString(parsed, "event_key"),
    kind: kind as RuntimeEventKind,
    source: source as RuntimeEventSource,
    level: level as RuntimeEventLevel,
    task_id: nullableString(parsed, "task_id"),
    dispatch_id: nullableString(parsed, "dispatch_id"),
    generation: nullablePositiveInteger(parsed, "generation"),
    message: requiredString(parsed, "message"),
    schema_version: positiveInteger(parsed, "schema_version"),
    attributes,
    attributes_sha256: attributesSha256,
    created_at: requiredString(parsed, "created_at"),
  };
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(record: JsonRecord, key: string): string {
  const value = record[key];
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Runtime event ${key} must be a non-empty string.`);
  }
  return value;
}

function nullableString(record: JsonRecord, key: string): string | null {
  const value = record[key];
  if (value === null) {
    return null;
  }
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`Runtime event ${key} must be null or a non-empty string.`);
  }
  return value;
}

function positiveInteger(record: JsonRecord, key: string): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new Error(`Runtime event ${key} must be a positive safe integer.`);
  }
  return value;
}

function nullablePositiveInteger(
  record: JsonRecord,
  key: string,
): number | null {
  const value = record[key];
  if (value === null) {
    return null;
  }
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new Error(
      `Runtime event ${key} must be null or a positive safe integer.`,
    );
  }
  return value;
}

function assertBrowserSafeAttributes(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertBrowserSafeAttributes);
    return;
  }
  if (!isRecord(value)) {
    return;
  }

  for (const [rawKey, nested] of Object.entries(value)) {
    const key = rawKey.trim().toLowerCase();
    if (SENSITIVE_ATTRIBUTE_KEYS.has(key) || key.endsWith("_api_key")) {
      throw new Error(`Runtime event attribute ${rawKey} is not browser-safe.`);
    }
    assertBrowserSafeAttributes(nested);
  }
}
