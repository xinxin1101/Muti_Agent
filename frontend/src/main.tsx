import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./app/App";
import { createAppQueryClient } from "./app/queryClient";
import "./styles.css";

const root = document.getElementById("root");

if (root === null) {
  throw new Error("DevFlow frontend root element was not found.");
}

const queryClient = createAppQueryClient();

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
