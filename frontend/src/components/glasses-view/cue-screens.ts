// What the glasses' display shows while a demo clip plays its scripted cues (cues.ts): a prompt
// is first heard (the wearer's words with the recording light), then answered under the same
// words (answerScreen); a trigger cue shows the message the glasses showed when it fired
// (messageScreen). The extensions keep this importable by Node's test runner.

import type { FlexBoxNode } from "@/app/glasses/types";

import { ANSWERED_S, type Cue, cueState, SPOKEN_S } from "./cues.ts";
import { answerScreen, messageScreen, plain } from "./screens.ts";

/** How long a prompt's words show alone, with the recording light, before the answer appears:
 * as if the glasses were still hearing them. A named assumption, about a short question's length. */
export const HEARD_S = 1.2;

export type CueView = {
  /** What is on the display, as `<cue id>:<phase>`; null when it is clear. */
  shown: string | null;
  screen: FlexBoxNode | null;
  /** The wearer is speaking: the display's recording light. */
  listening: boolean;
  /** The prompts being watched for (those that stay), once their answer is on the display. */
  watching: Cue[];
  /** What to say aloud as `shown` appears, or null. */
  speech: string | null;
  /** Changes exactly when anything above does: compare it instead of the objects. */
  key: string;
};

/** A trigger cue's text, "Title: text", as the cue recorder writes it (admin/recording.ts). */
export function triggerMessage(text: string): { title: string; text: string } {
  const split = text.indexOf(": ");
  if (split < 0) return { title: "", text };
  return { title: text.slice(0, split), text: text.slice(split + 2) };
}

/** What the display shows `at` seconds into a clip with these cues. */
export function cueView(cues: Cue[], at: number): CueView {
  const { active, spoken } = cueState(cues, at);
  const heard = spoken !== null && spoken.kind !== "trigger" && at - spoken.at_s < HEARD_S;
  let shown: string | null = null;
  let screen: FlexBoxNode | null = null;
  let speech: string | null = null;
  if (spoken?.kind === "trigger") {
    const message = triggerMessage(spoken.text);
    shown = `${spoken.id}:message`;
    screen = messageScreen({ type: "trigger", ...message });
    speech = message.title ? `${message.title}. ${message.text}` : message.text;
  } else if (spoken) {
    const answered = answerScreen(spoken.text, spoken.answer ?? "");
    if (heard || !spoken.answer) {
      shown = `${spoken.id}:heard`;
      // The answer's screen without the answer: the answer then appears under the words.
      screen = { ...answered, children: answered.children.slice(0, 1) };
    } else {
      shown = `${spoken.id}:answer`;
      screen = answered;
      speech = plain(spoken.answer);
    }
  }
  const watching = active.filter((cue) => !(heard && cue === spoken));
  return {
    shown,
    screen,
    listening: heard,
    watching,
    speech,
    key: [shown, heard, ...watching.map((cue) => cue.id)].join(" "),
  };
}

/** When the display clears after the clip's last cue, as cueState decides: a clip that ends
 * sooner holds its last frame until then, so its last answer is seen in full. */
export function cuesEnd(cues: Cue[]): number {
  const last = [...cues].sort((a, b) => a.at_s - b.at_s).at(-1);
  if (!last) return 0;
  return last.at_s + (last.answer || last.kind === "trigger" ? ANSWERED_S : SPOKEN_S);
}
