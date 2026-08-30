import { Outlet } from "react-router";

import { WorkspaceSidebar } from "./WorkspaceSidebar";

export function AppShell() {
  return (
    <div className="workspace-shell flex min-h-screen bg-stone-100 text-stone-800">
      <WorkspaceSidebar />
      <main className="min-w-0 flex-1 px-5 py-8 md:px-10 lg:px-14">
        <Outlet />
      </main>
    </div>
  );
}
