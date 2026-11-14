"use client";

import {
  History,
  LoaderCircle,
  Save,
  Sparkles,
  Trash2,
  Zap,
} from "lucide-react";
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

export type GlassesReply = components["schemas"]["GlassesReply"];

const RECALL_PROMPT = "Where have I seen this one?";
// The same questions the backend takes for déjà vu: a copy of _ASKS in
// backend/app/glasses/dejavu.py, so change the two together.
const RECALL =
  /deja vu|déjà vu|\b(know|remember|recogni[sz]e|seen|see) (this|that)( one| thing| place)?( before| already| from( somewhere| before)?)?\W*$/i;

const field =
  "w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus-visible:border-primary disabled:opacity-60";

// The fields of a trigger that can be edited, to tell whether a row has unsaved changes.
const EDITED: (keyof Draft)[] = ["name", "watch_for", "sigmas"];

/**
 * What your glasses watch out for. You say what you want watched, that is a goal, and Gemma
 * writes its triggers; each stream frame is then scored against the triggers of your enabled
 * goals, and one that reaches its level asks Gemma about the goal. A goal's triggers are switched
 * on and off and removed together. `lastMatches` (trigger id -> score and level on the latest
 * frame) shows how close each is. The list reloads on a new `firedCount`, so "last fired" and
 * the measured levels stay current, and each time it becomes `visible`, since Gemma may have
 * removed a goal in the chat meanwhile. A déjà vu question ("where have I seen this one?") asked
 * here goes to the glasses instead, and their reply to `onRecall`.
 */
export function TriggerEditor({
  lastMatches,
  firedCount = 0,
  visible = true,
  onRecall,
}: {
  lastMatches: Map<number, Match>;
  firedCount?: number;
  visible?: boolean;
  /** The glasses' reply to a déjà vu question, for the glasses view to show. */
  onRecall: (reply: GlassesReply) => void;
}) {
  const [goals, setGoals] = useState<Goal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [writing, setWriting] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data, error: problem } = await api.GET("/api/glasses/goals", {
        cache: "no-store",
      });
      if (problem || !data)
        setError(errorText(problem, "Could not load goals"));
      else setGoals(data);
    } catch {
      setError("Could not reach the backend");
    }
  }, []);

  useEffect(() => {
    // Loading when shown, and when a trigger fired, is the point of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (visible) void load();
  }, [load, firedCount, visible]);

  /** Déjà vu: asks, as the glasses would, where the newest stream frame was seen before. */
  async function recall(question: string) {
    setError(null);
    setWriting(true);
    try {
      const { data, error: problem } = await api.POST("/api/glasses/command", {
        body: { question },
      });
      if (problem || !data)
        return setError(errorText(problem, "Could not ask the glasses"));
      onRecall(data);
      setText("");
    } catch {
      setError("Could not reach the backend");
    } finally {
      setWriting(false);
    }
  }

  async function generate() {
    if (RECALL.test(text)) return recall(text.trim());
    setError(null);
    setWriting(true);
    try {
      const { error: problem } = await api.POST("/api/glasses/goals", {
        body: { text: text.trim() },
      });
      if (problem)
        return setError(errorText(problem, "Gemma could not write triggers"));
      setText("");
      await load();
    } catch {
      setError("Could not reach the backend");
    } finally {
      setWriting(false);
    }
  }

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
          Say what the glasses should watch out for and Gemma writes the
          triggers for that goal. They are yours alone. Each stream frame gets a
          score per trigger: how much more it looks like the &ldquo;watch
          for&rdquo; text than the last 20 seconds did. When a score reaches its
          level, Gemma looks at the frame with your goal in mind: it writes the
          message for the glasses, or finds a false alarm, says nothing and
          raises the level. After a message the stream is quiet for 5 seconds,
          so it can be read; after a false alarm for 2. To drop a goal, remove
          it here or ask Gemma to stop watching for it. Asking where you have
          seen this one instead looks up the newest stream frame among your
          earlier stills, and Gemma says whether any shows the same thing; the
          answer appears in the glasses view above.
        </p>
      </div>
      <form
        className="flex flex-col gap-2 rounded-lg border border-dashed border-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void generate();
        }}
      >
        <label
          htmlFor="trigger-goal"
          className="text-sm font-medium text-foreground"
        >
          What should the glasses watch out for?
        </label>
        <textarea
          id="trigger-goal"
          className={field}
          rows={2}
          value={text}
          maxLength={500}
          disabled={writing}
          placeholder="e.g. I'm allergic to nuts, warn me when I pick up food that might have them."
          onChange={(e) => setText(e.target.value)}
        />
        <Button type="submit" disabled={writing || !text.trim()}>
          {writing ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles aria-hidden="true" />
          )}
          {writing ? "Gemma is working…" : "Ask Gemma for triggers"}
        </Button>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="w-fit"
          disabled={writing}
          title="Looks up the newest stream frame among your earlier stills"
          onClick={() => void recall(RECALL_PROMPT)}
        >
          <History aria-hidden="true" /> {RECALL_PROMPT}
        </Button>
      </form>
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
          {goal.triggers.map((trigger) => (
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
          ))}
        </article>
      ))}
      {goals?.length === 0 && (
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
