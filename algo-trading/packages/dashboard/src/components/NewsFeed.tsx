/**
 * components/NewsFeed.tsx
 *
 * Scrollable list of recent NewsArticle records with FinBERT sentiment badges.
 *
 * Sentiment badges:
 *   positive → emerald-400 background
 *   negative → rose-400 background
 *   neutral  → zinc-600 background
 */
import { useNewsStore } from "@/store";
import type { NewsArticle } from "@/store";

function SentimentBadge({ label, score }: { label: NewsArticle["sentiment_label"]; score: number }) {
  const styles: Record<NewsArticle["sentiment_label"], string> = {
    positive: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30",
    negative: "bg-rose-500/20 text-rose-400 border-rose-500/30",
    neutral:  "bg-zinc-700 text-zinc-400 border-zinc-600",
  };

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium ${styles[label]}`}
    >
      {label.charAt(0).toUpperCase() + label.slice(1)}
      <span className="font-mono opacity-70">
        {score >= 0 ? "+" : ""}
        {score.toFixed(2)}
      </span>
    </span>
  );
}

interface Props {
  /** Max articles to display */
  limit?: number;
  /** Filter to a specific ticker */
  ticker?: string;
}

export default function NewsFeed({ limit = 100, ticker }: Props) {
  const articles = useNewsStore((s) => s.articles);

  const filtered = ticker
    ? articles.filter((a) => a.ticker === ticker)
    : articles;

  const visible = filtered.slice(0, limit);

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-700 px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
          News Sentiment{ticker ? ` · ${ticker}` : ""}
        </span>
        <span className="rounded-full bg-zinc-700 px-2 py-0.5 font-mono text-xs text-zinc-400">
          {filtered.length}
        </span>
      </div>

      {visible.length === 0 ? (
        <p className="py-8 text-center text-sm text-zinc-500">
          No news articles yet — articles stream in via the feed.
        </p>
      ) : (
        <ul className="divide-y divide-zinc-700/50">
          {visible.map((article) => (
            <li key={article.id} className="px-4 py-3 hover:bg-zinc-700/20">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-snug text-zinc-100">{article.headline}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
                    <span className="font-mono text-sky-400">{article.ticker}</span>
                    <span>{article.source}</span>
                    <span>{(() => { try { return new Date(article.published_at).toLocaleString(); } catch { return article.published_at?.slice(0, 16).replace("T", " ") ?? ""; } })()}</span>
                  </div>
                </div>
                <div className="shrink-0">
                  <SentimentBadge label={article.sentiment_label} score={article.sentiment_score} />
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
