import { Route, Routes } from "react-router";

import { AppShell } from "../components/AppShell";
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
        <Route
          path="*"
          element={
            <PlaceholderPage
              eyebrow="404"
              title="Page not found"
              description="This route is outside the current DevFlow product surface."
            />
          }
        />
      </Route>
    </Routes>
  );
}
