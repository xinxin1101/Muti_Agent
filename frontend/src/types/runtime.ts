export type RuntimeEventSummary = Readonly<{
  eventId: string;
  runId: string;
  sequence: number;
  kind: string;
  source: string;
  level: string;
  taskId: string | null;
  dispatchId: string | null;
  generation: number | null;
  message: string;
  schemaVersion: number;
  attributes: Readonly<Record<string, unknown>>;
  createdAt: string;
}>;

/**
 * Browser-safe observability DTOs intentionally exclude run_token and other
 * credentials. Backend persistence and success evidence remain authoritative.
 */
export type RuntimeTimelinePage = Readonly<{
  events: readonly RuntimeEventSummary[];
  nextAfterSequence: number | null;
}>;
