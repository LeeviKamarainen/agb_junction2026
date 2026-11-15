"use client";

import { useEffect, useEffectEvent, useRef, useState } from "react";

export type Facing = "environment" | "user";

// Let the exposure settle before the first frame is remembered: measured on laptop webcams,
// whose first frames come out dark.
const SETTLE_MS = 800;

/** The visitor's camera: started on demand, front or back on a phone, stopped on unmount. */
export function useCamera() {
  const streamRef = useRef<MediaStream | null>(null);
  const released = useRef(false); // the page let go of the camera (hidden or removed)
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [facing, setFacing] = useState<Facing>("environment");
  const [cameras, setCameras] = useState(0);
  const [busy, setBusy] = useState(false);

  /** Opens the camera; the reason it could not, or null. */
  async function start(direction: Facing = facing): Promise<string | null> {
    if (streamRef.current) return null;
    setBusy(true);
    try {
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error("open this site over HTTPS to use your camera");
      released.current = false;
      const opened = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: direction }, width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      // Known only once the camera is allowed: whether there is a second one to switch to.
      const devices = await navigator.mediaDevices.enumerateDevices();
      const count = devices.filter((d) => d.kind === "videoinput").length;
      // A phone or tablet has a front and a back camera even when it lists only one.
      setCameras(navigator.maxTouchPoints > 0 ? Math.max(count, 2) : count);
      await new Promise((resolve) => setTimeout(resolve, SETTLE_MS));
      // The page went away while the visitor was allowing the camera: nobody will stop it later.
      if (released.current) {
        opened.getTracks().forEach((track) => track.stop());
        return null;
      }
      // Only now, settled: the page streams whatever this gives it.
      streamRef.current = opened;
      setStream(opened);
      return null;
    } catch (problem) {
      return `Camera unavailable: ${problem instanceof Error ? problem.message : problem}`;
    } finally {
      setBusy(false);
    }
  }

  function stop() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setStream(null);
  }

  async function flip(): Promise<string | null> {
    const next = facing === "environment" ? "user" : "environment";
    setFacing(next);
    stop();
    return start(next);
  }

  // Hidden by Next or removed: the camera goes off, and one still being allowed is stopped as it
  // arrives (start checks `released`).
  const release = useEffectEvent(() => {
    released.current = true;
    stop();
  });
  useEffect(() => () => release(), []);

  return { stream, facing, cameras, busy, start, stop, flip };
}
