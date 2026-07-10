/**
 * pages/AiAnalyst.tsx — LLM-powered trading analyst.
 *
 * Sends live system data (portfolio, trades, technicals, news, risk) to an
 * LLM (OpenAI / Anthropic) and displays a structured analyst report:
 *   - Executive summary
 *   - Market commentary
 *   - Trade rationale (why the bot made its recent trades)
 *   - Risk assessment
 *   - Outlook + key bullet points
 *
 * Works in offline mode (no LLM key) — the backend generates a rule-based
 * report from the same data at no cost.
 */
import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchAiAnalysis, fetchAiHistory } from "@/lib/api";
import type { AiAnalystReport, AiAnalyseRequest } from "@/lib/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ProviderBadge({ provider, model }: { provider: string; model: string }) {
  const colour =
    provider === "openai"
      ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10"
      : provider === "anthropic"
      ? "text-violet-400 border-violet-500/40 bg-violet-500/10"
      : "text-zinc-400 border-zinc-600 bg-zinc-800";

  const label =
    provider === "openai"    ? "OpenAI"
    : provider === "anthropic" ? "Anthropic"
    : "Offline";

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${colour}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label} · {model}
    </span>
  );
}

function Section({
  title,
  children,
  accent = false,
}: {
  title: string;
  children: React.ReactNode;
  accent?: boolean;
}) {
  return (
    <div className={`rounded-lg border p-4 ${accent ? "border-sky-500/30 bg-sky-500/5" : "border-zinc-700 bg-zinc-800"}`}>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">{title}</div>
      {children}
    </div>
  );
}

function Prose({ text }: { text: string }) {
  return <p className="text-sm leading-relaxed text-zinc-300">{text || "—"}</p>;
}

// ---------------------------------------------------------------------------
// Report display
// ---------------------------------------------------------------------------
function ReportView({ report }: { report: AiAnalystReport }) {
  const [showCtx, setShowCtx] = useState(false);

  return (
    <div className="space-y-4">
      {/* Header meta */}
      <div className="flex flex-wrap items-center gap-3">
        <ProviderBadge provider={report.provider} model={report.model} />
        <span className="text-xs text-zinc-500">
          Generated {new Date(report.generated_at).toLocaleString()}
        </span>
        <span className="text-xs text-zinc-600">
          Tickers: {report.tickers.join(", ")}
        </span>
        <span className="ml-auto rounded border border-zinc-700 px-2 py-0.5 text-xs text-zinc-500 capitalize">
          Focus: {report.focus}
        </span>
      </div>

      {/* Summary — full-width highlight */}
      <Section title="Executive Summary" accent>
        <Prose text={report.summary} />
      </Section>

      {/* 2-column grid for main sections */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Market Commentary">
          <Prose text={report.market_commentary} />
        </Section>
        <Section title="Trade Rationale">
          <Prose text={report.trade_rationale} />
        </Section>
        <Section title="Risk Assessment">
          <Prose text={report.risk_assessment} />
        </Section>
        <Section title="Outlook">
          <Prose text={report.outlook} />
        </Section>
      </div>

      {/* Key points */}
      {report.key_points.length > 0 && (
        <Section title="Key Takeaways">
          <ul className="space-y-1.5">
            {report.key_points.map((pt, i) => (
              <li key={i} className="flex gap-2 text-sm text-zinc-300">
                <span className="mt-0.5 shrink-0 font-bold text-sky-500">›</span>
                {pt}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {/* Debug: raw context */}
      <div>
        <button
          onClick={() => setShowCtx(!showCtx)}
          className="text-xs text-zinc-600 hover:text-zinc-400"
        >
          {showCtx ? "▲ Hide" : "▼ Show"} data fed to analyst
        </button>
        {showCtx && (
          <pre className="mt-2 max-h-96 overflow-y-auto rounded bg-zinc-900 p-3 text-xs text-zinc-400">
            {JSON.stringify(report.context_snapshot, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// History panel
// ---------------------------------------------------------------------------
function HistoryPanel({ onSelect }: { onSelect: (r: AiAnalystReport) => void }) {
  const { data } = useQuery({
    queryKey: ["ai-history"],
    queryFn: () => fetchAiHistory(20),
    staleTime: 10_000,
  });

  const reports = data?.reports ?? [];
  if (reports.length === 0) return null;

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
      <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        Past Reports ({reports.length})
      </div>
      <ul className="space-y-1.5 max-h-64 overflow-y-auto">
        {reports.map((r, i) => (
          <li key={i}>
            <button
              onClick={() => onSelect(r)}
              className="w-full rounded px-3 py-2 text-left text-xs hover:bg-zinc-700/60"
            >
              <span className="text-zinc-400">{new Date(r.generated_at).toLocaleString()}</span>
              <span className="ml-2 capitalize text-zinc-500">{r.focus}</span>
              <span className="ml-2 text-zinc-600">{r.tickers.join(", ")}</span>
              <span className={`ml-2 capitalize ${r.provider === "offline" ? "text-zinc-600" : "text-sky-500"}`}>
                [{r.provider}]
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function AiAnalyst() {
  const [tickerInput, setTickerInput] = useState("");
  const [focus, setFocus] = useState<AiAnalyseRequest["focus"]>("full");
  const [includeTrades, setIncludeTrades] = useState(true);
  const [includeNews, setIncludeNews] = useState(true);
  const [activeReport, setActiveReport] = useState<AiAnalystReport | null>(null);

  const mutation = useMutation({
    mutationFn: (req: AiAnalyseRequest) => fetchAiAnalysis(req),
    onSuccess: (data) => setActiveReport(data),
  });

  function handleRun(e: React.FormEvent) {
    e.preventDefault();
    const tickers = tickerInput
      .split(/[\s,]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    mutation.mutate({ tickers, focus, include_trades: includeTrades, include_news: includeNews });
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-zinc-100">AI Analyst</h1>
        <p className="text-sm text-zinc-500">
          Sends live portfolio data, trades, technicals, and news to an LLM for a
          plain-English analyst briefing. Works offline (no API key) using rule-based commentary.
        </p>
      </div>

      {/* Config form */}
      <form
        onSubmit={handleRun}
        className="rounded-lg border border-zinc-700 bg-zinc-800 p-4 space-y-4"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {/* Tickers */}
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">
              Tickers <span className="text-zinc-600">(leave blank = use active positions)</span>
            </label>
            <input
              type="text"
              value={tickerInput}
              onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
              placeholder="AAPL MSFT NVDA   or leave blank"
              className="w-full rounded border border-zinc-600 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 placeholder-zinc-600 focus:border-sky-500 focus:outline-none"
            />
          </div>

          {/* Focus */}
          <div>
            <label className="mb-1 block text-xs font-medium text-zinc-400">Focus</label>
            <select
              value={focus}
              onChange={(e) => setFocus(e.target.value as AiAnalyseRequest["focus"])}
              className="w-full rounded border border-zinc-600 bg-zinc-900 px-3 py-2 text-sm text-zinc-100 focus:border-sky-500 focus:outline-none"
            >
              <option value="full">Full analysis</option>
              <option value="trades">Trade rationale</option>
              <option value="risk">Risk deep-dive</option>
              <option value="market">Market commentary</option>
              <option value="outlook">Forward outlook</option>
            </select>
          </div>
        </div>

        {/* Toggles */}
        <div className="flex flex-wrap gap-5">
          <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={includeTrades}
              onChange={(e) => setIncludeTrades(e.target.checked)}
              className="h-4 w-4 accent-sky-500"
            />
            Include recent trades
          </label>
          <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-zinc-400">
            <input
              type="checkbox"
              checked={includeNews}
              onChange={(e) => setIncludeNews(e.target.checked)}
              className="h-4 w-4 accent-sky-500"
            />
            Include news headlines
          </label>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="rounded bg-sky-600 px-5 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
          >
            {mutation.isPending ? "Analysing…" : "Run Analysis"}
          </button>
          {mutation.isPending && (
            <span className="text-xs text-zinc-500 animate-pulse">
              Gathering data and calling LLM…
            </span>
          )}
        </div>
      </form>

      {/* Error */}
      {mutation.isError && (
        <div className="rounded border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
          {(mutation.error as Error).message}
        </div>
      )}

      {/* Active report */}
      {activeReport && <ReportView report={activeReport} />}

      {/* Empty state */}
      {!activeReport && !mutation.isPending && !mutation.isError && (
        <div className="rounded-lg border border-zinc-700 bg-zinc-800/50 py-16 text-center">
          <p className="text-sm text-zinc-500">
            Click <strong className="text-zinc-300">Run Analysis</strong> to generate a briefing.
          </p>
          <p className="mt-1 text-xs text-zinc-600">
            Works without an API key — offline mode uses rule-based commentary from live indicators.
          </p>
          <p className="mt-1 text-xs text-zinc-600">
            Add <code className="text-zinc-500">LLM_API_KEY</code> to <code className="text-zinc-500">.env</code> to use GPT-4o or Claude.
          </p>
        </div>
      )}

      {/* History */}
      <HistoryPanel onSelect={setActiveReport} />
    </div>
  );
}
