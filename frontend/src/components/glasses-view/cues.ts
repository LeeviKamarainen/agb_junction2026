// A demo clip's prompts: what the wearer says at a time in the clip, and the answer Gemma gave,
// recorded beforehand. One that `stays` is active (a trigger, say) from then on, until `until_s`
// or until a later prompt `stops` it: by id, or "*" for all before. A cue of kind "trigger" is
// not said by the wearer: it is what the glasses showed when a trigger fired there.

export type Cue = {
  id: string;
  at_s: number;
  text: string;
  stays: boolean;
  stops?: string | null;
  until_s?: number | null;
  answer?: string;
  kind?: "prompt" | "trigger";
};

export const SPOKEN_S = 4; // how long a prompt is shown as being said,
export const ANSWERED_S = 8; // how long with Gemma's answer under it, or a trigger's message

/** At `at` seconds into the clip: the prompts still active, and the one being said, if any. */
export function cueState(cues: Cue[], at: number) {
  let active: Cue[] = [];
  let spoken: Cue | null = null;
  for (const cue of [...cues].sort((a, b) => a.at_s - b.at_s)) {
    if (cue.at_s > at) break;
    if (cue.stops === "*") active = [];
    else if (cue.stops) active = active.filter((c) => c.id !== cue.stops);
    if (cue.stays) active.push(cue);
    const shown = cue.answer || cue.kind === "trigger" ? ANSWERED_S : SPOKEN_S;
    spoken = at - cue.at_s < shown ? cue : null;
  }
  active = active.filter((cue) => cue.until_s == null || at < cue.until_s);
  return { active, spoken };
}

export const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
