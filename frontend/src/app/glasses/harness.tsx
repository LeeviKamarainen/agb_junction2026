"use client";

import {
  ArrowLeft,
  BellPlus,
  Eye,
  EyeOff,
  Mic,
  Maximize,
  Minimize,
  ChevronDown,
  Pointer,
  RotateCcw,
  SwitchCamera,
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

import type { components } from "@/lib/api/schema";
import { UserMenu } from "@/components/user-menu";
import { api } from "@/lib/api/browser";

import {
  type ClientMessage,
  GlassesSocket,
  type ServerMessage,
  type SocketState,
} from "./channel";
import { Display } from "./display";
import {
  type Recognition,
  recognitionConstructor,
  say,
  transcripts,
} from "./speech";
import type { FlexBoxNode, GlassesReply, Reminder } from "./types";

type LogEntry = {
  id: number;
  time: string;
  request: string;
  intent?: string;
  speech?: string | null;
  ms?: number;
  error?: string;
  json?: string;
  /** A watched frame or a ping: these come every second and can be hidden. */
  watch?: boolean;
};

/** A message sent over the socket, waiting for its answer. */
type Pending = {
  request: string;
  started: number;
  kind: "command" | "frame" | "ping";
};

const CAMERA_WARMUP_MS = 800;
const subscribeToNothing = () => () => {};
const landscapeQuery = "(orientation: landscape) and (max-height: 700px)";
function subscribeLandscape(changed: () => void) {
  const media = window.matchMedia(landscapeQuery);
  media.addEventListener("change", changed);
  return () => media.removeEventListener("change", changed);
}
const isLandscapePhone = () => window.matchMedia(landscapeQuery).matches;

// How often the camera sends a frame while watching. The backend checks the newest and drops
// any that arrive while it is busy, so a slow vision model never builds a queue.
const WATCH_EVERY_MS = 1000;
// An idle socket is pinged this often, so a dead connection shows in the status line.
const PING_EVERY_MS = 20_000;
// Two taps of the key within this time are a double tap.
const DOUBLE_TAP_MS = 400;

/** Resolves once the video is playing and has painted a new frame. */
async function liveFrame(video: HTMLVideoElement): Promise<boolean> {
  const deadline = performance.now() + 5000;
  while (video.paused || video.readyState < 2 || !video.videoWidth) {
    if (performance.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return new Promise<boolean>((resolve) => {
    let callback: number | undefined;
    const timeout = setTimeout(() => {
      if (callback !== undefined) video.cancelVideoFrameCallback(callback);
      resolve(false);
    }, 3000);
    const painted = () => {
      clearTimeout(timeout);
      resolve(true);
    };
    if ("requestVideoFrameCallback" in video)
      callback = video.requestVideoFrameCallback(painted);
    else setTimeout(painted, 100);
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
 * speechSynthesis = speakers, T T = Neural Band double tap. The rest is in the backend: commands,
 * watched frames and pushed warnings go over its WebSocket (/api/glasses/ws, see channel.ts); the
 * home screen and reminders over its HTTP routes.
 */
type HarnessProps = {
  onFrame?: (reply: components["schemas"]["FrameReply"]) => void;
  recalled?: GlassesReply | null;
  /** Inside another page (the Memory lab): no page header, one column. */
  embedded?: boolean;
  /** False while hidden behind another tab: Space no longer starts the microphone. */
  active?: boolean;
};

export function GlassesHarness({
  embedded = false,
  active = true,
  onFrame,
  recalled,
}: HarnessProps = {}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const controlsRef = useRef<HTMLElement>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const [fullscreenError, setFullscreenError] = useState<string | null>(null);
  const landscape = useSyncExternalStore(
    subscribeLandscape,
    isLandscapePhone,
    () => false,
  );
  const theater = !embedded && (landscape || fullscreen);
  useEffect(() => {
    if (theater) {
      rootRef.current?.scrollIntoView({ block: "start" });
      if (rootRef.current) rootRef.current.scrollTop = 0;
    }
  }, [theater]);
  useEffect(() => {
    const changed = () =>
      setFullscreen(document.fullscreenElement === rootRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);

  async function toggleFullscreen() {
    setFullscreenError(null);
    try {
      if (document.fullscreenElement === rootRef.current)
        await document.exitFullscreen();
      else if (rootRef.current?.requestFullscreen) {
        await rootRef.current.requestFullscreen();
        rootRef.current.scrollTop = 0;
        rootRef.current.scrollIntoView({ block: "start" });
      } else
        setFullscreenError(
          "Fullscreen is unavailable here. Rotate your phone for the edge-to-edge view.",
        );
    } catch {
      setFullscreenError(
        "Could not enter fullscreen. Rotate your phone for the edge-to-edge view.",
      );
    }
  }

  const [screen, setScreen] = useState<FlexBoxNode | null>(null);
  const [link, setLink] = useState<SocketState>({
    state: "connecting",
    attempt: 1,
  });
  const [log, setLog] = useState<LogEntry[]>([]);
  const [camera, setCamera] = useState<MediaStream | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [heard, setHeard] = useState("");
  const [micError, setMicError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [speak, setSpeak] = useState(true);
  const [seeThrough, setSeeThrough] = useState(!embedded);
  const [cameraFacing, setCameraFacing] = useState<"environment" | "user">(
    "environment",
  );
  const [cameraStarting, setCameraStarting] = useState(false);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [newReminder, setNewReminder] = useState("");
  const [reminderError, setReminderError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [streamNote, setStreamNote] = useState("");
  const memoryBusy = useRef(false);
  const memoryStreamId = useRef("");
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
  const cameraGeneration = useRef(0);
  const cameraMounted = useRef(false);
  const cameraRequest = useRef<Promise<MediaStream | null> | null>(null);
  const recognitionRef = useRef<Recognition | null>(null);
  const finalRef = useRef("");
  // After a double tap: the next words, spoken or typed, are the question the mic opened for.
  const questionRef = useRef(false);
  const nextId = useRef(1);
  const lastTapRef = useRef(0);
  const socketRef = useRef<GlassesSocket | null>(null);
  const pendingRef = useRef(new Map<number, Pending>());
  const nextMessageId = useRef(1);
  const dismissRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );

  function addLog(entry: Omit<LogEntry, "id" | "time">) {
    const id = nextId.current++;
    const time = new Date().toLocaleTimeString();
    setLog((current) => [{ id, time, ...entry }, ...current].slice(0, MAX_LOG));
  }

  function apply(request: string, reply: GlassesReply, started?: number) {
    if (reply.intent === "remind") void loadReminders();
    // A shown screen clears itself: back to the empty display.
    clearTimeout(dismissRef.current);
    if (reply.dismiss_after_s)
      dismissRef.current = setTimeout(
        () => void dismiss(reply.dismiss_after_s ?? 0),
        reply.dismiss_after_s * 1000,
      );
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
    // A trigger can arrive while a question is recording; it must not cancel that question.
    if (reply.client_action === "listen_question") startListening("question");
  }

  function fail(request: string, error: string, watch = false) {
    addLog({ request, error, watch });
  }

  /**
   * Sends a message over the socket, remembers it until its answer comes and logs it. `jpeg`
   * goes as a binary frame right after a frame's header. False when not sent.
   */
  function send(
    // The socket sends `auth` itself, on opening.
    message: Exclude<ClientMessage, { type: "auth" }>,
    request: string,
    jpeg?: { blob: Blob; width: number; height: number },
  ): boolean {
    const socket = socketRef.current;
    const kind = message.type;
    const id = "id" in message ? message.id : undefined;
    const sent =
      socket !== null &&
      (jpeg && kind === "frame"
        ? socket.sendJpeg({ type: kind, id: id ?? 0 }, jpeg.blob)
        : socket.send(message));
    const watch = kind === "frame" || kind === "ping" || kind === "watch";
    const what = jpeg
      ? ` + JPEG ${jpeg.width}×${jpeg.height}, ${Math.round(jpeg.blob.size / 1024)} kB`
      : "";
    if (!sent) {
      fail(
        `↑ ${kind} · ${request}`,
        socket?.open
          ? "Not sent: the previous frames are still going out."
          : "Not sent: the backend socket is not connected yet.",
        watch,
      );
      return false;
    }
    if (id !== undefined && kind !== "watch")
      pendingRef.current.set(id, { request, started: performance.now(), kind });
    addLog({
      request: `↑ ${kind}${id !== undefined ? ` #${id}` : ""} · ${request}`,
      intent: `sent${what}`,
      json: JSON.stringify(message, null, 2),
      watch,
    });
    return true;
  }

  function command(
    body: { action: string } | { question: string },
    request: string,
  ) {
    send({ type: "command", id: nextMessageId.current++, ...body }, request);
  }

  /** What the backend sent over the socket. */
  function received(message: ServerMessage) {
    const pending =
      message.id === null ? undefined : pendingRef.current.get(message.id);
    if (message.id !== null) pendingRef.current.delete(message.id);
    const label = `${message.id === null ? "" : ` #${message.id}`}${
      pending ? ` · ${pending.request}` : ""
    }`;
    const ms = pending
      ? Math.round(performance.now() - pending.started)
      : undefined;
    if (message.type === "pong") {
      addLog({
        request: `↓ pong${label}`,
        ms,
        intent: "connection alive",
        json: JSON.stringify(message, null, 2),
        watch: true,
      });
    } else if (message.type === "error") {
      fail(
        `↓ error${label}`,
        `${message.status}: ${message.detail}`,
        pending?.kind === "frame",
      );
      if (pending?.kind === "frame") setLastWatch(`Error: ${message.detail}`);
    } else if (message.type === "watch_result") {
      const { result } = message;
      const outcome = result.warning
        ? `warning: ${result.seen.join(", ")} in view`
        : result.seen.length
          ? `sees ${result.seen.join(", ")}, already warned in the last minute`
          : "nothing to warn about";
      const dropped = message.dropped
        ? ` · ${message.dropped} newer frame${message.dropped === 1 ? "" : "s"} skipped while checking`
        : "";
      setLastWatch(
        `${new Date().toLocaleTimeString()} · ${outcome} · ${result.note} · checked in ${message.ms} ms${dropped}`,
      );
      addLog({
        request: `↓ watch_result${label}`,
        ms,
        intent: `${outcome} · ${result.note}${dropped}`,
        json: JSON.stringify(message, null, 2),
        watch: true,
      });
    } else if (message.id === null) {
      // Pushed: a warning from a watched frame.
      apply(`↓ reply, pushed: warning`, message.reply);
      void loadReminders();
    } else {
      apply(`↓ reply${label}`, message.reply, pending?.started);
    }
    // Frames the backend dropped for a newer one never get an answer.
    if (message.type === "watch_result" && message.id !== null)
      for (const [id, waiting] of pendingRef.current)
        if (waiting.kind === "frame" && id < message.id)
          pendingRef.current.delete(id);
  }

  function socketChanged(next: SocketState) {
    setLink(next);
    if (next.state === "open" && watching)
      send({ type: "watch", on: true }, "watching again after reconnecting");
    if (next.state === "closed") {
      for (const [id, waiting] of pendingRef.current)
        if (waiting.kind !== "frame" && waiting.kind !== "ping")
          fail(
            `#${id} · ${waiting.request}`,
            "The socket closed before the answer came.",
          );
      pendingRef.current.clear();
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

  function startCamera(facing = cameraFacing): Promise<MediaStream | null> {
    if (!cameraMounted.current) return Promise.resolve(null);
    if (streamRef.current) return Promise.resolve(streamRef.current);
    if (cameraRequest.current) return cameraRequest.current;
    const generation = cameraGeneration.current;
    setCameraStarting(true);
    const request = (async () => {
      await Promise.resolve(); // publish the shared request before any synchronous API error
      try {
        if (!navigator.mediaDevices?.getUserMedia) {
          throw new Error(
            "Open this site over HTTPS to use your phone camera.",
          );
        }
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: facing },
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
          audio: false,
        });
        if (!cameraMounted.current || generation !== cameraGeneration.current) {
          stream.getTracks().forEach((track) => track.stop());
          return null;
        }
        streamRef.current = stream;
        setCamera(stream);
        setSeeThrough(!embedded);
        setCameraError(null);
        return stream;
      } catch (error) {
        if (cameraMounted.current && generation === cameraGeneration.current)
          setCameraError(`Camera unavailable: ${error}`);
        return null;
      } finally {
        if (generation === cameraGeneration.current) {
          cameraRequest.current = null;
          if (cameraMounted.current) setCameraStarting(false);
        }
      }
    })();
    cameraRequest.current = request;
    return request;
  }

  function stopCamera() {
    cameraGeneration.current++;
    cameraRequest.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCameraStarting(false);
    setCamera(null);
    setSeeThrough(false);
    setWatching(false);
    setStreaming(false);
  }

  async function switchCamera() {
    const next = cameraFacing === "environment" ? "user" : "environment";
    const resumeStreaming = streaming;
    const resumeWatching = watching;
    const resumeSeeThrough = seeThrough;
    stopCamera();
    setCameraFacing(next);
    const stream = await startCamera(next);
    if (stream && streamRef.current === stream) {
      setSeeThrough(resumeSeeThrough);
      setWatching(resumeWatching);
      if (resumeStreaming) memoryStreamId.current = crypto.randomUUID();
      setStreaming(resumeStreaming);
    }
  }

  /** A JPEG of the webcam's current frame, or the reason there is none. */
  async function grabFrame(): Promise<
    { blob: Blob; width: number; height: number } | { error: string }
  > {
    const justStarted = streamRef.current === null;
    const stream = await startCamera();
    const video = previewRef.current;
    if (!stream || !video)
      return { error: "No camera: allow camera access, then try again." };
    if (video.srcObject !== stream) video.srcObject = stream;
    if (!(await liveFrame(video)))
      return {
        error: "Camera did not produce a frame. Try turning it off and on.",
      };
    // A webcam that just turned on is still adjusting its exposure; its first frames are dark.
    if (justStarted)
      await new Promise((resolve) => setTimeout(resolve, CAMERA_WARMUP_MS));
    if (streamRef.current !== stream || !cameraMounted.current)
      return { error: "Camera was stopped before the photo was ready." };
    const canvas = document.createElement("canvas");
    const scale = Math.min(
      1,
      896 / Math.max(video.videoWidth, video.videoHeight),
    );
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas
      .getContext("2d")
      ?.drawImage(video, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", 0.9),
    );
    if (!blob) return { error: "Could not encode the frame as JPEG." };
    return { blob, width: canvas.width, height: canvas.height };
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

  async function toggleMemoryStream() {
    if (streaming) {
      setWatching(false);
      setStreaming(false);
      return;
    }
    if (!(await startCamera())) return;
    memoryStreamId.current = crypto.randomUUID();
    setStreaming(true);
    setWatching(true);
  }

  // Same memory/trigger pipeline as the phone app. Only one upload in flight; skip overdue frames.
  async function memoryTick(stopped: () => boolean) {
    if (memoryBusy.current) return;
    memoryBusy.current = true;
    try {
      const frame = await grabFrame();
      if (stopped()) return;
      if ("error" in frame) {
        setStreamNote(frame.error);
        setStreaming(false);
        return;
      }
      const form = new FormData();
      form.append("frame", frame.blob, "frame.jpg");
      form.append("source", "stream");
      form.append("stream_id", memoryStreamId.current);
      form.append("seen_at", new Date().toISOString());
      const { data, error } = await api.POST("/api/glasses/frames", {
        body: form as never,
        bodySerializer: () => form,
      });
      if (stopped()) return;
      if (!data) {
        setStreamNote(
          typeof error?.detail === "string"
            ? error.detail
            : "Stream upload failed. Check the backend connection.",
        );
        return;
      }
      setStreamNote(
        data.note ?? (data.stored ? "Memory stored" : "Frame checked"),
      );
      onFrame?.(data);
      if (data.message)
        apply("Stream", {
          intent: "trigger",
          speech: data.message.text,
          dismiss_after_s: 8,
          screen: {
            type: "flexbox",
            direction: "column",
            justify: "end",
            align: "stretch",
            padding: 32,
            gap: 16,
            children: [
              { type: "text", value: data.message.text, style: "BODY" },
            ],
          },
        });
    } catch {
      if (!stopped()) setStreamNote("Could not reach the backend. Retrying…");
    } finally {
      memoryBusy.current = false;
    }
  }
  const onMemoryTick = useEffectEvent(memoryTick);
  useEffect(() => {
    if (!streaming || !active) return;
    let stopped = false;
    const timer = setInterval(() => void onMemoryTick(() => stopped), 500);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [streaming, active]);
  const showRecalled = useEffectEvent((reply: GlassesReply) =>
    apply("Memory question", reply),
  );
  useEffect(() => {
    // Synchronize the emulator with a reply received by the Memory panel's chat.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (recalled) showRecalled(recalled);
  }, [recalled]);

  /** One watched frame: the backend checks the newest for every reminder's object. */
  async function watchOnce(stopped: () => boolean) {
    const frame = await grabFrame();
    // Stopped while the frame was being grabbed: the backend no longer expects it.
    if (stopped()) return;
    if ("error" in frame) {
      setLastWatch(`Stopped watching: ${frame.error}`);
      setWatching(false);
      fail("watch frame", frame.error, true);
      return;
    }
    send({ type: "frame", id: nextMessageId.current++ }, "watch frame", frame);
  }

  function ping() {
    if (socketRef.current?.open)
      send({ type: "ping", id: nextMessageId.current++ }, "keepalive");
  }

  /** The Neural Band's double index-finger tap: ask a question. */
  function doubleTap() {
    void command({ action: "double_tap" }, "double tap");
  }

  /**
   * Either way the words go to the memory as a question. "hold": hold to talk, sent on release.
   * "question": after a double tap, the mic closes on a pause; silence goes back home.
   */
  function startListening(mode: "hold" | "question" = "hold") {
    if (recognitionRef.current) return;
    const Recognition = recognitionConstructor();
    if (!Recognition) {
      if (mode === "question")
        setMicError("No speech recognition here: use Chrome, or type below.");
      return;
    }
    if (mode === "question") questionRef.current = true;
    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.continuous = mode === "hold";
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
        if (!text) return;
        void command({ question: text }, `asked “${text}”`);
      } else if (text) void command({ question: text }, `said “${text}”`);
    };
    recognitionRef.current = recognition;
    setMicError(null);
    setHeard("");
    setListening(true);
    try {
      recognition.start();
    } catch {
      recognitionRef.current = null;
      questionRef.current = false;
      setListening(false);
      setMicError(
        "Could not start the microphone. Try again or type your question.",
      );
    }
  }

  const pauseMicrophone = useEffectEvent(() => {
    questionRef.current = false;
    finalRef.current = "";
    recognitionRef.current?.abort();
  });
  useEffect(() => {
    if (!active) pauseMicrophone();
  }, [active]);

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
      // Typed instead of spoken after a double tap: the open microphone is no longer needed.
      questionRef.current = false;
      recognitionRef.current?.abort();
    }
    void command({ question: text }, `asked “${text}” (typed)`);
  }

  // One socket per harness, reconnecting with backoff. The browser sends the login cookie with
  // it; a login token in ?token= of the page address is sent as the first message instead.
  const onSocketMessage = useEffectEvent(received);
  const onSocketState = useEffectEvent(socketChanged);
  const onPing = useEffectEvent(ping);
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    const socket = new GlassesSocket(token, {
      onState: (state) => onSocketState(state),
      onMessage: (message) => onSocketMessage(message),
    });
    socketRef.current = socket;
    const pinger = setInterval(() => onPing(), PING_EVERY_MS);
    return () => {
      clearInterval(pinger);
      socket.close();
      socketRef.current = null;
    };
  }, []);

  // Open the app once on load.
  const onFirstHome = useEffectEvent(showHome);
  const onFirstReminders = useEffectEvent(() => loadReminders());
  useEffect(() => {
    fetchHome().then(onFirstHome);
    void onFirstReminders();
  }, []);

  // While watching, send a frame a second; the backend checks the newest one it has.
  const onWatchTick = useEffectEvent((stopped: () => boolean) =>
    watchOnce(stopped),
  );
  const onWatching = useEffectEvent((on: boolean) => {
    // Leaving the page closes the socket first: nothing to tell it then.
    if (!socketRef.current) return;
    if (!on)
      for (const [id, waiting] of pendingRef.current)
        if (waiting.kind === "frame") pendingRef.current.delete(id);
    send({ type: "watch", on }, on ? "start watching" : "stop watching");
  });
  useEffect(() => {
    if (!watching || !active) return;
    onWatching(true);
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const started = performance.now();
      await onWatchTick(() => stopped);
      const left = WATCH_EVERY_MS - (performance.now() - started);
      if (!stopped) timer = setTimeout(tick, Math.max(0, left));
    };
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
      onWatching(false);
    };
  }, [watching, active]);

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
  useEffect(() => {
    cameraMounted.current = true;
    return () => {
      cameraMounted.current = false;
      releaseCamera();
    };
  }, []);

  return (
    <div
      ref={rootRef}
      className={`gh-root ${embedded ? "gh-embedded" : "gh-page"}${theater ? " gh-theater" : ""}`}
    >
      {embedded ? (
        <StatusLine link={link} />
      ) : (
        <header className="gh-header">
          <div>
            <p className="gh-eyebrow">GLASSES HARNESS</p>
            <h1>What the wearer sees</h1>
          </div>
          <div className="gh-header-side">
            <nav className="gh-nav" aria-label="Pages">
              <UserMenu requireLogin />
              <Link href="/memory">Memory lab</Link>
              <Link href="/">
                <ArrowLeft size={14} aria-hidden="true" /> Home
              </Link>
            </nav>
            <StatusLine link={link} />
          </div>
        </header>
      )}

      <main className="gh-main">
        <section className="gh-stage" aria-label="Display">
          <Display
            ref={displayRef}
            immersive={!embedded}
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
          {!embedded ? (
            <div className="gh-pov-actions">
              {!camera ? (
                <button
                  type="button"
                  className="gh-chip"
                  onClick={() =>
                    cameraStarting ? stopCamera() : void startCamera()
                  }
                >
                  <Video size={16} aria-hidden="true" />{" "}
                  {cameraStarting ? "Cancel camera" : "Start camera"}
                </button>
              ) : null}
              <button
                type="button"
                className="gh-chip"
                aria-pressed={streaming}
                onClick={() => void toggleMemoryStream()}
              >
                {streaming ? "Stop stream" : "Start memory stream"}
              </button>
              <button
                type="button"
                className="gh-chip"
                aria-pressed={listening}
                onClick={() =>
                  listening ? stopListening() : startListening("question")
                }
              >
                <Mic size={16} aria-hidden="true" />
                {listening ? "Send question" : "Ask with microphone"}
              </button>
              <button
                type="button"
                className="gh-chip"
                onClick={() => void toggleFullscreen()}
              >
                {fullscreen ? (
                  <Minimize size={16} aria-hidden="true" />
                ) : (
                  <Maximize size={16} aria-hidden="true" />
                )}
                {fullscreen ? "Exit fullscreen" : "Fullscreen"}
              </button>
              <button
                type="button"
                className="gh-chip"
                onClick={() =>
                  controlsRef.current?.scrollIntoView({
                    behavior: "smooth",
                    block: "start",
                  })
                }
              >
                <ChevronDown size={16} aria-hidden="true" /> Controls
              </button>
              {fullscreenError ? (
                <p role="status" className="gh-error">
                  {fullscreenError}
                </p>
              ) : null}
            </div>
          ) : null}
          <div className="gh-stage-bar">
            <span>
              <span className="gh-keyboard-hint">
                600 × 600 · T T = double tap (ask) · hold Space = talk
              </span>
              <span className="gh-phone-hint">
                Turn your phone sideways · rear camera preferred
              </span>
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

        <aside ref={controlsRef} className="gh-controls">
          <section className="gh-card" aria-labelledby={ids.camera}>
            <div className="gh-card-heading">
              <h2 id={ids.camera}>Camera</h2>
              {camera || cameraStarting ? (
                <button type="button" className="gh-chip" onClick={stopCamera}>
                  <VideoOff size={14} aria-hidden="true" />{" "}
                  {cameraStarting ? "Cancel camera" : "Turn off"}
                </button>
              ) : (
                <button
                  type="button"
                  className="gh-chip"
                  disabled={cameraStarting}
                  onClick={() => void startCamera()}
                >
                  <Video size={14} aria-hidden="true" />{" "}
                  {cameraStarting ? "Opening camera…" : "Start camera"}
                </button>
              )}
            </div>
            {camera ? (
              <button
                type="button"
                className="gh-chip"
                disabled={cameraStarting}
                onClick={() => void switchCamera()}
              >
                <SwitchCamera size={16} aria-hidden="true" /> Switch camera
              </button>
            ) : null}
            <div className="gh-preview">
              <video
                ref={previewRef}
                autoPlay
                muted
                playsInline
                hidden={!camera}
              />
              {!camera ? (
                <p>
                  Start your phone camera to see its feed here and on the
                  display. Rotate to landscape for the best view.
                </p>
              ) : null}
            </div>
            {cameraError ? <p className="gh-error">{cameraError}</p> : null}
          </section>

          <section className="gh-card" aria-labelledby={ids.inputs}>
            <h2 id={ids.inputs}>Glasses &amp; Neural Band</h2>
            <p className="gh-note">
              The display has no buttons: the wearer uses the Neural Band.
            </p>
            <div className="gh-controls-row">
              <button
                type="button"
                className="gh-primary"
                onClick={() => doubleTap()}
              >
                <Pointer size={16} aria-hidden="true" /> Double tap (ask) · T T
              </button>
            </div>
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
                : "Double tap, then ask about anything you’ve seen."}
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

          <section className="gh-card" aria-label="Memory streaming">
            <button
              type="button"
              className="gh-chip"
              aria-pressed={streaming}
              onClick={() => void toggleMemoryStream()}
            >
              {streaming ? "Stop stream" : "Start memory stream (2 fps)"}
            </button>
            <p role="status" className="gh-note">
              {streaming
                ? "Streaming to memory and checking your triggers. "
                : "Stream stopped. "}
              {streamNote}
            </p>
            <Link href="/memory?tab=stream">
              Configure memories and triggers
            </Link>
          </section>

          {embedded ? (
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
                watching, the camera sends a frame every second over the socket
                and the backend pushes a warning when the thing is in view.
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
              {reminderError ? (
                <p className="gh-error">{reminderError}</p>
              ) : null}
            </section>
          ) : null}

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
                  Frames &amp; pings
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

function StatusLine({ link }: { link: SocketState }) {
  const status =
    link.state === "open"
      ? "online"
      : link.state === "closed"
        ? "offline"
        : "connecting";
  return (
    <p className={`gh-status gh-status-${status}`} role="status">
      <span aria-hidden="true" />
      {link.state === "open"
        ? "Backend connected (WebSocket)"
        : link.state === "closed"
          ? `Backend ${link.reason}${link.retryInMs === null ? "" : `; retrying in ${Math.ceil(link.retryInMs / 1000)} s`}`
          : link.attempt > 1
            ? `Reconnecting… (attempt ${link.attempt})`
            : "Connecting…"}
    </p>
  );
}
