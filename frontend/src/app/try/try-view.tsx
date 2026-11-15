"use client";

import {
  Camera,
  Eye,
  LoaderCircle,
  Pause,
  Play,
  RotateCcw,
  SendHorizontal,
  SwitchCamera,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useEffectEvent, useRef, useState } from "react";

import { clock } from "@/components/glasses-view/cues";
import { type Evidence, GlassesView } from "@/components/glasses-view/glasses-view";
import { FPS, RECALL } from "@/components/glasses-view/glasses";
import {
  answerScreen,
  MESSAGE_S,
  messageScreen,
  plain,
  secondsToRead,
} from "@/components/glasses-view/screens";
import { HoldToTalk } from "@/components/hold-to-talk";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";
import { ensureSession, useSession } from "@/lib/session";

import { say } from "../glasses/speech";
import type { FlexBoxNode } from "../glasses/types";
import { decode } from "../memory/pca";
import { errorText } from "../memory/types";
import { MemoryTrail, type TrailPoint } from "./memory-trail";
import { REVEAL_SLACK_S } from "./trail";
import { useCamera } from "./use-camera";

type Clip = components["schemas"]["ShowcasePublic"];
type FrameReply = components["schemas"]["FrameReply"];
type Goal = components["schemas"]["GoalPublic"];
type MemoryVector = components["schemas"]["MemoryVector"];
type SearchResult = components["schemas"]["SearchResult"];

/** A demo clip's id, or the visitor's camera. */
type SourceKey = number | "camera";
/** What the right lens shows: a screen, the frame it rests on, and for how many seconds (null:
 * until something replaces it). */
type Lens = { screen: FlexBoxNode; evidence: Evidence | null; seconds: number | null };
type Exchange = {
  id: number;
  question: string;
  answer?: string;
  evidence?: Evidence | null;
  error?: string;
};

const MAX_SIDE = 896; // the glasses stream at 504x896: send about that much, not more
const HISTORY = 3; // exchanges listed under the view; the lens shows only the newest, briefly
// The camera's frames are fetched for the map at most this often while frames are being stored.
const TRAIL_REFRESH_MS = 1500;
// How often to look again for the frames of a clip published moments ago, which the backend is
// still storing as memories (a minute of video takes about that long to embed).
const CLIP_RETRY_MS = 5000;
const CAMERA_EXAMPLES = [
  "Where did I leave my keys?",
  "Warn me when I see a cup",
  "Where have I seen this before?",
];
const DEJA_VU_EXAMPLE = "Where have I seen this before?";
// The collection camera frames are stored in (the backend's default for /api/glasses/frames): with
// the camera on, questions, déjà vu and the map cover only the visitor's own frames in it (`mine`:
// the admin's own too, who would otherwise see everyone's).
const CAMERA_SCOPE = ["glasses"];

/** While Gemma searches: the question small, as the answer screen will show it. */
function thinkingScreen(question: string): FlexBoxNode {
  return {
    type: "flexbox",
    direction: "column",
    gap: 16,
    padding: 32,
    justify: "end",
    align: "stretch",
    children: [
      { type: "text", value: question, style: "CAPTION" },
      { type: "text", value: "Searching your memory…", style: "BODY" },
    ],
  };
}

function timeOfDay(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * Try it: watch one of the demo clips, or your own camera, through the glasses and use them as
 * the wearer would. One memory is in play at a time, cut at now: a clip's frames from its start
 * to the playhead (the backend embedded them when the clip was published), or the visitor's own
 * camera frames. Questions, déjà vu and the map on the right see only that memory. While it plays,
 * frames stream at FPS to /api/glasses/frames against the visitor's goals (clip frames only
 * checked, camera frames also stored); pausing stops time: nothing new is seen or fires. There is
 * no seeking; Replay starts the clip over. The answer shows in the right lens with the frame it
 * rests on.
 */
export function TryView() {
  const params = useSearchParams();
  const { user } = useSession();
  const camera = useCamera();

  const [clips, setClips] = useState<Clip[] | null>(null);
  const [sourceKey, setSourceKey] = useState<SourceKey | null>(null);
  const [started, setStarted] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [ended, setEnded] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const [imageIndex, setImageIndex] = useState(0);
  const [cameraLive, setCameraLive] = useState(true);
  const [sound, setSound] = useState(true);
  const [listening, setListening] = useState(false);
  const [lens, setLens] = useState<Lens | null>(null);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [history, setHistory] = useState<Exchange[]>([]);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [points, setPoints] = useState<TrailPoint[]>([]);
  const [highlight, setHighlight] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);

  const mediaRef = useRef<HTMLVideoElement | HTMLImageElement | null>(null);
  const setMedia = useCallback((element: HTMLVideoElement | HTMLImageElement | null) => {
    mediaRef.current = element;
  }, []);
  const streamId = useRef("");
  // Counts what the view shows (a source, a replay): an answer or a frame's reply that comes back
  // after it changed is about something no longer in view, and is dropped.
  const generation = useRef(0);
  const [starting, setStarting] = useState(false);
  const sending = useRef(false); // one frame at a time; frames due meanwhile are skipped
  const shownKey = useRef<SourceKey | null>(null); // the source the map's points belong to
  const lastFetched = useRef<number | null>(null);
  const trailTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  const playable = (clips ?? []).filter((c) => c.assets.length);
  const clip = typeof sourceKey === "number" ? playable.find((c) => c.id === sourceKey) : undefined;
  const onCamera = sourceKey === "camera";
  const streaming = started && (onCamera ? Boolean(camera.stream) && cameraLive : playing);

  // The published clips, public.
  useEffect(() => {
    api
      .GET("/api/demo/clips", { cache: "no-store" })
      .then(({ data }) => setClips((data ?? []).filter((c) => c.assets.length)))
      .catch(() => setError("Could not load the demo videos."));
  }, []);

  // ?clip= names the source: a clip's id, or "camera". The home page's Try it out sets it and the
  // chips keep it in step (choose). Next keeps this page alive between visits, so a new ?clip=
  // can reach a page that is playing something else: it switches as its chip would.
  const param = params.get("clip");
  const wanted: SourceKey | null =
    param === "camera" ? "camera" : param && /^\d+$/.test(param) ? Number(param) : null;
  const follow = useEffectEvent((list: Clip[], want: SourceKey | null) => {
    const known = want === "camera" || list.some((c) => c.id === want);
    if (sourceKey === null) setSourceKey(known ? want : (list[0]?.id ?? "camera"));
    else if (known && want !== null && want !== sourceKey) void choose(want);
  });
  useEffect(() => {
    if (!clips) return;
    // The address changed outside this page (a link elsewhere): follow it once this render is
    // done, as a click on its chip would.
    const timer = setTimeout(() => follow(clips, wanted));
    return () => clearTimeout(timer);
  }, [clips, wanted]);

  // A returning guest's goals; after that they change only through this page.
  const loadGoals = useEffectEvent(() => void refreshGoals());
  useEffect(() => {
    if (user) loadGoals();
  }, [user]);

  async function refreshGoals() {
    const { data } = await api.GET("/api/glasses/goals");
    if (data) setGoals(data);
  }

  async function removeGoal(goal: Goal) {
    const { error: problem } = await api.DELETE("/api/glasses/goals/{id}", {
      params: { path: { id: goal.id } },
    });
    if (problem) setError(errorText(problem, "Could not stop watching for it"));
    await refreshGoals();
  }

  function speak(text: string) {
    if (sound && text) say(text);
  }

  function show(next: Lens | null) {
    setLens(next);
  }
  useEffect(() => {
    if (!lens?.seconds) return;
    const timer = setTimeout(() => setLens(null), lens.seconds * 1000);
    return () => clearTimeout(timer);
  }, [lens]);

  // ---- The map: one point per remembered frame -------------------------------------------

  function toPoint(memory: MemoryVector, at: number, label: string): TrailPoint {
    return { id: memory.id, vector: decode(memory.embedding), at, imageUrl: memory.image_url, label };
  }

  /** All of a clip's frames at once: they are revealed as the playhead passes them. */
  async function loadClipTrail(id: number) {
    shownKey.current = id;
    const { data, error: problem } = await api.GET("/api/memories/vectors", {
      params: { query: { clip: id } },
      cache: "no-store",
    });
    if (shownKey.current !== id) return; // the visitor moved on meanwhile
    if (problem || !data) return setError(errorText(problem, "Could not load the memory map"));
    // A clip published moments ago is still being stored as memories, in the background.
    if (!data.length) {
      setTimeout(() => shownKey.current === id && void loadClipTrail(id), CLIP_RETRY_MS);
      return;
    }
    setPoints(
      data.map((memory) => toPoint(memory, memory.clip_at_s ?? 0, clock(memory.clip_at_s ?? 0))),
    );
  }

  /** The visitor's camera memory (CAMERA_SCOPE, what their questions search): all of it at
   * first, then only what was stored since. */
  async function loadCameraTrail() {
    const after = lastFetched.current;
    const { data, error: problem } = await api.GET("/api/memories/vectors", {
      params: { query: { collection: CAMERA_SCOPE, mine: true, after_id: after ?? undefined } },
      cache: "no-store",
    });
    if (shownKey.current !== "camera") return; // the visitor moved on meanwhile
    if (problem || !data) return setError(errorText(problem, "Could not load the memory map"));
    if (!data.length) return;
    lastFetched.current = Math.max(lastFetched.current ?? 0, ...data.map((memory) => memory.id));
    setPoints((current) => {
      // Two loads can overlap (the first one and a refresh after a stored frame).
      const have = new Set(current.map((point) => point.id));
      const fresh = data.filter((memory) => !have.has(memory.id)).toSorted((a, b) => a.id - b.id);
      return [
        ...current,
        ...fresh.map((memory) =>
          toPoint(memory, new Date(memory.created_at).getTime(), timeOfDay(memory.created_at)),
        ),
      ];
    });
  }

  function scheduleCameraTrail() {
    if (trailTimer.current) return;
    trailTimer.current = setTimeout(() => {
      trailTimer.current = undefined;
      void loadCameraTrail();
    }, TRAIL_REFRESH_MS);
  }
  useEffect(
    () => () => {
      // Also when Next hides the page: a timer cleared but still recorded would block every
      // refresh after the visitor comes back.
      clearTimeout(trailTimer.current);
      trailTimer.current = undefined;
    },
    [],
  );

  // ---- Playing ---------------------------------------------------------------------------

  /** Seconds into the clip on the view: what the glasses are seeing now. */
  function position(): number {
    const media = mediaRef.current;
    if (clip?.kind === "video" && media instanceof HTMLVideoElement) return media.currentTime;
    return clip ? imageIndex * clip.image_seconds : 0;
  }

  /** Plays a video. A play cut short (the clip changed, the page hid) is no failure. Only Start,
   * a new source (the clip's autoPlay) and Replay start one: a page Next shows again stays as the
   * visitor left it, paused by GlassesView. */
  function play(media: HTMLVideoElement) {
    media.play().catch((problem: unknown) => {
      if (problem instanceof DOMException && problem.name === "AbortError") return;
      setError(`The video could not play: ${problem instanceof Error ? problem.message : problem}`);
    });
  }

  // An image sequence shows each image for its clip's image_seconds.
  const advanceImage = useEffectEvent(() => {
    if (!clip) return;
    if (imageIndex + 1 < clip.assets.length) {
      setImageIndex(imageIndex + 1);
      setPlayhead((imageIndex + 1) * clip.image_seconds);
    } else {
      setPlaying(false);
      setEnded(true);
    }
  });
  useEffect(() => {
    if (!playing || clip?.kind !== "images") return;
    const timer = setInterval(() => advanceImage(), clip.image_seconds * 1000);
    return () => clearInterval(timer);
  }, [playing, clip?.id, clip?.kind, clip?.image_seconds]);

  useEffect(() => {
    const media = mediaRef.current;
    if (!onCamera || !(media instanceof HTMLVideoElement)) return;
    media.srcObject = camera.stream;
    // GlassesView pauses the view when Next hides this page, which clears autoplay.
    if (camera.stream) play(media);
  }, [onCamera, camera.stream]);

  // React sets `muted` only when the element is created; the sound toggle changes it after.
  useEffect(() => {
    const media = mediaRef.current;
    if (!onCamera && media instanceof HTMLVideoElement) media.muted = !sound;
  }, [sound, onCamera, clip?.id]);

  async function start() {
    if (starting) return;
    setError(null);
    // Within the tap: an iPhone plays sound, and speaks, only when first asked during one.
    const media = mediaRef.current;
    if (!onCamera && media instanceof HTMLVideoElement) play(media);
    if (sound) say("");
    setStarting(true);
    try {
      await ensureSession();
    } catch (problem) {
      if (media instanceof HTMLVideoElement) media.pause();
      setError(problem instanceof Error ? problem.message : String(problem));
      return;
    } finally {
      setStarting(false);
    }
    generation.current += 1;
    streamId.current = crypto.randomUUID();
    setStarted(true);
    void refreshGoals();
    if (onCamera) {
      shownKey.current = "camera";
      const problem = await camera.start();
      if (problem) setError(problem);
      void loadCameraTrail();
    } else if (clip) {
      if (clip.kind === "images") setPlaying(true);
      void loadClipTrail(clip.id);
    }
  }

  async function choose(next: SourceKey) {
    if (next === sourceKey || camera.busy || starting) return;
    if (onCamera) camera.stop();
    generation.current += 1;
    // The address says what is shown, so going back and forth through history keeps the choice.
    window.history.replaceState(null, "", `/try?clip=${next}`);
    setSourceKey(next);
    setPoints([]);
    setHighlight([]);
    setLens(null);
    setPlaying(false);
    setEnded(false);
    setPlayhead(0);
    setImageIndex(0);
    setError(null);
    // A new stream for the new picture: the triggers' baseline is measured per stream.
    streamId.current = crypto.randomUUID();
    lastFetched.current = null;
    shownKey.current = next;
    if (!started) return;
    if (next === "camera") {
      setCameraLive(true);
      const problem = await camera.start();
      if (problem) setError(problem);
      void loadCameraTrail();
    } else {
      if (playable.find((c) => c.id === next)?.kind === "images") setPlaying(true);
      void loadClipTrail(next);
    }
  }

  function togglePlay() {
    if (onCamera) return setCameraLive((live) => !live);
    const media = mediaRef.current;
    if (clip?.kind === "video" && media instanceof HTMLVideoElement) {
      if (media.paused) play(media);
      else media.pause();
    } else setPlaying((on) => !on);
  }

  function replay() {
    generation.current += 1;
    setEnded(false);
    setPlayhead(0);
    setImageIndex(0);
    setHighlight([]);
    setLens(null);
    streamId.current = crypto.randomUUID();
    const media = mediaRef.current;
    if (clip?.kind === "video" && media instanceof HTMLVideoElement) {
      media.currentTime = 0;
      play(media);
    } else setPlaying(true);
  }

  // ---- Streaming, as the glasses do ------------------------------------------------------

  /** The picture on the view now as a JPEG at most MAX_SIDE wide or tall, or null. */
  async function grab(): Promise<Blob | null> {
    const media = mediaRef.current;
    let width = 0;
    let height = 0;
    if (media instanceof HTMLVideoElement) {
      if (media.readyState < 2) return null;
      [width, height] = [media.videoWidth, media.videoHeight];
    } else if (media instanceof HTMLImageElement) {
      if (!media.complete) return null;
      [width, height] = [media.naturalWidth, media.naturalHeight];
    }
    if (!media || !width || !height) return null;
    const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    canvas.getContext("2d")?.drawImage(media, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  }

  async function sendFrame() {
    if (sending.current) return;
    sending.current = true;
    const seen = generation.current;
    try {
      const blob = await grab();
      if (!blob) return;
      const form = new FormData();
      form.append("frame", blob, "frame.jpg");
      form.append("source", "stream");
      form.append("stream_id", streamId.current);
      form.append("seen_at", new Date().toISOString());
      // The demo clips are demo memories already: only check them against the goals.
      form.append("store", String(onCamera));
      form.append("client", "live"); // for the admin's stream wall
      const response = await fetch("/api/glasses/frames", { method: "POST", body: form });
      const body = await response.json().catch(() => null);
      if (response.status === 401) {
        setStarted(false);
        setPlaying(false);
        setError("Your guest session ended. Press Start for a new one.");
        window.dispatchEvent(new Event("session-changed"));
        return;
      }
      if (!response.ok) {
        setError(errorText(body, `The backend answered ${response.status}`));
        return;
      }
      if (seen !== generation.current) return; // a frame of what is no longer in view
      const reply = body as FrameReply;
      if (reply.message) {
        show({ screen: messageScreen(reply.message), evidence: null, seconds: MESSAGE_S });
        speak(`${reply.message.title}. ${reply.message.text}`);
      }
      if (reply.stored) scheduleCameraTrail();
    } catch {
      setError("Could not reach the backend");
    } finally {
      sending.current = false;
    }
  }

  const tick = useEffectEvent(() => void sendFrame());
  useEffect(() => {
    if (!streaming) return;
    const timer = setInterval(() => tick(), 1000 / FPS);
    return () => clearInterval(timer);
  }, [streaming]);

  // ---- Asking ----------------------------------------------------------------------------

  function toEvidence(result: SearchResult | undefined): Evidence | null {
    if (!result?.image_url) return null;
    const point = points.find((p) => p.id === result.id);
    return {
      src: result.image_url,
      alt: result.text || "The remembered frame",
      caption: point && clip ? `Seen at ${point.label}` : `Seen at ${timeOfDay(result.created_at)}`,
    };
  }

  function record(id: number, change: Partial<Exchange>) {
    setHistory((list) => list.map((item) => (item.id === id ? { ...item, ...change } : item)));
  }

  async function ask(text: string) {
    const asked = text.trim();
    if (!asked || asking) return;
    const id = Date.now();
    setQuestion("");
    setAsking(true);
    setHistory((list) => [{ id, question: asked }, ...list].slice(0, HISTORY));
    show({ screen: thinkingScreen(asked), evidence: null, seconds: null });
    // The memory being watched, up to now: the clip up to the playhead, or the camera's frames.
    const scope = clip
      ? { demo_clip_id: clip.id, demo_at_s: position(), mine: false }
      : { mine: true };
    const seen = generation.current;
    try {
      await ensureSession();
      if (RECALL.test(asked)) {
        // Déjà vu, as the glasses ask it: where what is in view was seen before.
        const { data, error: problem } = await api.POST("/api/glasses/command", {
          body: { question: asked, ...scope },
        });
        if (problem || !data) throw new Error(errorText(problem, "Could not ask the glasses"));
        record(id, { answer: data.speech ?? "" });
        if (seen !== generation.current) return; // about a picture no longer in view
        show({ screen: data.screen, evidence: null, seconds: data.dismiss_after_s ?? MESSAGE_S });
        speak(data.speech ?? "");
      } else {
        const { data, error: problem } = await api.POST("/api/ask", {
          body: { question: asked, ...scope, collections: clip ? undefined : CAMERA_SCOPE },
        });
        if (problem || !data) throw new Error(errorText(problem, "Could not get an answer"));
        const evidence = toEvidence(data.evidence?.[0]);
        record(id, { answer: plain(data.answer), evidence });
        // The answer may have started or stopped watching for something.
        void refreshGoals();
        if (seen !== generation.current) return; // about a picture no longer in view
        show({
          screen: answerScreen(asked, data.answer),
          evidence,
          seconds: secondsToRead(`${asked} ${plain(data.answer)}`),
        });
        setHighlight((data.evidence ?? []).map((result) => result.id));
        speak(data.answer);
      }
    } catch (problem) {
      if (seen === generation.current) show(null);
      record(id, { error: problem instanceof Error ? problem.message : String(problem) });
    } finally {
      setAsking(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(question);
  }

  // ---- The page --------------------------------------------------------------------------

  const examples = clip
    ? [
        ...clip.cues.filter((cue) => cue.kind !== "trigger").map((cue) => cue.text),
        DEJA_VU_EXAMPLE,
      ]
    : CAMERA_EXAMPLES;
  const shown = onCamera ? points : points.filter((point) => point.at <= playhead + REVEAL_SLACK_S);

  const media = onCamera ? (
    <video
      ref={setMedia}
      autoPlay
      muted
      playsInline
      disableRemotePlayback
      style={camera.facing === "user" ? { transform: "scaleX(-1)" } : undefined}
    />
  ) : clip?.kind === "video" ? (
    <video
      key={clip.id}
      ref={setMedia}
      src={clip.assets[0].url}
      autoPlay={started}
      muted={!sound}
      playsInline
      disableRemotePlayback
      preload="auto"
      onPlay={() => setPlaying(true)}
      onPause={() => setPlaying(false)}
      onEnded={() => {
        setPlaying(false);
        setEnded(true);
      }}
      onTimeUpdate={(event) => setPlayhead(event.currentTarget.currentTime)}
    />
  ) : clip ? (
    // A plain <img>: the backend serves the demo images as they are.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      ref={setMedia}
      src={clip.assets[Math.min(imageIndex, clip.assets.length - 1)].url}
      alt={clip.assets[Math.min(imageIndex, clip.assets.length - 1)].label}
    />
  ) : (
    <p className="m-auto p-6 text-center text-sm text-white/70">
      {clips === null ? "Loading…" : "No demo videos yet: try your camera."}
    </p>
  );

  const startButton = (
    <button
      type="button"
      className="try-start"
      onClick={() => void start()}
      disabled={camera.busy || starting}
    >
      {camera.busy || starting ? (
        <LoaderCircle className="animate-spin" aria-hidden="true" />
      ) : onCamera ? (
        <Camera aria-hidden="true" />
      ) : (
        <Play aria-hidden="true" />
      )}
      {onCamera ? "Start your camera" : "Start"}
    </button>
  );
  // The camera also stops when Next hides this page (useCamera's cleanup): Start opens it again.
  const needsStart = !started || (onCamera && !camera.stream && !camera.busy);
  const overlay = needsStart ? (
    sourceKey !== null && startButton
  ) : ended ? (
    <button type="button" className="try-start" onClick={replay}>
      <RotateCcw aria-hidden="true" /> Replay
    </button>
  ) : null;

  const status = (
    <>
      <span>{onCamera ? "Your camera" : clip ? clip.title : "Demo"}</span>
      {streaming && (
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2 animate-pulse rounded-full bg-red-500" aria-hidden="true" /> Live
        </span>
      )}
    </>
  );

  return (
    <div className="try-grid">
      <h1 className="sr-only">Try GlassMemory</h1>
      <section aria-label="Through the glasses" className="flex min-w-0 flex-col gap-3">
        <GlassesView
          media={mediaRef}
          screen={lens?.screen ?? null}
          evidence={lens?.evidence ?? null}
          listening={listening}
          status={status}
          overlay={overlay}
        >
          {media}
        </GlassesView>

        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="What the glasses see" className="flex flex-wrap gap-1.5">
            {playable.map((option) => (
              <button
                key={option.id}
                type="button"
                className="try-chip"
                aria-pressed={sourceKey === option.id}
                disabled={starting}
                onClick={() => void choose(option.id)}
              >
                {option.title}
              </button>
            ))}
            <button
              type="button"
              className="try-chip"
              aria-pressed={onCamera}
              disabled={starting}
              onClick={() => void choose("camera")}
            >
              <Camera size={14} aria-hidden="true" /> Your camera
            </button>
          </div>
          <span className="ml-auto flex gap-1.5">
            {onCamera && camera.stream && camera.cameras > 1 && (
              <Button
                variant="ghost"
                size="icon"
                aria-label={camera.facing === "environment" ? "Front camera" : "Back camera"}
                disabled={camera.busy}
                onClick={() => void camera.flip().then((problem) => problem && setError(problem))}
              >
                <SwitchCamera aria-hidden="true" />
              </Button>
            )}
            {started && (
              <Button
                variant="ghost"
                size="icon"
                aria-label={streaming ? "Pause" : "Play"}
                onClick={togglePlay}
              >
                {streaming ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-pressed={sound}
              aria-label={sound ? "Sound on" : "Sound off"}
              onClick={() => {
                if (sound) window.speechSynthesis?.cancel();
                setSound(!sound);
              }}
            >
              {sound ? <Volume2 aria-hidden="true" /> : <VolumeX aria-hidden="true" />}
            </Button>
          </span>
        </div>

        <form onSubmit={submit} className="flex items-center gap-2">
          <HoldToTalk
            label="Hold to ask"
            className="h-10"
            disabled={asking}
            onText={(text) => void ask(text)}
            onListening={setListening}
          />
          <label htmlFor="try-question" className="sr-only">
            Your question
          </label>
          <input
            id="try-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={2000}
            placeholder="Ask what you've seen, or say “notify me when…”"
            className="h-10 min-w-0 flex-1 rounded-md border border-border bg-card px-3 text-sm outline-none focus-visible:border-primary"
          />
          <Button type="submit" size="lg" disabled={asking || !question.trim()}>
            {asking ? (
              <LoaderCircle className="animate-spin" aria-hidden="true" />
            ) : (
              <SendHorizontal aria-hidden="true" />
            )}
            Ask
          </Button>
        </form>

        <div className="flex flex-wrap gap-1.5" aria-label="Things to ask">
          {examples.map((example) => (
            <button
              key={example}
              type="button"
              className="try-suggestion"
              disabled={asking}
              onClick={() => void ask(example)}
            >
              {example}
            </button>
          ))}
        </div>

        {goals.length > 0 && (
          <ul className="flex flex-wrap items-center gap-1.5" aria-label="Watching for">
            {goals.map((goal) => (
              <li key={goal.id} className="try-goal">
                <Eye size={13} aria-hidden="true" />
                <span className="truncate">{goal.text}</span>
                <button
                  type="button"
                  aria-label={`Stop watching for ${goal.text}`}
                  onClick={() => void removeGoal(goal)}
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}

        {history.length > 0 && (
          <ol className="flex flex-col gap-2" aria-label="Your questions" aria-live="polite">
            {history.map((item) => (
              <li key={item.id} className="try-exchange">
                <div className="min-w-0 flex-1">
                  <p className="text-xs text-muted-foreground">{item.question}</p>
                  <p className={`text-sm ${item.error ? "text-destructive" : ""}`}>
                    {item.error ?? item.answer ?? "…"}
                  </p>
                </div>
                {item.evidence && (
                  // eslint-disable-next-line @next/next/no-img-element -- served by the API
                  <img src={item.evidence.src} alt={item.evidence.alt} title={item.evidence.caption} />
                )}
              </li>
            ))}
          </ol>
        )}

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </section>

      <aside aria-labelledby="try-memory-title" className="try-memory">
        <div className="flex items-baseline justify-between gap-2">
          <h2 id="try-memory-title" className="text-sm font-medium">
            Memory
          </h2>
          <span className="text-xs text-muted-foreground">
            {shown.length} {shown.length === 1 ? "frame" : "frames"} remembered
          </span>
        </div>
        <MemoryTrail
          points={points}
          visibleUntil={onCamera ? undefined : playhead}
          highlight={highlight}
          className="try-trail"
        />
        <p className="text-xs text-muted-foreground">
          Each point is a remembered frame; similar frames sit close together.
        </p>
      </aside>
    </div>
  );
}
