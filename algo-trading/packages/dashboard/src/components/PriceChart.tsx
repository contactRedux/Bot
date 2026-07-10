/**
 * components/PriceChart.tsx
 *
 * Apple-style area chart for a selected ticker:
 *  - Gradient-filled area (green when positive, red when negative)
 *  - EMA-20 overlay (dashed)
 *  - Signal markers as ReferenceDots
 *  - No grid lines, minimal axes
 */
import {
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceDot,
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
  data: PricePoint[];
}

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
    <div className="rounded-lg border border-[#e5e5ea] bg-white px-3 py-2 text-xs shadow-sm">
      <p className="mb-1 font-mono text-[#6e6e73]">{label}</p>
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

  const closes  = data.map((d) => d.close);
  const emas    = computeEma(closes);
  const enriched = data.map((d, i) => ({ ...d, ema20: emas[i] }));

  const firstClose = enriched[0]?.close ?? 0;
  const lastClose  = enriched[enriched.length - 1]?.close ?? 0;
  const isPositive = lastClose >= firstClose;
  const chartColor = isPositive ? "#30d158" : "#ff3b30";

  const signals: SignalItem[] = (signalsData?.signals ?? []).filter(
    (s) => s.ticker === ticker,
  );

  return (
    <div className="rounded-xl border border-[#e5e5ea] bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-[#1d1d1f]">{ticker}</h2>
        <span className="text-xs text-[#6e6e73]">Close · EMA-20 · Signals</span>
      </div>

      {data.length === 0 ? (
        <p className="py-12 text-center text-sm text-[#6e6e73]">No price data available</p>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={enriched} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="priceAreaGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={chartColor} stopOpacity={0.18} />
                <stop offset="95%" stopColor={chartColor} stopOpacity={0}    />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="time"
              tick={{ fill: "#6e6e73", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              minTickGap={60}
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fill: "#6e6e73", fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={58}
              tickFormatter={(v: number) => v.toFixed(0)}
            />
            <Tooltip content={<ChartTooltip />} />

            {/* Close price area */}
            <Area
              type="monotone"
              dataKey="close"
              name="Close"
              stroke={chartColor}
              strokeWidth={1.5}
              fill="url(#priceAreaGrad)"
              dot={false}
              activeDot={{ r: 3 }}
            />

            {/* EMA-20 overlay */}
            <Line
              type="monotone"
              dataKey="ema20"
              name="EMA 20"
              stroke="#007aff"
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
                fill={s.signal > 0 ? "#30d158" : "#ff3b30"}
                stroke="none"
                label={{
                  value: s.signal > 0 ? "▲" : "▼",
                  position: s.signal > 0 ? "top" : "bottom",
                  fontSize: 11,
                  fill: s.signal > 0 ? "#30d158" : "#ff3b30",
                }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
