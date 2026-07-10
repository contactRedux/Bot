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
    <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
          Trading Engine
        </h2>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
            running
              ? "bg-[#30d158]/10 text-[#30d158]"
              : "bg-[#f5f5f7] text-[#6e6e73]"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${running ? "bg-[#30d158]" : "bg-[#c7c7cc]"}`}
          />
          {running ? "RUNNING" : "STOPPED"}
        </span>
      </div>

      {/* Stats row */}
      {status && (
        <div className="mb-4 grid grid-cols-3 gap-3 text-center">
          <div className="rounded-lg bg-[#f5f5f7] px-2 py-1.5">
            <p className="text-xs text-[#6e6e73]">Mode</p>
            <p className="font-mono text-sm font-semibold text-[#1d1d1f] uppercase">
              {status.trading_mode}
            </p>
          </div>
          <div className="rounded-lg bg-[#f5f5f7] px-2 py-1.5">
            <p className="text-xs text-[#6e6e73]">Interval</p>
            <p className="font-mono text-sm font-semibold text-[#1d1d1f]">
              {status.bar_interval}
            </p>
          </div>
          <div className="rounded-lg bg-[#f5f5f7] px-2 py-1.5">
            <p className="text-xs text-[#6e6e73]">Loops</p>
            <p className="font-mono text-sm font-semibold text-[#1d1d1f]">
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
              className="rounded-full border border-[#e5e5ea] bg-[#f5f5f7] px-2 py-0.5 font-mono text-xs text-[#3a3a3c]"
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
          className="flex-1 rounded-lg border border-[#30d158]/30 px-3 py-1.5 text-xs font-medium text-[#30d158] transition hover:bg-[#30d158]/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy && !running ? "Starting…" : "Start Engine"}
        </button>
        <button
          onClick={handleStop}
          disabled={busy || !running}
          className="flex-1 rounded-lg border border-[#ff3b30]/30 px-3 py-1.5 text-xs font-medium text-[#ff3b30] transition hover:bg-[#ff3b30]/10 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy && running ? "Stopping…" : "Stop Engine"}
        </button>
      </div>

      {msg && (
        <p className="mt-2 text-xs text-[#6e6e73]">{msg}</p>
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
      <p className="py-8 text-center text-sm text-[#6e6e73]">Loading strategies…</p>
    );
  }

  const strategies = data?.strategies ?? [];

  if (strategies.length === 0) {
    return (
      <p className="py-8 text-center text-sm text-[#6e6e73]">
        No strategies registered. Start the API with strategies enabled.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-xl border border-[#e5e5ea] bg-white">
      <div className="border-b border-[#e5e5ea] px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
          Registered Strategies ({strategies.length})
        </span>
      </div>
      <ul className="divide-y divide-[#e5e5ea]">
        {strategies.map((s) => (
          <li key={s.strategy_id} className="flex items-center gap-4 px-4 py-3">
            {/* Toggle */}
            <button
              onClick={() =>
                toggleMut.mutate({ id: s.strategy_id, enabled: !s.enabled })
              }
              disabled={toggleMut.isPending}
              className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                s.enabled ? "bg-[#30d158]" : "bg-[#c7c7cc]"
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
                <span className="text-sm font-medium text-[#1d1d1f]">
                  {s.display_name}
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    s.enabled
                      ? "bg-[#30d158]/10 text-[#30d158]"
                      : "bg-[#f5f5f7] text-[#6e6e73]"
                  }`}
                >
                  {s.enabled ? "enabled" : "disabled"}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-[#6e6e73]">{s.description}</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {s.tickers.slice(0, 6).map((t) => (
                  <span key={t} className="font-mono text-xs text-[#007aff]">
                    {t}
                  </span>
                ))}
                {s.tickers.length > 6 && (
                  <span className="text-xs text-[#8e8e93]">
                    +{s.tickers.length - 6} more
                  </span>
                )}
              </div>
            </div>

            {/* Weight */}
            <div className="text-right">
              <p className="text-xs text-[#6e6e73]">Weight</p>
              <p className="font-mono text-sm font-semibold text-[#1d1d1f]">
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
      <h1 className="text-xl font-semibold text-[#1d1d1f]">Strategy Manager</h1>
      <EngineControl />
      <StrategyList />
    </div>
  );
}
