"use client";

import {
  type RefObject,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from "react";
import type { components } from "@/lib/api/schema";
import type { Cue } from "@/components/glasses-view/cues";
import { errorText } from "../memory/types";

type FrameReply = components["schemas"]["FrameReply"];
type Goal = components["schemas"]["GoalPublic"];
export type LiveEvent = {
  id: number;
  at: number; // seconds into the clip
  kind: "said" | "answer" | "trigger" | "error";
  text: string;
};

const FPS = 2; // as the glasses stream
const noChange = { stays: false, stops: null };

/**
 * Records what Gemma does with a demo clip, so that playback can show it without asking again.
 * While recording, the frame on screen goes to /api/glasses/frames twice a second as the video
 * plays, and each prompt goes to Gemma (/api/ask/stream) once the video is at or past it, so
 * "tell me when…" starts a real goal and "stop…" removes one. Gemma's answer to a prompt goes to
 * `onAnswer`, and the message of a trigger that fired to `onTrigger` with the clip time of the
 * frame it fired on. With an answer comes what the prompt did to the guest's goals: whether it
 * started one (it `stays` active) and which earlier prompt's goal it removed (it `stops` that).
 * The video pauses whenever Gemma says something, an answer or a trigger, to be read.
 *
 * None of it touches the admin's account: a recording runs as a guest made for it (the requests
 * carry its token and no cookie), which sees only the demo memories and what it adds, and is
 * deleted with its goals, frames and vectors when the recording stops. One left behind, by a
 * closed tab say, expires like any guest.
 *
 * Scrubbing says the prompts passed on the way, each once a recording and in order, and sends
 * the one frame landed on; a paused video sends nothing else. A trigger fires on what has just
 * come into view, measured against the last 20 seconds of frames (glasses/scores.py), so play
 * the clip through for a recording that matches what the glasses would do.
 */
export function useRecording(
  video: RefObject<HTMLVideoElement | null>,
  cues: Cue[],
  onAnswer: (
    cueId: string,
    answer: string,
    did: { stays: boolean; stops: string | null },
  ) => void,
  onTrigger: (at: number, message: string) => void,
) {
  const [live, setLive] = useState(false);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const token = useRef(""); // the recording's guest
  const startedBy = useRef(new Map<number, string>()); // goal id: the prompt that started it
  const said = useRef(new Set<string>()); // the prompts already said in this recording
  const speaking = useRef(Promise.resolve()); // prompts go to Gemma one after another
  const sending = useRef(false); // one frame at a time; frames due meanwhile are skipped
  const streamId = useRef("");
  const eventId = useRef(0);

  function log(kind: LiveEvent["kind"], text: string) {
    const at = video.current?.currentTime ?? 0;
    const id = eventId.current++;
    // A failing backend fails every frame: say so once, not twice a second.
    setEvents((e) =>
      kind === "error" && e.some((earlier) => earlier.text === text)
        ? e
        : [...e, { id, at, kind, text }],
    );
  }

  /** A request as the recording's guest: its token, and never the admin's cookie. */
  const asGuest = (path: string, init: RequestInit = {}) =>
    fetch(path, {
      ...init,
      credentials: "omit",
      headers: { ...init.headers, Authorization: `Bearer ${token.current}` },
    });

  async function say(cue: Cue) {
    const text = cue.text.trim();
    log("said", text);
    try {
      const response = await asGuest("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      if (!token.current) return; // the recording stopped meanwhile
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        return log(
          "error",
          errorText(body, `The backend answered ${response.status}`),
        );
      }
      // Newline-delimited JSON steps; only how it ended matters here.
      const steps = (await response.text())
        .split("\n")
        .filter((line) => line.trim())
        .map((line) => JSON.parse(line) as { type: string; content?: string });
      const end = steps.findLast(
        (step) => step.type === "answer" || step.type === "error",
      );
      log(
        end?.type === "answer" ? "answer" : "error",
        end?.content ?? "The stream ended without an answer.",
      );
      video.current?.pause();
      if (end?.type !== "answer" || !end.content) return;
      const reply = await asGuest("/api/glasses/goals");
      if (!reply.ok) return onAnswer(cue.id, end.content, noChange);
      const now = ((await reply.json()) as Goal[]).filter((g) => g.enabled);
      setGoals(now);
      // What this prompt did: the goals that are new, and the ones that are gone.
      const known = startedBy.current;
      const fresh = now.filter((g) => !known.has(g.id));
      const gone = [...known.keys()].filter(
        (id) => !now.some((g) => g.id === id),
      );
      const stopped = new Set(gone.map((id) => known.get(id) as string));
      gone.forEach((id) => known.delete(id));
      fresh.forEach((g) => known.set(g.id, cue.id));
      onAnswer(cue.id, end.content, {
        stays: fresh.length > 0,
        stops: stopped.size > 1 ? "*" : ([...stopped][0] ?? null),
      });
    } catch {
      log("error", "Could not reach the backend");
    }
  }

  /** The frame on screen, when the video plays; `always` for the one a scrub landed on. */
  async function sendFrame(always = false) {
    const element = video.current;
    if (sending.current || !element || element.readyState < 2) return;
    if (!always && (element.paused || element.ended)) return;
    sending.current = true;
    const at = element.currentTime;
    try {
      const canvas = document.createElement("canvas");
      // The glasses stream at 504x896; send about that much.
      const scale = Math.min(
        1,
        896 / Math.max(element.videoWidth, element.videoHeight),
      );
      canvas.width = Math.round(element.videoWidth * scale);
      canvas.height = Math.round(element.videoHeight * scale);
      canvas
        .getContext("2d")
        ?.drawImage(element, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.85),
      );
      if (!blob) return;
      const form = new FormData();
      form.append("frame", blob, "frame.jpg");
      form.append("source", "stream");
      form.append("stream_id", streamId.current);
      form.append("seen_at", new Date().toISOString());
      const response = await asGuest("/api/glasses/frames", {
        method: "POST",
        body: form,
      });
      const body = await response.json().catch(() => null);
      if (!token.current) return; // the recording stopped meanwhile
      if (!response.ok)
        return log(
          "error",
          errorText(body, `The backend answered ${response.status}`),
        );
      const { message } = body as FrameReply;
      if (message) {
        log("trigger", `${message.title}: ${message.text}`);
        onTrigger(at, `${message.title}: ${message.text}`);
        video.current?.pause();
      }
    } catch {
      log("error", "Could not reach the backend");
    } finally {
      sending.current = false;
    }
  }

  const tick = useEffectEvent(() => void sendFrame());
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => tick(), 1000 / FPS);
    return () => clearInterval(timer);
  }, [live]);

  /** Say the prompts up to `time` that this run has not said yet, in the clip's order. */
  function sayUpTo(time: number) {
    for (const cue of [...cues].sort((a, b) => a.at_s - b.at_s)) {
      if (cue.kind === "trigger" || cue.at_s > time || !cue.text.trim())
        continue;
      if (said.current.has(cue.id)) continue;
      said.current.add(cue.id);
      speaking.current = speaking.current.then(() => say(cue));
    }
  }

  return {
    live,
    /** Say a prompt now, whatever its time: one typed in during the recording. */
    ask(cue: Cue) {
      said.current.add(cue.id);
      speaking.current = speaking.current.then(() => say(cue));
    },
    events,
    goals,
    /** The video played on to `time`. */
    onTime(time: number) {
      if (live) sayUpTo(time);
    },
    /** The video was scrubbed to `time`. */
    onSeeked(time: number) {
      if (!live) return;
      sayUpTo(time);
      void sendFrame(true);
    },
    async toggle() {
      if (live) {
        setLive(false);
        setGoals([]);
        // Deletes the guest with all it added; failing that, it expires on its own.
        const ended = asGuest("/api/auth/guest", { method: "DELETE" });
        token.current = ""; // what is still on its way back is dropped
        await ended.catch(() => {});
        return;
      }
      setEvents([]);
      setGoals([]);
      said.current = new Set();
      startedBy.current = new Map();
      try {
        // Without credentials the guest's cookie is not kept: the admin stays logged in.
        const response = await fetch("/api/auth/guest", {
          method: "POST",
          credentials: "omit",
        });
        if (!response.ok) throw new Error();
        token.current = ((await response.json()) as { token: string }).token;
      } catch {
        return log("error", "Could not start a guest account to record with");
      }
      streamId.current = crypto.randomUUID();
      setLive(true);
      sayUpTo(video.current?.currentTime ?? 0);
    },
  };
}
