import { NavLink, Outlet } from "react-router";

const navigation = [
  { to: "/", label: "Foundation", end: true },
  { to: "/projects", label: "Projects", end: false },
  { to: "/runs/new", label: "New Run", end: false },
  { to: "/runs", label: "Runs", end: true },
] as const;

export function AppShell() {
  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-950/95">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-6 py-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.25em] text-cyan-300">
              DevFlow
            </p>
            <p className="text-sm text-slate-400">
              Evidence-driven multi-agent runtime
            </p>
          </div>

          <nav aria-label="Primary" className="flex flex-wrap justify-end gap-2">
            {navigation.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  [
                    "rounded-md px-3 py-2 text-sm font-medium transition",
                    isActive
                      ? "bg-cyan-400/15 text-cyan-200"
                      : "text-slate-400 hover:bg-slate-900 hover:text-slate-100",
                  ].join(" ")
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-6 py-10">
        <Outlet />
      </main>
    </div>
  );
}
