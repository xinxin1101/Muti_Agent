import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as productApi from "../api/product";
import { WorkspaceSidebar } from "./WorkspaceSidebar";

vi.mock("../api/product");

function renderSidebar() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><WorkspaceSidebar /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(productApi.listProjects).mockResolvedValue([
    { project_id: "11111111-1111-1111-1111-111111111111", repository_url: "https://github.com/example/visible", default_branch: "main", created_at: "2026-08-31T00:00:00Z", run_count: 0, workspace_ready: true, provision_status: "READY", provision_error_code: null, provision_error_message: null, lifecycle_state: "ACTIVE" },
    { project_id: "22222222-2222-2222-2222-222222222222", repository_url: "https://github.com/example/archived", default_branch: "main", created_at: "2026-08-31T00:00:00Z", run_count: 2, workspace_ready: true, provision_status: "ARCHIVED", provision_error_code: null, provision_error_message: null, lifecycle_state: "ARCHIVED" },
  ]);
  vi.mocked(productApi.listRuns).mockResolvedValue([]);
  vi.mocked(productApi.listDevelopmentSessions).mockResolvedValue([]);
});

describe("WorkspaceSidebar", () => {
  it("hides archived projects even when a cached API response contains one", async () => {
    renderSidebar();

    expect(await screen.findByText("example/visible")).toBeInTheDocument();
    expect(screen.queryByText("example/archived")).not.toBeInTheDocument();
    expect(productApi.listDevelopmentSessions).toHaveBeenCalledTimes(1);
    expect(productApi.listDevelopmentSessions).toHaveBeenCalledWith(
      "11111111-1111-1111-1111-111111111111",
    );
  });
});
