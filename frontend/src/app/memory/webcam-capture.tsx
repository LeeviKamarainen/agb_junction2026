"use client";

import { Camera, LoaderCircle, Video, VideoOff } from "lucide-react";
import { useEffect, useEffectEvent, useRef, useState } from "react";

import { Button } from "@/components/ui/button";

// A webcam that just turned on is still adjusting its exposure; its first frames are dark.
const WARMUP_MS = 800;

type Props = {
  /** Called with each captured frame as a JPEG file. */
  onCapture: (file: File) => void;
  disabled?: boolean;
};

/** Turn on the webcam and capture frames as image files, to add as memories. */
export function WebcamCapture({ onCapture, disabled }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const startedAt = useRef(0);
  const [on, setOn] = useState(false);
  const [starting, setStarting] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [count, setCount] = useState(0);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      streamRef.current = stream;
      startedAt.current = performance.now();
      setOn(true);
    } catch (problem) {
      setError(`Webcam unavailable: ${problem}`);
    } finally {
      setStarting(false);
    }
  }

  function stop() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setOn(false);
  }

  async function capture() {
    const video = videoRef.current;
    if (!video || !streamRef.current) return;
    setCapturing(true);
    try {
      if (video.paused) await video.play().catch(() => {});
      if (video.readyState < 2) {
        await new Promise((resolve) =>
          video.addEventListener("loadeddata", resolve, { once: true }),
        );
      }
      const warm = WARMUP_MS - (performance.now() - startedAt.current);
      if (warm > 0) await new Promise((resolve) => setTimeout(resolve, warm));
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d")?.drawImage(video, 0, 0);
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.9),
      );
      if (!blob) throw new Error("Could not encode the frame as JPEG");
      const now = new Date();
      const stamp = now.toTimeString().slice(0, 8).replaceAll(":", "");
      onCapture(
        new File([blob], `webcam-${stamp}.jpg`, {
          type: "image/jpeg",
          lastModified: now.getTime(),
        }),
      );
      setCount((n) => n + 1);
    } catch (problem) {
      setError(String(problem));
    } finally {
      setCapturing(false);
    }
  }

  // Show the stream once the <video> exists.
  useEffect(() => {
    if (videoRef.current)
      videoRef.current.srcObject = on ? streamRef.current : null;
  }, [on]);

  // Release the webcam when leaving the page (Next.js also hides pages it navigates away from).
  const release = useEffectEvent(stop);
  useEffect(() => () => release(), []);

  if (!on) {
    return (
      <div className="flex flex-col gap-1">
        <Button
          variant="secondary"
          className="h-auto py-3"
          onClick={() => void start()}
          disabled={disabled || starting}
        >
          {starting ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Video aria-hidden="true" />
          )}
          Use the webcam
        </Button>
        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <video
        ref={videoRef}
        autoPlay
        muted
        playsInline
        className="aspect-video w-full rounded-md bg-black object-cover"
      />
      <div className="flex items-center gap-2">
        <Button
          className="flex-1"
          onClick={() => void capture()}
          disabled={disabled || capturing}
        >
          {capturing ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Camera aria-hidden="true" />
          )}
          Capture
        </Button>
        <Button variant="ghost" onClick={stop}>
          <VideoOff aria-hidden="true" /> Turn off
        </Button>
      </div>
      <p className="text-xs text-muted-foreground" aria-live="polite">
        {count
          ? `${count} captured: caption them below, then upload.`
          : "Each capture joins the list below, ready to caption and upload."}
      </p>
      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
