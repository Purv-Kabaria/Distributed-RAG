"use client";

import { useState, useEffect, useCallback } from "react";
import { Trash2, RefreshCw, FileText, Image, Music, Video, File } from "lucide-react";
import { getDocuments, deleteDocument, Document } from "@/lib/api";
import StatusBadge from "./StatusBadge";

const TYPE_ICON: Record<string, React.ReactNode> = {
  text:  <FileText size={14} />,
  pdf:   <FileText size={14} />,
  image: <Image size={14} />,
  audio: <Music size={14} />,
  video: <Video size={14} />,
};

function timeAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export default function DocumentList({ refreshSignal }: { refreshSignal: number }) {
  const [docs, setDocs] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getDocuments();
      setDocs(data);
      setError(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to load documents";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  // Poll every 3 s to catch status updates
  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load, refreshSignal]);

  async function handleDelete(id: string) {
    setDeleting(id);
    try {
      await deleteDocument(id);
      setDocs((prev) => prev.filter((d) => d.id !== id));
      setError(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Failed to delete document";
      setError(msg);
    } finally {
      setDeleting(null);
    }
  }

  if (loading) {
    return (
      <div className="space-y-2">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-12 rounded-lg skeleton" />
        ))}
      </div>
    );
  }

  if (docs.length === 0) {
    return (
      <div className="text-center py-10 text-[var(--color-text-muted)]">
        <File size={32} className="mx-auto mb-2 opacity-30" />
        <p className="text-sm">No documents yet. Upload something above.</p>
        {error && <p className="text-xs text-[var(--color-danger)] mt-2">{error}</p>}
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-[var(--color-text-muted)]">{docs.length} document{docs.length !== 1 ? "s" : ""}</span>
        <button
          onClick={load}
          className="text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
        >
          <RefreshCw size={13} />
        </button>
      </div>
      {error && (
        <div className="rounded-lg border border-red-900/40 bg-[#1e0808] px-3 py-2 text-xs text-[var(--color-danger)] mb-2">
          {error}
        </div>
      )}
      {docs.map((doc) => (
        <div
          key={doc.id}
          className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] hover:border-[var(--color-brand)]/30 transition-colors group"
        >
          <span className="text-[var(--color-text-muted)] shrink-0">
            {TYPE_ICON[doc.file_type] ?? <File size={14} />}
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-[var(--color-text-primary)] truncate">{doc.original_name}</p>
            <p className="text-xs text-[var(--color-text-muted)]">
              {doc.chunk_count} chunk{doc.chunk_count !== 1 ? "s" : ""} &nbsp;·&nbsp; {timeAgo(doc.created_at)}
            </p>
          </div>
          <StatusBadge status={doc.status} />
          <button
            onClick={() => handleDelete(doc.id)}
            disabled={deleting === doc.id}
            className="opacity-0 group-hover:opacity-100 text-[var(--color-text-muted)] hover:text-[var(--color-danger)] transition-all disabled:opacity-30"
          >
            <Trash2 size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}
