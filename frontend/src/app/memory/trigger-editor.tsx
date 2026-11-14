"use client";

import { Plus, Save, Trash2, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";

import { errorText, formatTime } from "./types";

export type Trigger = components["schemas"]["TriggerPublic"];
type Draft = components["schemas"]["TriggerCreate"];

const field =
  "w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm outline-none focus-visible:border-primary disabled:opacity-60";

const EMPTY: Draft = {
  name: "",
  watch_for: "",
  instruction: "",
  max_distance: 0.75,
  cooldown_s: 120,
  enabled: true,
};

/**
 * What the glasses watch for: each frame they send is compared with every enabled trigger's
 * "watch for" text, and the closest one within its distance fires a message on the glasses.
 * `lastDistances` (trigger id -> distance from the latest frame) helps pick a distance; a new
 * `firedCount` reloads the list, so "last fired" stays current.
 */
export function TriggerEditor({
  lastDistances,
  firedCount = 0,
}: {
  lastDistances: Map<number, number>;
  firedCount?: number;
}) {
  const [triggers, setTriggers] = useState<Trigger[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);

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

  async function save(id: number | null, body: Draft) {
    setError(null);
    const { error: problem } =
      id === null
        ? await api.POST("/api/glasses/triggers", { body })
        : await api.PUT("/api/glasses/triggers/{id}", {
            params: { path: { id } },
            body,
          });
    if (problem)
      return setError(errorText(problem, "Could not save the trigger"));
    if (id === null) setDraft(EMPTY);
    await load();
  }

  async function remove(id: number) {
    await api.DELETE("/api/glasses/triggers/{id}", {
      params: { path: { id } },
    });
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
          Every frame is compared with each trigger&rsquo;s &ldquo;watch
          for&rdquo; text. The closest within its distance (0 identical, 1
          unrelated) fires once per cooldown, and Gemma writes the message from
          the frame and the instruction. The latest frame&rsquo;s distance shows
          next to each, to help pick one.
        </p>
      </div>
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      {triggers?.map((trigger) => (
        <TriggerRow
          key={trigger.id}
          trigger={trigger}
          distance={lastDistances.get(trigger.id)}
          onSave={(body) => save(trigger.id, body)}
          onDelete={() => remove(trigger.id)}
        />
      ))}
      {triggers?.length === 0 && (
        <p className="text-sm text-muted-foreground">No triggers yet.</p>
      )}
      <form
        className="flex flex-col gap-2 rounded-lg border border-dashed border-border p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void save(null, draft);
        }}
      >
        <p className="text-sm font-medium">New trigger</p>
        <Fields draft={draft} onChange={setDraft} />
        <Button
          type="submit"
          variant="secondary"
          disabled={
            !draft.name.trim() ||
            !draft.watch_for.trim() ||
            !draft.instruction.trim()
          }
        >
          <Plus aria-hidden="true" /> Add trigger
        </Button>
      </form>
    </section>
  );
}

function TriggerRow({
  trigger,
  distance,
  onSave,
  onDelete,
}: {
  trigger: Trigger;
  distance: number | undefined;
  onSave: (body: Draft) => void;
  onDelete: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(trigger);
  const changed = (Object.keys(EMPTY) as (keyof Draft)[]).some(
    (key) => draft[key] !== trigger[key],
  );
  const within = distance !== undefined && distance <= trigger.max_distance;
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
        {distance !== undefined && (
          <span className={within ? "font-medium text-primary" : ""}>
            latest frame: {distance.toFixed(3)}
          </span>
        )}
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
        Max distance
        <input
          className={field}
          type="number"
          step={0.01}
          min={0}
          max={2}
          value={draft.max_distance ?? 0.75}
          onChange={(e) => set({ max_distance: Number(e.target.value) })}
        />
      </label>
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Cooldown (s)
        <input
          className={field}
          type="number"
          min={0}
          max={86400}
          value={draft.cooldown_s ?? 120}
          onChange={(e) => set({ cooldown_s: Number(e.target.value) })}
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
