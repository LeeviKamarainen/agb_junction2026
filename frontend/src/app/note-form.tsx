"use client";

import { ArrowUpRight, LoaderCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState, useTransition } from "react";

import { api } from "@/lib/api/browser";

export function NoteForm() {
  const router = useRouter();
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [refreshing, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const pending = saving || refreshing;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const trimmed = text.trim();
    if (!trimmed) {
      setError("A thought needs a few words. Add yours above.");
      return;
    }

    setSaving(true);
    setError(null);
    setMessage("");
    try {
      const { response } = await api.POST("/api/notes", {
        body: { text: trimmed },
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) {
        setError(
          "Your note couldn’t be saved. Your words are still here — try again.",
        );
        return;
      }
      setText("");
      setMessage("Added to the scratchpad. Keep the ideas coming.");
      startTransition(() => router.refresh());
    } catch {
      setError(
        "Couldn’t reach the scratchpad. Your words are still here — try again.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="note-form" aria-busy={pending}>
      <label htmlFor="note-text">Leave a thought</label>
      <textarea
        id="note-text"
        name="note"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
          setError(null);
          setMessage("");
        }}
        placeholder="What if we built something that…"
        required
        maxLength={500}
        rows={3}
        disabled={pending}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? "note-error note-hint" : "note-hint"}
      />
      <div className="note-form-bottom">
        <span id="note-hint">
          For the ideas that won’t wait.
          <span className="character-count" aria-hidden="true">
            {text.length}/500
          </span>
        </span>
        <button
          className="post-button"
          type="submit"
          disabled={pending || !text.trim()}
        >
          {pending ? (
            <>
              Adding…{" "}
              <LoaderCircle className="spinner" size={16} aria-hidden="true" />
            </>
          ) : (
            <>
              Post a note <ArrowUpRight size={17} aria-hidden="true" />
            </>
          )}
        </button>
      </div>
      {error && (
        <p id="note-error" className="form-error" role="alert">
          {error}
        </p>
      )}
      <p className="form-message" role="status">
        {message}
      </p>
    </form>
  );
}
