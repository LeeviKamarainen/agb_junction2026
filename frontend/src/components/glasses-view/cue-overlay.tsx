import { Bot, Eye, Mic } from "lucide-react";
import { type Cue, cueState } from "./cues";

/** Over a demo's media (a `relative` box): what is being watched for, what was just said and
 * Gemma's answer to it. */
export function Overlay({
  active,
  caption,
  answer,
}: {
  active: { id: string | number; text: string }[];
  caption?: string | null;
  answer?: string | null;
}) {
  return (
    <div
      aria-live="polite"
      className="pointer-events-none absolute inset-0 flex flex-col justify-between p-3 text-sm text-white"
    >
      <ul className="flex flex-col items-start gap-1">
        {active.map((item) => (
          <li
            key={item.id}
            className="flex max-w-full items-center gap-1.5 rounded-full bg-black/70 px-2.5 py-1 text-xs"
          >
            <Eye size={12} aria-hidden="true" className="shrink-0" />
            <span className="truncate">{item.text}</span>
          </li>
        ))}
      </ul>
      {caption || answer ? (
        <div className="mb-10 flex max-w-full flex-col gap-1.5 self-center rounded-lg bg-black/75 px-3 py-2">
          {caption ? (
            <p className="flex items-center gap-2">
              <Mic size={14} aria-hidden="true" className="shrink-0" />
              {caption}
            </p>
          ) : null}
          {answer ? (
            <p className="flex items-start gap-2">
              <Bot size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
              {answer}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** The scripted prompts at `at` seconds: the ones active now, and the one being said. */
export function CueOverlay({ cues, at }: { cues: Cue[]; at: number }) {
  const { active, spoken } = cueState(cues, at);
  return (
    <Overlay
      active={active}
      caption={spoken && spoken.kind !== "trigger" ? `“${spoken.text}”` : null}
      answer={spoken?.kind === "trigger" ? spoken.text : spoken?.answer}
    />
  );
}
