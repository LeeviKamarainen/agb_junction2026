"use client";

import {
  ArrowLeft,
  Camera,
  Mic,
  RotateCcw,
  Send,
  Video,
  VideoOff,
  Volume2,
  VolumeX,
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
import type { FlexBoxNode, GlassesReply } from "./types";

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
};

// Long enough to see "Taking a photo…", like the shutter delay on the glasses.
const CAPTURE_DELAY_MS = 400;
const CAMERA_WARMUP_MS = 800;
const subscribeToNothing = () => () => {};

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
const MAX_LOG = 50;

/**
 * Stands in for the glasses and the Android app: webcam = camera, laptop mic = microphone,
 * speechSynthesis = speakers, arrow keys + Enter = Neural Band. Everything else happens in the
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
    log: `${uid}-log`,
  };
  const displayRef = useRef<HTMLDivElement>(null);
  const previewRef = useRef<HTMLVideoElement>(null);
  const backdropRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<Recognition | null>(null);
  const finalRef = useRef("");
  const nextId = useRef(1);

  function addLog(entry: Omit<LogEntry, "id" | "time">) {
    const id = nextId.current++;
    const time = new Date().toLocaleTimeString();
    setLog((current) => [{ id, time, ...entry }, ...current].slice(0, MAX_LOG));
  }

  function apply(request: string, reply: GlassesReply, started?: number) {
    setStatus("online");
    setScreen(reply.screen);
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
  }

  function fail(request: string, error: string) {
    setStatus("offline");
    addLog({ request, error });
  }

  async function command(
    body: { text: string } | { action: string },
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
  }

  async function capturePhoto() {
    const request = "capture photo (webcam)";
    const justStarted = streamRef.current === null;
    const stream = await startCamera();
    const video = previewRef.current;
    if (!stream || !video) {
      fail(request, "No camera: allow webcam access, then try again.");
      return;
    }
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
    if (!blob) {
      fail(request, "Could not encode the frame as JPEG.");
      return;
    }
    const form = new FormData();
    form.append("photo", blob, "capture.jpg");
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
        `${request}, ${canvas.width}×${canvas.height}`,
        (await response.json()) as GlassesReply,
        started,
      );
      onPhotoStored?.();
    } catch (error) {
      fail(request, String(error));
    }
  }

  function startListening() {
    if (recognitionRef.current) return;
    const Recognition = recognitionConstructor();
    if (!Recognition) return;
    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    finalRef.current = "";
    recognition.onresult = (event) => {
      const { final, interim } = transcripts(event);
      finalRef.current = final;
      setHeard([final, interim].filter(Boolean).join(" "));
    };
    recognition.onerror = (event) => {
      if (event.error === "not-allowed")
        setMicError("Microphone access was blocked.");
      else if (event.error !== "no-speech" && event.error !== "aborted")
        setMicError(`Speech recognition error: ${event.error}`);
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
      const text = finalRef.current.trim();
      if (text) void command({ text }, `said “${text}”`);
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
    void command({ text }, `said “${text}” (typed)`);
  }

  // Open the app once on load.
  const onFirstHome = useEffectEvent(showHome);
  useEffect(() => {
    fetchHome().then(onFirstHome);
  }, []);

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
    if (!active || event.code !== "Space" || event.repeat) return;
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, button")) return;
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
              600 × 600 · ← → ↑ ↓ move focus · Enter = pinch · hold Space = talk
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
            <button
              type="button"
              className="gh-primary"
              onClick={() => void capturePhoto()}
            >
              <Camera size={16} aria-hidden="true" /> Capture photo
            </button>
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
                onPointerDown={startListening}
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
                : "Try “take a photo” or “where did I leave my keys?”"}
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

          <section className="gh-card gh-log-card" aria-labelledby={ids.log}>
            <div className="gh-card-heading">
              <h2 id={ids.log}>Backend log</h2>
              <button
                type="button"
                className="gh-chip"
                onClick={() => setLog([])}
              >
                Clear
              </button>
            </div>
            <ol className="gh-log">
              {log.map((entry) => (
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
