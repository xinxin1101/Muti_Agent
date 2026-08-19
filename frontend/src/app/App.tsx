import { Route, Routes } from "react-router";

import { AppShell } from "../components/AppShell";
import { FoundationPage } from "../pages/FoundationPage";
import { PlaceholderPage } from "../pages/PlaceholderPage";

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<FoundationPage />} />
        <Route
          path="projects"
          element={
            <PlaceholderPage
              eyebrow="Step 4.2"
              title="Projects"
              description="Repository and project management UI is intentionally deferred to the next product page step."
            />
          }
        />
        <Route
          path="runs"
          element={
            <PlaceholderPage
              eyebrow="Step 4.2"
              title="Runs"
              description="Run creation, dashboard, and task detail views will consume backend truth without becoming scheduler authority."
            />
          }
        />
        <Route
          path="*"
          element={
            <PlaceholderPage
              eyebrow="404"
              title="Page not found"
              description="This route is outside the bounded Step 4.1 frontend surface."
            />
          }
        />
      </Route>
    </Routes>
  );
}
