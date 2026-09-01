import { Route, Routes } from "react-router";

import { AppShell } from "../components/AppShell";
import { DevelopmentSessionPage } from "../pages/DevelopmentSessionPage";
import { FoundationPage } from "../pages/FoundationPage";
import { NewRunPage } from "../pages/NewRunPage";
import { PlaceholderPage } from "../pages/PlaceholderPage";
import { ProjectsPage } from "../pages/ProjectsPage";
import { RunDashboardPage } from "../pages/RunDashboardPage";
import { RunsPage } from "../pages/RunsPage";
import { TaskDetailPage } from "../pages/TaskDetailPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<FoundationPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="runs" element={<RunsPage />} />
        <Route path="runs/new" element={<NewRunPage />} />
        <Route path="runs/:runId" element={<RunDashboardPage />} />
        <Route path="runs/:runId/tasks/:taskId" element={<TaskDetailPage />} />
        <Route path="development-sessions/:sessionId" element={<DevelopmentSessionPage />} />
        <Route
          path="*"
          element={
            <PlaceholderPage
              eyebrow="404"
              title="未找到页面"
              description="此路由不在当前 DevFlow 产品功能范围内。"
            />
          }
        />
      </Route>
    </Routes>
  );
}
