"use client";

import { useRef, useState } from "react";
import {
  Bell,
  Circle,
  Download,
  Plus,
  Scissors,
  Send,
  Square,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { type Cue, clock } from "@/components/glasses-view/cues";
import { CueOverlay, Overlay } from "@/components/glasses-view/cue-overlay";
import { useRecording } from "./recording";

const field = "rounded border border-border bg-background px-2 py-1 text-sm";

/**
 * The prompts of one demo clip: scrub the video to where the wearer would say something, add the
 * prompt there, and say whether it stays active and when or by what it stops. The preview shows
 * what the Demos page will. "Record with Gemma" plays the clip to the backend as the glasses
 * would see and hear it (recording.ts) and writes what came back into the prompts: Gemma's
 * answer to each, whether it started or stopped a trigger, and a row for each time one fired.
 * While recording, a prompt can also be typed in at the video's position, as the wearer would
 * say it there. Stopping saves the demo and downloads the prompts and answers as a file.
 */
export function CueEditor({
  cues,
  videoUrl,
  disabled,
  onChange,
  onRecord,
  onRecorded,
  onExport,
  onEndAt,
}: {
  cues: Cue[];
  /** The clip's video; without one (an image sequence, no upload yet) times are typed in. */
  videoUrl?: string;
  disabled: boolean;
  onChange: (cues: Cue[]) => void;
  /** A recording has something to write: applied to the cues as they are by then. */
  onRecord: (update: (cues: Cue[]) => Cue[]) => void;
  /** A recording was stopped: save the demo and export it. */
  onRecorded: () => void;
  /** Download the prompts and answers as a file. */
  onExport: () => void;
  /** Crop the demo to end at this time of its video. */
  onEndAt: (seconds: number) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [at, setAt] = useState(0);
  const [saying, setSaying] = useState("");
  const run = useRecording(
    video,
    cues,
    (id, answer, did) =>
      onRecord((all) =>
        all.map((c) => (c.id === id ? { ...c, answer, ...did } : c)),
      ),
    (time, message) =>
      onRecord((all) => [
        ...all,
        {
          id: crypto.randomUUID().slice(0, 8),
          at_s: Math.round(time * 10) / 10,
          text: message.slice(0, 300),
          stays: false,
          kind: "trigger",
        },
      ]),
  );
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
              run.onSeeked(e.currentTarget.currentTime);
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
                until_s: null,
                answer: "",
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
            onClick={() => {
              // A new recording replaces the trigger rows of the last one.
              if (!run.live)
                onRecord((all) => all.filter((c) => c.kind !== "trigger"));
              void run.toggle();
              if (run.live) onRecorded();
            }}
          >
            {run.live ? (
              <Square aria-hidden="true" />
            ) : (
              <Circle aria-hidden="true" />
            )}
            {run.live ? "Stop recording" : "Record with Gemma"}
          </Button>
        ) : null}
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || !cues.length}
          onClick={onExport}
        >
          <Download aria-hidden="true" /> Export prompts
        </Button>
        {videoUrl ? (
          <Button
            variant="outline"
            size="sm"
            disabled={disabled || at < 0.1}
            onClick={() => onEndAt(Math.round(at * 10) / 10)}
          >
            <Scissors aria-hidden="true" /> End demo at {clock(at)}
          </Button>
        ) : null}
      </div>
      {run.live ? (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const text = saying.trim();
            if (!text) return;
            const cue: Cue = {
              id: crypto.randomUUID().slice(0, 8),
              at_s: Math.round((video.current?.currentTime ?? at) * 10) / 10,
              text,
              stays: false,
              stops: null,
              until_s: null,
              answer: "",
            };
            onRecord((all) => [...all, cue]);
            run.ask(cue);
            setSaying("");
          }}
        >
          <input
            className={`${field} min-w-0 flex-1`}
            value={saying}
            maxLength={300}
            placeholder={`Say something to Gemma at ${clock(at)}`}
            aria-label="Say something to Gemma at the video's position"
            onChange={(e) => setSaying(e.target.value)}
          />
          <Button type="submit" size="sm" disabled={!saying.trim()}>
            <Send aria-hidden="true" /> Say
          </Button>
          <span className="basis-full text-xs text-muted-foreground">
            Recording as a throwaway guest: nothing is kept on your account. The
            video pauses when Gemma answers or a trigger fires. Stopping saves
            the demo and downloads the prompts.
          </span>
        </form>
      ) : null}
      {run.events.length ? (
        <ol
          aria-label="Recording log"
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
          {cues.map((cue, index) => {
            const start = (
              <input
                className={field}
                type="number"
                min={0}
                max={3600}
                step={0.1}
                value={cue.at_s}
                disabled={disabled}
                title="Start (seconds)"
                aria-label={`Prompt ${index + 1} start time in seconds`}
                onChange={(e) =>
                  patch(cue.id, { at_s: Number(e.target.value) })
                }
                onFocus={() => {
                  if (video.current) video.current.currentTime = cue.at_s;
                }}
              />
            );
            const remove = (
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
            );
            const text = (
              <input
                className={`${field} min-w-0`}
                value={cue.text}
                maxLength={300}
                placeholder="Tell me when my bus comes"
                disabled={disabled}
                aria-label={`Prompt ${index + 1} text`}
                onChange={(e) => patch(cue.id, { text: e.target.value })}
              />
            );
            // What a trigger showed when it fired: recorded, with only its time and words.
            if (cue.kind === "trigger")
              return (
                <li
                  key={cue.id}
                  className="grid items-center gap-2 border-b border-border pb-2 sm:grid-cols-[70px_auto_1fr_auto]"
                >
                  {start}
                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    <Bell size={12} aria-hidden="true" /> Trigger fired
                  </span>
                  {text}
                  {remove}
                </li>
              );
            return (
              <li
                key={cue.id}
                className="grid items-center gap-2 border-b border-border pb-2 sm:grid-cols-[70px_70px_1fr_auto_160px_auto]"
              >
                {start}
                <input
                  className={field}
                  type="number"
                  min={0}
                  max={3600}
                  step={0.1}
                  value={cue.until_s ?? ""}
                  placeholder="stop"
                  disabled={disabled}
                  title="Stop (seconds): when it is no longer active. Empty: until another prompt stops it."
                  aria-label={`Prompt ${index + 1} stop time in seconds`}
                  onChange={(e) =>
                    patch(cue.id, {
                      until_s:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
                {text}
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
                {remove}
                <input
                  className={`${field} sm:col-span-6`}
                  value={cue.answer ?? ""}
                  maxLength={1000}
                  placeholder="Gemma's answer, shown under the prompt. Record with Gemma fills it in."
                  disabled={disabled}
                  aria-label={`Prompt ${index + 1} answer`}
                  onChange={(e) => patch(cue.id, { answer: e.target.value })}
                />
              </li>
            );
          })}
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
