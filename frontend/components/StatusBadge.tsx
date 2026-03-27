"use client";

const STATUS_STYLES: Record<string, string> = {
  queued:     "bg-[#1e2028] text-[var(--color-text-secondary)] border border-[var(--color-bg-border)]",
  extracting: "bg-[#1c1a05] text-[var(--color-warning)] border border-amber-900/40",
  chunking:   "bg-[#1c1a05] text-[var(--color-warning)] border border-amber-900/40",
  embedding:  "bg-[#0f0c2a] text-[var(--color-brand-hover)] border border-indigo-900/40",
  done:       "bg-[#052010] text-[var(--color-success)] border border-green-900/40",
  failed:     "bg-[#1e0808] text-[var(--color-danger)] border border-red-900/40",
  ok:         "bg-[#052010] text-[var(--color-success)] border border-green-900/40",
  unreachable:"bg-[#1e0808] text-[var(--color-danger)] border border-red-900/40",
};

const DOTS: Record<string, boolean> = {
  extracting: true,
  chunking:   true,
  embedding:  true,
};

export default function StatusBadge({ status }: { status: string }) {
  const s = status?.toLowerCase() ?? "unknown";
  const cls = STATUS_STYLES[s] ?? STATUS_STYLES.queued;
  const pulse = DOTS[s];

  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full ${cls}`}>
      {pulse ? (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 bg-current" />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-current" />
        </span>
      ) : (
        <span className="h-1.5 w-1.5 rounded-full bg-current" />
      )}
      {s}
    </span>
  );
}
