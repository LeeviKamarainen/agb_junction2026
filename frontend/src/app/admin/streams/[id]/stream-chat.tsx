"use client";

import { Eye, LoaderCircle, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";

import { AskChat } from "../../../memory/ask-chat";

type Goal = components["schemas"]["GoalPublic"];

const RELOAD_MS = 5_000; // the goals are reloaded this often: the user may change them too

const EXAMPLES = [
  "What is in view right now?",
  "Warn them when a bicycle comes into view",
  "What did they see in the last hour?",
];

/**
 * Beside a stream in full: what its user's glasses are watching for, and a chat with Gemma about
 * the stream as that user. Gemma sees the newest frame and searches the user's memories, and can
 * start or stop watching for something; a goal can also be removed here directly.
 */
export function StreamChat({ feedId }: { feedId: string }) {
  const [goals, setGoals] = useState<Goal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const { data, response } = await api.GET(
        "/api/admin/streams/{feed_id}/goals",
        {
          params: { path: { feed_id: feedId } },
          cache: "no-store",
        },
      );
      if (data) {
        setGoals(data);
        setError(null);
      } else setError(`Could not load the goals (HTTP ${response.status}).`);
    } catch {
      setError("Could not reach the backend.");
    }
  }, [feedId]);

  useEffect(() => {
    // Loads now and every few seconds; setState happens once each request answers.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    const timer = setInterval(() => void load(), RELOAD_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function remove(goal: Goal) {
    setRemoving(goal.id);
    try {
      const { response } = await api.DELETE(
        "/api/admin/streams/{feed_id}/goals/{goal_id}",
        {
          params: { path: { feed_id: feedId, goal_id: goal.id } },
        },
      );
      if (!response.ok && response.status !== 404)
        setError(`Could not remove “${goal.text}” (HTTP ${response.status}).`);
    } catch {
      setError("Could not reach the backend.");
    } finally {
      setRemoving(null);
      await load();
    }
  }

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <section
        aria-labelledby="goals-title"
        className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3"
      >
        <h2
          id="goals-title"
          className="flex items-center gap-1.5 text-sm font-medium"
        >
          <Eye size={14} aria-hidden="true" /> Watching for
        </h2>
        {error && (
          <p role="alert" className="text-xs text-destructive">
            {error}
          </p>
        )}
        {goals === null ? (
          <p className="text-xs text-muted-foreground">Loading…</p>
        ) : goals.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Nothing yet. Ask Gemma below to watch for something.
          </p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {goals.map((goal) => (
              <li
                key={goal.id}
                className="flex items-start gap-2 rounded-md border border-border px-2.5 py-1.5"
              >
                <div className="min-w-0 flex-1">
                  <p
                    className={`text-sm ${goal.enabled ? "" : "text-muted-foreground line-through"}`}
                  >
                    {goal.text}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {goal.triggers
                      .map((trigger) => trigger.watch_for)
                      .join(" · ")}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => void remove(goal)}
                  disabled={removing !== null}
                  aria-label={`Stop watching for “${goal.text}”`}
                  title="Stop watching for this"
                  className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
                >
                  {removing === goal.id ? (
                    <LoaderCircle
                      size={14}
                      className="animate-spin"
                      aria-hidden="true"
                    />
                  ) : (
                    <X size={14} aria-hidden="true" />
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <AskChat
        endpoint={`/api/admin/streams/${encodeURIComponent(feedId)}/ask/stream`}
        examples={EXAMPLES}
        intro="Ask about this stream as its user: Gemma sees the newest frame and searches the user's memories. Ask it to watch for something, or to stop, and it changes this user's triggers."
        className="h-[60vh] min-h-96"
        onDone={() => void load()}
      />
    </div>
  );
}
