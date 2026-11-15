"use client";

import { ChevronDown, Save, SlidersHorizontal, Trash2, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";

import { errorText, formatTime } from "./types";

type Goal = components["schemas"]["GoalPublic"];
export type Trigger = components["schemas"]["TriggerPublic"];
type Draft = components["schemas"]["TriggerDraft"];
/** A trigger's score on the latest stream frame, and the level it has to reach. */
export type Match = { score: number | null; level: number | null };

const field =
  "w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus-visible:border-primary disabled:opacity-60";

// The fields of a trigger that can be edited, to tell whether a row has unsaved changes.
const EDITED: (keyof Draft)[] = ["name", "watch_for", "sigmas"];

/**
 * What your glasses watch out for. You ask Gemma in the chat to watch for something, that is a
 * goal, and it writes its triggers; each stream frame is then scored against the triggers of your enabled
 * goals, and one that reaches its level asks Gemma about the goal. A goal's triggers are switched
 * on and off and removed together. `lastMatches` (trigger id -> score and level on the latest
 * frame) shows how close each is. The list reloads on a new `firedCount`, so "last fired" and
 * the measured levels stay current, and each time it becomes `visible`, since Gemma may have
 * added or removed a goal in the chat meanwhile.
 */
export function TriggerEditor({
  lastMatches,
  firedCount = 0,
  visible = true,
  compact = false,
}: {
  lastMatches: Map<number, Match>;
  firedCount?: number;
  visible?: boolean;
  /** For the home page: one line per goal with its switch, no explanation; a goal's triggers
   * open for tuning on demand. */
  compact?: boolean;
}) {
  const [goals, setGoals] = useState<Goal[] | null>(null);
  // Compact: the goals whose triggers are open for tuning.
  const [tuning, setTuning] = useState<Set<number>>(new Set());
  const toggleTuning = (id: number) =>
    setTuning((open) => {
      const next = new Set(open);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const { data, error: problem, response } = await api.GET("/api/glasses/goals", {
        cache: "no-store",
      });
      if (response.status === 401) {
        // Nobody logged in (just logged out, or the guest expired): no goals, and no error.
        setGoals(null);
        setError(null);
      } else if (problem || !data) setError(errorText(problem, "Could not load goals"));
      else setGoals(data);
    } catch {
      setError("Could not reach the backend");
    }
  }, []);

  useEffect(() => {
    // Loading when shown, and when a trigger fired, is the point of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (visible) void load();
    else {
      // Hidden, or nobody logged in any more: nothing of the last login's goals stays.
      setGoals(null);
      setError(null);
    }
  }, [load, firedCount, visible]);

  /** Runs one change, shows what went wrong, and reloads the goals. */
  async function change(
    request: Promise<{ error?: unknown }>,
    fallback: string,
  ) {
    setError(null);
    try {
      const { error: problem } = await request;
      if (problem) setError(errorText(problem, fallback));
    } catch {
      setError("Could not reach the backend");
    }
    await load();
  }

  /** A goal's triggers, each with its score and level and its fields to tune. */
  function triggerRows(goal: Goal) {
    return goal.triggers.map((trigger) => (
      <TriggerRow
        // Remounts when the trigger itself changed, so the fields never show an old draft.
        key={`${trigger.id}:${trigger.name}:${trigger.watch_for}:${trigger.sigmas}`}
        trigger={trigger}
        match={lastMatches.get(trigger.id)}
        onSave={(body) =>
          void change(
            api.PUT("/api/glasses/triggers/{id}", {
              params: { path: { id: trigger.id } },
              body,
            }),
            "Could not save the trigger",
          )
        }
        onDelete={() =>
          void change(
            api.DELETE("/api/glasses/triggers/{id}", {
              params: { path: { id: trigger.id } },
            }),
            "Could not drop the trigger",
          )
        }
      />
    ));
  }

  if (compact)
    return (
      <section aria-labelledby="watching-title" className="flex flex-col gap-2 text-sm">
        <h2
          id="watching-title"
          className="flex items-center gap-1.5 text-xs font-medium tracking-wide text-muted-foreground"
        >
          <Zap size={13} aria-hidden="true" /> WATCHING FOR
        </h2>
        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
        {goals?.length ? (
          <ul className="flex flex-col gap-1.5">
            {goals.map((goal) => (
              <li
                key={goal.id}
                className={`flex flex-col rounded-md border border-border bg-card ${goal.enabled ? "" : "opacity-60"}`}
              >
                <div className="flex items-center gap-2 px-3 py-1.5">
                <span className="min-w-0 flex-1 truncate" title={goal.text}>
                  {goal.text}
                </span>
                <button
                  type="button"
                  aria-expanded={tuning.has(goal.id)}
                  aria-label={`Tune the ${goal.triggers.length} triggers of: ${goal.text}`}
                  title="Tune its triggers"
                  className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
                  onClick={() => toggleTuning(goal.id)}
                >
                  <SlidersHorizontal size={13} aria-hidden="true" />
                  {goal.triggers.length}
                  <ChevronDown
                    size={12}
                    aria-hidden="true"
                    className={tuning.has(goal.id) ? "rotate-180" : ""}
                  />
                </button>
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={goal.enabled}
                    aria-label={`Watch for: ${goal.text}`}
                    onChange={(e) =>
                      void change(
                        api.PUT("/api/glasses/goals/{id}", {
                          params: { path: { id: goal.id } },
                          body: { enabled: e.target.checked },
                        }),
                        "Could not switch the goal",
                      )
                    }
                  />
                  On
                </label>
                <button
                  type="button"
                  aria-label={`Stop watching for: ${goal.text}`}
                  className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
                  onClick={() =>
                    void change(
                      api.DELETE("/api/glasses/goals/{id}", {
                        params: { path: { id: goal.id } },
                      }),
                      "Could not remove the goal",
                    )
                  }
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
                </div>
                {tuning.has(goal.id) && (
                  <div className="flex flex-col gap-2 border-t border-border p-2">
                    {triggerRows(goal)}
                  </div>
                )}
              </li>
            ))}
          </ul>
        ) : (
          !error && (
            <p className="text-xs text-muted-foreground">
              Nothing yet. Say &ldquo;warn me when I see my keys&rdquo; to have the glasses watch
              for something.
            </p>
          )
        )}
      </section>
    );

  return (
    <section
      aria-labelledby="triggers-title"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4"
    >
      <div>
        <h2
          id="triggers-title"
          className="flex items-center gap-2 font-semibold"
        >
          <Zap size={16} aria-hidden="true" /> What the glasses watch out for
        </h2>
        <p className="text-xs text-muted-foreground">
          Ask Gemma in the chat above to watch out for something and it writes
          the triggers for that goal. They are yours alone. Each stream frame
          gets a score per trigger: how much more it looks like the &ldquo;watch
          for&rdquo; text than the last 20 seconds did. When a score reaches its
          level, Gemma looks at the frame with your goal in mind: it writes the
          message for the glasses, or finds a false alarm, says nothing and
          raises the level. After a message the stream is quiet for 5 seconds,
          so it can be read; after a false alarm for 2. To drop a goal, remove
          it here or ask Gemma to stop watching for it.
        </p>
      </div>
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      {goals?.map((goal) => (
        <article
          key={goal.id}
          aria-label={`Goal: ${goal.text}`}
          className={`flex flex-col gap-2 rounded-lg border border-border p-3 ${goal.enabled ? "" : "opacity-60"}`}
        >
          <div className="flex flex-wrap items-start justify-between gap-2">
            <p className="min-w-0 flex-1 text-sm font-medium">
              &ldquo;{goal.text}&rdquo;
            </p>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={goal.enabled}
                onChange={(e) =>
                  void change(
                    api.PUT("/api/glasses/goals/{id}", {
                      params: { path: { id: goal.id } },
                      body: { enabled: e.target.checked },
                    }),
                    "Could not switch the goal",
                  )
                }
              />
              Watching
            </label>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() =>
                void change(
                  api.DELETE("/api/glasses/goals/{id}", {
                    params: { path: { id: goal.id } },
                  }),
                  "Could not remove the goal",
                )
              }
            >
              <Trash2 aria-hidden="true" /> Remove goal
            </Button>
          </div>
          {triggerRows(goal)}
        </article>
      ))}
      {!error && (goals ?? []).length === 0 && (
        <p className="text-sm text-muted-foreground">
          Nothing is being watched for yet.
        </p>
      )}
    </section>
  );
}

function TriggerRow({
  trigger,
  match,
  onSave,
  onDelete,
}: {
  trigger: Trigger;
  match: Match | undefined;
  onSave: (body: Draft) => void;
  onDelete: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(trigger);
  const changed = EDITED.some((key) => draft[key] !== trigger[key]);
  const set = (edit: Partial<Draft>) => setDraft({ ...draft, ...edit });
  // The level on the latest frame when streaming, else the one last measured.
  const level = match?.level ?? trigger.level;
  const score = match?.score ?? null;
  const reached = score !== null && level !== null && score >= level;
  return (
    <form
      className="flex flex-col gap-2 rounded-md border border-border bg-background/60 p-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          name: draft.name,
          watch_for: draft.watch_for,
          sigmas: draft.sigmas,
        });
      }}
    >
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          last fired{" "}
          {trigger.last_fired_at ? formatTime(trigger.last_fired_at) : "never"}
        </span>
        <span className={reached ? "font-medium text-primary" : ""}>
          {score !== null && `score ${score.toFixed(3)} · `}
          {level === null
            ? "level not measured yet"
            : `level ${level.toFixed(3)}`}
        </span>
      </div>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,2fr)_minmax(0,3fr)_minmax(0,1fr)]">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Title on the glasses
          <input
            className={field}
            value={draft.name}
            maxLength={60}
            onChange={(e) => set({ name: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Watch for
          <input
            className={field}
            value={draft.watch_for}
            maxLength={300}
            onChange={(e) => set({ watch_for: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          Spreads
          <input
            className={field}
            type="number"
            step={0.5}
            min={0}
            max={10}
            title="Level: this many spreads above the trigger's own noise"
            value={draft.sigmas ?? 3}
            onChange={(e) => set({ sigmas: Number(e.target.value) })}
          />
        </label>
      </div>
      <div className="flex gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={!changed || !draft.name.trim() || !draft.watch_for.trim()}
        >
          <Save aria-hidden="true" /> Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDelete}>
          <Trash2 aria-hidden="true" /> Drop
        </Button>
      </div>
    </form>
  );
}
