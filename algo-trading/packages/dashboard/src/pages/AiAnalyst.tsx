/**
 * pages/AiAnalyst.tsx — LLM-powered trading analyst — Apple light aesthetic.
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
      ? "text-[#30d158] border-[#30d158]/30 bg-[#30d158]/10"
      : provider === "anthropic"
      ? "text-purple-600 border-purple-300/40 bg-purple-50"
      : "text-[#6e6e73] border-[#e5e5ea] bg-[#f5f5f7]";

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
    <div className={`rounded-xl border p-4 ${accent ? "border-[#007aff]/20 bg-[#007aff]/5" : "border-[#e5e5ea] bg-white"}`}>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">{title}</div>
      {children}
    </div>
  );
}

function Prose({ text }: { text: string }) {
  return <p className="text-sm leading-relaxed text-[#3a3a3c]">{text || "—"}</p>;
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
        <span className="text-xs text-[#6e6e73]">
          Generated {new Date(report.generated_at).toLocaleString()}
        </span>
        <span className="text-xs text-[#8e8e93]">
          Tickers: {report.tickers.join(", ")}
        </span>
        <span className="ml-auto rounded-lg border border-[#e5e5ea] px-2 py-0.5 text-xs text-[#6e6e73] capitalize">
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
              <li key={i} className="flex gap-2 text-sm text-[#3a3a3c]">
                <span className="mt-0.5 shrink-0 font-bold text-[#007aff]">›</span>
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
          className="text-xs text-[#8e8e93] hover:text-[#6e6e73]"
        >
          {showCtx ? "▲ Hide" : "▼ Show"} data fed to analyst
        </button>
        {showCtx && (
          <pre className="mt-2 max-h-96 overflow-y-auto rounded-xl bg-[#f5f5f7] p-3 text-xs text-[#3a3a3c]">
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
    <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
      <div className="mb-3 text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
        Past Reports ({reports.length})
      </div>
      <ul className="space-y-1.5 max-h-64 overflow-y-auto">
        {reports.map((r, i) => (
          <li key={i}>
            <button
              onClick={() => onSelect(r)}
              className="w-full rounded-lg px-3 py-2 text-left text-xs hover:bg-[#f5f5f7]"
            >
              <span className="text-[#6e6e73]">{new Date(r.generated_at).toLocaleString()}</span>
              <span className="ml-2 capitalize text-[#6e6e73]">{r.focus}</span>
              <span className="ml-2 text-[#8e8e93]">{r.tickers.join(", ")}</span>
              <span className={`ml-2 capitalize ${r.provider === "offline" ? "text-[#8e8e93]" : "text-[#007aff]"}`}>
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
        <h1 className="text-xl font-semibold text-[#1d1d1f]">AI Analyst</h1>
        <p className="text-sm text-[#6e6e73]">
          Sends live portfolio data, trades, technicals, and news to an LLM for a
          plain-English analyst briefing. Works offline (no API key) using rule-based commentary.
        </p>
      </div>

      {/* Config form */}
      <form
        onSubmit={handleRun}
        className="rounded-xl border border-[#e5e5ea] bg-white p-4 space-y-4"
      >
        <div className="grid gap-4 sm:grid-cols-2">
          {/* Tickers */}
          <div>
            <label className="mb-1 block text-xs font-medium text-[#6e6e73]">
              Tickers <span className="text-[#8e8e93]">(leave blank = use active positions)</span>
            </label>
            <input
              type="text"
              value={tickerInput}
              onChange={(e) => setTickerInput(e.target.value.toUpperCase())}
              placeholder="AAPL MSFT NVDA   or leave blank"
              className="w-full rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] px-3 py-2 text-sm text-[#1d1d1f] placeholder-[#8e8e93] focus:border-[#007aff] focus:outline-none"
            />
          </div>

          {/* Focus */}
          <div>
            <label className="mb-1 block text-xs font-medium text-[#6e6e73]">Focus</label>
            <select
              value={focus}
              onChange={(e) => setFocus(e.target.value as AiAnalyseRequest["focus"])}
              className="w-full rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] px-3 py-2 text-sm text-[#1d1d1f] focus:border-[#007aff] focus:outline-none"
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
          <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-[#6e6e73]">
            <input
              type="checkbox"
              checked={includeTrades}
              onChange={(e) => setIncludeTrades(e.target.checked)}
              className="h-4 w-4 accent-[#007aff]"
            />
            Include recent trades
          </label>
          <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-[#6e6e73]">
            <input
              type="checkbox"
              checked={includeNews}
              onChange={(e) => setIncludeNews(e.target.checked)}
              className="h-4 w-4 accent-[#007aff]"
            />
            Include news headlines
          </label>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={mutation.isPending}
            className="rounded-xl bg-[#007aff] px-5 py-2 text-sm font-medium text-white hover:bg-[#007aff]/90 disabled:opacity-50"
          >
            {mutation.isPending ? "Analysing…" : "Run Analysis"}
          </button>
          {mutation.isPending && (
            <span className="text-xs text-[#6e6e73] animate-pulse">
              Gathering data and calling LLM…
            </span>
          )}
        </div>
      </form>

      {/* Error */}
      {mutation.isError && (
        <div className="rounded-xl border border-[#ff3b30]/20 bg-[#ff3b30]/5 px-4 py-3 text-sm text-[#ff3b30]">
          {(() => {
            const msg = (mutation.error as Error).message ?? "";
            if (msg.includes("404"))
              return "AI Analyst endpoint not found. Make sure you are running the latest API server version.";
            if (msg.includes("LLM_API_KEY") || msg.includes("api_key"))
              return "LLM API key not configured. Add LLM_API_KEY to your .env file, or run without a key for offline mode.";
            return `Analysis failed: ${msg}`;
          })()}
        </div>
      )}

      {/* Active report */}
      {activeReport && <ReportView report={activeReport} />}

      {/* Empty state */}
      {!activeReport && !mutation.isPending && !mutation.isError && (
        <div className="rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] py-16 text-center">
          <p className="text-sm text-[#6e6e73]">
            Click <strong className="text-[#1d1d1f]">Run Analysis</strong> to generate a briefing.
          </p>
          <p className="mt-2 text-xs text-[#8e8e93]">
            <strong className="text-[#6e6e73]">Offline mode</strong> — no API key needed. Generates rule-based commentary
            from live technical indicators, portfolio state, and news sentiment.
          </p>
          <p className="mt-1 text-xs text-[#8e8e93]">
            Add <code className="text-[#6e6e73]">LLM_API_KEY</code> (OpenAI or Anthropic) to{" "}
            <code className="text-[#6e6e73]">.env</code> for LLM-powered reports.
          </p>
        </div>
      )}

      {/* History */}
      <HistoryPanel onSelect={setActiveReport} />
    </div>
  );
}
