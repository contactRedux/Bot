/**
 * components/NewsFeed.tsx
 *
 * Scrollable list of recent NewsArticle records with FinBERT sentiment badges.
 * Apple light aesthetic.
 */
import { useNewsStore } from "@/store";
import type { NewsArticle } from "@/store";

function SentimentBadge({ label, score }: { label: NewsArticle["sentiment_label"]; score: number }) {
  const styles: Record<NewsArticle["sentiment_label"], string> = {
    positive: "bg-[#30d158]/10 text-[#30d158] border-[#30d158]/25",
    negative: "bg-[#ff3b30]/10 text-[#ff3b30] border-[#ff3b30]/25",
    neutral:  "bg-[#f5f5f7] text-[#6e6e73] border-[#e5e5ea]",
  };

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${styles[label]}`}
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
  limit?: number;
  ticker?: string;
}

export default function NewsFeed({ limit = 100, ticker }: Props) {
  const articles = useNewsStore((s) => s.articles);

  const filtered = ticker
    ? articles.filter((a) => a.ticker === ticker)
    : articles;

  const visible = filtered.slice(0, limit);

  return (
    <div className="rounded-xl border border-[#e5e5ea] bg-white">
      <div className="flex items-center justify-between border-b border-[#e5e5ea] px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-[#6e6e73]">
          News Sentiment{ticker ? ` · ${ticker}` : ""}
        </span>
        <span className="rounded-full bg-[#f5f5f7] px-2 py-0.5 font-mono text-xs text-[#6e6e73]">
          {filtered.length}
        </span>
      </div>

      {visible.length === 0 ? (
        <p className="py-8 text-center text-sm text-[#6e6e73]">
          No news articles yet — articles stream in via the feed.
        </p>
      ) : (
        <ul className="divide-y divide-[#e5e5ea]">
          {visible.map((article) => (
            <li key={article.id} className="px-4 py-3 hover:bg-[#f5f5f7]">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm leading-snug text-[#1d1d1f]">{article.headline}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[#6e6e73]">
                    <span className="font-mono text-[#007aff]">{article.ticker}</span>
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
