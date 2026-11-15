"use client";

import { useRef, useState } from "react";
import { Plus, Radio, Square, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { type Cue, clock } from "../demos/cues";
import { CueOverlay, Overlay } from "../demos/cue-overlay";
import { useLiveRun } from "./live-run";

const field = "rounded border border-border bg-background px-2 py-1 text-sm";

/**
 * The prompts of one demo clip: scrub the video to where the wearer would say something, add the
 * prompt there, and say whether it stays active and what it stops. The preview shows what the
 * Demos page will; "Run live" plays the clip to the backend as the glasses would see and hear it
 * (live-run.ts), and shows what Gemma and the triggers really do.
 */
export function CueEditor({
  cues,
  videoUrl,
  disabled,
  onChange,
}: {
  cues: Cue[];
  /** The clip's video; without one (an image sequence, no upload yet) times are typed in. */
  videoUrl?: string;
  disabled: boolean;
  onChange: (cues: Cue[]) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [at, setAt] = useState(0);
  const run = useLiveRun(video, cues);
  const latest = run.events.at(-1);
  const patch = (id: string, change: Partial<Cue>) =>
    onChange(cues.map((c) => (c.id === id ? { ...c, ...change } : c)));
  return (
    <fieldset className="flex flex-col gap-3 rounded border border-border p-3">
      <legend className="px-1 text-sm">Prompts</legend>
      {videoUrl ? (
        <div className="showcase-media relative">
          <video
            ref={video}
            src={videoUrl}
            controls
            muted
            playsInline
            preload="metadata"
            onTimeUpdate={(e) => {
              setAt(e.currentTarget.currentTime);
              run.onTime(e.currentTarget.currentTime);
            }}
            onSeeked={(e) => {
              setAt(e.currentTarget.currentTime);
              run.onTime(e.currentTarget.currentTime);
            }}
          />
          {run.live ? (
            <Overlay
              active={run.goals.map((g) => ({ id: g.id, text: g.text }))}
              caption={latest?.text}
            />
          ) : (
            <CueOverlay cues={cues} at={at} />
          )}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || cues.length >= 100}
          onClick={() =>
            onChange([
              ...cues,
              {
                id: crypto.randomUUID().slice(0, 8),
                at_s: Math.round(at * 10) / 10,
                text: "",
                stays: true,
                stops: null,
              },
            ])
          }
        >
          <Plus aria-hidden="true" /> Add prompt at {clock(at)}
        </Button>
        {videoUrl ? (
          <Button
            variant={run.live ? "default" : "outline"}
            size="sm"
            disabled={disabled}
            onClick={() => void run.toggle()}
          >
            {run.live ? (
              <Square aria-hidden="true" />
            ) : (
              <Radio aria-hidden="true" />
            )}
            {run.live ? "Stop live run" : "Run live"}
          </Button>
        ) : null}
        {run.live ? (
          <span className="text-xs text-muted-foreground">
            Play or scrub the video: what it shows and the prompts it passes go
            to Gemma as your glasses would send them.
          </span>
        ) : null}
      </div>
      {run.events.length ? (
        <ol
          aria-label="Live run log"
          className="flex max-h-48 flex-col gap-1 overflow-y-auto text-xs"
        >
          {run.events.map((event) => (
            <li
              key={event.id}
              className={
                event.kind === "error" ? "text-destructive" : undefined
              }
            >
              <span className="font-mono text-muted-foreground">
                {clock(event.at)}
              </span>{" "}
              {
                {
                  said: "You",
                  answer: "Gemma",
                  trigger: "Trigger",
                  error: "Error",
                }[event.kind]
              }
              : {event.text}
            </li>
          ))}
        </ol>
      ) : null}
      {cues.length ? (
        <ol className="flex flex-col gap-2">
          {cues.map((cue, index) => (
            <li
              key={cue.id}
              className="grid items-center gap-2 border-b border-border pb-2 sm:grid-cols-[80px_1fr_auto_170px_auto]"
            >
              <input
                className={field}
                type="number"
                min={0}
                max={3600}
                step={0.1}
                value={cue.at_s}
                disabled={disabled}
                aria-label={`Prompt ${index + 1} time in seconds`}
                onChange={(e) =>
                  patch(cue.id, { at_s: Number(e.target.value) })
                }
                onFocus={() => {
                  if (video.current) video.current.currentTime = cue.at_s;
                }}
              />
              <input
                className={field}
                value={cue.text}
                maxLength={300}
                placeholder="Tell me when my bus comes"
                disabled={disabled}
                aria-label={`Prompt ${index + 1} text`}
                onChange={(e) => patch(cue.id, { text: e.target.value })}
              />
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={cue.stays}
                  disabled={disabled}
                  onChange={(e) => patch(cue.id, { stays: e.target.checked })}
                />
                Stays active
              </label>
              <select
                className={field}
                value={cue.stops ?? ""}
                disabled={disabled}
                aria-label={`What prompt ${index + 1} stops`}
                onChange={(e) =>
                  // A prompt that stops something is rarely a trigger itself.
                  patch(cue.id, {
                    stops: e.target.value || null,
                    ...(e.target.value ? { stays: false } : {}),
                  })
                }
              >
                <option value="">Stops nothing</option>
                <option value="*">Stops all earlier</option>
                {cues
                  .filter(
                    (c) => c.id !== cue.id && c.stays && c.at_s <= cue.at_s,
                  )
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      Stops: {c.text || clock(c.at_s)}
                    </option>
                  ))}
              </select>
              <Button
                size="icon-sm"
                variant="ghost"
                disabled={disabled}
                aria-label={`Remove prompt ${index + 1}`}
                onClick={() =>
                  onChange(
                    cues
                      .filter((c) => c.id !== cue.id)
                      .map((c) =>
                        c.stops === cue.id ? { ...c, stops: null } : c,
                      ),
                  )
                }
              >
                <Trash2 aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-xs text-muted-foreground">
          No prompts yet. Pause where the wearer would speak and add one; it is
          saved with “Save changes”.
        </p>
      )}
    </fieldset>
  );
}
