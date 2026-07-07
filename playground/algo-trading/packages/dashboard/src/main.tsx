import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import App from "./App.tsx";

/**
 * Application entry point.
 *
 * Providers mounted here:
 *   - BrowserRouter    — client-side routing
 *   - QueryClientProvider — react-query for REST API data fetching + caching
 *
 * Global Zustand stores are initialised on demand (no provider needed).
 * The WebSocket feed is initialised inside <App> via the useWebSocketFeed hook.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Stale time: 30 seconds — most trading data is time-sensitive
      staleTime: 30_000,
      retry: 2,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
