import { useEffect, useRef } from "react";
import { useSignalStore, useNewsStore, usePortfolioStore, useRiskStore } from "@/store";

/**
 * useWebSocketFeed
 *
 * Connects to the FastAPI WebSocket endpoint at /ws/feed and routes
 * incoming JSON messages to the appropriate Zustand store slice.
 *
 * Message envelope format (from api/ws/feed.py):
 *   { "type": "signal" | "news" | "portfolio" | "risk" | "fill", "data": {...} }
 *
 * Full implementation (auto-reconnect, filter subscriptions) in Sub-Task 10.
 * This stub establishes the connection and basic routing.
 */
export function useWebSocketFeed() {
  const wsRef = useRef<WebSocket | null>(null);
  const addSignal = useSignalStore((s) => s.addSignal);
  const addArticle = useNewsStore((s) => s.addArticle);
  const setSnapshot = usePortfolioStore((s) => s.setSnapshot);
  const setRiskStatus = useRiskStore((s) => s.setStatus);

  useEffect(() => {
    const wsUrl = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000/ws/feed";
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.info("[WS] Connected to feed:", wsUrl);
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as { type: string; data: unknown };
        switch (msg.type) {
          case "signal":
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            addSignal(msg.data as any);
            break;
          case "news":
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            addArticle(msg.data as any);
            break;
          case "portfolio":
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            setSnapshot(msg.data as any);
            break;
          case "risk":
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            setRiskStatus(msg.data as any);
            break;
          default:
            break;
        }
      } catch {
        // Malformed message — ignore
      }
    };

    ws.onerror = (e) => console.warn("[WS] Error:", e);
    ws.onclose = () => console.info("[WS] Disconnected");

    return () => {
      ws.close();
    };
  }, [addSignal, addArticle, setSnapshot, setRiskStatus]);

  return wsRef;
}
