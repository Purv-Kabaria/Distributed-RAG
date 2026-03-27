"use client";

import { useState, useRef, useCallback } from "react";
import { Upload, File, X, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { uploadDocument } from "@/lib/api";

interface UploadItem {
  id: string;
  file: File;
  state: "pending" | "uploading" | "done" | "error";
  docId?: string;
  error?: string;
}

export default function UploadPanel({ onUploaded }: { onUploaded?: () => void }) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback((files: FileList | File[]) => {
    const arr = Array.from(files);
    const newItems: UploadItem[] = arr.map((f) => ({
      id: Math.random().toString(36).slice(2),
      file: f,
      state: "pending",
    }));
    setItems((prev) => [...prev, ...newItems]);
    newItems.forEach((item) => uploadFile(item));
  }, []);

  async function uploadFile(item: UploadItem) {
    setItems((prev) =>
      prev.map((i) => (i.id === item.id ? { ...i, state: "uploading" } : i))
    );
    try {
      const res = await uploadDocument(item.file);
      setItems((prev) =>
        prev.map((i) =>
          i.id === item.id ? { ...i, state: "done", docId: res.doc_id } : i
        )
      );
      onUploaded?.();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Upload failed";
      setItems((prev) =>
        prev.map((i) =>
          i.id === item.id ? { ...i, state: "error", error: msg } : i
        )
      );
    }
  }

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      addFiles(e.dataTransfer.files);
    },
    [addFiles]
  );

  const removeItem = (id: string) =>
    setItems((prev) => prev.filter((i) => i.id !== id));

  const ICON: Record<UploadItem["state"], React.ReactNode> = {
    pending:   <Loader2 size={14} className="text-[var(--color-text-muted)] animate-spin" />,
    uploading: <Loader2 size={14} className="text-[var(--color-brand-hover)] animate-spin" />,
    done:      <CheckCircle2 size={14} className="text-[var(--color-success)]" />,
    error:     <AlertCircle size={14} className="text-[var(--color-danger)]" />,
  };

  return (
    <div className="space-y-4">
      {/* Drop Zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`
          relative cursor-pointer rounded-[var(--radius-card)] border-2 border-dashed
          transition-all duration-200 p-8 text-center select-none
          ${dragging
            ? "border-[var(--color-brand)] bg-[#1a1a2e] shadow-[var(--shadow-glow)]"
            : "border-[var(--color-bg-border)] hover:border-[var(--color-brand)] hover:bg-[#16181f]"
          }
        `}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="text/*,image/*,audio/*,video/*,application/pdf,.txt,.md,.csv,.json"
          className="hidden"
          onChange={(e) => e.target.files && addFiles(e.target.files)}
        />
        <Upload size={28} className="mx-auto mb-3 text-[var(--color-text-muted)]" />
        <p className="text-sm font-medium text-[var(--color-text-primary)]">
          Drop files here or <span className="text-[var(--color-brand-hover)] underline underline-offset-2">browse</span>
        </p>
        <p className="mt-1 text-xs text-[var(--color-text-muted)]">
          Text · PDF · Image · Audio · Video &nbsp;·&nbsp; Max 200 MB each
        </p>
      </div>

      {/* File list */}
      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex items-center gap-3 rounded-lg bg-[var(--color-bg-elevated)] border border-[var(--color-bg-border)] px-3 py-2 animate-slide-up"
            >
              <File size={14} className="shrink-0 text-[var(--color-text-muted)]" />
              <span className="flex-1 truncate text-xs text-[var(--color-text-secondary)]">
                {item.file.name}
              </span>
              <span className="text-xs text-[var(--color-text-muted)]">
                {(item.file.size / 1024).toFixed(0)} KB
              </span>
              {ICON[item.state]}
              {item.error && (
                <span className="text-xs text-[var(--color-danger)] truncate max-w-[120px]" title={item.error}>
                  {item.error}
                </span>
              )}
              {(item.state === "done" || item.state === "error") && (
                <button
                  onClick={(e) => { e.stopPropagation(); removeItem(item.id); }}
                  className="text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)] transition-colors"
                >
                  <X size={12} />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
