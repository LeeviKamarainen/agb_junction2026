"use client";

import { ChevronLeft, ChevronRight, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { formatTime, type Memory } from "./types";

const PAGE_SIZE = 24;

/** Bound the rendered image count and keep every thumbnail at a stable aspect ratio. */
export function MemoryGallery({ memories, isAdmin, username, onDelete }: {
  memories: Memory[];
  isAdmin: boolean;
  username?: string;
  onDelete: (id: number) => Promise<void>;
}) {
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const grid = useRef<HTMLUListElement>(null);
  function goToPage(next: number) { setPage(next); grid.current?.scrollTo({ top: 0 }); }
  const dialog = useRef<HTMLDialogElement>(null);
  const filtered = memories.filter((memory) =>
    `${memory.id} ${memory.text} ${memory.collection} ${memory.owner ?? ""}`.toLowerCase().includes(query.toLowerCase().trim()),
  );
  const lastPage = Math.max(0, Math.ceil(filtered.length / PAGE_SIZE) - 1);
  const currentPage = Math.min(page, lastPage);
  const start = currentPage * PAGE_SIZE;
  const visible = filtered.slice(start, start + PAGE_SIZE);
  const focused = memories.find((memory) => memory.id === selected);

  useEffect(() => {
    const modal = dialog.current;
    if (focused && modal && !modal.open) modal.showModal();
    if (!focused) modal?.close();
  }, [focused]);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <label className="flex flex-col gap-1 text-sm">
        Find a memory
        <input type="search" value={query} onChange={(event) => { setQuery(event.target.value); goToPage(0); }}
          placeholder="Caption, collection or memory number"
          className="w-full rounded-md border border-border bg-background px-3 py-2" />
      </label>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {filtered.length ? `${start + 1}–${Math.min(start + PAGE_SIZE, filtered.length)} of ${filtered.length} memories` : "No memories match your search."}
      </p>
      <ul ref={grid} className="grid max-h-[60vh] auto-rows-max grid-cols-[repeat(auto-fill,minmax(min(100%,130px),1fr))] items-start gap-3 overflow-y-auto p-1">
        {visible.map((memory) => (
          <li key={memory.id} className="min-w-0 overflow-hidden rounded-lg border border-border bg-background/60">
            <button type="button" className="relative block aspect-[4/3] w-full overflow-hidden bg-black/20 text-left focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-[-2px]"
              onClick={() => setSelected(memory.id)} aria-label={`Open memory ${memory.id}`}>
              {memory.image_url ? (
                // eslint-disable-next-line @next/next/no-img-element -- authenticated API image
                <img src={memory.image_url} alt={memory.text || `Memory ${memory.id}`} loading="lazy" decoding="async"
                  className="absolute inset-0 h-full w-full object-cover" />
              ) : <span className="absolute inset-0 line-clamp-5 overflow-hidden p-3 text-xs">{memory.text}</span>}
            </button>
            <div className="flex min-w-0 flex-col gap-1 p-2 text-xs">
              <span className="truncate" title={memory.text || memory.collection}>{memory.text || memory.collection}</span>
              <time className="truncate text-[11px] text-muted-foreground">{formatTime(memory.created_at)}</time>
              <div className="flex min-w-0 items-center justify-between gap-1">
                <span className="truncate text-[11px] text-muted-foreground">#{memory.id} · {memory.demo ? "demo" : memory.owner && memory.owner !== username ? memory.owner : "yours"}</span>
                {isAdmin || !memory.demo ? (
                  <Button variant="ghost" size="icon-sm" onClick={() => void onDelete(memory.id)} aria-label={`Delete memory ${memory.id}`}>
                    <Trash2 size={14} aria-hidden="true" />
                  </Button>
                ) : null}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {lastPage > 0 ? (
        <nav aria-label="Memory pages" className="flex items-center justify-between gap-2">
          <Button variant="outline" disabled={currentPage === 0} onClick={() => goToPage(currentPage - 1)}><ChevronLeft aria-hidden="true" /> Previous</Button>
          <span className="text-xs">{currentPage + 1} / {lastPage + 1}</span>
          <Button variant="outline" disabled={currentPage === lastPage} onClick={() => goToPage(currentPage + 1)}>Next <ChevronRight aria-hidden="true" /></Button>
        </nav>
      ) : null}
      <dialog ref={dialog} onClose={() => setSelected(null)} aria-label="Memory detail"
        className="fixed inset-0 m-auto max-h-[90dvh] w-[min(90vw,900px)] overflow-auto rounded-xl border border-border bg-card p-4 text-foreground backdrop:bg-black/80">
        {focused ? (
          <>
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-sm">Memory #{focused.id} · {formatTime(focused.created_at)} · {focused.collection}</p>
              <Button variant="ghost" size="icon" onClick={() => dialog.current?.close()} aria-label="Close memory"><X aria-hidden="true" /></Button>
            </div>
            {focused.image_url ? (
              // eslint-disable-next-line @next/next/no-img-element -- full image viewed on request
              <img src={focused.image_url} alt={focused.text || `Memory ${focused.id}`} className="max-h-[65dvh] w-full object-contain" />
            ) : null}
            <p className="mt-3 whitespace-pre-wrap break-words text-sm">{focused.text}</p>
          </>
        ) : null}
      </dialog>
    </div>
  );
}
