import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as productApi from "../api/product";
import { DevelopmentSessionPage } from "./DevelopmentSessionPage";

vi.mock("../api/product");

const sessionId = "33333333-3333-3333-3333-333333333333";
const projectId = "11111111-1111-1111-1111-111111111111";

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[`/development-sessions/${sessionId}`]}><Routes><Route path="/development-sessions/:sessionId" element={<DevelopmentSessionPage />} /></Routes></MemoryRouter></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(productApi.getDevelopmentSession).mockResolvedValue({
    session_id: sessionId, project_id: projectId, requirement: "实现五子棋网页游戏", base_commit: "a".repeat(40), state: "RUNNING", planning_diagnostic: "", latest_run_id: "22222222-2222-2222-2222-222222222222", resumed_from_run_id: null, work_packages: [{ task_id: "core", state: "SUCCEEDED", source_run_id: null, commit_sha: null, completed_interfaces: [], verification_summary: "", failure_summary: "", remaining_budget_tokens: null }], created_at: "2026-08-31T00:00:00Z", updated_at: "2026-08-31T00:01:00Z",
  });
  vi.mocked(productApi.getProject).mockResolvedValue({ project_id: projectId, repository_url: "https://github.com/example/test", default_branch: "main", created_at: "2026-08-31T00:00:00Z", run_count: 1, workspace_ready: true, provision_status: "READY", provision_error_code: null, provision_error_message: null });
  vi.mocked(productApi.getDevelopmentSessionTimeline).mockResolvedValue([{ entry_id: 1, session_id: sessionId, kind: "USER_REQUIREMENT", title: "用户提出开发需求", detail: "需求已保存。", run_id: null, task_id: null, metadata: {}, created_at: "2026-08-31T00:00:00Z" }]);
  vi.mocked(productApi.getDevelopmentSessionRecovery).mockResolvedValue({ session_id: sessionId, source_run_id: null, baseline_commit: "a".repeat(40), current_commit: "a".repeat(40), baseline_state: "UNCHANGED", reusable_work_package_ids: ["core"], checkpointed_work_package_ids: [], remaining_work_package_ids: ["web"], next_action: "继续未完成工作包", budget: { planning_remaining_tokens: 4000, development_remaining_tokens: 6000, repair_remaining_tokens: 1000, estimated_new_development_tokens: 5000, estimated_tokens_saved: 6000 } });
  vi.mocked(productApi.getDependencyEnvironment).mockResolvedValue({ dependency_fingerprint: "a".repeat(64), profile_kind: "PYTHON_BASE", package_manager: "PYTHON", cache_state: "HIT", artifact_bytes: 1, build_duration_ms: 1, log_tail: "" });
  vi.mocked(productApi.previewDevelopmentSessionCommand).mockResolvedValue({ session_id: sessionId, intent: "DELETE_PROJECT", action_name: "永久删除项目本地数据", target_label: "test", impact: ["GitHub 仓库不会被删除。"], token_cost: "不消耗模型 Token", affects_local_data: true, confirmation_required: true, executable_after_confirmation: true, confirmation_hint: "下一步仍需二次确认。" });
});

describe("DevelopmentSessionPage", () => {
  it("shows durable session facts and previews a destructive command without deleting", async () => {
    renderPage();
    expect(await screen.findByText("实现五子棋网页游戏")).toBeInTheDocument();
    expect(screen.getByText("用户提出开发需求")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("告诉 DevFlow 下一步要做什么"), { target: { value: "删除 test 项目" } });
    fireEvent.click(screen.getByRole("button", { name: "生成确认卡片" }));

    await waitFor(() => expect(productApi.previewDevelopmentSessionCommand).toHaveBeenCalledWith(sessionId, "删除 test 项目"));
    expect(await screen.findByText("确认：永久删除项目本地数据")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成确认卡片" })).toHaveClass("df-button-primary");
    expect(productApi.deleteProject).not.toHaveBeenCalled();
  });
});
