"use client";

import type { Thread } from "@/lib/types";

export function ThreadSidebar({
  threads,
  selectedId,
  onSelect,
  onCreate,
  onDelete,
  busy,
}: {
  threads: Thread[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onDelete: (id: string) => void;
  busy: boolean;
}) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-white/10 bg-ink text-paper">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-xs font-medium uppercase tracking-widest text-white/50">
          Conversations
        </h2>
        <button
          type="button"
          onClick={onCreate}
          disabled={busy}
          className="rounded border border-white/20 px-2 py-0.5 text-xs text-white/80 hover:bg-white/10 disabled:opacity-40"
        >
          New
        </button>
      </div>
      <ul className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {threads.length === 0 ? (
          <li className="px-2 py-6 text-sm text-white/40">No conversations yet.</li>
        ) : (
          threads.map((thread) => (
            <li key={thread.id} className="group relative">
              <button
                type="button"
                onClick={() => onSelect(thread.id)}
                className={`w-full rounded px-3 py-2 pr-8 text-left text-sm ${
                  thread.id === selectedId
                    ? "bg-white/15 text-white"
                    : "text-white/70 hover:bg-white/5"
                }`}
              >
                <span className="line-clamp-2">{thread.title}</span>
              </button>
              <button
                type="button"
                aria-label={`Delete ${thread.title}`}
                onClick={() => onDelete(thread.id)}
                className="absolute right-2 top-2 hidden rounded px-1 text-xs text-white/40 hover:text-white group-hover:block"
              >
                ×
              </button>
            </li>
          ))
        )}
      </ul>
    </aside>
  );
}
