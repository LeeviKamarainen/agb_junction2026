"use client";

import { LoaderCircle, Save, Sparkles, Trash2, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";

import { errorText, formatTime } from "./types";

export type Trigger = components["schemas"]["TriggerPublic"];
type Draft = components["schemas"]["TriggerCreate"];
/** A trigger's score on the latest stream frame, and the level it has to reach. */
export type Match = { score: number | null; level: number | null };

const field =
  "w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus-visible:border-primary disabled:opacity-60";

// The fields of a trigger that can be edited, to tell whether a row has unsaved changes.
const EDITED: (keyof Draft)[] = [
  "name",
  "watch_for",
  "instruction",
  "sigmas",
  "enabled",
];

/**
 * What the glasses watch for. The wearer says what they want watched and Gemma writes the
 * triggers; each stream frame is then scored against every enabled trigger's "watch for" text,
 * and one that reaches its level fires a message on the glasses. `lastMatches` (trigger id ->
 * score and level on the latest frame) shows how close each is; a new `firedCount` reloads the
 * list, so "last fired" and the measured levels stay current.
 */
export function TriggerEditor({
  lastMatches,
  firedCount = 0,
}: {
  lastMatches: Map<number, Match>;
  firedCount?: number;
}) {
  const [triggers, setTriggers] = useState<Trigger[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [goal, setGoal] = useState("");
  const [writing, setWriting] = useState(false);

  const load = useCallback(async () => {
    const { data, error: problem } = await api.GET("/api/glasses/triggers", {
      cache: "no-store",
    });
    if (problem || !data)
      setError(errorText(problem, "Could not load triggers"));
    else setTriggers(data);
  }, []);

  useEffect(() => {
    // Loading on mount, and when a trigger fired, is the point of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load, firedCount]);

  async function generate() {
    setError(null);
    setWriting(true);
    try {
      const { error: problem } = await api.POST(
        "/api/glasses/triggers/generate",
        { body: { goal: goal.trim() } },
      );
      if (problem)
        return setError(errorText(problem, "Gemma could not write triggers"));
      setGoal("");
      await load();
    } catch {
      setError("Could not reach the backend");
    } finally {
      setWriting(false);
    }
  }

  async function save(id: number, body: Draft) {
    setError(null);
    const { error: problem } = await api.PUT("/api/glasses/triggers/{id}", {
      params: { path: { id } },
      body,
    });
    if (problem)
      return setError(errorText(problem, "Could not save the trigger"));
    await load();
  }

  async function remove(id: number) {
    setError(null);
    const { error: problem } = await api.DELETE("/api/glasses/triggers/{id}", {
      params: { path: { id } },
    });
    if (problem) setError(errorText(problem, "Could not delete the trigger"));
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
          <Zap size={16} aria-hidden="true" /> Triggers
        </h2>
        <p className="text-xs text-muted-foreground">
          Say what the glasses should watch out for and Gemma writes the
          triggers. Each stream frame gets a score per trigger: how much more it
          looks like the &ldquo;watch for&rdquo; text than the last 20 seconds
          did. When the score reaches the trigger&rsquo;s level, Gemma looks at
          the frame: it writes the message for the glasses, or finds a false
          alarm, says nothing and raises the level. After a message the stream
          is quiet for 5 seconds, so it can be read; after a false alarm for 2.
          A level is measured from the trigger&rsquo;s own noise after about ten
          frames of streaming.
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
          value={goal}
          maxLength={500}
          disabled={writing}
          placeholder="e.g. I'm allergic to nuts, warn me when I pick up food that might have them."
          onChange={(e) => setGoal(e.target.value)}
        />
        <Button type="submit" disabled={writing || !goal.trim()}>
          {writing ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Sparkles aria-hidden="true" />
          )}
          {writing ? "Gemma is writing triggers…" : "Ask Gemma for triggers"}
        </Button>
      </form>
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      {triggers?.map((trigger) => (
        <TriggerRow
          key={trigger.id}
          trigger={trigger}
          match={lastMatches.get(trigger.id)}
          onSave={(body) => save(trigger.id, body)}
          onDelete={() => remove(trigger.id)}
        />
      ))}
      {triggers?.length === 0 && (
        <p className="text-sm text-muted-foreground">No triggers yet.</p>
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
  // The level on the latest frame when streaming, else the one last measured.
  const level = match?.level ?? trigger.level;
  const score = match?.score ?? null;
  const reached = score !== null && level !== null && score >= level;
  return (
    <form
      className="flex flex-col gap-2 rounded-lg border border-border bg-background/60 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(draft);
      }}
    >
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          #{trigger.id} · last fired{" "}
          {trigger.last_fired_at ? formatTime(trigger.last_fired_at) : "never"}
        </span>
        <span className={reached ? "font-medium text-primary" : ""}>
          {score !== null && `score ${score.toFixed(3)} · `}
          {level === null
            ? "level not measured yet"
            : `level ${level.toFixed(3)}`}
        </span>
      </div>
      <Fields draft={draft} onChange={setDraft} />
      <div className="flex gap-2">
        <Button type="submit" size="sm" disabled={!changed}>
          <Save aria-hidden="true" /> Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDelete}>
          <Trash2 aria-hidden="true" /> Delete
        </Button>
      </div>
    </form>
  );
}

function Fields({
  draft,
  onChange,
}: {
  draft: Draft;
  onChange: (draft: Draft) => void;
}) {
  const set = (change: Partial<Draft>) => onChange({ ...draft, ...change });
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Name (the title on the glasses)
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
          placeholder="e.g. a set of keys"
          onChange={(e) => set({ watch_for: e.target.value })}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground sm:col-span-2">
        Instruction for Gemma
        <textarea
          className={field}
          rows={2}
          value={draft.instruction}
          maxLength={1000}
          placeholder="e.g. Tell the wearer where their keys are."
          onChange={(e) => set({ instruction: e.target.value })}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Level: spreads above its noise
        <input
          className={field}
          type="number"
          step={0.5}
          min={0}
          max={10}
          value={draft.sigmas ?? 3}
          onChange={(e) => set({ sigmas: Number(e.target.value) })}
        />
      </label>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={draft.enabled ?? true}
          onChange={(e) => set({ enabled: e.target.checked })}
        />
        Enabled
      </label>
    </div>
  );
}
