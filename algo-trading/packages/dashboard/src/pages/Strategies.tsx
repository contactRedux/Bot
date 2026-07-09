/**
 * pages/Strategies.tsx — Live strategy management + trading engine control.
 *
 * Sections:
 *  - Trading Engine control (start / stop, status badge, loop count)
 *  - Strategy list with enable/disable toggles
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchTradingStatus,
  fetchStrategies,
  startTrading,
  stopTrading,
  toggleStrategy,
} from "@/lib/api";
import { useTradingStore } from "@/store";

// ---------------------------------------------------------------------------
// Engine control panel
// ---------------------------------------------------------------------------

function EngineControl() {
  const qc = useQueryClient();
  const running = useTradingStore((s) => s.running);
  const setRunning = useTradingStore((s) => s.setRunning);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const { data: status } = useQuery({
    queryKey: ["tradingStatus"],
    queryFn: fetchTradingStatus,
    refetchInterval: 5_000,
  });

  // Sync running state from the polled status into the Zustand store
  useEffect(() => {
    if (status !== undefined) setRunning(status.running);
  }, [status, setRunning]);

  const handleStart = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await startTrading();
      setMsg(r.message);
      setRunning(true);
      qc.invalidateQueries({ queryKey: ["tradingStatus"] });
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Start failed");
    } finally {
      setBusy(false);
    }
  };

  const handleStop = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await stopTrading();
      setMsg(r.message);
      setRunning(false);
      qc.invalidateQueries({ queryKey: ["tradingStatus"] });
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : "Stop failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Trading Engine
        </h2>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
            running
              ? "bg-emerald-500/20 text-emerald-400"
              : "bg-zinc-700 text-zinc-400"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${running ? "bg-emerald-400" : "bg-zinc-500"}`}
          />
          {running ? "RUNNING" : "STOPPED"}
        </span>
      </div>

      {/* Stats row */}
      {status && (
        <div className="mb-4 grid grid-cols-3 gap-3 text-center">
          <div className="rounded bg-zinc-700/50 px-2 py-1.5">
            <p className="text-xs text-zinc-500">Mode</p>
            <p className="font-mono text-sm font-semibold text-zinc-100 uppercase">
              {status.trading_mode}
            </p>
          </div>
          <div className="rounded bg-zinc-700/50 px-2 py-1.5">
            <p className="text-xs text-zinc-500">Interval</p>
            <p className="font-mono text-sm font-semibold text-zinc-100">
              {status.bar_interval}
            </p>
          </div>
          <div className="rounded bg-zinc-700/50 px-2 py-1.5">
            <p className="text-xs text-zinc-500">Loops</p>
            <p className="font-mono text-sm font-semibold text-zinc-100">
              {status.loop_count}
            </p>
          </div>
        </div>
      )}

      {/* Tickers */}
      {status?.tickers && status.tickers.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-1.5">
          {status.tickers.map((t) => (
            <span
              key={t}
              className="rounded border border-zinc-600 px-2 py-0.5 font-mono text-xs text-zinc-300"
            >
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Buttons */}
      <div className="flex gap-3">
        <button
          onClick={handleStart}
          disabled={busy || running}
          className="flex-1 rounded border border-emerald-500/40 px-3 py-1.5 text-xs font-medium text-emerald-400 transition hover:bg-emerald-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy && !running ? "Starting…" : "Start Engine"}
        </button>
        <button
          onClick={handleStop}
          disabled={busy || !running}
          className="flex-1 rounded border border-rose-500/40 px-3 py-1.5 text-xs font-medium text-rose-400 transition hover:bg-rose-500/20 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy && running ? "Stopping…" : "Stop Engine"}
        </button>
      </div>

      {msg && (
        <p className="mt-2 text-xs text-zinc-400">{msg}</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Strategy list with toggle
// ---------------------------------------------------------------------------

function StrategyList() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["strategies"],
    queryFn: fetchStrategies,
    refetchInterval: 10_000,
  });

  const toggleMut = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleStrategy(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }),
  });

  if (isLoading) {
    return (
      <p className="py-8 text-center text-sm text-zinc-500">Loading strategies…</p>
    );
  }

  const strategies = data?.strategies ?? [];

  if (strategies.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-zinc-500">
        No strategies registered. Start the API with strategies enabled.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-zinc-700">
      <div className="border-b border-zinc-700 px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Registered Strategies ({strategies.length})
        </span>
      </div>
      <ul className="divide-y divide-zinc-700/50">
        {strategies.map((s) => (
          <li key={s.strategy_id} className="flex items-center gap-4 px-4 py-3">
            {/* Toggle */}
            <button
              onClick={() =>
                toggleMut.mutate({ id: s.strategy_id, enabled: !s.enabled })
              }
              disabled={toggleMut.isPending}
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                s.enabled ? "bg-emerald-500" : "bg-zinc-600"
              }`}
              aria-label={`Toggle ${s.display_name}`}
            >
              <span
                className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform duration-200 ${
                  s.enabled ? "translate-x-4" : "translate-x-0"
                }`}
              />
            </button>

            {/* Info */}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-zinc-100">
                  {s.display_name}
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    s.enabled
                      ? "bg-emerald-500/20 text-emerald-400"
                      : "bg-zinc-700 text-zinc-500"
                  }`}
                >
                  {s.enabled ? "enabled" : "disabled"}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-zinc-500">{s.description}</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {s.tickers.slice(0, 6).map((t) => (
                  <span
                    key={t}
                    className="font-mono text-xs text-sky-400"
                  >
                    {t}
                  </span>
                ))}
                {s.tickers.length > 6 && (
                  <span className="text-xs text-zinc-600">
                    +{s.tickers.length - 6} more
                  </span>
                )}
              </div>
            </div>

            {/* Weight */}
            <div className="text-right">
              <p className="text-xs text-zinc-500">Weight</p>
              <p className="font-mono text-sm font-semibold text-zinc-100">
                {(s.allocation_weight * 100).toFixed(0)}%
              </p>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Strategies() {
  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold text-zinc-100">Strategy Manager</h1>
      <EngineControl />
      <StrategyList />
    </div>
  );
}
