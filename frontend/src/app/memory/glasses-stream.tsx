"use client";

import {
  Bell,
  Camera,
  Circle,
  History,
  LoaderCircle,
  Send,
  Square,
  Video,
  VideoOff,
} from "lucide-react";
import {
  type MouseEvent,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import type { components } from "@/lib/api/schema";

import type { LayoutNode } from "../glasses/types";
import { errorText } from "./types";

export type GlassesReply = components["schemas"]["GlassesReply"];
type FrameReply = components["schemas"]["FrameReply"];
type Message = components["schemas"]["GlassesMessage"];

const FPS = 2;
const MESSAGE_MS = 8_000; // how long a trigger's message stays on the glasses
const DOUBLE_TAP_MS = 450; // two taps closer than this are a double tap

/** The glasses' déjà vu screen in the glasses view: its stills with their captions, and its text. */
function Recalled({ screen }: { screen: LayoutNode }) {
  const stills: { src: string; alt: string; caption: string }[] = [];
  const lines: { value: string; heading: boolean }[] = [];
  (function walk(node: LayoutNode) {
    if (node.type === "text")
      lines.push({ value: node.value, heading: node.style === "HEADING" });
    if (node.type !== "flexbox") return;
    const [image, caption] = node.children;
    // A still is a box of an image and the caption under it.
    if (image?.type === "image" && caption?.type === "text")
      stills.push({ src: image.src, alt: image.alt, caption: caption.value });
    else node.children.forEach(walk);
  })(screen);
  return (
    <span
      role="status"
      className="absolute inset-x-4 bottom-4 flex flex-col gap-2 rounded-lg bg-black/75 p-3 text-sm text-white"
    >
      {lines
        .filter((line) => line.heading)
        .map((line) => (
          <span
            key={line.value}
            className="flex items-center gap-1.5 font-semibold"
          >
            <History size={14} aria-hidden="true" /> {line.value}
          </span>
        ))}
      {stills.length > 0 && (
        <span className="grid grid-cols-3 gap-2">
          {stills.map((still) => (
            <span key={still.src} className="flex flex-col gap-1">
              {/* A plain <img>: the backend serves the still as it is. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={still.src}
                alt={still.alt}
                className={`aspect-video w-full rounded-md object-cover ${
                  still.caption.startsWith("Same") ? "ring-2 ring-white" : ""
                }`}
              />
              <span className="text-xs text-white/70">{still.caption}</span>
            </span>
          ))}
        </span>
      )}
      {lines
        .filter((line) => !line.heading)
        .map((line) => (
          <span key={line.value}>{line.value}</span>
        ))}
    </span>
  );
}

/**
 * A stand-in for the glasses and the phone: the webcam is the glasses' camera. A double click on
 * the glasses view (the Neural Band's double tap) starts or stops a 2 fps stream to
 * /api/glasses/frames; a single click takes one photo. The view shows what the glasses display
 * would: a small icon top right while recording or sending, a trigger's message, and the
 * reply to a déjà vu question (`recalled`), which takes the message's place.
 */
export function GlassesStream({
  onReply,
  recalled,
}: {
  /** Every reply, e.g. to show each trigger's score and level. */
  onReply: (reply: FrameReply) => void;
  /** The glasses' reply to "where have I seen this one?", while it is on the display. */
  recalled?: GlassesReply | null;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const cameraRef = useRef<MediaStream | null>(null);
  const busy = useRef(false); // one request at a time; frames due meanwhile are skipped
  const singlePending = useRef(false); // a photo asked for while streaming
  const streamId = useRef("");
  const lastTap = useRef(0);
  const singleTap = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState<Message | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState({ sent: 0, stored: 0, skipped: 0 });
  const [lastNote, setLastNote] = useState("");

  async function startCamera(): Promise<boolean> {
    if (cameraRef.current) return true;
    try {
      cameraRef.current = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      setCameraOn(true);
      setError(null);
      await new Promise((resolve) => setTimeout(resolve, 800)); // let the exposure settle
      return true;
    } catch (problem) {
      setError(`Webcam unavailable: ${problem}`);
      return false;
    }
  }

  function stopCamera() {
    setStreaming(false);
    cameraRef.current?.getTracks().forEach((track) => track.stop());
    cameraRef.current = null;
    setCameraOn(false);
  }

  async function grab(): Promise<Blob | null> {
    const video = videoRef.current;
    if (!video || video.readyState < 2) return null;
    const canvas = document.createElement("canvas");
    // The glasses stream at 504x896; send about that much, not the webcam's full size.
    const scale = Math.min(
      1,
      896 / Math.max(video.videoWidth, video.videoHeight),
    );
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas
      .getContext("2d")
      ?.drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  }

  async function send(source: "stream" | "single") {
    if (busy.current) return;
    busy.current = true;
    if (source === "single") {
      singlePending.current = false;
      setSending(true);
    }
    try {
      const blob = await grab();
      if (!blob) return;
      const form = new FormData();
      form.append("frame", blob, "frame.jpg");
      form.append("source", source);
      form.append("stream_id", source === "stream" ? streamId.current : "");
      form.append("seen_at", new Date().toISOString());
      const response = await fetch("/api/glasses/frames", {
        method: "POST",
        body: form,
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        setError(errorText(body, `The backend answered ${response.status}`));
        return;
      }
      const reply = body as FrameReply;
      setError(null);
      setStats((s) => ({
        sent: s.sent + 1,
        stored: s.stored + (reply.stored ? 1 : 0),
        skipped: s.skipped + (reply.stored ? 0 : 1),
      }));
      setLastNote(
        reply.note ??
          (reply.stored ? `stored as memory #${reply.memory_id}` : ""),
      );
      if (reply.message) setMessage(reply.message);
      onReply(reply);
    } catch {
      setError("Could not reach the backend");
    } finally {
      busy.current = false;
      if (source === "single") setSending(false);
    }
  }

  // A photo asked for while streaming goes as the next frame, like the phone app does.
  const tick = useEffectEvent(
    () => void send(singlePending.current ? "single" : "stream"),
  );

  useEffect(() => {
    if (!streaming) return;
    const timer = setInterval(() => tick(), 1000 / FPS);
    return () => clearInterval(timer);
  }, [streaming]);

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), MESSAGE_MS);
    return () => clearTimeout(timer);
  }, [message]);

  useEffect(() => {
    if (videoRef.current)
      videoRef.current.srcObject = cameraOn ? cameraRef.current : null;
  }, [cameraOn]);

  const release = useEffectEvent(stopCamera);
  useEffect(() => () => release(), []);

  async function toggleStream() {
    if (streaming) {
      if (singlePending.current) {
        singlePending.current = false; // the frame it waited for will not come now
        setSending(false);
      }
      return setStreaming(false);
    }
    if (!(await startCamera())) return;
    streamId.current = crypto.randomUUID();
    setStreaming(true);
  }

  async function single() {
    if (streaming) {
      // A stream frame is usually uploading right now; send the photo as the next frame instead.
      singlePending.current = true;
      setSending(true);
      return;
    }
    if (!(await startCamera())) return;
    await send("single");
  }

  // Like the Neural Band: one tap takes a photo, a double tap starts or stops the stream. A
  // single tap waits a moment to be sure no second tap follows.
  function tap(event: MouseEvent) {
    event.preventDefault();
    const now = performance.now();
    if (now - lastTap.current < DOUBLE_TAP_MS) {
      if (singleTap.current) clearTimeout(singleTap.current);
      singleTap.current = null;
      lastTap.current = 0;
      void toggleStream();
      return;
    }
    lastTap.current = now;
    singleTap.current = setTimeout(() => {
      singleTap.current = null;
      void single();
    }, DOUBLE_TAP_MS);
  }

  return (
    <section
      aria-labelledby="stream-title"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4"
    >
      <div>
        <h2 id="stream-title" className="font-semibold">
          Glasses stream
        </h2>
        <p className="text-xs text-muted-foreground">
          The webcam stands in for the glasses&rsquo; camera. Click the view
          once to send one photo, double click it to start or stop streaming{" "}
          {FPS} frames a second, as a Neural Band tap and double tap do. Every
          frame is embedded into the glasses collection; the frames of a stream
          are also checked against the triggers.
        </p>
      </div>

      <button
        type="button"
        onClick={tap}
        aria-label="Glasses view: click for one photo, double click to start or stop the stream"
        className="relative mx-auto aspect-square w-full max-w-[420px] overflow-hidden rounded-lg bg-black text-left"
      >
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          className={`absolute inset-0 h-full w-full object-cover opacity-35 ${cameraOn ? "" : "hidden"}`}
        />
        {!cameraOn && (
          <span className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-muted-foreground">
            Camera off. Click to take a photo, double click to stream.
          </span>
        )}
        {(streaming || sending) && (
          <span
            className="absolute top-3 right-3 flex items-center gap-1 rounded-full bg-black/60 px-2 py-1 text-xs font-medium text-white"
            aria-live="polite"
          >
            {streaming ? (
              <>
                <Circle
                  size={10}
                  className="animate-pulse fill-red-500 text-red-500"
                  aria-hidden="true"
                />
                <Video size={14} aria-hidden="true" /> REC
              </>
            ) : (
              <>
                <Send size={13} aria-hidden="true" /> Sending
              </>
            )}
          </span>
        )}
        {recalled && <Recalled screen={recalled.screen} />}
        {message && !recalled && (
          <span
            className="absolute inset-x-4 bottom-4 flex flex-col gap-1 rounded-lg bg-black/75 p-3 text-white"
            role="status"
          >
            {/* As on the glasses: a trigger gets a bell and a heading, a reply a small label. */}
            {message.type === "trigger" ? (
              <span className="flex items-center gap-1.5 text-sm font-semibold">
                <Bell size={14} aria-hidden="true" /> {message.title}
              </span>
            ) : (
              message.title && (
                <span className="text-xs text-white/70">{message.title}</span>
              )
            )}
            <span className="text-sm">{message.text}</span>
          </span>
        )}
      </button>

      <div className="flex flex-wrap gap-2">
        <Button
          onClick={() => void toggleStream()}
          variant={streaming ? "destructive" : "default"}
        >
          {streaming ? (
            <Square aria-hidden="true" />
          ) : (
            <Video aria-hidden="true" />
          )}
          {streaming ? "Stop stream" : `Start stream (${FPS} fps)`}
        </Button>
        <Button
          variant="secondary"
          onClick={() => void single()}
          disabled={sending}
        >
          {sending ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Camera aria-hidden="true" />
          )}
          Send one photo
        </Button>
        {cameraOn && (
          <Button variant="ghost" onClick={stopCamera}>
            <VideoOff aria-hidden="true" /> Camera off
          </Button>
        )}
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {stats.sent} sent · {stats.stored} stored · {stats.skipped} not stored
        {lastNote && ` · last: ${lastNote}`}
      </p>
      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
