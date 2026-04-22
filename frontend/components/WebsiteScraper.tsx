"use client";

import { useState } from "react";
import { Globe, Loader2 } from "lucide-react";
import { scrapeWebsite } from "@/lib/api";

export default function WebsiteScraper({ onQueued }: { onQueued?: () => void }) {
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(25);
  const [singlePageOnly, setSinglePageOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ doc_id: string; pages_scraped: number; source_url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<Array<{ url: string; pages: number }>>([]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await scrapeWebsite(url.trim(), maxPages, singlePageOnly);
      setResult({ doc_id: r.doc_id, pages_scraped: r.pages_scraped, source_url: r.source_url });
      setHistory((prev) => [{ url: r.source_url, pages: r.pages_scraped }, ...prev].slice(0, 5));
      onQueued?.();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Scrape failed";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-[var(--radius-card)] glass-panel p-5 space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Website Scraper</h2>
        <p className="text-xs text-[var(--color-text-muted)] mt-1">
          Enter a URL and the system will crawl same-domain pages, ingest text, and enqueue embeddings.
        </p>
      </div>
      <form onSubmit={submit} className="space-y-3">
        <div className="flex gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com"
            className="flex-1 bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)] text-sm rounded-xl px-4 py-2.5 focus:outline-none focus:border-[var(--color-brand)]"
          />
          <select
            value={maxPages}
            onChange={(e) => setMaxPages(Number(e.target.value))}
            disabled={singlePageOnly}
            className="bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] text-[var(--color-text-secondary)] rounded-xl px-3 py-2.5 text-sm focus:outline-none focus:border-[var(--color-brand)]"
          >
            {[10, 25, 50, 75, 100].map((n) => (
              <option key={n} value={n}>
                {n} pages
              </option>
            ))}
          </select>
        </div>
        <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
          <input
            type="checkbox"
            checked={singlePageOnly}
            onChange={(e) => setSinglePageOnly(e.target.checked)}
            className="accent-[var(--color-brand)]"
          />
          Single page only (scrape only the provided link)
        </label>
        <button
          type="submit"
          disabled={loading || !url.trim()}
          className="bg-[var(--color-brand)] hover:bg-[var(--color-brand-hover)] disabled:opacity-40 text-white rounded-xl px-4 py-2.5 transition-colors flex items-center gap-2 text-sm"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <Globe size={15} />}
          Scrape and Ingest
        </button>
      </form>
      <div className="text-[11px] text-[var(--color-text-muted)]">
        {singlePageOnly
          ? "Scrapes only the exact URL you provide."
          : "Crawls same-domain HTML pages only. Use page limit to control runtime and token size."}
      </div>
      {result && (
        <div className="text-xs text-[var(--color-success)] bg-[#052010] border border-green-900/40 rounded-lg px-3 py-2">
          Queued successfully: {result.pages_scraped} pages from {result.source_url}
        </div>
      )}
      {error && (
        <div className="text-xs text-[var(--color-danger)] bg-[#1e0808] border border-red-900/40 rounded-lg px-3 py-2">
          {error}
        </div>
      )}
      {history.length > 0 && (
        <div className="space-y-1">
          <div className="text-xs text-[var(--color-text-muted)]">Recent scrape jobs</div>
          {history.map((h, idx) => (
            <div key={`${h.url}-${idx}`} className="text-xs text-[var(--color-text-secondary)] flex items-center justify-between rounded bg-[var(--color-bg-elevated)]/70 px-2 py-1">
              <span className="truncate max-w-[75%]">{h.url}</span>
              <span className="font-mono">{h.pages}p</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
