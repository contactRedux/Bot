/**
 * pages/NewsFeed.tsx — News Sentiment Feed page.
 *
 * Wraps the NewsFeed component with optional ticker filter controls.
 * Polls /api/news every 60 seconds so the feed refreshes without a page reload.
 */
import { useState, useEffect } from "react";
import NewsFeedComponent from "@/components/NewsFeed";
import { fetchNews } from "@/lib/api";
import { useNewsStore } from "@/store";

const ALL = "__ALL__";

const COMMON_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "BTC-USD", "ETH-USD"];

export default function NewsFeed() {
  const [ticker, setTicker] = useState<string>(ALL);
  const setArticles = useNewsStore((s) => s.setArticles);

  // Poll /api/news every 60 s when this page is open
  useEffect(() => {
    const load = () => {
      fetchNews(ticker === ALL ? undefined : ticker, 200)
        .then((res) => { if (res.articles.length > 0) setArticles(res.articles); })
        .catch(() => undefined);
    };
    load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, [ticker, setArticles]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-zinc-100">News Sentiment Feed</h1>

        {/* Ticker filter */}
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setTicker(ALL)}
            className={`rounded px-3 py-1 text-xs font-medium transition-colors ${
              ticker === ALL
                ? "bg-sky-500/20 text-sky-400"
                : "text-zinc-400 hover:text-zinc-100"
            }`}
          >
            All
          </button>
          {COMMON_TICKERS.map((t) => (
            <button
              key={t}
              onClick={() => setTicker(t === ticker ? ALL : t)}
              className={`rounded px-3 py-1 font-mono text-xs font-medium transition-colors ${
                ticker === t
                  ? "bg-sky-500/20 text-sky-400"
                  : "text-zinc-400 hover:text-zinc-100"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <NewsFeedComponent ticker={ticker === ALL ? undefined : ticker} limit={200} />
    </div>
  );
}
