export type RuntimeEventLevel = "INFO" | "WARNING" | "ERROR";

export type RuntimeEventSource =
  | "PERSISTENCE"
  | "LEASE"
  | "DISPATCH"
  | "WORKER"
  | "RUNTIME"
  | "AGENT"
  | "VERIFICATION"
  | "REVIEW"
  | "REPAIR"
  | "INTEGRATION";

export type RuntimeEventKind =
  | "RUN_STARTED"
  | "RUN_FINALIZED"
  | "LEASE_ACQUIRED"
  | "LEASE_TAKEN_OVER"
  | "LEASE_HEARTBEAT"
  | "LEASE_RELEASED"
  | "EVIDENCE_RECORDED";

/**
 * Browser-safe projection of the accepted backend PersistedRuntimeEvent shape.
 * Field names intentionally remain snake_case at the wire boundary.
 */
export type RuntimeEventSummary = Readonly<{
  id: number;
  event_id: string;
  run_id: string;
  sequence: number;
  event_key: string;
  kind: RuntimeEventKind;
  source: RuntimeEventSource;
  level: RuntimeEventLevel;
  task_id: string | null;
  dispatch_id: string | null;
  generation: number | null;
  message: string;
  schema_version: number;
  attributes: Readonly<Record<string, unknown>>;
  attributes_sha256: string;
  created_at: string;
}>;

/**
 * Runtime timeline DTOs intentionally exclude run_token and other credentials.
 * Backend persistence and success evidence remain authoritative.
 */
export type RuntimeTimelinePage = Readonly<{
  events: readonly RuntimeEventSummary[];
  next_after_sequence: number | null;
}>;
