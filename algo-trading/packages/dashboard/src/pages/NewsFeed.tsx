/**
 * pages/NewsFeed.tsx — News Sentiment Feed page — Apple light aesthetic.
 */
import { useState, useRef } from "react";
import NewsFeedComponent from "@/components/NewsFeed";
import { fetchNewsForTicker, fetchNews } from "@/lib/api";
import { useNewsStore } from "@/store";
import type { NewsArticleDTO } from "@/lib/api";

type FetchState = "idle" | "loading" | "done" | "error";

const FETCH_TIMEOUT_MS = 30_000;

export default function NewsFeed() {
  const [input, setInput]               = useState("");
  const [fetchState, setFetchState]     = useState<FetchState>("idle");
  const [lastTicker, setLastTicker]     = useState<string>("");
  const [fetchResults, setFetchResults] = useState<NewsArticleDTO[]>([]);
  const [fetchMsg, setFetchMsg]         = useState<string>("");
  const inputRef = useRef<HTMLInputElement>(null);

  const addArticle  = useNewsStore((s) => s.addArticle);
  const setArticles = useNewsStore((s) => s.setArticles);
  const articles    = useNewsStore((s) => s.articles);

  const handleFetch = async () => {
    const ticker = input.trim().toUpperCase();
    if (!ticker) return;
    setFetchState("loading");
    setLastTicker(ticker);
    setFetchResults([]);
    setFetchMsg("");

    const timeoutId = window.setTimeout(() => {
      setFetchState("error");
      setFetchMsg("Request timed out — news sources may be slow or unavailable. Try again shortly.");
    }, FETCH_TIMEOUT_MS);

    try {
      const res = await fetchNewsForTicker(ticker, 20);
      window.clearTimeout(timeoutId);
      if (res.articles.length > 0) {
        setFetchResults(res.articles);
        res.articles.forEach((a) => addArticle(a as Parameters<typeof addArticle>[0]));
        setFetchMsg(`${res.articles.length} article${res.articles.length !== 1 ? "s" : ""} found (${res.inserted} new saved).`);
      } else {
        setFetchMsg(
          "No articles found from yfinance, NewsAPI, or GDELT for this ticker. " +
          "The ticker may be too obscure or news sources may be temporarily unavailable. " +
          "Adding a NewsAPI key in .env will improve coverage."
        );
      }
      setFetchState("done");
    } catch {
      window.clearTimeout(timeoutId);
      setFetchMsg("Fetch failed — check if the API server is running.");
      setFetchState("error");
    }
  };

  const handleLoadAll = async () => {
    try {
      const res = await fetchNews(undefined, 200);
      if (res.articles.length > 0) setArticles(res.articles);
    } catch {
      // silently ignore
    }
  };

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-[#1d1d1f]">News Sentiment Feed</h1>
        <button
          onClick={handleLoadAll}
          className="rounded-lg border border-[#e5e5ea] px-3 py-1 text-xs text-[#6e6e73] hover:border-[#007aff] hover:text-[#007aff]"
        >
          Refresh all
        </button>
      </div>

      {/* Ticker search / fetch */}
      <div className="rounded-xl border border-[#e5e5ea] bg-white p-4 space-y-3">
        <p className="text-xs text-[#6e6e73]">
          Pull live articles for any ticker (yfinance · NewsAPI · GDELT):
        </p>
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleFetch()}
            placeholder="e.g. AMD, NVDA, BTC-USD…"
            className="flex-1 rounded-xl border border-[#e5e5ea] bg-[#f5f5f7] px-3 py-2 font-mono text-sm text-[#1d1d1f] placeholder-[#8e8e93] focus:border-[#007aff] focus:outline-none"
          />
          <button
            onClick={handleFetch}
            disabled={fetchState === "loading" || !input.trim()}
            className="rounded-xl border border-[#007aff]/30 bg-[#007aff]/10 px-4 py-2 text-xs font-semibold text-[#007aff] hover:bg-[#007aff]/20 disabled:opacity-40"
          >
            {fetchState === "loading" ? "Fetching…" : "Fetch"}
          </button>
        </div>

        {/* Fetch result message */}
        {fetchMsg && (
          <p className={`text-xs ${fetchState === "error" ? "text-[#ff3b30]" : fetchResults.length === 0 ? "text-[#6e6e73]" : "text-[#30d158]"}`}>
            {lastTicker && <span className="font-mono mr-1 text-[#007aff]">{lastTicker}</span>}
            {fetchMsg}
          </p>
        )}

        {/* Results list from this fetch */}
        {fetchResults.length > 0 && (
          <ul className="divide-y divide-[#e5e5ea] border-t border-[#e5e5ea] pt-2 max-h-72 overflow-y-auto">
            {fetchResults.map((a) => (
              <li key={a.id} className="py-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    {a.url ? (
                      <a
                        href={a.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm leading-snug text-[#1d1d1f] hover:text-[#007aff]"
                      >
                        {a.headline}
                      </a>
                    ) : (
                      <p className="text-sm leading-snug text-[#1d1d1f]">{a.headline}</p>
                    )}
                    <div className="mt-0.5 flex flex-wrap gap-2 text-xs text-[#6e6e73]">
                      <span className="font-mono text-[#007aff]">{a.ticker}</span>
                      <span>{a.source}</span>
                      <span>{new Date(a.published_at).toLocaleString()}</span>
                    </div>
                  </div>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${
                      a.sentiment_label === "positive"
                        ? "bg-[#30d158]/10 text-[#30d158] border-[#30d158]/25"
                        : a.sentiment_label === "negative"
                        ? "bg-[#ff3b30]/10 text-[#ff3b30] border-[#ff3b30]/25"
                        : "bg-[#f5f5f7] text-[#6e6e73] border-[#e5e5ea]"
                    }`}
                  >
                    {a.sentiment_label.charAt(0).toUpperCase() + a.sentiment_label.slice(1)}
                    {" "}{a.sentiment_score >= 0 ? "+" : ""}{a.sentiment_score.toFixed(2)}
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* All stored articles */}
      <div>
        <p className="mb-2 text-xs text-[#6e6e73]">
          All stored articles ({articles.length}):
        </p>
        <NewsFeedComponent limit={200} />
      </div>
    </div>
  );
}
