/**
 * pages/BotAnalysis.tsx — Bot Analysis: live view of every ticker the engine is watching.
 * Apple light aesthetic.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchBotWatchlist } from "@/lib/api";
import type { BotTickerItem } from "@/lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ratingCls(r: BotTickerItem["technical_rating"]) {
  switch (r) {
    case "Strong Buy":  return "bg-[#30d158]/10 text-[#30d158] border-[#30d158]/25";
    case "Buy":         return "bg-[#30d158]/8 text-[#30d158] border-[#30d158]/15";
    case "Hold":        return "bg-[#f5f5f7] text-[#6e6e73] border-[#e5e5ea]";
    case "Sell":        return "bg-[#ff3b30]/8 text-[#ff3b30] border-[#ff3b30]/15";
    case "Strong Sell": return "bg-[#ff3b30]/10 text-[#ff3b30] border-[#ff3b30]/25";
  }
}

function pctCls(v: number) {
  return v > 0 ? "text-[#30d158]" : v < 0 ? "text-[#ff3b30]" : "text-[#6e6e73]";
}

function pctFmt(v: number) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtPrice(v: number) {
  return v.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: v >= 100 ? 2 : 4,
  });
}

// ---------------------------------------------------------------------------
// Mini horizontal signal-score bar centred at zero
// ---------------------------------------------------------------------------
function MiniScoreBar({ score }: { score: number }) {
  const pct = Math.abs(score) * 50;
  const color =
    score >= 0.3  ? "bg-[#30d158]" :
    score <= -0.3 ? "bg-[#ff3b30]" : "bg-[#c7c7cc]";
  return (
    <div className="relative flex h-2 w-16 items-center rounded-full bg-[#f5f5f7]">
      <div className="absolute left-1/2 h-full w-px bg-[#e5e5ea]" />
      <div
        className={`absolute h-2 rounded-full ${color}`}
        style={{ width: `${pct}%`, [score >= 0 ? "left" : "right"]: "50%" }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stacked coloured bar showing SB/B/H/S/SS breakdown
// ---------------------------------------------------------------------------
function ConsensusBar({ item }: { item: BotTickerItem }) {
  const c = item.analyst_consensus;
  if (!c || c.total_analysts === 0) return <span className="text-xs text-[#8e8e93]">—</span>;

  const total = c.total_analysts;
  const segs = [
    { n: c.strong_buy,  cls: "bg-[#30d158]"     },
    { n: c.buy,         cls: "bg-[#30d158]/70"   },
    { n: c.hold,        cls: "bg-[#c7c7cc]"      },
    { n: c.sell,        cls: "bg-[#ff3b30]/70"   },
    { n: c.strong_sell, cls: "bg-[#ff3b30]"      },
  ];

  return (
    <div className="space-y-0.5">
      <div className="flex h-2 w-24 overflow-hidden rounded-full">
        {segs.map(({ n, cls }, i) =>
          n > 0 ? (
            <div key={i} className={cls} style={{ width: `${(n / total) * 100}%` }} />
          ) : null
        )}
      </div>
      <div className="flex items-center gap-1 text-xs text-[#6e6e73]">
        <span className="font-medium text-[#1d1d1f]">{c.consensus_rating ?? "—"}</span>
        {c.consensus_score !== null && <span>({c.consensus_score.toFixed(1)}/5)</span>}
        <span className="text-[#8e8e93]">· {total}a</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Expandable table row
// ---------------------------------------------------------------------------
function TickerRow({
  item,
  expanded,
  onToggle,
}: {
  item: BotTickerItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  const sig = item.last_signal;
  const posColor =
    item.position_status === "long"  ? "text-[#30d158]" :
    item.position_status === "short" ? "text-[#ff3b30]" : "text-[#8e8e93]";

  return (
    <>
      <tr
        className="cursor-pointer border-t border-[#e5e5ea] hover:bg-[#f5f5f7] transition-colors"
        onClick={onToggle}
      >
        {/* Ticker + position */}
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            <span className="font-mono font-semibold text-[#007aff]">{item.ticker}</span>
            {item.position_status !== "flat" && (
              <span className={`text-xs font-medium ${posColor}`}>
                {item.position_status.toUpperCase()} {Math.abs(item.position_qty).toFixed(0)}
              </span>
            )}
          </div>
        </td>

        {/* Price + % changes */}
        <td className="px-4 py-3 font-mono text-sm">
          <div className="text-[#1d1d1f]">{fmtPrice(item.price)}</div>
          <div className="flex gap-2 text-xs">
            <span className={pctCls(item.pct_change_1d)}>{pctFmt(item.pct_change_1d)} 1d</span>
            <span className={pctCls(item.pct_change_1m)}>{pctFmt(item.pct_change_1m)} 1m</span>
          </div>
        </td>

        {/* Technical rating */}
        <td className="px-4 py-3">
          <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${ratingCls(item.technical_rating)}`}>
            {item.technical_rating}
          </span>
          <div className="mt-0.5 text-xs text-[#6e6e73]">{item.confidence_pct.toFixed(0)}% conf</div>
        </td>

        {/* Analyst consensus */}
        <td className="px-4 py-3">
          <ConsensusBar item={item} />
        </td>

        {/* Upside to target */}
        <td className="px-4 py-3 font-mono text-sm">
          {item.analyst_consensus?.target_price_avg ? (
            <div>
              <div className={pctCls(item.upside_to_target_pct ?? 0)}>
                {item.upside_to_target_pct !== null ? pctFmt(item.upside_to_target_pct) : "—"}
              </div>
              <div className="text-xs text-[#6e6e73]">
                tgt {fmtPrice(item.analyst_consensus.target_price_avg)}
              </div>
            </div>
          ) : (
            <span className="text-[#8e8e93]">—</span>
          )}
        </td>

        {/* Engine signal */}
        <td className="px-4 py-3">
          {sig ? (
            <div>
              <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold ${
                sig.direction === "buy"
                  ? "bg-[#30d158]/10 text-[#30d158]"
                  : "bg-[#ff3b30]/10 text-[#ff3b30]"
              }`}>
                {sig.direction?.toUpperCase()}
              </span>
              <div className="mt-0.5 text-xs text-[#6e6e73]">
                {sig.confidence !== null ? `${((sig.confidence as number) * 100).toFixed(0)}% · ` : ""}
                {sig.strategy_id}
              </div>
              {sig.timestamp && (
                <div className="text-xs text-[#8e8e93]">
                  {new Date(sig.timestamp).toLocaleTimeString()}
                </div>
              )}
            </div>
          ) : (
            <span className="text-xs text-[#8e8e93]">No signal yet</span>
          )}
        </td>

        {/* Chevron */}
        <td className="px-4 py-3 text-right text-xs text-[#8e8e93]">
          {expanded ? "▲" : "▼"}
        </td>
      </tr>

      {/* Expanded detail */}
      {expanded && (
        <tr className="border-t border-[#e5e5ea] bg-[#f5f5f7]">
          <td colSpan={7} className="px-6 py-4">
            <div className="grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-3 lg:grid-cols-5">
              {Object.entries(item.signal_scores).map(([label, score]) => (
                <div key={label} className="flex items-center justify-between gap-2">
                  <span className="text-xs text-[#6e6e73] capitalize">
                    {label.replace(/_/g, " ")}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <MiniScoreBar score={score} />
                    <span className={`w-9 text-right font-mono text-xs ${score >= 0 ? "text-[#30d158]" : "text-[#ff3b30]"}`}>
                      {score >= 0 ? "+" : ""}{score.toFixed(2)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
            {item.analyst_consensus?.target_price_low !== null &&
             item.analyst_consensus?.target_price_low !== undefined && (
              <div className="mt-3 text-xs text-[#6e6e73]">
                Price target range:{" "}
                <span className="text-[#1d1d1f]">
                  {fmtPrice(item.analyst_consensus.target_price_low!)} –{" "}
                  {fmtPrice(item.analyst_consensus.target_price_high!)}
                </span>
                {" "}(avg {fmtPrice(item.analyst_consensus.target_price_avg!)})
                {" · "}{item.analyst_consensus.total_analysts} analysts
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Filter / sort types
// ---------------------------------------------------------------------------
type RatingFilter = "all" | "buy" | "hold" | "sell";
type SortKey     = "ticker" | "price" | "technical" | "upside" | "confidence";

const _ratingOrder: Record<string, number> = {
  "Strong Buy": 0, "Buy": 1, "Hold": 2, "Sell": 3, "Strong Sell": 4,
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function BotAnalysis() {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [ratingFilter, setRatingFilter] = useState<RatingFilter>("all");
  const [sortKey, setSortKey]   = useState<SortKey>("technical");
  const [sortAsc, setSortAsc]   = useState(false);

  const { data, isLoading, isError, dataUpdatedAt, refetch } = useQuery({
    queryKey: ["bot-watchlist"],
    queryFn: fetchBotWatchlist,
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const toggleExpand = (ticker: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(ticker) ? next.delete(ticker) : next.add(ticker);
      return next;
    });
  };

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc((a) => !a);
    else { setSortKey(key); setSortAsc(key === "ticker"); }
  };

  const filtered = (data?.tickers ?? []).filter((t) => {
    if (ratingFilter === "all")  return true;
    if (ratingFilter === "buy")  return t.technical_rating === "Strong Buy" || t.technical_rating === "Buy";
    if (ratingFilter === "hold") return t.technical_rating === "Hold";
    if (ratingFilter === "sell") return t.technical_rating === "Sell" || t.technical_rating === "Strong Sell";
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    let cmp = 0;
    if      (sortKey === "ticker")     cmp = a.ticker.localeCompare(b.ticker);
    else if (sortKey === "price")      cmp = a.price - b.price;
    else if (sortKey === "technical")  cmp = _ratingOrder[a.technical_rating] - _ratingOrder[b.technical_rating]
                                             || b.technical_score - a.technical_score;
    else if (sortKey === "upside")     cmp = (a.upside_to_target_pct ?? -999) - (b.upside_to_target_pct ?? -999);
    else if (sortKey === "confidence") cmp = a.confidence_pct - b.confidence_pct;
    return sortAsc ? cmp : -cmp;
  });

  const countFor = (f: RatingFilter) =>
    (data?.tickers ?? []).filter((t) =>
      f === "buy"  ? ["Strong Buy","Buy"].includes(t.technical_rating)
    : f === "hold" ? t.technical_rating === "Hold"
    : f === "sell" ? ["Sell","Strong Sell"].includes(t.technical_rating)
    : true
    ).length;

  const SortTh = ({ label, sk }: { label: string; sk: SortKey }) => (
    <th
      className="cursor-pointer select-none px-4 py-2 text-left text-xs text-[#6e6e73] hover:text-[#1d1d1f] transition-colors"
      onClick={() => handleSort(sk)}
    >
      {label}
      {sortKey === sk && <span className="ml-1 text-[#007aff]">{sortAsc ? "↑" : "↓"}</span>}
    </th>
  );

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-[#1d1d1f]">Bot Analysis</h1>
          <p className="mt-0.5 text-xs text-[#6e6e73]">
            Live view of every ticker the engine is monitoring · auto-refreshes every 60 s
          </p>
        </div>
        <div className="flex items-center gap-4">
          {data && (
            <div className="flex items-center gap-1.5 text-xs">
              <span className={`h-1.5 w-1.5 rounded-full ${data.engine_running ? "bg-[#30d158]" : "bg-[#c7c7cc]"}`} />
              <span className={data.engine_running ? "text-[#30d158]" : "text-[#6e6e73]"}>
                {data.engine_running ? `Running · loop ${data.loop_count}` : "Engine stopped"}
              </span>
            </div>
          )}
          {dataUpdatedAt > 0 && (
            <span className="text-xs text-[#8e8e93]">
              Updated {new Date(dataUpdatedAt).toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={() => void refetch()}
            className="rounded-lg border border-[#e5e5ea] px-3 py-1 text-xs text-[#6e6e73] hover:border-[#007aff] hover:text-[#007aff]"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Filter tabs */}
      <div className="flex flex-wrap gap-2">
        {(["all", "buy", "hold", "sell"] as RatingFilter[]).map((f) => (
          <button
            key={f}
            onClick={() => setRatingFilter(f)}
            className={`rounded-full border px-3 py-1 text-xs font-medium capitalize transition-colors ${
              ratingFilter === f
                ? "border-[#007aff]/30 bg-[#007aff]/10 text-[#007aff]"
                : "border-[#e5e5ea] text-[#6e6e73] hover:text-[#1d1d1f]"
            }`}
          >
            {f === "all" ? `All (${data?.count ?? 0})` : `${f.charAt(0).toUpperCase() + f.slice(1)} (${countFor(f)})`}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="rounded-xl border border-[#e5e5ea] bg-white">
        {isLoading ? (
          <div className="py-16 text-center text-sm text-[#6e6e73]">
            Loading analysis for all watched tickers…
            <br />
            <span className="text-xs text-[#8e8e93]">
              First load fetches live data — may take 20–40 s
            </span>
          </div>
        ) : isError ? (
          <div className="py-12 text-center text-sm text-[#ff3b30]">
            Failed to load — check that the API server is running.
          </div>
        ) : sorted.length === 0 ? (
          <div className="py-12 text-center text-sm text-[#6e6e73]">
            {(data?.count ?? 0) === 0
              ? "The trading engine has no tickers loaded yet. Check strategy_config.yaml."
              : "No tickers match the selected filter."}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-[#e5e5ea]">
                <tr>
                  <SortTh label="Ticker / Position" sk="ticker" />
                  <SortTh label="Price" sk="price" />
                  <SortTh label="Technical Rating" sk="technical" />
                  <th className="px-4 py-2 text-left text-xs text-[#6e6e73]">
                    Wall St. Consensus
                  </th>
                  <SortTh label="Upside" sk="upside" />
                  <th className="px-4 py-2 text-left text-xs text-[#6e6e73]">
                    Engine Signal
                  </th>
                  <th className="px-4 py-2" />
                </tr>
              </thead>
              <tbody>
                {sorted.map((item) => (
                  <TickerRow
                    key={item.ticker}
                    item={item}
                    expanded={expanded.has(item.ticker)}
                    onToggle={() => toggleExpand(item.ticker)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] px-4 py-3">
        <p className="text-xs leading-relaxed text-[#6e6e73]">
          <span className="text-[#1d1d1f]">Technical Rating</span> — composite of 10
          indicators (RSI, MACD, Bollinger Bands, MA trend, Stochastic, Williams %R, CCI,
          EMA cross, VWAP). &nbsp;
          <span className="text-[#1d1d1f]">Wall St. Consensus</span> — analyst
          recommendation breakdown from yfinance (SB/B/H/S/SS, weighted 5→1). &nbsp;
          <span className="text-[#1d1d1f]">Engine Signal</span> — most recent directional
          signal from the strategy engine. Click a row to expand per-indicator scores.
        </p>
      </div>
    </div>
  );
}
