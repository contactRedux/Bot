/**
 * pages/NewsFeed.tsx — News Sentiment Feed page.
 *
 * Usage:
 *   - Type any ticker in the search box and press "Fetch" (or Enter).
 *   - The page calls POST /api/news/fetch which pulls live from NewsAPI+GDELT.
 *   - Results are shown in a list.  If nothing comes back: "No articles found."
 *   - Existing articles from the store (previously fetched) are shown below.
 */
import { useState, useRef } from "react";
import NewsFeedComponent from "@/components/NewsFeed";
import { fetchNewsForTicker, fetchNews } from "@/lib/api";
import { useNewsStore } from "@/store";
import type { NewsArticleDTO } from "@/lib/api";

type FetchState = "idle" | "loading" | "done" | "error";

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
    try {
      const res = await fetchNewsForTicker(ticker, 20);
      if (res.articles.length > 0) {
        setFetchResults(res.articles);
        // Also push into global store so the list below updates
        res.articles.forEach((a) => addArticle(a as Parameters<typeof addArticle>[0]));
        setFetchMsg(`${res.articles.length} article${res.articles.length !== 1 ? "s" : ""} found (${res.inserted} new saved).`);
      } else {
        setFetchMsg("No articles found for this ticker.");
      }
      setFetchState("done");
    } catch {
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
        <h1 className="text-xl font-semibold text-zinc-100">News Sentiment Feed</h1>
        <button
          onClick={handleLoadAll}
          className="rounded border border-zinc-600 px-3 py-1 text-xs text-zinc-400 hover:border-zinc-400 hover:text-zinc-100"
        >
          Refresh all
        </button>
      </div>

      {/* Ticker search / fetch */}
      <div className="rounded-lg border border-zinc-700 bg-zinc-800 p-4 space-y-3">
        <p className="text-xs text-zinc-500">
          Pull live articles for any ticker from NewsAPI + GDELT:
        </p>
        <div className="flex gap-2">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && handleFetch()}
            placeholder="e.g. AMD, NVDA, BTC-USD…"
            className="flex-1 rounded border border-zinc-600 bg-zinc-900 px-3 py-2 font-mono text-sm text-zinc-100 placeholder-zinc-600 focus:border-sky-400 focus:outline-none"
          />
          <button
            onClick={handleFetch}
            disabled={fetchState === "loading" || !input.trim()}
            className="rounded border border-sky-500/50 bg-sky-500/15 px-4 py-2 text-xs font-semibold text-sky-400 hover:bg-sky-500/25 disabled:opacity-40"
          >
            {fetchState === "loading" ? "Fetching…" : "Fetch"}
          </button>
        </div>

        {/* Fetch result message */}
        {fetchMsg && (
          <p className={`text-xs ${fetchState === "error" ? "text-rose-400" : fetchResults.length === 0 ? "text-zinc-500" : "text-emerald-400"}`}>
            {lastTicker && <span className="font-mono mr-1 text-sky-400">{lastTicker}</span>}
            {fetchMsg}
          </p>
        )}

        {/* Results list from this fetch */}
        {fetchResults.length > 0 && (
          <ul className="divide-y divide-zinc-700/50 border-t border-zinc-700 pt-2 max-h-72 overflow-y-auto">
            {fetchResults.map((a) => (
              <li key={a.id} className="py-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    {a.url ? (
                      <a
                        href={a.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm leading-snug text-zinc-100 hover:text-sky-400"
                      >
                        {a.headline}
                      </a>
                    ) : (
                      <p className="text-sm leading-snug text-zinc-100">{a.headline}</p>
                    )}
                    <div className="mt-0.5 flex flex-wrap gap-2 text-xs text-zinc-500">
                      <span className="font-mono text-sky-400">{a.ticker}</span>
                      <span>{a.source}</span>
                      <span>{new Date(a.published_at).toLocaleString()}</span>
                    </div>
                  </div>
                  <span
                    className={`shrink-0 rounded border px-2 py-0.5 text-xs font-medium ${
                      a.sentiment_label === "positive"
                        ? "bg-emerald-500/20 text-emerald-400 border-emerald-500/30"
                        : a.sentiment_label === "negative"
                        ? "bg-rose-500/20 text-rose-400 border-rose-500/30"
                        : "bg-zinc-700 text-zinc-400 border-zinc-600"
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
        <p className="mb-2 text-xs text-zinc-500">
          All stored articles ({articles.length}):
        </p>
        <NewsFeedComponent limit={200} />
      </div>
    </div>
  );
}
