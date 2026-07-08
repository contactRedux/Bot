/**
 * components/PriceChart.tsx
 *
 * Line chart for a selected ticker with:
 *  - Close price line (sky-400)
 *  - EMA-20 overlay (emerald-400, dashed)
 *  - Signal markers rendered as reference dots (▲ buy = emerald, ▼ sell = rose)
 *
 * Data is fetched from GET /portfolio/positions for available tickers,
 * then GET /signals/latest to get signal positions.
 *
 * Recharts doesn't ship a built-in candlestick; we use a ComposedChart with
 * a Line for close price and scatter-style ReferenceDots for signals.
 */
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceDot,
  Legend,
} from "recharts";
import { useQuery } from "@tanstack/react-query";
import { fetchSignals } from "@/lib/api";
import type { SignalItem } from "@/lib/types";

interface PricePoint {
  time: string;
  close: number;
  ema20?: number;
}

interface Props {
  ticker: string;
  /** Price data supplied by parent (loaded from DataStore via REST) */
  data: PricePoint[];
}

// Compute EMA on a series of closes
function computeEma(closes: number[], period = 20): (number | undefined)[] {
  const k = 2 / (period + 1);
  const result: (number | undefined)[] = new Array(closes.length).fill(undefined);
  let ema: number | undefined;
  for (let i = 0; i < closes.length; i++) {
    if (i < period - 1) continue;
    if (ema === undefined) {
      ema = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
      result[i] = Math.round(ema * 100) / 100;
    } else {
      ema = closes[i] * k + ema * (1 - k);
      result[i] = Math.round(ema * 100) / 100;
    }
  }
  return result;
}

// Custom tooltip
const ChartTooltip = ({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
}) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded border border-zinc-600 bg-zinc-800 px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-mono text-zinc-400">{label}</p>
      {payload.map((p) => (
        <p key={p.name} style={{ color: p.color }}>
          {p.name}: <span className="font-mono font-semibold">{p.value.toFixed(2)}</span>
        </p>
      ))}
    </div>
  );
};

export default function PriceChart({ ticker, data }: Props) {
  const { data: signalsData } = useQuery({
    queryKey: ["signals"],
    queryFn: fetchSignals,
    refetchInterval: 5_000,
  });

  const closes = data.map((d) => d.close);
  const emas = computeEma(closes);
  const enriched = data.map((d, i) => ({ ...d, ema20: emas[i] }));

  // Signals for this ticker
  const signals: SignalItem[] = (signalsData?.signals ?? []).filter(
    (s) => s.ticker === ticker,
  );

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-mono text-sm font-semibold text-zinc-100">{ticker}</h2>
        <span className="text-xs text-zinc-500">Close · EMA-20 · Signals</span>
      </div>

      {data.length === 0 ? (
        <p className="py-12 text-center text-sm text-zinc-500">No price data available</p>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={enriched} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#3f3f46" />
            <XAxis
              dataKey="time"
              tick={{ fill: "#a1a1aa", fontSize: 11 }}
              tickLine={false}
              minTickGap={60}
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fill: "#a1a1aa", fontSize: 11 }}
              tickLine={false}
              width={58}
              tickFormatter={(v: number) => v.toFixed(0)}
            />
            <Tooltip content={<ChartTooltip />} />
            <Legend
              wrapperStyle={{ fontSize: 11, color: "#a1a1aa" }}
              iconType="line"
            />

            {/* Close price */}
            <Line
              type="monotone"
              dataKey="close"
              name="Close"
              stroke="#38bdf8"
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
            />

            {/* EMA-20 overlay */}
            <Line
              type="monotone"
              dataKey="ema20"
              name="EMA 20"
              stroke="#34d399"
              strokeWidth={1}
              strokeDasharray="4 3"
              dot={false}
              connectNulls
            />

            {/* Signal markers */}
            {signals.map((s, i) => (
              <ReferenceDot
                key={i}
                x={s.timestamp.slice(0, 10)}
                yAxisId={0}
                r={5}
                fill={s.signal > 0 ? "#34d399" : "#fb7185"}
                stroke="none"
                label={{
                  value: s.signal > 0 ? "▲" : "▼",
                  position: s.signal > 0 ? "top" : "bottom",
                  fontSize: 11,
                  fill: s.signal > 0 ? "#34d399" : "#fb7185",
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
