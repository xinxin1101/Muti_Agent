import { describe, expect, it } from "vitest";

import {
  parseRuntimeEventSummary,
  runtimeEventStreamUrl,
} from "./runtime-events";

const event = {
  id: 1,
  event_id: "33333333-3333-3333-3333-333333333333",
  run_id: "22222222-2222-2222-2222-222222222222",
  sequence: 1,
  event_key: "run:started",
  kind: "RUN_STARTED",
  source: "PERSISTENCE",
  level: "INFO",
  task_id: null,
  dispatch_id: null,
  generation: null,
  message: "Persisted run started.",
  schema_version: 1,
  attributes: {
    project_id: "11111111-1111-1111-1111-111111111111",
    task_count: 1,
  },
  attributes_sha256: "a".repeat(64),
  created_at: "2026-08-19T00:00:00Z",
} as const;

describe("runtime event SSE boundary", () => {
  it("builds a resumable event stream URL", () => {
    expect(
      runtimeEventStreamUrl(event.run_id, 7),
    ).toBe(
      `http://localhost:8000/api/v1/runs/${event.run_id}/events?after_sequence=7`,
    );
  });

  it("parses a browser-safe runtime event", () => {
    const parsed = parseRuntimeEventSummary(JSON.stringify(event));

    expect(parsed.sequence).toBe(1);
    expect(parsed.kind).toBe("RUN_STARTED");
    expect(parsed.attributes.task_count).toBe(1);
  });

  it("rejects sensitive nested attributes even if the wire payload is JSON", () => {
    expect(() =>
      parseRuntimeEventSummary(
        JSON.stringify({
          ...event,
          attributes: {
            nested: {
              run_token: "must-never-reach-the-browser",
            },
          },
        }),
      ),
    ).toThrow(/browser-safe/);
  });

  it("rejects unknown runtime event enums", () => {
    expect(() =>
      parseRuntimeEventSummary(
        JSON.stringify({
          ...event,
          kind: "MODEL_SAYS_SUCCESS",
        }),
      ),
    ).toThrow(/Unknown runtime event kind/);
  });
});
