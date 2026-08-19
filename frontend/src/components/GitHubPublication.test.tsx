import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as productApi from "../api/product";
import { GitHubPublication } from "./GitHubPublication";

vi.mock("../api/product");

const runId = "22222222-2222-2222-2222-222222222222";

type PublicationState = "READY" | "PUBLISHING" | "PUBLISHED";

function publication(state: PublicationState = "READY") {
  return {
    run_id: runId,
    project_id: "11111111-1111-1111-1111-111111111111",
    state,
    source_basis: "SINGLE_TASK" as const,
    source_commit: "a".repeat(40),
    source_evidence_id: 7,
    source_evidence_sha256: "b".repeat(64),
    repository_slug: "example/repo",
    base_branch: "main",
    branch_name: `devflow/run-${runId}`,
    publisher_configured: true,
    attempt_count: state === "READY" ? 0 : 1,
    pull_request_number: state === "PUBLISHED" ? 42 : null,
    pull_request_url:
      state === "PUBLISHED" ? "https://github.com/example/repo/pull/42" : null,
    pull_request_state: state === "PUBLISHED" ? ("open" as const) : null,
    pull_request_draft: state === "PUBLISHED" ? true : null,
    last_error_code: null,
    last_error_message: null,
  };
}

function renderPublication(status: "RUNNING" | "SUCCEEDED" | "FAILED") {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <GitHubPublication runId={runId} runStatus={status} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(productApi.getGitHubPublication).mockResolvedValue(publication());
  vi.mocked(productApi.publishGitHubDraft).mockResolvedValue(publication("PUBLISHED"));
});

describe("GitHubPublication", () => {
  it("does not query or publish before persisted Run success", () => {
    renderPublication("RUNNING");
    expect(
      screen.getByText(/eligible only after persisted Run status is SUCCEEDED/),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Create Draft PR" }),
    ).not.toBeInTheDocument();
    expect(productApi.getGitHubPublication).not.toHaveBeenCalled();
    expect(productApi.publishGitHubDraft).not.toHaveBeenCalled();
  });

  it("renders only backend-selected publication facts", async () => {
    renderPublication("SUCCEEDED");
    expect(
      await screen.findByRole("button", { name: "Create Draft PR" }),
    ).toBeInTheDocument();
    expect(screen.getByText(`devflow/run-${runId}`)).toBeInTheDocument();
    expect(screen.getByText("example/repo")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/token/i)).not.toBeInTheDocument();
  });

  it("leaves PUBLISHING claim expiry and takeover authority in the backend", async () => {
    vi.mocked(productApi.getGitHubPublication).mockResolvedValue(
      publication("PUBLISHING"),
    );
    renderPublication("SUCCEEDED");

    const button = await screen.findByRole("button", { name: "Retry publication" });
    expect(button).toBeEnabled();
    expect(
      screen.getByText(/backend rejects a still-live claim and takes over only after/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/attempt_token/i)).not.toBeInTheDocument();

    fireEvent.click(button);
    await waitFor(() =>
      expect(productApi.publishGitHubDraft).toHaveBeenCalledWith(runId),
    );
  });

  it("publishes with no browser selector form and leaves Run status outside the component", async () => {
    renderPublication("SUCCEEDED");
    fireEvent.click(await screen.findByRole("button", { name: "Create Draft PR" }));

    await waitFor(() =>
      expect(productApi.publishGitHubDraft).toHaveBeenCalledWith(runId),
    );
    const link = await screen.findByRole("link", { name: "Open Draft PR #42" });
    expect(link).toHaveAttribute("href", "https://github.com/example/repo/pull/42");
    expect(screen.queryByText(/Run SUCCEEDED by GitHub/i)).not.toBeInTheDocument();
  });
});