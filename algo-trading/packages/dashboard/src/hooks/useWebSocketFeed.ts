import { useEffect, useRef, useCallback } from "react";
import {
  useSignalStore,
  useNewsStore,
  usePortfolioStore,
  useRiskStore,
  useFillStore,
  useWsStore,
} from "@/store";
import type { WSEvent } from "@/lib/types";

/**
 * useWebSocketFeed
 *
 * Connects to the FastAPI WebSocket at /ws/feed and routes incoming
 * WSEvent envelopes to the correct Zustand store slice.
 *
 * Features
 * --------
 * - Auto-reconnect with exponential back-off (1 s → 2 s → 4 s … max 30 s)
 * - Unmount cleanup (closes socket, cancels reconnect timer)
 * - Routes all event_type values defined in WSEvent:
 *     bar               → (logged, available for future price chart)
 *     signal            → signalStore
 *     fill              → fillStore
 *     risk_alert        → riskStore (sets status.halted fields)
 *     portfolio_update  → portfolioStore (snapshot + equity curve point)
 *     heartbeat         → wsStore.lastHeartbeat
 *     backtest_progress → (emitted; BacktestExplorer polls via REST)
 */
export function useWebSocketFeed() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const reconnectDelayRef = useRef<number>(1_000);
  const unmountedRef = useRef(false);

  // Store actions
  const addSignal      = useSignalStore((s) => s.addSignal);
  const addArticle     = useNewsStore((s) => s.addArticle);
  const setSnapshot    = usePortfolioStore((s) => s.setSnapshot);
  const appendEquity   = usePortfolioStore((s) => s.appendEquityPoint);
  const setRiskStatus  = useRiskStore((s) => s.setStatus);
  const addFill        = useFillStore((s) => s.addFill);
  const setConnected   = useWsStore((s) => s.setConnected);
  const setHeartbeat   = useWsStore((s) => s.setLastHeartbeat);
  const setLastEvent   = useWsStore((s) => s.setLastEventType);

  const connect = useCallback(() => {
    if (unmountedRef.current) return;

    const wsUrl =
      (import.meta.env.VITE_WS_URL as string | undefined) ??
      "ws://localhost:8000/ws/feed";

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectDelayRef.current = 1_000; // reset back-off
      setConnected(true);
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg = JSON.parse(event.data as string) as WSEvent;
        setLastEvent(msg.event_type);

        switch (msg.event_type) {
          case "signal":
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            addSignal(msg.payload as any);
            break;

          case "fill":
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            addFill(msg.payload as any);
            break;

          case "portfolio_update": {
            const p = msg.payload;
            // Full snapshot shape from REST response — also accepted here
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            setSnapshot(p as any);
            // Append point to equity curve
            if (typeof p.equity === "number") {
              appendEquity({ timestamp: msg.timestamp, equity: p.equity as number });
            } else if (typeof p.total_equity === "number") {
              appendEquity({ timestamp: msg.timestamp, equity: p.total_equity as number });
            }
            break;
          }

          case "risk_alert":
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            setRiskStatus(msg.payload as any);
            break;

          case "heartbeat":
            setHeartbeat(msg.timestamp);
            break;

          case "bar":
            // Bars are consumed by the price chart via React Query (REST),
            // but we track that bars are flowing.
            break;

          case "backtest_progress":
            // Handled by polling in BacktestExplorer — no store update needed.
            break;

          default:
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            addArticle(msg.payload as any); // unexpected type — treat as news
            break;
        }
      } catch {
        // Malformed JSON — ignore
      }
    };

    ws.onerror = () => {
      // Let onclose handle reconnect
    };

    ws.onclose = () => {
      setConnected(false);
      if (unmountedRef.current) return;

      const delay = reconnectDelayRef.current;
      reconnectDelayRef.current = Math.min(delay * 2, 30_000);

      reconnectTimerRef.current = window.setTimeout(() => {
        if (!unmountedRef.current) connect();
      }, delay);
    };
  }, [
    addSignal,
    addFill,
    addArticle,
    setSnapshot,
    appendEquity,
    setRiskStatus,
    setConnected,
    setHeartbeat,
    setLastEvent,
  ]);

  useEffect(() => {
    unmountedRef.current = false;
    connect();

    return () => {
      unmountedRef.current = true;
      if (reconnectTimerRef.current !== null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return wsRef;
}
