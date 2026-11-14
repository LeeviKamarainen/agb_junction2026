"use client";

import {
  ImagePlus,
  LoaderCircle,
  NotebookPen,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import { type ChangeEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";

import {
  errorText,
  formatTime,
  type Memory,
  toLocalInput,
} from "./types";

type Pending = {
  key: string;
  file: File;
  preview: string;
  caption: string;
  takenAt: string; // datetime-local value
  status: "waiting" | "uploading" | "done" | "failed";
  error?: string;
};

const field =
  "w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus-visible:border-primary disabled:opacity-60";

export function MemoryLibrary() {
  const [memories, setMemories] = useState<Memory[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending[]>([]);
  const [uploading, setUploading] = useState(false);
  const [note, setNote] = useState("");
  const [noteTime, setNoteTime] = useState("");
  const [noteState, setNoteState] = useState<{
    saving: boolean;
    error?: string;
  }>({ saving: false });

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const { data, error } = await api.GET("/api/memories", {
        cache: "no-store",
      });
      if (error || !data) throw new Error(errorText(error, "Could not load"));
      setMemories(data);
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "Could not load the memories",
      );
    }
  }, []);

  useEffect(() => {
    // Loading on mount is the point of this effect: the list lives in the database.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  function choose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // so choosing the same file again still fires
    setPending((current) => [
      ...current,
      ...files.map((file) => ({
        key: `${file.name}-${file.lastModified}-${Math.random()}`,
        file,
        preview: URL.createObjectURL(file),
        caption: "",
        takenAt: toLocalInput(new Date(file.lastModified)),
        status: "waiting" as const,
      })),
    ]);
  }

  function update(key: string, change: Partial<Pending>) {
    setPending((current) =>
      current.map((item) => (item.key === key ? { ...item, ...change } : item)),
    );
  }

  function remove(key: string) {
    setPending((current) => {
      const item = current.find((entry) => entry.key === key);
      if (item) URL.revokeObjectURL(item.preview);
      return current.filter((entry) => entry.key !== key);
    });
  }

  async function uploadAll() {
    setUploading(true);
    // One request per image keeps each body small and shows progress per file.
    for (const item of pending.filter((entry) => entry.status !== "done")) {
      update(item.key, { status: "uploading", error: undefined });
      const form = new FormData();
      form.append("file", item.file);
      form.append("text", item.caption.trim());
      if (item.takenAt) {
        form.append("created_at", new Date(item.takenAt).toISOString());
      }
      try {
        const response = await fetch("/api/memories/image", {
          method: "POST",
          body: form,
        });
        const body = await response.json().catch(() => null);
        if (!response.ok) {
          update(item.key, {
            status: "failed",
            error: errorText(body, `Upload failed (${response.status})`),
          });
          continue;
        }
        update(item.key, { status: "done" });
        setMemories((current) =>
          current ? sortNewestFirst([body as Memory, ...current]) : current,
        );
      } catch {
        update(item.key, {
          status: "failed",
          error: "Could not reach the backend",
        });
      }
    }
    setUploading(false);
    setPending((current) => {
      current
        .filter((entry) => entry.status === "done")
        .forEach((entry) => URL.revokeObjectURL(entry.preview));
      return current.filter((entry) => entry.status !== "done");
    });
  }

  async function addNote() {
    const text = note.trim();
    if (!text) return;
    setNoteState({ saving: true });
    try {
      const { data, error } = await api.POST("/api/memories/text", {
        body: {
          text,
          created_at: noteTime ? new Date(noteTime).toISOString() : null,
        },
      });
      if (error || !data) {
        setNoteState({
          saving: false,
          error: errorText(error, "Could not save"),
        });
        return;
      }
      setNote("");
      setNoteState({ saving: false });
      setMemories((current) =>
        current ? sortNewestFirst([data, ...current]) : current,
      );
    } catch {
      setNoteState({ saving: false, error: "Could not reach the backend" });
    }
  }

  async function deleteMemory(id: number) {
    const { response } = await api.DELETE("/api/memories/{id}", {
      params: { path: { id } },
    });
    if (response.ok) {
      setMemories((current) => current?.filter((m) => m.id !== id) ?? null);
    }
  }

  const waiting = pending.filter((entry) => entry.status !== "done").length;

  return (
    <section
      aria-labelledby="library-title"
      className="flex min-h-0 flex-col gap-4 rounded-xl border border-border bg-card p-4"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="library-title" className="font-semibold">
          Memories{" "}
          <span className="text-sm font-normal text-muted-foreground">
            {memories ? memories.length : ""}
          </span>
        </h2>
        <Button variant="ghost" size="sm" onClick={() => void load()}>
          <RefreshCw aria-hidden="true" /> Reload
        </Button>
      </div>

      {/* Images */}
      <div className="flex flex-col gap-3 rounded-lg border border-dashed border-border p-3">
        <label className="flex cursor-pointer items-center justify-center gap-2 rounded-md bg-secondary px-3 py-3 text-sm hover:text-primary">
          <ImagePlus size={18} aria-hidden="true" />
          Choose images (one or many)
          <input
            type="file"
            accept="image/*"
            multiple
            className="sr-only"
            onChange={choose}
            disabled={uploading}
          />
        </label>

        {pending.length > 0 && (
          <>
            <p className="text-xs text-muted-foreground">
              Captions are optional; they make an image findable by keyword.
              &ldquo;Seen at&rdquo; starts as the file&rsquo;s date: set it to
              test time filters.
            </p>
            <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto">
              {pending.map((item) => (
                <li
                  key={item.key}
                  className="flex gap-3 rounded-md bg-background/60 p-2"
                >
                  {/* eslint-disable-next-line @next/next/no-img-element -- a local blob preview */}
                  <img
                    src={item.preview}
                    alt=""
                    className="size-16 shrink-0 rounded object-cover"
                  />
                  <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-xs text-muted-foreground">
                        {item.file.name}
                      </span>
                      <StatusBadge item={item} />
                    </div>
                    <input
                      className={field}
                      placeholder="Caption, e.g. keys on the hall table"
                      value={item.caption}
                      disabled={uploading}
                      onChange={(e) =>
                        update(item.key, { caption: e.target.value })
                      }
                      aria-label={`Caption for ${item.file.name}`}
                    />
                    <div className="flex items-center gap-2">
                      <input
                        type="datetime-local"
                        className={field}
                        value={item.takenAt}
                        disabled={uploading}
                        onChange={(e) =>
                          update(item.key, { takenAt: e.target.value })
                        }
                        aria-label={`Seen at, for ${item.file.name}`}
                      />
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => remove(item.key)}
                        disabled={uploading}
                        aria-label={`Remove ${item.file.name}`}
                      >
                        <X aria-hidden="true" />
                      </Button>
                    </div>
                    {item.error && (
                      <p className="text-xs text-destructive" role="alert">
                        {item.error}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
            <Button
              onClick={() => void uploadAll()}
              disabled={uploading || waiting === 0}
            >
              {uploading ? (
                <LoaderCircle className="animate-spin" aria-hidden="true" />
              ) : (
                <Upload aria-hidden="true" />
              )}
              {uploading
                ? "Uploading and embedding…"
                : `Upload ${waiting} image${waiting === 1 ? "" : "s"}`}
            </Button>
          </>
        )}
      </div>

      {/* Text notes */}
      <form
        className="flex flex-col gap-2 rounded-lg border border-dashed border-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void addNote();
        }}
      >
        <label htmlFor="note" className="flex items-center gap-2 text-sm">
          <NotebookPen size={16} aria-hidden="true" /> Add a text memory
        </label>
        <textarea
          id="note"
          className={field}
          rows={2}
          maxLength={2000}
          placeholder="e.g. Had coffee with Aino at the Junction lobby"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          disabled={noteState.saving}
        />
        <div className="flex items-center gap-2">
          <input
            type="datetime-local"
            className={field}
            value={noteTime}
            onChange={(e) => setNoteTime(e.target.value)}
            aria-label="Seen at (empty: now)"
            title="Seen at (empty: now)"
            disabled={noteState.saving}
          />
          <Button
            type="submit"
            variant="secondary"
            disabled={noteState.saving || !note.trim()}
          >
            {noteState.saving ? "Saving…" : "Add"}
          </Button>
        </div>
        {noteState.error && (
          <p className="text-xs text-destructive" role="alert">
            {noteState.error}
          </p>
        )}
      </form>

      {/* Stored */}
      {loadError && (
        <p className="text-sm text-destructive" role="alert">
          {loadError}
        </p>
      )}
      {memories === null && !loadError && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
      {memories?.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No memories yet. Add a few images above, then ask about them.
        </p>
      )}
      {memories && memories.length > 0 && (
        <ul className="grid max-h-[60vh] grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3">
          {memories.map((memory) => (
            <li
              key={memory.id}
              className="group relative flex flex-col overflow-hidden rounded-md border border-border bg-background/60"
            >
              {memory.image_url ? (
                /* eslint-disable-next-line @next/next/no-img-element -- served by the API */
                <img
                  src={memory.image_url}
                  alt={memory.text || `Memory ${memory.id}`}
                  className="aspect-[4/3] w-full object-cover"
                  loading="lazy"
                />
              ) : (
                <p className="line-clamp-4 aspect-[4/3] p-2 text-xs">
                  {memory.text}
                </p>
              )}
              <div className="flex flex-col gap-0.5 p-1.5 text-[11px] text-muted-foreground">
                <span>
                  #{memory.id} · {formatTime(memory.created_at)}
                </span>
                {memory.image_url && memory.text && (
                  <span className="truncate text-foreground">
                    {memory.text}
                  </span>
                )}
              </div>
              <Button
                variant="destructive"
                size="icon-xs"
                className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                onClick={() => void deleteMemory(memory.id)}
                aria-label={`Delete memory ${memory.id}`}
              >
                <Trash2 aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function StatusBadge({ item }: { item: Pending }) {
  const labels = {
    waiting: "ready",
    uploading: "uploading…",
    done: "stored",
    failed: "failed",
  };
  const colour =
    item.status === "failed"
      ? "text-destructive"
      : item.status === "uploading"
        ? "text-primary"
        : "text-muted-foreground";
  return <span className={`shrink-0 text-xs ${colour}`}>{labels[item.status]}</span>;
}

function sortNewestFirst(memories: Memory[]): Memory[] {
  return memories.toSorted(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime() ||
      b.id - a.id,
  );
}
