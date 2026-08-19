import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import { App } from "./App";

describe("App", () => {
  it("renders the bounded Step 4.1 foundation", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("heading", {
        name: "React / TypeScript UI foundation",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Foundation scope")).toBeInTheDocument();
  });

  it("keeps product pages as explicit Step 4.2 placeholders", () => {
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getByText("Step 4.2")).toBeInTheDocument();
  });

  it("renders a bounded not-found state", () => {
    render(
      <MemoryRouter initialEntries={["/outside-step-4-1"]}>
        <App />
      </MemoryRouter>,
    );

    expect(
      screen.getByRole("heading", { name: "Page not found" }),
    ).toBeInTheDocument();
  });
});
