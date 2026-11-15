// A demo clip's prompts: what the wearer says at a time in the clip. One that `stays` is active
// (a trigger, say) from then on, until a later prompt `stops` it: by id, or "*" for all before.

export type Cue = {
  id: string;
  at_s: number;
  text: string;
  stays: boolean;
  stops?: string | null;
};

export const SPOKEN_S = 4; // how long a prompt is shown as being said

/** At `at` seconds into the clip: the prompts still active, and the one being said, if any. */
export function cueState(cues: Cue[], at: number) {
  let active: Cue[] = [];
  let spoken: Cue | null = null;
  for (const cue of [...cues].sort((a, b) => a.at_s - b.at_s)) {
    if (cue.at_s > at) break;
    if (cue.stops === "*") active = [];
    else if (cue.stops) active = active.filter((c) => c.id !== cue.stops);
    if (cue.stays) active.push(cue);
    spoken = at - cue.at_s < SPOKEN_S ? cue : null;
  }
  return { active, spoken };
}

export const clock = (seconds: number) =>
  `${Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0")}:${Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0")}`;
