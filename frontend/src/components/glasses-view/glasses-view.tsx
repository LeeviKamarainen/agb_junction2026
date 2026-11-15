"use client";

import { Maximize, Minimize } from "lucide-react";
import {
  type CSSProperties,
  type ReactNode,
  type RefObject,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { Display } from "@/app/glasses/display";
import "@/app/glasses/glasses.css";
import type { FlexBoxNode } from "@/app/glasses/types";

import { type FrameShape, TALL, WIDE } from "./glasses-frame";
import "./glasses-view.css";

export type Evidence = { src: string; alt: string; caption?: string };

type Props = {
  /** The picture: a <video> or <img>, drawn whole (object-fit: contain), never cropped. */
  children: ReactNode;
  /** The same element, to paint the blurred fill around the picture from. */
  media?: RefObject<HTMLVideoElement | HTMLImageElement | null>;
  /** What the glasses' display shows now; null clears it, as the real display is empty when idle. */
  screen: FlexBoxNode | null;
  /** The memory frame an answer rests on, under the display (the real glasses don't show it). */
  evidence?: Evidence | null;
  /** The microphone is open: the display's red recording light. */
  listening?: boolean;
  /** Labels at the top-left; each child is drawn as a pill. */
  status?: ReactNode;
  /** Centred over the picture, e.g. a Start button. */
  overlay?: ReactNode;
  className?: string;
};

const DISPLAY_PX = 600; // the glasses' display, which Display draws at this size and scales down
const FADE_MS = 250; // the display's fade in and out; glasses-view.css reads it as --gv-fade
const FILL_FPS = 10; // the blurred fill only carries the picture's colours, not its motion
const FILL_SIDE = 48; // px on the fill's long side: it is blurred anyway

const ignore = () => {}; // the screens shown here have no buttons

const hasContent = (screen: FlexBoxNode | null): screen is FlexBoxNode =>
  Boolean(screen?.children.length);

/**
 * What the wearer sees: the picture through the glasses' frame, and the glasses' display in the
 * right lens, drawn from the same layout the real glasses get. The picture sits inside the
 * lenses, never under the frame; a blurred copy of it fills the lenses around it.
 */
export function GlassesView({
  children,
  media,
  screen,
  evidence = null,
  listening = false,
  status,
  overlay,
  className,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const fillRef = useRef<HTMLCanvasElement>(null);
  const squareRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const id = useId().replace(/[^\w-]/g, "");

  // Drawn: the newest screen and evidence while there are any, kept a moment after they clear so
  // they can fade out.
  const on = hasContent(screen) || evidence !== null;
  const [kept, setKept] = useState({ screen, evidence });
  if (on && (kept.screen !== screen || kept.evidence !== evidence)) setKept({ screen, evidence });
  // The glass card behind the screen, hugging its content (in the display's own pixels), so it
  // covers no more of the picture than the text needs.
  const [card, setCard] = useState<{ top: number; bottom: number } | null>(null);
  useEffect(() => {
    if (on) return;
    const timer = setTimeout(() => {
      setKept({ screen: null, evidence: null });
      setCard(null); // measured afresh for the next screen, so it never appears at this one's size
    }, FADE_MS);
    return () => clearTimeout(timer);
  }, [on]);

  // The display's scale on the page, which sets how far its text must grow to stay readable.
  const [scale, setScale] = useState(1);
  useEffect(() => {
    const square = squareRef.current;
    if (!square) return;
    const observer = new ResizeObserver(([entry]) => {
      // A hidden view measures 0 wide: keep the scale it had rather than divide by zero.
      if (entry.contentRect.width > 0) setScale(Math.min(1, entry.contentRect.width / DISPLAY_PX));
    });
    observer.observe(square);
    return () => observer.disconnect();
  }, []);

  // Measured as each screen is drawn, before it is painted, and again whenever its parts change
  // size (the text growing to stay readable, a font arriving). A screen too long for the display
  // runs over its top rather than being cut off, and the card follows it there.
  useLayoutEffect(() => {
    const root = contentRef.current?.firstElementChild;
    if (!root || !hasContent(kept.screen)) return;
    const padding = kept.screen.padding;
    const measure = () => {
      const parts = [...root.children].filter((part) => part instanceof HTMLElement);
      if (!parts.length) return;
      const top = Math.min(...parts.map((part) => part.offsetTop)) - padding;
      const bottom = Math.max(...parts.map((part) => part.offsetTop + part.offsetHeight)) + padding;
      setCard((old) => (old?.top === top && old.bottom === bottom ? old : { top, bottom }));
    };
    measure();
    const observer = new ResizeObserver(measure);
    for (const part of root.children) observer.observe(part);
    return () => observer.disconnect();
  }, [kept.screen]);

  // The fill: the picture shrunk to a few pixels, redrawn ~10 times a second, blurred by CSS.
  useEffect(() => {
    const canvas = fillRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context || !media) return;
    let drawn = "";
    const timer = setInterval(() => {
      const element = media.current;
      const picture = element ? frameOf(element) : null;
      if (!element || !picture || picture.frame === drawn) return;
      drawn = picture.frame;
      const shrink = FILL_SIDE / Math.max(picture.width, picture.height);
      const width = Math.max(1, Math.round(picture.width * shrink));
      const height = Math.max(1, Math.round(picture.height * shrink));
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      context.drawImage(element, 0, 0, width, height);
    }, 1000 / FILL_FPS);
    return () => clearInterval(timer);
  }, [media]);

  // Next keeps a page it navigates away from, hidden, with its effects cleaned up (React's
  // <Activity>; see preserving-ui-state.md in Next's docs): a hidden video would play on, sound
  // and all, and speech would carry over to the next page. Stop both as the view hides, which a
  // layout effect's cleanup does before it is hidden; the page's own effects start them again.
  useLayoutEffect(
    () => () => {
      const element = media?.current;
      if (element instanceof HTMLVideoElement) element.pause();
      if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    },
    [media],
  );

  // Fullscreen: the browser's own where it can; an iPhone only lets a <video> go fullscreen, so
  // there the view fills the window instead.
  const [native, setNative] = useState(false);
  const [windowed, setWindowed] = useState(false);
  useEffect(() => {
    const changed = () => setNative(document.fullscreenElement === rootRef.current);
    document.addEventListener("fullscreenchange", changed);
    return () => document.removeEventListener("fullscreenchange", changed);
  }, []);
  useEffect(() => {
    if (!windowed) return;
    const leave = (event: KeyboardEvent) => {
      if (event.key === "Escape") setWindowed(false);
    };
    const page = document.documentElement.style;
    const scrolling = page.overflow; // restored as it was, should something else have set it
    page.overflow = "hidden";
    window.addEventListener("keydown", leave);
    return () => {
      page.overflow = scrolling;
      window.removeEventListener("keydown", leave);
    };
  }, [windowed]);

  async function toggleFullscreen() {
    if (document.fullscreenElement) return void (await document.exitFullscreen());
    if (windowed) return setWindowed(false);
    try {
      if (rootRef.current?.requestFullscreen) return void (await rootRef.current.requestFullscreen());
    } catch {
      // Refused (inside an iframe, say): fill the window instead.
    }
    setWindowed(true);
  }

  const full = native || windowed;
  const showCard = on && hasContent(kept.screen) && card !== null;
  return (
    <div
      ref={rootRef}
      className={className ? `gv ${className}` : "gv"}
      data-full={windowed ? "window" : native ? "native" : undefined}
      style={{ "--gv-fade": `${FADE_MS}ms` } as CSSProperties}
    >
      <div className="gv-stage" role="region" aria-label="The view through the glasses">
        <canvas ref={fillRef} className="gv-fill" aria-hidden="true" />
        <div className="gv-picture">{children}</div>
        {/* Over the picture, as real rims are: they hide its edges, never its middle. */}
        <Frame id={`${id}-wide`} shape={WIDE} className="gv-frame-wide" />
        <Frame id={`${id}-tall`} shape={TALL} className="gv-frame-tall" />
        <div className="gv-lens" data-on={on} style={{ "--gv-scale": scale } as CSSProperties}>
          <div ref={squareRef} className="gv-square">
            <div
              className="gv-card"
              data-on={showCard}
              style={
                card
                  ? {
                      top: `${(card.top / DISPLAY_PX) * 100}%`,
                      height: `${((card.bottom - card.top) / DISPLAY_PX) * 100}%`,
                    }
                  : undefined
              }
            />
            <div className="gv-display">
              <Display
                ref={contentRef}
                screen={kept.screen}
                seeThrough
                listening={listening}
                onAction={ignore}
              />
            </div>
          </div>
          {kept.evidence ? (
            <figure className="gv-evidence">
              {/* A plain <img>: memory stills come from the backend as they are. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={kept.evidence.src} alt={kept.evidence.alt} />
              {kept.evidence.caption ? <figcaption>{kept.evidence.caption}</figcaption> : null}
            </figure>
          ) : null}
        </div>
        {status ? <div className="gv-status">{status}</div> : null}
        {overlay ? <div className="gv-overlay">{overlay}</div> : null}
        <button
          type="button"
          className="gv-full"
          aria-label={full ? "Leave fullscreen" : "Fullscreen"}
          title={full ? "Leave fullscreen" : "Fullscreen"}
          onClick={() => void toggleFullscreen()}
        >
          {full ? <Minimize aria-hidden="true" /> : <Maximize aria-hidden="true" />}
        </button>
      </div>
    </div>
  );
}

/** The picture's size and an id for its current frame, once it has one to draw. */
function frameOf(element: HTMLVideoElement | HTMLImageElement) {
  if (element instanceof HTMLVideoElement) {
    if (element.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return null;
    return {
      width: element.videoWidth,
      height: element.videoHeight,
      frame: `${element.currentSrc} ${element.currentTime}`,
    };
  }
  if (!element.complete || !element.naturalWidth) return null;
  return { width: element.naturalWidth, height: element.naturalHeight, frame: element.currentSrc };
}

/** The glasses' frame seen from behind: solid black acetate around the lenses, lit from above (a
 * sheen along its top, a bright bevel on its inner edges) and shading the lenses next to it. */
function Frame({ id, shape, className }: { id: string; shape: FrameShape; className: string }) {
  const { width, height, lenses } = shape;
  const body = `M 0 0 H ${width} V ${height} H 0 Z ${lenses}`;
  return (
    <svg
      className={`gv-frame ${className}`}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={`${id}-acetate`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#202226" />
          <stop offset="0.18" stopColor="#0e0f11" />
          <stop offset="0.7" stopColor="#08090a" />
          <stop offset="1" stopColor="#121316" />
        </linearGradient>
        <linearGradient id={`${id}-bevel`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.34" />
          <stop offset="0.3" stopColor="#fff" stopOpacity="0.12" />
          <stop offset="1" stopColor="#fff" stopOpacity="0.05" />
        </linearGradient>
        <radialGradient id={`${id}-gloss`}>
          <stop offset="0" stopColor="#fff" stopOpacity="0.16" />
          <stop offset="1" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
        <filter id={`${id}-shade`} x="-5%" y="-5%" width="110%" height="110%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
        <filter id={`${id}-soft`} x="-5%" y="-5%" width="110%" height="110%">
          <feGaussianBlur stdDeviation="0.8" />
        </filter>
        <clipPath id={`${id}-lenses`}>
          <path d={lenses} />
        </clipPath>
        <clipPath id={`${id}-body`}>
          <path d={body} clipRule="evenodd" />
        </clipPath>
      </defs>
      <g clipPath={`url(#${id}-lenses)`}>
        <path
          d={lenses}
          fill="none"
          stroke="#000"
          strokeOpacity="0.6"
          strokeWidth="30"
          filter={`url(#${id}-shade)`}
        />
      </g>
      <path d={body} fillRule="evenodd" fill={`url(#${id}-acetate)`} />
      <g clipPath={`url(#${id}-body)`}>
        {shape.gloss.map((spot) => (
          <ellipse key={`${spot.cx} ${spot.cy}`} {...spot} fill={`url(#${id}-gloss)`} />
        ))}
      </g>
      <path
        d={lenses}
        fill="none"
        stroke={`url(#${id}-bevel)`}
        strokeWidth="3"
        filter={`url(#${id}-soft)`}
      />
    </svg>
  );
}
