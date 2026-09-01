import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as productApi from "../api/product";
import { ProjectsPage } from "./ProjectsPage";

vi.mock("../api/product");

const projectId = "11111111-1111-1111-1111-111111111111";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><ProjectsPage /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(productApi.listProjects).mockResolvedValue([
    { project_id: projectId, repository_url: "https://github.com/example/test", default_branch: "main", created_at: "2026-08-31T00:00:00Z", run_count: 2, workspace_ready: true, provision_status: "READY", provision_error_code: null, provision_error_message: null, lifecycle_state: "ACTIVE" },
  ]);
  vi.mocked(productApi.getProjectDeletionPreview).mockResolvedValue({
    project_id: projectId,
    required_confirmation_name: "example/test",
    confirmation_token: "server-issued-token",
    confirmation_expires_at: "2026-08-31T01:00:00Z",
    run_count: 2,
    development_session_count: 1,
    local_workspace_bytes: 1024,
    project_cache_bytes: 2048,
    local_credential_count: 1,
    github_repository_will_be_preserved: true,
  });
});

describe("ProjectsPage", () => {
  it("requires server preview and typed project confirmation before permanent deletion", async () => {
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "永久删除" }));
    await waitFor(() => expect(productApi.getProjectDeletionPreview).toHaveBeenCalledWith(
      projectId,
      expect.anything(),
    ));
    expect(await screen.findByRole("dialog", { name: "永久删除项目确认" })).toHaveTextContent("不会删除 GitHub 仓库");
    expect(screen.getByRole("dialog", { name: "永久删除项目确认" }).querySelector(".df-dialog")).not.toBeNull();
    expect(screen.getByRole("button", { name: "确认永久删除" })).toHaveClass("df-button-danger");
    expect(productApi.deleteProject).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText(/请输入 example\/test/), { target: { value: "example/test" } });
    fireEvent.click(screen.getByRole("button", { name: "确认永久删除" }));
    await waitFor(() => expect(productApi.deleteProject).toHaveBeenCalledWith(projectId, {
      confirmation_token: "server-issued-token",
      confirmation_name: "example/test",
    }));
  });
});
