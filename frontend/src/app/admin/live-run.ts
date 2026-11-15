"use client";

import {
  type RefObject,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from "react";
import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";
import type { Cue } from "../demos/cues";
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

/**
 * Plays a demo clip as if the glasses saw it: while the video runs, its frames go to
 * /api/glasses/frames (so they are stored and checked against the triggers) and each prompt goes
 * to Gemma (/api/ask/stream) when the video reaches it, so "tell me when…" starts a real goal
 * and "stop…" removes one. All of it happens on the logged-in account; the goals a run started
 * are removed when it stops, its stored frames stay in the glasses collection.
 */
export function useLiveRun(
  video: RefObject<HTMLVideoElement | null>,
  cues: Cue[],
) {
  const [live, setLive] = useState(false);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const before = useRef(new Set<number>()); // the goals that were there before the run
  const last = useRef(0); // the clip time up to which prompts have been said
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

  async function ownGoals(): Promise<Goal[]> {
    const { data } = await api.GET("/api/glasses/goals");
    return data ?? [];
  }

  async function say(text: string) {
    log("said", text);
    try {
      const response = await fetch("/api/ask/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
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
      setGoals(
        (await ownGoals()).filter(
          (g) => g.enabled && !before.current.has(g.id),
        ),
      );
    } catch {
      log("error", "Could not reach the backend");
    }
  }

  async function sendFrame() {
    const element = video.current;
    if (
      sending.current ||
      !element ||
      element.paused ||
      element.ended ||
      element.readyState < 2
    )
      return;
    sending.current = true;
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
      const response = await fetch("/api/glasses/frames", {
        method: "POST",
        body: form,
      });
      const body = await response.json().catch(() => null);
      if (!response.ok)
        return log(
          "error",
          errorText(body, `The backend answered ${response.status}`),
        );
      const { message } = body as FrameReply;
      if (message) log("trigger", `${message.title}: ${message.text}`);
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

  /** Just before `time`, so a prompt exactly there is still said. */
  const rewind = (time: number) => {
    last.current = time - 0.001;
  };

  return {
    live,
    events,
    goals,
    /** The video moved on to `time`: say the prompts it passed. */
    onTime(time: number) {
      if (!live) return;
      for (const cue of cues)
        if (cue.at_s > last.current && cue.at_s <= time && cue.text.trim())
          void say(cue.text.trim());
      last.current = time;
    },
    /** The video jumped: nothing between here and there was said. */
    onSeeking: rewind,
    async toggle() {
      if (!live) {
        before.current = new Set((await ownGoals()).map((g) => g.id));
        streamId.current = crypto.randomUUID();
        rewind(video.current?.currentTime ?? 0);
        setEvents([]);
        setGoals([]);
        return setLive(true);
      }
      setLive(false);
      setGoals([]);
      for (const goal of await ownGoals())
        if (!before.current.has(goal.id))
          await api.DELETE("/api/glasses/goals/{id}", {
            params: { path: { id: goal.id } },
          });
    },
  };
}
