"use client";

import {
  ArrowLeft,
  BellPlus,
  Camera,
  Eye,
  EyeOff,
  Mic,
  Pointer,
  RotateCcw,
  Send,
  Trash2,
  Video,
  VideoOff,
  Volume2,
  VolumeX,
  Zap,
} from "lucide-react";
import {
  type FormEvent,
  useEffect,
  useEffectEvent,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import Link from "next/link";

import { api } from "@/lib/api/browser";

import { Display } from "./display";
import {
  type Recognition,
  recognitionConstructor,
  say,
  transcripts,
} from "./speech";
import type { FlexBoxNode, GlassesReply, Reminder, WatchResult } from "./types";

type Status = "connecting" | "online" | "offline";

type LogEntry = {
  id: number;
  time: string;
  request: string;
  intent?: string;
  speech?: string | null;
  ms?: number;
  error?: string;
  json?: string;
  /** A watched frame: these come every few seconds and can be hidden. */
  watch?: boolean;
};

// Long enough to see "Taking a photo…", like the shutter delay on the glasses.
const CAPTURE_DELAY_MS = 400;
const CAMERA_WARMUP_MS = 800;
const subscribeToNothing = () => () => {};
// How often the camera checks for reminders while watching, start to start. A check that takes
// longer delays the next one: never two at once.
const WATCH_EVERY_MS = 1000;
// Two taps of the key within this time are a double tap.
const DOUBLE_TAP_MS = 400;

/** Resolves once the video is playing and has painted a new frame. */
async function liveFrame(video: HTMLVideoElement) {
  if (video.paused || video.readyState < 2) {
    await new Promise((resolve) =>
      video.addEventListener("playing", resolve, { once: true }),
    );
  }
  await new Promise<void>((resolve) => {
    if ("requestVideoFrameCallback" in video)
      video.requestVideoFrameCallback(() => resolve());
    else setTimeout(resolve, 100);
  });
}

async function fetchHome(): Promise<
  { reply: GlassesReply } | { error: string }
> {
  try {
    const { data, response } = await api.GET("/api/glasses/home", {
      signal: AbortSignal.timeout(5000),
    });
    return data ? { reply: data } : { error: `HTTP ${response.status}` };
  } catch (error) {
    return { error: `${error}. Is the backend running (make dev)?` };
  }
}
// Watching adds a line a second; keep about ten minutes of them.
const MAX_LOG = 600;

/**
 * Stands in for the glasses and the Android app: webcam = camera, laptop mic = microphone,
 * speechSynthesis = speakers, C = capture button, T T = Neural Band double tap. The rest is in the
 * backend's /api/glasses endpoints, the same ones the Android app calls.
 */
type HarnessProps = {
  /** Inside another page (the Memory lab): no page header, one column. */
  embedded?: boolean;
  /** False while hidden behind another tab: Space no longer starts the microphone. */
  active?: boolean;
  /** After a photo upload, which may have added an image memory. */
  onPhotoStored?: () => void;
};

export function GlassesHarness({
  embedded = false,
  active = true,
  onPhotoStored,
}: HarnessProps = {}) {
  const [screen, setScreen] = useState<FlexBoxNode | null>(null);
  const [status, setStatus] = useState<Status>("connecting");
  const [log, setLog] = useState<LogEntry[]>([]);
  const [camera, setCamera] = useState<MediaStream | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState("");
  const [micError, setMicError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [speak, setSpeak] = useState(true);
  const [seeThrough, setSeeThrough] = useState(false);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [newReminder, setNewReminder] = useState("");
  const [reminderError, setReminderError] = useState<string | null>(null);
  const [watching, setWatching] = useState(false);
  const [lastWatch, setLastWatch] = useState<string | null>(null);
  const [showWatchLog, setShowWatchLog] = useState(true);
  // Known only in the browser; the server render assumes support, then the browser corrects it.
  const recognitionSupported = useSyncExternalStore(
    subscribeToNothing,
    () => recognitionConstructor() !== null,
    () => true,
  );

  // Unique per harness: Next.js can keep a hidden copy of this page mounted beside another.
  const uid = useId();
  const ids = {
    typed: `${uid}-typed`,
    camera: `${uid}-camera`,
    voice: `${uid}-voice`,
    inputs: `${uid}-inputs`,
    reminders: `${uid}-reminders`,
    newReminder: `${uid}-new-reminder`,
    log: `${uid}-log`,
  };
  const displayRef = useRef<HTMLDivElement>(null);
  const previewRef = useRef<HTMLVideoElement>(null);
  const backdropRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<Recognition | null>(null);
  const finalRef = useRef("");
  // After Ask: the next words, spoken or typed, are a question about the photos.
  const questionRef = useRef(false);
  const nextId = useRef(1);
  const lastTapRef = useRef(0);
  const dismissRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  function addLog(entry: Omit<LogEntry, "id" | "time">) {
    const id = nextId.current++;
    const time = new Date().toLocaleTimeString();
    setLog((current) => [{ id, time, ...entry }, ...current].slice(0, MAX_LOG));
  }

  function showWarning(reply: GlassesReply) {
    apply("", reply, undefined, false);
  }

  function apply(
    request: string,
    reply: GlassesReply,
    started?: number,
    log = true,
  ) {
    setStatus("online");
    if (reply.intent === "remind") void loadReminders();
    // A shown screen clears itself: back to the empty display.
    clearTimeout(dismissRef.current);
    if (reply.dismiss_after_s)
      dismissRef.current = setTimeout(
        () => void dismiss(reply.dismiss_after_s ?? 0),
        reply.dismiss_after_s * 1000,
      );
    setScreen(reply.screen);
    if (log)
      addLog({
        request,
        ms:
          started === undefined
            ? undefined
            : Math.round(performance.now() - started),
        intent: reply.intent,
        speech: reply.speech,
        json: JSON.stringify(reply, null, 2),
      });
    if (reply.speech && speak) say(reply.speech);
    if (reply.client_action === "capture_photo") {
      setTimeout(() => void capturePhoto(), CAPTURE_DELAY_MS);
    }
    questionRef.current = reply.client_action === "listen_question";
    if (questionRef.current) startListening("question");
  }

  function fail(request: string, error: string) {
    setStatus("offline");
    addLog({ request, error });
  }

  async function command(
    body: { text: string } | { action: string } | { question: string },
    request: string,
  ) {
    const started = performance.now();
    try {
      const { data, error, response } = await api.POST("/api/glasses/command", {
        body,
        signal: AbortSignal.timeout(70000),
      });
      if (data) apply(request, data, started);
      else fail(request, `HTTP ${response.status}: ${JSON.stringify(error)}`);
    } catch (error) {
      fail(request, String(error));
    }
  }

  async function goHome() {
    showHome(await fetchHome());
  }

  /** The shown screen's time is up: back to the empty display, from GET /home. */
  async function dismiss(after: number) {
    const request = `dismiss after ${after} s · GET /api/glasses/home`;
    const started = performance.now();
    const result = await fetchHome();
    if ("error" in result) {
      fail(request, result.error);
      return;
    }
    setScreen(result.reply.screen);
    addLog({
      request,
      ms: Math.round(performance.now() - started),
      intent: result.reply.intent,
      json: JSON.stringify(result.reply, null, 2),
    });
  }

  function showHome(result: Awaited<ReturnType<typeof fetchHome>>) {
    if ("reply" in result) apply("open the app", result.reply);
    else fail("open the app", result.error);
  }

  async function startCamera(): Promise<MediaStream | null> {
    if (streamRef.current) return streamRef.current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      streamRef.current = stream;
      setCamera(stream);
      setCameraError(null);
      return stream;
    } catch (error) {
      setCameraError(`Camera unavailable: ${error}`);
      return null;
    }
  }

  function stopCamera() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCamera(null);
    setSeeThrough(false);
    setWatching(false);
  }

  /** A JPEG of the webcam's current frame, or the reason there is none. */
  async function grabFrame(): Promise<
    { blob: Blob; width: number; height: number } | { error: string }
  > {
    const justStarted = streamRef.current === null;
    const stream = await startCamera();
    const video = previewRef.current;
    if (!stream || !video)
      return { error: "No camera: allow webcam access, then try again." };
    if (video.srcObject !== stream) video.srcObject = stream;
    await liveFrame(video);
    // A webcam that just turned on is still adjusting its exposure; its first frames are dark.
    if (justStarted)
      await new Promise((resolve) => setTimeout(resolve, CAMERA_WARMUP_MS));
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.9),
    );
    if (!blob) return { error: "Could not encode the frame as JPEG." };
    return { blob, width: canvas.width, height: canvas.height };
  }

  async function capturePhoto() {
    const request = "capture photo (webcam)";
    const frame = await grabFrame();
    if ("error" in frame) {
      fail(request, frame.error);
      return;
    }
    const form = new FormData();
    form.append("photo", frame.blob, "capture.jpg");
    try {
      // openapi-fetch sends JSON; multipart goes through fetch directly.
      // The backend waits for the memory API to store and describe the photo.
      const started = performance.now();
      const response = await fetch("/api/glasses/photos", {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(90000),
      });
      if (!response.ok) {
        fail(request, `HTTP ${response.status}: ${await response.text()}`);
        return;
      }
      apply(
        `${request}, ${frame.width}×${frame.height}`,
        (await response.json()) as GlassesReply,
        started,
      );
      onPhotoStored?.();
    } catch (error) {
      fail(request, String(error));
    }
  }

  async function loadReminders() {
    const request = "GET /api/glasses/reminders";
    const started = performance.now();
    try {
      const { data, response } = await api.GET("/api/glasses/reminders");
      if (!data) {
        fail(request, `HTTP ${response.status}`);
        return;
      }
      setReminders(data);
      addLog({
        request,
        ms: Math.round(performance.now() - started),
        intent: data.length
          ? `${data.length} reminder${data.length === 1 ? "" : "s"}: ${data
              .map((r) => r.target)
              .join(", ")}`
          : "no reminders",
        json: JSON.stringify(data, null, 2),
      });
    } catch (error) {
      fail(request, String(error));
    }
  }

  async function addReminder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = newReminder.trim();
    if (!text) return;
    const request = `POST /api/glasses/reminders “${text}”`;
    const started = performance.now();
    const { data, response } = await api.POST("/api/glasses/reminders", {
      body: { text },
    });
    const ms = Math.round(performance.now() - started);
    if (data)
      addLog({
        request,
        ms,
        intent: `watching for: ${data.target}`,
        json: JSON.stringify(data, null, 2),
      });
    else fail(request, `HTTP ${response.status}`);
    if (data) {
      setNewReminder("");
      setReminderError(null);
      void loadReminders();
    } else
      setReminderError(
        response.status === 422
          ? "Couldn’t tell what to watch for: name the thing, like “the stool”."
          : `HTTP ${response.status}`,
      );
  }

  async function deleteReminder(id: number) {
    const request = `DELETE /api/glasses/reminders/${id}`;
    const started = performance.now();
    const { response } = await api.DELETE(
      "/api/glasses/reminders/{reminder_id}",
      { params: { path: { reminder_id: id } } },
    );
    if (response.ok)
      addLog({
        request,
        ms: Math.round(performance.now() - started),
        intent: "deleted",
      });
    else fail(request, `HTTP ${response.status}`);
    void loadReminders();
  }

  async function pretendSeen(reminder: Reminder) {
    const request = `pretend “${reminder.target}” is in view · POST /api/glasses/reminders/${reminder.id}/trigger`;
    const started = performance.now();
    const { data, response } = await api.POST(
      "/api/glasses/reminders/{reminder_id}/trigger",
      { params: { path: { reminder_id: reminder.id } } },
    );
    if (data) apply(request, data, started);
    else fail(request, `HTTP ${response.status}`);
  }

  /** One watched frame: the backend checks it for every reminder's object. */
  async function watchOnce() {
    const frame = await grabFrame();
    if ("error" in frame) {
      setLastWatch(`Stopped watching: ${frame.error}`);
      setWatching(false);
      addLog({ request: "watch frame", error: frame.error, watch: true });
      return;
    }
    const request = `watch frame ${frame.width}×${frame.height} · POST /api/glasses/watch`;
    const form = new FormData();
    form.append("photo", frame.blob, "watch.jpg");
    try {
      const started = performance.now();
      const response = await fetch("/api/glasses/watch", {
        method: "POST",
        body: form,
        signal: AbortSignal.timeout(90000),
      });
      if (!response.ok) {
        const error = `HTTP ${response.status}: ${await response.text()}`;
        setLastWatch(error);
        addLog({ request, error, watch: true });
        return;
      }
      const result = (await response.json()) as WatchResult;
      const ms = Math.round(performance.now() - started);
      const outcome = result.warning
        ? `warning: ${result.seen.join(", ")} in view`
        : result.seen.length
          ? `sees ${result.seen.join(", ")}, already warned in the last minute`
          : "nothing to warn about";
      setLastWatch(
        `${new Date().toLocaleTimeString()} · ${outcome} · ${result.note} · ${ms} ms`,
      );
      addLog({
        request,
        ms,
        intent: `${outcome} · ${result.note}`,
        speech: result.warning?.speech,
        json: JSON.stringify(result, null, 2),
        watch: true,
      });
      if (result.warning) {
        // Drawn and spoken like any reply; the line above already logged it.
        showWarning(result.warning);
        void loadReminders();
      }
    } catch (error) {
      setLastWatch(String(error));
      addLog({ request, error: String(error), watch: true });
    }
  }

  /** The capture button on the glasses' frame: take a photo. */
  function captureButton() {
    void command({ action: "take_photo" }, "capture button");
  }

  /** The Neural Band's double index-finger tap: ask about the photos. */
  function doubleTap() {
    void command({ action: "double_tap" }, "double tap");
  }

  /**
   * "command": hold to talk, the words are a command. "question": after Ask, the mic closes on
   * a pause and the words go to the memory as a question; silence goes back home.
   */
  function startListening(mode: "command" | "question" = "command") {
    if (recognitionRef.current) return;
    const Recognition = recognitionConstructor();
    if (!Recognition) {
      if (mode === "question")
        setMicError("No speech recognition here: use Chrome, or type below.");
      return;
    }
    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.continuous = mode === "command";
    recognition.interimResults = true;
    finalRef.current = "";
    recognition.onresult = (event) => {
      const { final, interim } = transcripts(event);
      finalRef.current = final;
      setHeard([final, interim].filter(Boolean).join(" "));
    };
    let blocked = false;
    recognition.onerror = (event) => {
      const problem =
        event.error === "not-allowed"
          ? "Microphone access was blocked."
          : event.error === "network"
            ? "This browser can’t reach its speech service (Chrome sends the audio to Google; Brave, Chromium and some networks block it). Use Google Chrome, Edge or Safari."
            : event.error === "no-speech" || event.error === "aborted"
              ? null
              : `Speech recognition error: ${event.error}.`;
      if (!problem) return;
      // No words will come: after Ask, the Listening screen waits for a typed question.
      blocked = event.error === "not-allowed" || event.error === "network";
      setMicError(
        blocked && mode === "question"
          ? `${problem} Type the question below.`
          : problem,
      );
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
      const text = finalRef.current.trim();
      if (mode === "question") {
        // Typed instead, or no mic: the Listening screen waits for a typed question.
        if (!questionRef.current || blocked) return;
        questionRef.current = false;
        void command({ question: text }, `asked “${text}”`);
      } else if (text) void command({ text }, `said “${text}”`);
    };
    recognitionRef.current = recognition;
    setMicError(null);
    setHeard("");
    setListening(true);
    recognition.start();
  }

  function stopListening() {
    // Stopping (not aborting) lets the last words settle; onend sends them.
    recognitionRef.current?.stop();
  }

  function submitTyped(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = typed.trim();
    if (!text) return;
    setTyped("");
    if (questionRef.current) {
      questionRef.current = false;
      recognitionRef.current?.abort();
      void command({ question: text }, `asked “${text}” (typed)`);
    } else void command({ text }, `said “${text}” (typed)`);
  }

  // Open the app once on load.
  const onFirstHome = useEffectEvent(showHome);
  const onFirstReminders = useEffectEvent(() => loadReminders());
  useEffect(() => {
    fetchHome().then(onFirstHome);
    void onFirstReminders();
  }, []);

  // While watching, check a frame, wait, check the next: never two at once.
  const onWatchTick = useEffectEvent(() => watchOnce());
  useEffect(() => {
    if (!watching) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const started = performance.now();
      await onWatchTick();
      const left = WATCH_EVERY_MS - (performance.now() - started);
      if (!stopped) timer = setTimeout(tick, Math.max(0, left));
    };
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [watching]);

  // Both <video>s show the same webcam stream.
  useEffect(() => {
    for (const video of [previewRef.current, backdropRef.current]) {
      if (video) video.srcObject = camera;
    }
  }, [camera, seeThrough]);

  // Like the glasses, focus the first button of a new screen, unless the user is typing.
  useEffect(() => {
    const active = document.activeElement;
    if (
      active instanceof HTMLInputElement ||
      active instanceof HTMLTextAreaElement
    )
      return;
    displayRef.current
      ?.querySelector<HTMLButtonElement>("button")
      ?.focus({ preventScroll: true });
  }, [screen]);

  // Hold Space to talk, like holding the glasses' touchpad.
  const onKey = useEffectEvent((event: KeyboardEvent) => {
    if (!active || event.repeat) return;
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea")) return;
    // C: the glasses' capture button.
    if (event.code === "KeyC" && event.type === "keydown") {
      captureButton();
      return;
    }
    // Double-press T: the Neural Band's double index-finger tap.
    if (event.code === "KeyT" && event.type === "keydown") {
      const now = performance.now();
      if (now - lastTapRef.current < DOUBLE_TAP_MS) {
        lastTapRef.current = 0;
        doubleTap();
      } else lastTapRef.current = now;
      return;
    }
    if (event.code !== "Space" || target.closest("button")) return;
    event.preventDefault();
    if (event.type === "keydown") startListening();
    else stopListening();
  });
  useEffect(() => {
    const handler = (event: KeyboardEvent) => onKey(event);
    window.addEventListener("keydown", handler);
    window.addEventListener("keyup", handler);
    return () => {
      window.removeEventListener("keydown", handler);
      window.removeEventListener("keyup", handler);
    };
  }, []);

  // Release the webcam when leaving the page. Next.js keeps a page it navigates away from
  // mounted but hidden, and runs this then too, so the controls must show the camera as off.
  const releaseCamera = useEffectEvent(stopCamera);
  useEffect(() => () => releaseCamera(), []);

  return (
    <div className={`gh-root ${embedded ? "gh-embedded" : "gh-page"}`}>
      {embedded ? (
        <StatusLine status={status} />
      ) : (
        <header className="gh-header">
          <div>
            <p className="gh-eyebrow">GLASSES HARNESS</p>
            <h1>What the wearer sees</h1>
          </div>
          <div className="gh-header-side">
            <nav className="gh-nav" aria-label="Pages">
              <Link href="/memory">Memory lab</Link>
              <Link href="/">
                <ArrowLeft size={14} aria-hidden="true" /> Home
              </Link>
            </nav>
            <StatusLine status={status} />
          </div>
        </header>
      )}

      <main className="gh-main">
        <section className="gh-stage" aria-label="Display">
          <Display
            ref={displayRef}
            screen={screen}
            listening={listening}
            seeThrough={seeThrough && camera !== null}
            onAction={(action) =>
              void command({ action }, `pressed “${action}”`)
            }
          >
            {seeThrough && camera ? (
              <video
                ref={backdropRef}
                className="gh-backdrop"
                autoPlay
                muted
                playsInline
              />
            ) : null}
          </Display>
          <div className="gh-stage-bar">
            <span>
              600 × 600 · C = capture button · T T = double tap (ask) · hold
              Space = talk
            </span>
            <span className="gh-stage-actions">
              <label className="gh-toggle">
                <input
                  type="checkbox"
                  checked={seeThrough}
                  disabled={!camera}
                  onChange={(event) => setSeeThrough(event.target.checked)}
                />
                See-through
              </label>
              <button
                type="button"
                className="gh-chip"
                onClick={() => void goHome()}
              >
                <RotateCcw size={14} aria-hidden="true" /> Restart app
              </button>
            </span>
          </div>
        </section>

        <aside className="gh-controls">
          <section className="gh-card" aria-labelledby={ids.inputs}>
            <h2 id={ids.inputs}>Glasses &amp; Neural Band</h2>
            <p className="gh-note">
              The display has no buttons: the wearer uses the capture button on
              the frame and the Neural Band.
            </p>
            <div className="gh-controls-row">
              <button
                type="button"
                className="gh-primary"
                onClick={() => captureButton()}
              >
                <Camera size={16} aria-hidden="true" /> Capture button · C
              </button>
              <button
                type="button"
                className="gh-primary"
                onClick={() => doubleTap()}
              >
                <Pointer size={16} aria-hidden="true" /> Double tap (ask) · T T
              </button>
            </div>
          </section>

          <section className="gh-card" aria-labelledby={ids.camera}>
            <div className="gh-card-heading">
              <h2 id={ids.camera}>Camera</h2>
              {camera ? (
                <button type="button" className="gh-chip" onClick={stopCamera}>
                  <VideoOff size={14} aria-hidden="true" /> Turn off
                </button>
              ) : (
                <button
                  type="button"
                  className="gh-chip"
                  onClick={() => void startCamera()}
                >
                  <Video size={14} aria-hidden="true" /> Turn on webcam
                </button>
              )}
            </div>
            <div className="gh-preview">
              <video
                ref={previewRef}
                autoPlay
                muted
                playsInline
                hidden={!camera}
              />
              {!camera ? (
                <p>The webcam stands in for the glasses&apos; camera.</p>
              ) : null}
            </div>
            {cameraError ? <p className="gh-error">{cameraError}</p> : null}
          </section>

          <section className="gh-card" aria-labelledby={ids.voice}>
            <div className="gh-card-heading">
              <h2 id={ids.voice}>Voice</h2>
              <button
                type="button"
                className="gh-chip"
                aria-pressed={speak}
                onClick={() => setSpeak((on) => !on)}
              >
                {speak ? (
                  <Volume2 size={14} aria-hidden="true" />
                ) : (
                  <VolumeX size={14} aria-hidden="true" />
                )}
                {speak ? "Speaker on" : "Speaker off"}
              </button>
            </div>
            {recognitionSupported ? (
              <button
                type="button"
                className={`gh-talk ${listening ? "gh-talk-on" : ""}`}
                onPointerDown={() => startListening()}
                onPointerUp={stopListening}
                onPointerLeave={stopListening}
              >
                <Mic size={18} aria-hidden="true" />
                {listening ? "Listening… release to send" : "Hold to talk"}
              </button>
            ) : (
              <p className="gh-note">
                This browser has no speech recognition; use Chrome, or type
                below.
              </p>
            )}
            <p className="gh-heard" aria-live="polite">
              {heard
                ? `“${heard}”`
                : "Double tap, then say a question about your photos."}
            </p>
            {micError ? <p className="gh-error">{micError}</p> : null}
            <form className="gh-typed" onSubmit={submitTyped}>
              <label htmlFor={ids.typed} className="gh-sr-only">
                Type what the wearer says
              </label>
              <input
                id={ids.typed}
                value={typed}
                onChange={(event) => setTyped(event.target.value)}
                placeholder="…or type what the wearer says"
                maxLength={500}
                autoComplete="off"
              />
              <button
                type="submit"
                className="gh-chip"
                disabled={!typed.trim()}
              >
                <Send size={14} aria-hidden="true" /> Send
              </button>
            </form>
          </section>

          <section className="gh-card" aria-labelledby={ids.reminders}>
            <div className="gh-card-heading">
              <h2 id={ids.reminders}>Reminders</h2>
              <button
                type="button"
                className="gh-chip"
                aria-pressed={watching}
                onClick={() => setWatching((on) => !on)}
              >
                {watching ? (
                  <EyeOff size={14} aria-hidden="true" />
                ) : (
                  <Eye size={14} aria-hidden="true" />
                )}
                {watching ? "Stop watching" : "Watch"}
              </button>
            </div>
            <p className="gh-note">
              Say or type “remind me not to trip over the stool”. While
              watching, the webcam sends a frame every second and the glasses
              warn when the thing is in view.
            </p>
            {lastWatch ? (
              <p className="gh-heard" aria-live="polite">
                {lastWatch}
              </p>
            ) : null}
            {reminders.length ? (
              <ReminderList
                reminders={reminders}
                onPretend={pretendSeen}
                onDelete={deleteReminder}
              />
            ) : (
              <p className="gh-note">No reminders yet.</p>
            )}
            <form className="gh-typed" onSubmit={addReminder}>
              <label htmlFor={ids.newReminder} className="gh-sr-only">
                New reminder
              </label>
              <input
                id={ids.newReminder}
                value={newReminder}
                onChange={(event) => setNewReminder(event.target.value)}
                placeholder="Remind me not to trip over the stool"
                maxLength={500}
                autoComplete="off"
              />
              <button
                type="submit"
                className="gh-chip"
                disabled={!newReminder.trim()}
              >
                <BellPlus size={14} aria-hidden="true" /> Add
              </button>
            </form>
            {reminderError ? <p className="gh-error">{reminderError}</p> : null}
          </section>

          <section className="gh-card gh-log-card" aria-labelledby={ids.log}>
            <div className="gh-card-heading">
              <h2 id={ids.log}>Backend log</h2>
              <span className="gh-stage-actions">
                <label className="gh-toggle">
                  <input
                    type="checkbox"
                    checked={showWatchLog}
                    onChange={(event) => setShowWatchLog(event.target.checked)}
                  />
                  Watch frames
                </label>
                <button
                  type="button"
                  className="gh-chip"
                  onClick={() => setLog([])}
                >
                  Clear
                </button>
              </span>
            </div>
            <ol className="gh-log">
              {log
                .filter((entry) => showWatchLog || !entry.watch)
                .map((entry) => (
                  <li
                    key={entry.id}
                    className={entry.error ? "gh-log-error" : undefined}
                  >
                    <div className="gh-log-line">
                      <time>{entry.time}</time>
                      <span>{entry.request}</span>
                      {entry.ms !== undefined ? (
                        <span className="gh-log-ms">{entry.ms} ms</span>
                      ) : null}
                    </div>
                    {entry.error ? (
                      <p>{entry.error}</p>
                    ) : (
                      <details>
                        <summary>
                          → <code>{entry.intent}</code>
                          {entry.speech ? ` · says “${entry.speech}”` : ""}
                        </summary>
                        <pre>{entry.json}</pre>
                      </details>
                    )}
                  </li>
                ))}
            </ol>
          </section>
        </aside>
      </main>
    </div>
  );
}

function ReminderList({
  reminders,
  onPretend,
  onDelete,
}: {
  reminders: Reminder[];
  onPretend: (reminder: Reminder) => void;
  onDelete: (id: number) => void;
}) {
  return (
    <ul className="gh-reminders">
      {reminders.map((reminder) => (
        <li key={reminder.id}>
          <div>
            <strong>{reminder.target}</strong>
            <small title={reminder.text}>{reminder.text}</small>
          </div>
          <button
            type="button"
            className="gh-chip"
            title="Show the warning as if it were in view"
            onClick={() => onPretend(reminder)}
          >
            <Zap size={14} aria-hidden="true" /> Pretend seen
          </button>
          <button
            type="button"
            className="gh-chip"
            aria-label={`Delete the reminder about ${reminder.target}`}
            onClick={() => onDelete(reminder.id)}
          >
            <Trash2 size={14} aria-hidden="true" />
          </button>
        </li>
      ))}
    </ul>
  );
}

function StatusLine({ status }: { status: Status }) {
  return (
    <p className={`gh-status gh-status-${status}`} role="status">
      <span aria-hidden="true" />
      {status === "online"
        ? "Backend connected"
        : status === "offline"
          ? "Backend unreachable"
          : "Connecting…"}
    </p>
  );
}
