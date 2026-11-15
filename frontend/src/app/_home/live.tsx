"use client";

import {
  Bell,
  Circle,
  Film,
  Glasses,
  Maximize,
  MessageSquareText,
  Minimize,
  SkipForward,
  Square,
  SwitchCamera,
  Video,
  Volume2,
  VolumeX,
} from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState } from "react";

import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";
import { ensureSession, useSession } from "@/lib/session";

import "../glasses/glasses.css";
import { Display } from "../glasses/display";
import { say } from "../glasses/speech";
import {
  answerScreen,
  MESSAGE_S,
  messageScreen,
  plain as plainAnswer,
  secondsToRead,
} from "./glasses-screens";
import { type AskChatHandle, AskChat } from "../memory/ask-chat";
import {
  FPS,
  type GlassesReply,
  RECALL,
  Recalled,
} from "./recalled";
import { type Match, TriggerEditor } from "../memory/trigger-editor";
import { errorText } from "../memory/types";
import { HoldToTalk } from "./hold-to-talk";

type Clip = components["schemas"]["ShowcasePublic"];
type FrameReply = components["schemas"]["FrameReply"];
type Message = components["schemas"]["GlassesMessage"];
type Source = "demo" | "camera";
type Facing = "environment" | "user";

const MAX_SIDE = 896; // the glasses stream at 504x896: send about that much, not more

const EXAMPLES = [
  "Where have I seen this one?",
  "Warn me when I see a bicycle",
  "Where did I leave my keys?",
];

/**
 * The home page: the live view of what the glasses see, the day chat and the triggers. The view
 * plays the published demo clips until the visitor switches to their own camera (front or back
 * on a phone). Streaming sends a frame every 1/FPS seconds to /api/glasses/frames, as the
 * glasses do: the demo clips are only checked against the triggers (they are demo memories
 * already), camera frames are also stored as the visitor's memories. The chat asks about the
 * demo memories and the visitor's own. Asking, streaming or a goal starts a guest when nobody is
 * logged in.
 */
export function LiveHome() {
  const { user } = useSession();
  const stageRef = useRef<HTMLDivElement>(null);
  const cameraRef = useRef<HTMLVideoElement>(null);
  const clipVideoRef = useRef<HTMLVideoElement>(null);
  const clipImageRef = useRef<HTMLImageElement>(null);
  const chatRef = useRef<AskChatHandle>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const streamId = useRef("");
  const busy = useRef(false); // one frame at a time; frames due meanwhile are skipped

  const [source, setSource] = useState<Source>("demo");
  const [facing, setFacing] = useState<Facing>("environment");
  const [cameras, setCameras] = useState(0);
  const [camera, setCamera] = useState<MediaStream | null>(null);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [clips, setClips] = useState<Clip[] | null>(null);
  const [clipIndex, setClipIndex] = useState(0);
  const [imageIndex, setImageIndex] = useState(0);
  const [streaming, setStreaming] = useState(false);
  const [stats, setStats] = useState({ sent: 0, stored: 0 });
  const [message, setMessage] = useState<Message | null>(null);
  const [answer, setAnswer] = useState<{ question: string; text: string } | null>(null);
  const [recalled, setRecalled] = useState<GlassesReply | null>(null);
  const [matches, setMatches] = useState<Map<number, Match>>(new Map());
  const [fired, setFired] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [speak, setSpeak] = useState(true);
  // The glasses' display over the view: what the wearer would see of a trigger or an answer.
  const [glassesOn, setGlassesOn] = useState(false);
  const [listening, setListening] = useState(false);
  // The control bar's height: the glasses' display sits above it, so nothing hides its text.
  const barRef = useRef<HTMLDivElement>(null);
  const [barHeight, setBarHeight] = useState(0);
  useEffect(() => {
    const bar = barRef.current;
    if (!bar) return;
    const observer = new ResizeObserver(([entry]) =>
      setBarHeight(Math.ceil(entry.target.getBoundingClientRect().height)),
    );
    observer.observe(bar);
    return () => observer.disconnect();
  }, []);
  const [nativeFullscreen, setNativeFullscreen] = useState(false);
  const [cssFullscreen, setCssFullscreen] = useState(false);
  const immersive = nativeFullscreen || cssFullscreen;

  const playable = (clips ?? []).filter((clip) => clip.assets.length);
  const clip = playable.length ? playable[clipIndex % playable.length] : undefined;

  // The demo clips, public: no login needed to watch them.
  useEffect(() => {
    api
      .GET("/api/demo/clips", { cache: "no-store" })
      .then(({ data }) => setClips(data ?? []))
      .catch(() => setClips([]));
  }, []);

  function nextClip() {
    setImageIndex(0);
    setClipIndex((index) => index + 1);
  }

  // An image sequence shows each image for its clip's image_seconds, then the next clip.
  const advanceImage = useEffectEvent(() => {
    if (!clip) return;
    if (imageIndex + 1 < clip.assets.length) setImageIndex(imageIndex + 1);
    else nextClip();
  });
  useEffect(() => {
    if (source !== "demo" || clip?.kind !== "images") return;
    const timer = setInterval(() => advanceImage(), clip.image_seconds * 1000);
    return () => clearInterval(timer);
  }, [source, clip?.id, clip?.kind, clip?.image_seconds]);

  async function startCamera(direction: Facing = facing): Promise<boolean> {
    if (streamRef.current) return true;
    setCameraBusy(true);
    try {
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error("open this site over HTTPS to use your camera");
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: direction },
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
      streamRef.current = stream;
      setCamera(stream);
      setError(null);
      // Known only once the camera is allowed: whether there is a second one to switch to.
      const devices = await navigator.mediaDevices.enumerateDevices();
      // A phone or tablet has a front and a back camera even when it lists only one.
      const count = devices.filter((d) => d.kind === "videoinput").length;
      setCameras(navigator.maxTouchPoints > 0 ? Math.max(count, 2) : count);
      await new Promise((resolve) => setTimeout(resolve, 800)); // let the exposure settle
      return true;
    } catch (problem) {
      setError(`Camera unavailable: ${problem instanceof Error ? problem.message : problem}`);
      return false;
    } finally {
      setCameraBusy(false);
    }
  }

  function stopCamera() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCamera(null);
  }

  async function chooseSource(next: Source) {
    if (next === source) return;
    setRecalled(null); // it was about the picture being left
    // A new stream for the new picture: the triggers' baseline is measured per stream.
    streamId.current = crypto.randomUUID();
    if (next === "camera") {
      setSource("camera");
      if (!(await startCamera())) {
        setSource("demo");
        setStreaming(false);
      }
    } else {
      stopCamera();
      setSource("demo");
    }
  }

  async function flipCamera() {
    const next = facing === "environment" ? "user" : "environment";
    setFacing(next);
    stopCamera();
    if (!(await startCamera(next))) setStreaming(false);
  }

  useEffect(() => {
    if (cameraRef.current) cameraRef.current.srcObject = camera;
  }, [camera]);

  const release = useEffectEvent(stopCamera);
  useEffect(() => () => release(), []);

  /** The picture on the view now as a JPEG at most MAX_SIDE wide or tall, or null. */
  async function grab(): Promise<Blob | null> {
    let element: HTMLVideoElement | HTMLImageElement | null;
    let width: number;
    let height: number;
    if (source === "camera" || clip?.kind === "video") {
      const video = source === "camera" ? cameraRef.current : clipVideoRef.current;
      if (!video || video.readyState < 2 || !video.videoWidth) return null;
      [element, width, height] = [video, video.videoWidth, video.videoHeight];
    } else {
      const image = clipImageRef.current;
      if (!image?.complete || !image.naturalWidth) return null;
      [element, width, height] = [image, image.naturalWidth, image.naturalHeight];
    }
    const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    canvas.getContext("2d")?.drawImage(element, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  }

  async function sendFrame() {
    if (busy.current) return;
    busy.current = true;
    try {
      const blob = await grab();
      if (!blob) return;
      const form = new FormData();
      form.append("frame", blob, "frame.jpg");
      form.append("source", "stream");
      form.append("stream_id", streamId.current);
      form.append("seen_at", new Date().toISOString());
      // The demo clips are demo memories already: only check them against the triggers.
      form.append("store", String(source === "camera"));
      form.append("client", "live"); // for the admin's stream wall
      const response = await fetch("/api/glasses/frames", { method: "POST", body: form });
      const body = await response.json().catch(() => null);
      if (response.status === 401) {
        setStreaming(false);
        setError("Your guest session ended. Start the stream again for a new one.");
        window.dispatchEvent(new Event("session-changed"));
        return;
      }
      if (!response.ok) {
        setError(errorText(body, `The backend answered ${response.status}`));
        return;
      }
      const reply = body as FrameReply;
      setError(null);
      setStats((s) => ({ sent: s.sent + 1, stored: s.stored + (reply.stored ? 1 : 0) }));
      if (reply.message) {
        setMessage(reply.message);
        if (speak) say(`${reply.message.title}. ${reply.message.text}`);
      }
      setMatches(
        new Map(
          reply.matches?.map((m) => [m.trigger_id, { score: m.score ?? null, level: m.level ?? null }]),
        ),
      );
      if (reply.matches?.some((m) => m.fired)) setFired((n) => n + 1);
    } catch {
      setError("Could not reach the backend");
    } finally {
      busy.current = false;
    }
  }

  const tick = useEffectEvent(() => void sendFrame());
  useEffect(() => {
    if (!streaming) return;
    const timer = setInterval(() => tick(), 1000 / FPS);
    return () => clearInterval(timer);
  }, [streaming]);

  async function toggleStream() {
    if (streaming) return setStreaming(false);
    try {
      await ensureSession();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
      return;
    }
    if (source === "camera" && !(await startCamera())) return;
    if (!streamId.current) streamId.current = crypto.randomUUID();
    setStats({ sent: 0, stored: 0 });
    setStreaming(true);
  }

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), MESSAGE_S * 1000);
    return () => clearTimeout(timer);
  }, [message]);
  useEffect(() => {
    if (!answer) return;
    // As long as the glasses would show it.
    const seconds = secondsToRead(`${answer.question} ${plainAnswer(answer.text)}`);
    const timer = setTimeout(() => setAnswer(null), seconds * 1000);
    return () => clearTimeout(timer);
  }, [answer]);
  useEffect(() => {
    if (!recalled) return;
    const timer = setTimeout(() => setRecalled(null), (recalled.dismiss_after_s ?? 10) * 1000);
    return () => clearTimeout(timer);
  }, [recalled]);

  /** Where the demo clip on the view is now, in seconds: what its frame is in memory. */
  function clipPosition(): number {
    if (clip?.kind === "video") return clipVideoRef.current?.currentTime ?? 0;
    return (clip ? imageIndex % clip.assets.length : 0) * (clip?.image_seconds ?? 0);
  }

  /** Déjà vu, as the glasses ask it: where what is on the view was seen before. The newest
   * camera frame; or the demo clip's frame where it is playing, since the clips are memories. */
  async function recall(question: string): Promise<string> {
    await ensureSession();
    const demo =
      source === "demo" && clip ? { demo_clip_id: clip.id, demo_at_s: clipPosition() } : {};
    const { data, error: problem } = await api.POST("/api/glasses/command", {
      body: { question, ...demo },
    });
    if (problem || !data) throw new Error(errorText(problem, "Could not ask the glasses"));
    setRecalled(data);
    return data.speech ?? "";
  }

  // Fullscreen: the browser's own where it can; an iPhone only lets a <video> go fullscreen, so
  // there the view fills the window instead.
  useEffect(() => {
    const changed = () => setNativeFullscreen(document.fullscreenElement === stageRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  useEffect(() => {
    if (!cssFullscreen) return;
    const leave = (event: KeyboardEvent) => event.key === "Escape" && setCssFullscreen(false);
    document.documentElement.style.overflow = "hidden";
    window.addEventListener("keydown", leave);
    return () => {
      document.documentElement.style.overflow = "";
      window.removeEventListener("keydown", leave);
    };
  }, [cssFullscreen]);

  async function toggleFullscreen() {
    if (document.fullscreenElement) return void (await document.exitFullscreen());
    if (cssFullscreen) return setCssFullscreen(false);
    try {
      if (stageRef.current?.requestFullscreen) return void (await stageRef.current.requestFullscreen());
    } catch {
      // Refused (an iframe, say): fill the window instead.
    }
    setCssFullscreen(true);
  }

  const overlay = recalled ? (
    <Recalled inline screen={recalled.screen} />
  ) : message ? (
    <span role="status" className="live-card">
      <span className="flex items-center gap-1.5 text-sm font-semibold">
        {message.type === "trigger" && <Bell size={14} aria-hidden="true" />}
        {message.title}
      </span>
      <span className="text-sm">{message.text}</span>
    </span>
  ) : answer ? (
    <span role="status" className="live-card">
      <span className="flex items-center gap-1.5 text-xs text-white/70">
        <MessageSquareText size={13} aria-hidden="true" /> {answer.question}
      </span>
      <span className="line-clamp-5 text-sm">{answer.text}</span>
    </span>
  ) : null;

  // What the glasses would show now: déjà vu's screen from the backend, else a trigger's
  // message, else the latest answer; nothing (a clear display) otherwise.
  const glassesScreen = recalled
    ? recalled.screen
    : message
      ? messageScreen(message)
      : answer
        ? answerScreen(answer.question, answer.text)
        : null;

  const chip =
    "inline-flex items-center gap-1.5 rounded-md bg-white/10 px-2.5 py-1.5 text-xs font-medium text-white backdrop-blur hover:bg-white/20 disabled:opacity-50";

  return (
    <div className="live-grid">
      <section aria-labelledby="live-title" className="flex min-w-0 flex-col gap-3">
        <div>
          <p className="text-[11px] tracking-wide text-muted-foreground">
            YOUR DAY, REMEMBERED
          </p>
          <h1 id="live-title" className="text-xl font-medium tracking-tight">
            What your glasses see
          </h1>
        </div>

        <div
          ref={stageRef}
          className={`live-stage ${immersive ? "live-stage-full" : ""} ${cssFullscreen ? "live-stage-window" : ""}`}
          onDoubleClick={(event) => {
            if (!(event.target as HTMLElement).closest("button")) void toggleStream();
          }}
        >
          {source === "camera" ? (
            <video
              ref={cameraRef}
              autoPlay
              muted
              playsInline
              className={`live-media ${facing === "user" ? "-scale-x-100" : ""}`}
            />
          ) : clip?.kind === "video" ? (
            <video
              key={clip.id}
              ref={clipVideoRef}
              src={clip.assets[0].url}
              autoPlay
              muted
              playsInline
              loop={playable.length === 1}
              onEnded={nextClip}
              className="live-media"
            />
          ) : clip ? (
            // A plain <img>: the backend serves the demo images as they are.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              ref={clipImageRef}
              src={clip.assets[imageIndex % clip.assets.length].url}
              alt={clip.assets[imageIndex % clip.assets.length].label}
              className="live-media"
            />
          ) : (
            <p className="m-auto max-w-xs p-6 text-center text-sm text-white/70">
              {clips === null
                ? "Loading the demo clips…"
                : "No demo clips are published yet. Switch to your camera to try it live."}
            </p>
          )}

          <span className="absolute top-3 left-3 rounded-full bg-black/60 px-2.5 py-1 text-xs text-white">
            {source === "camera"
              ? `Your camera · ${facing === "user" ? "front" : "back"}`
              : clip
                ? `Demo · ${clip.title}`
                : "Demo"}
          </span>
          {streaming && (
            <span
              aria-live="polite"
              className="absolute top-3 right-3 flex items-center gap-1 rounded-full bg-black/60 px-2 py-1 text-xs font-medium text-white"
            >
              <Circle size={10} className="animate-pulse fill-red-500 text-red-500" aria-hidden="true" />
              {FPS} fps
            </span>
          )}

          {glassesOn && (
            <div
              className="live-glasses"
              aria-label="What the glasses would show"
              style={{ bottom: barHeight + 12 }}
            >
              <Display
                screen={glassesScreen}
                seeThrough
                listening={listening}
                onAction={() => {}}
              />
            </div>
          )}

          <div className="absolute inset-x-0 bottom-0 flex flex-col gap-2 p-3">
            {!glassesOn && overlay}
            <div ref={barRef} className="live-bar" onDoubleClick={(event) => event.stopPropagation()}>
              <span className="inline-flex rounded-md bg-white/10 p-0.5" role="group" aria-label="What the view shows">
                {(["demo", "camera"] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={source === option}
                    disabled={cameraBusy}
                    onClick={() => void chooseSource(option)}
                    className={`inline-flex items-center gap-1 rounded px-2 py-1 text-xs font-medium ${
                      source === option ? "bg-white text-black" : "text-white"
                    }`}
                  >
                    {option === "demo" ? <Film size={13} aria-hidden="true" /> : <Video size={13} aria-hidden="true" />}
                    {option === "demo" ? "Demo" : "Camera"}
                  </button>
                ))}
              </span>
              {source === "camera" && camera && cameras > 1 && (
                <button type="button" className={chip} disabled={cameraBusy} onClick={() => void flipCamera()}>
                  <SwitchCamera size={14} aria-hidden="true" />
                  {facing === "environment" ? "Front camera" : "Back camera"}
                </button>
              )}
              {source === "demo" && playable.length > 1 && (
                <button type="button" className={chip} onClick={nextClip}>
                  <SkipForward size={14} aria-hidden="true" /> <span className="max-sm:sr-only">Next clip</span>
                </button>
              )}
              <button
                type="button"
                className={`${chip} ${streaming ? "bg-red-600!" : ""}`}
                disabled={cameraBusy || (source === "demo" && !clip)}
                onClick={() => void toggleStream()}
              >
                {streaming ? <Square size={14} aria-hidden="true" /> : <Circle size={14} aria-hidden="true" />}
                {streaming ? "Stop" : "Stream"}
              </button>
              <HoldToTalk
                label="Ask"
                className="bg-white/10! px-2.5! py-1.5! text-xs! text-white!"
                onText={(text) => chatRef.current?.ask(text)}
                onListening={setListening}
              />
              <button
                type="button"
                className={`${chip} ${glassesOn ? "bg-white! text-black!" : ""}`}
                aria-pressed={glassesOn}
                title="Show what the glasses' display would show over the view"
                onClick={() => setGlassesOn((on) => !on)}
              >
                <Glasses size={14} aria-hidden="true" />
                <span className="max-sm:sr-only">Glasses display</span>
              </button>
              <span className="ml-auto inline-flex gap-2">
                <button
                  type="button"
                  className={chip}
                  aria-pressed={speak}
                  aria-label={speak ? "Speaker on" : "Speaker off"}
                  onClick={() => setSpeak((on) => !on)}
                >
                  {speak ? <Volume2 size={14} aria-hidden="true" /> : <VolumeX size={14} aria-hidden="true" />}
                </button>
                <button type="button" className={chip} onClick={() => void toggleFullscreen()}>
                  {immersive ? <Minimize size={14} aria-hidden="true" /> : <Maximize size={14} aria-hidden="true" />}
                  <span className="max-sm:sr-only">{immersive ? "Exit fullscreen" : "Fullscreen"}</span>
                </button>
              </span>
            </div>
          </div>
        </div>

        <p className="text-xs text-muted-foreground" aria-live="polite">
          {streaming
            ? `Streaming ${FPS} frames a second · ${stats.sent} checked against your triggers${
                source === "camera" ? ` · ${stats.stored} stored as your memories` : ""
              }`
            : source === "demo"
              ? "Playing the demo day. Stream it to check it against your triggers, or switch to your camera."
              : "Stream your camera to check it against your triggers and remember what it sees."}
          {" "}Double-tap the view to start or stop, as with the Neural Band.
        </p>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
      </section>

      <div className="flex min-w-0 flex-col gap-4">
        <AskChat
          ref={chatRef}
          compact={false}
          voice
          pinned={[]}
          beforeAsk={ensureSession}
          examples={EXAMPLES}
          onListening={setListening}
          intro="Ask about the demo day, or what your camera has seen. Ask Gemma to warn you about something, then stream: every frame is checked for it."
          className="min-h-[24rem] lg:max-h-[70vh]"
          special={(question) =>
            RECALL.test(question) ? recall(question) : null
          }
          onAnswer={(text, question) => {
            setAnswer({ question, text });
            if (speak) say(text);
          }}
          // Gemma may have added or removed a goal: reload the triggers.
          onDone={() => setFired((n) => n + 1)}
        />
        <p className="text-xs text-muted-foreground">
          {user
            ? user.role === "guest"
              ? "You are a temporary guest: your goals and camera memories are deleted after 60 minutes."
              : null
            : "No account needed: your first question or stream starts a 60-minute guest session."}
        </p>
      </div>

      <div className="live-triggers">
        <TriggerEditor lastMatches={matches} firedCount={fired} visible={Boolean(user)} />
      </div>
    </div>
  );
}
