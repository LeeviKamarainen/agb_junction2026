"use client";

import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

import {
  byTime,
  type Fit,
  fit,
  layout,
  type Motion,
  nearest,
  placeCard,
  retarget,
  revealed,
  sameIds,
  shade,
  type Spot,
  spotAt,
} from "./trail";

// The memory as the glasses build it: one point per remembered frame, placed by the frame's
// embedding on the first two principal components of all of them (similar frames close
// together), each appearing as the playhead reaches it.

export type TrailPoint = {
  id: number; // memory id
  vector: Float32Array; // its embedding, decoded
  at: number; // order and colour key: seconds into the clip, or epoch ms for camera frames
  imageUrl: string | null; // the frame, for the hover thumbnail
  label: string; // e.g. "0:12" or "10:42:03"
};

const RADIUS = 5; // of a point, in CSS pixels: a 10 px dot, over the dataviz skill's 8 px minimum
const GAP = 2; // the ring of background around each point that keeps overlapping ones apart (dataviz skill)
const PAD = 3 * RADIUS; // from the outermost points to the edge, as on the Memory lab map: room for their rings
const NOW = "#ffffff";
const FOUND = "#d08770"; // an answer's frames, as on the Memory lab map
const REACH = 24; // how far from a point the pointer may be and still pick it: half the 48 px touch target Material Design asks for
const TAP = 6; // pixels a finger may move and still tap rather than scroll, as on the Memory lab map
const MOVE_MS = 400; // a refit's move: over before the next camera frame, 500 ms later at 2 fps
const GROW_MS = 350; // a newly reached point growing in, likewise within one frame's 500 ms
const NOW_MS = 1800; // one pulse of the newest point's ring: slow, so it stays calm (by eye)
const FOUND_MS = 1200; // one pulse of an answer's ring: quicker, to draw the eye there (by eye)
const SPREAD = 12; // how far a pulse spreads beyond its ring, in CSS pixels (by eye)
const CARD_GAP = 14; // from the pointer to the card, clear of the cursor's arrow
const CARD_MARGIN = 8; // from the card to the viewport's edge

type Layer = { canvas: HTMLCanvasElement; context: CanvasRenderingContext2D };

function layer(canvas: HTMLCanvasElement): Layer {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser cannot draw the memory trail: no 2D canvas");
  return { canvas, context };
}

/** A point's size as it grows in, over t from 0 to 1: an ease out that overshoots by 10%. */
function grow(t: number): number {
  const s = t - 1;
  return 1 + 2.70158 * s ** 3 + 1.70158 * s ** 2; // "back" easing, c = 1.70158
}

/**
 * Draws the trail on two stacked canvases: the points below, redrawn only when they change or
 * move; the rings above (the newest point, an answer's, the pointer's), redrawn every
 * frame while one pulses, which costs a few arcs however many points there are.
 */
class Painter {
  private readonly box: HTMLElement;
  private readonly points: Layer;
  private readonly rings: Layer;
  private readonly surface: string;
  private readonly observer: ResizeObserver;
  private readonly calm: MediaQueryList;
  private width = 0;
  private height = 0;
  private scale = 0;
  private resized = true;
  private fitted: Fit | null = null;
  private motion: Motion | null = null;
  private order: readonly TrailPoint[] = [];
  private index = new Map<number, number>();
  private colours: string[] = [];
  private count = 0;
  private reachedAt = new Map<number, number>(); // when each reached point was reached
  private growUntil = 0;
  private highlight: ReadonlySet<number> = new Set();
  private hovered: number | null = null;
  // What the last drawing put where: the reached points in order, and how far each had grown.
  private drawnOrder: readonly TrailPoint[] = [];
  private drawn = 0;
  private xs = new Float64Array(0);
  private ys = new Float64Array(0);
  private grown = new Float64Array(0);
  private stale = true;
  private settling = false;
  private frame = 0;

  constructor(box: HTMLElement, points: HTMLCanvasElement, rings: HTMLCanvasElement) {
    this.box = box;
    this.points = layer(points);
    this.rings = layer(rings);
    // The ring around each point is the box's own background, whatever the page made it.
    this.surface = getComputedStyle(box).backgroundColor;
    this.calm = window.matchMedia("(prefers-reduced-motion: reduce)");
    this.calm.addEventListener("change", this.wake);
    this.observer = new ResizeObserver(() => {
      this.resized = true;
      this.draw(); // now, before the stretched old drawing is painted
    });
    this.observer.observe(box);
  }

  destroy() {
    cancelAnimationFrame(this.frame);
    this.observer.disconnect();
    this.calm.removeEventListener("change", this.wake);
  }

  /** The points in time order, how many of them the playhead has reached, the answer's ids. */
  update(order: readonly TrailPoint[], count: number, highlight: ReadonlySet<number>) {
    const now = performance.now();
    const ids = order.map((p) => p.id);
    if (!this.fitted || !sameIds(this.fitted.ids, ids)) {
      this.fitted = fit(order, this.fitted);
      const spots = layout(this.fitted.coords);
      const to = new Map(this.fitted.ids.map((id, i) => [id, spots[i]]));
      this.motion = retarget(this.motion, to, now, MOVE_MS);
    }
    this.order = order;
    this.index = new Map(ids.map((id, i) => [id, i]));
    const first = order[0]?.at ?? 0;
    const last = order[order.length - 1]?.at ?? 0;
    this.colours = order.map((p) => shade(last > first ? (p.at - first) / (last - first) : 1));
    this.count = count;
    const reached = new Set(ids.slice(0, count));
    // Forget the points the playhead went back past, so they grow in again when it returns.
    for (const id of this.reachedAt.keys()) if (!reached.has(id)) this.reachedAt.delete(id);
    for (const id of reached) {
      if (this.reachedAt.has(id)) continue;
      this.reachedAt.set(id, now);
      this.growUntil = now + GROW_MS;
    }
    this.highlight = highlight;
    this.wake();
  }

  hover(id: number | null) {
    this.hovered = id;
    this.kick();
  }

  /** The id of the reached point drawn nearest (x, y), in CSS pixels, if one is within reach. */
  pick(x: number, y: number): number | null {
    const i = nearest(this.xs, this.ys, this.drawn, x, y, REACH);
    return i < 0 ? null : this.drawnOrder[i].id;
  }

  private kick() {
    if (!this.frame) this.frame = requestAnimationFrame(this.draw);
  }

  private wake = () => {
    this.stale = true;
    this.kick();
  };

  private draw = () => {
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    const now = performance.now();
    const calm = this.calm.matches;
    if (this.resized || window.devicePixelRatio !== this.scale) this.fitSize();
    const moving = !calm && !!this.motion && now < this.motion.start + this.motion.duration;
    const growing = !calm && now < this.growUntil;
    // Once more after a move or a growth ends, so that it ends exactly in place.
    if (this.stale || moving || growing || this.settling) this.drawPoints(calm ? Infinity : now);
    this.stale = false;
    this.settling = moving || growing;
    this.drawRings(now, calm);
    if (this.settling || (this.drawn > 0 && !calm)) this.kick();
  };

  private fitSize() {
    this.resized = false;
    this.width = this.box.clientWidth;
    this.height = this.box.clientHeight;
    this.scale = window.devicePixelRatio;
    for (const { canvas } of [this.points, this.rings]) {
      canvas.width = Math.round(this.width * this.scale);
      canvas.height = Math.round(this.height * this.scale);
    }
    this.stale = true;
  }

  private drawPoints(now: number) {
    const { context } = this.points;
    const { width, height, order, count } = this;
    context.setTransform(this.scale, 0, 0, this.scale, 0, 0);
    context.clearRect(0, 0, width, height);
    if (this.xs.length < count) {
      this.xs = new Float64Array(count);
      this.ys = new Float64Array(count);
      this.grown = new Float64Array(count);
    }
    const { xs, ys, grown } = this;
    for (let i = 0; i < count; i++) {
      const { id } = order[i];
      const spot = this.motion && spotAt(this.motion, id, now);
      if (!spot) throw new Error(`Memory ${id} is not on the map`);
      xs[i] = PAD + spot[0] * (width - 2 * PAD);
      ys[i] = height - PAD - spot[1] * (height - 2 * PAD);
      grown[i] = Math.min(1, (now - this.reachedAt.get(id)!) / GROW_MS);
    }

    // The points in time order, so the newest lie on top, and an answer's frames above them all.
    const found: number[] = [];
    for (let i = 0; i < count; i++) {
      if (this.highlight.has(order[i].id)) found.push(i);
      else this.dot(i);
    }
    for (const i of found) this.dot(i);
    context.globalAlpha = 1;
    this.drawnOrder = order;
    this.drawn = count;
  }

  private dot(i: number) {
    const { context } = this.points;
    const size = grow(this.grown[i]);
    context.globalAlpha = this.grown[i];
    context.fillStyle = this.surface;
    context.beginPath();
    context.arc(this.xs[i], this.ys[i], (RADIUS + GAP) * size, 0, 2 * Math.PI);
    context.fill();
    context.fillStyle = this.colours[i];
    context.beginPath();
    context.arc(this.xs[i], this.ys[i], RADIUS * size, 0, 2 * Math.PI);
    context.fill();
  }

  private drawRings(now: number, calm: boolean) {
    const { context } = this.rings;
    context.setTransform(this.scale, 0, 0, this.scale, 0, 0);
    context.clearRect(0, 0, this.width, this.height);
    const placed = (id: number) => {
      const i = this.index.get(id);
      return i !== undefined && i < this.drawn ? i : -1;
    };
    const ring = (i: number, radius: number, colour: string, width: number, alpha: number) => {
      context.globalAlpha = alpha * this.grown[i];
      context.strokeStyle = colour;
      context.lineWidth = width;
      context.beginPath();
      context.arc(this.xs[i], this.ys[i], radius, 0, 2 * Math.PI);
      context.stroke();
    };
    // A steady ring, and a second that spreads from it and fades, once a period.
    const pulse = (i: number, radius: number, colour: string, width: number, alpha: number, period: number) => {
      ring(i, radius, colour, width, alpha);
      if (calm) return;
      const p = (now % period) / period;
      ring(i, radius + SPREAD * p, colour, 2, alpha * (1 - p));
    };
    if (this.drawn > 0) pulse(this.drawn - 1, RADIUS + GAP + 2, NOW, 1.5, 0.7, NOW_MS);
    for (const id of this.highlight) {
      const i = placed(id);
      if (i >= 0) pulse(i, RADIUS + GAP + 5, FOUND, 2.5, 1, FOUND_MS);
    }
    const hovered = this.hovered === null ? -1 : placed(this.hovered);
    if (hovered >= 0) ring(hovered, RADIUS + GAP, NOW, 2, 1);
    context.globalAlpha = 1;
  }
}

/** The 2D map of the memory, growing as the playhead reaches each remembered frame. */
export function MemoryTrail({
  points,
  visibleUntil,
  highlight,
  className,
}: {
  /** Every point that may appear; the map is fitted on all of them. */
  points: TrailPoint[];
  /** Draw only the points with `at` at or before this (a demo clip's playhead); all when undefined. */
  visibleUntil?: number;
  /** Memory ids an answer rests on: ringed and pulsing when visible. */
  highlight?: number[];
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pointsCanvas = useRef<HTMLCanvasElement>(null);
  const ringsCanvas = useRef<HTMLCanvasElement>(null);
  const card = useRef<HTMLDivElement>(null);
  const painter = useRef<Painter | null>(null);
  const pointer = useRef<Spot>([0, 0]); // the last pointer position, in the viewport
  const tap = useRef<Spot | null>(null); // where a finger went down
  const [hover, setHover] = useState<{ id: number; touch: boolean } | null>(null);

  const order = useMemo(() => byTime(points), [points]);
  const index = useMemo(() => new Map(order.map((p, i) => [p.id, i])), [order]);
  const count = revealed(order, visibleUntil);
  const highlightKey = (highlight ?? []).join(",");
  const highlighted = useMemo(
    () => new Set(highlightKey ? highlightKey.split(",").map(Number) : []),
    [highlightKey],
  );

  useEffect(() => {
    if (!box.current || !pointsCanvas.current || !ringsCanvas.current) return;
    const created = new Painter(box.current, pointsCanvas.current, ringsCanvas.current);
    painter.current = created;
    return () => {
      created.destroy();
      painter.current = null;
    };
  }, []);

  useEffect(() => {
    painter.current?.update(order, count, highlighted);
  }, [order, count, highlighted]);

  // Only a reached point has a card: one the playhead goes back past loses it.
  const at = hover ? index.get(hover.id) : undefined;
  const shown = at !== undefined && at < count ? order[at] : null;
  const shownId = shown?.id ?? null;
  const touch = hover?.touch ?? false;

  useEffect(() => {
    painter.current?.hover(shownId);
  }, [shownId]);

  const place = useCallback(() => {
    const element = card.current;
    if (!element) return;
    const [left, top] = placeCard(
      pointer.current,
      [element.offsetWidth, element.offsetHeight],
      [window.innerWidth, window.innerHeight],
      CARD_GAP,
      CARD_MARGIN,
    );
    element.style.left = `${left}px`;
    element.style.top = `${top}px`;
  }, []);

  // Before the card is painted, and again at each tap: it opens beside the pointer.
  useLayoutEffect(() => {
    if (shown) place();
  }, [hover, shown, place]);

  // A card a tap opened closes at a tap anywhere else. Any card closes when a scroll moves the map,
  // since the point it describes is then no longer under the pointer; a chat scrolling beside it
  // leaves it open.
  useEffect(() => {
    if (shownId === null) return;
    const close = () => setHover(null);
    const away = (event: PointerEvent) => {
      if (event.target !== ringsCanvas.current) close();
    };
    const scrolled = (event: Event) => {
      const { target } = event;
      if (target === document || (target instanceof Node && target.contains(box.current))) close();
    };
    window.addEventListener("scroll", scrolled, { capture: true, passive: true });
    if (touch) document.addEventListener("pointerdown", away, true);
    return () => {
      window.removeEventListener("scroll", scrolled, { capture: true });
      document.removeEventListener("pointerdown", away, true);
    };
  }, [shownId, touch]);

  function pick(event: ReactPointerEvent<HTMLCanvasElement>): number | null {
    const rect = event.currentTarget.getBoundingClientRect();
    return painter.current?.pick(event.clientX - rect.left, event.clientY - rect.top) ?? null;
  }

  function move(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (event.pointerType !== "mouse") return;
    pointer.current = [event.clientX, event.clientY];
    const id = pick(event);
    if (id === null) setHover(null);
    else if (id !== hover?.id) setHover({ id, touch: false });
    else place(); // the same point: the card follows the pointer
  }

  function down(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (event.pointerType !== "mouse") tap.current = [event.clientX, event.clientY];
  }

  function up(event: ReactPointerEvent<HTMLCanvasElement>) {
    const start = tap.current;
    tap.current = null;
    if (!start || Math.hypot(event.clientX - start[0], event.clientY - start[1]) > TAP) return;
    pointer.current = [event.clientX, event.clientY];
    const id = pick(event);
    setHover(id === null ? null : { id, touch: true });
  }

  return (
    <div
      ref={box}
      className={cn("relative h-full min-h-48 w-full overflow-hidden rounded-lg bg-card", className)}
    >
      <canvas ref={pointsCanvas} aria-hidden="true" className="absolute inset-0 size-full" />
      <canvas
        ref={ringsCanvas}
        role="img"
        aria-label="Map of the remembered frames, similar frames close together"
        className="absolute inset-0 size-full touch-manipulation"
        onPointerMove={move}
        onPointerLeave={(event) => {
          if (event.pointerType === "mouse") setHover(null);
        }}
        onPointerDown={down}
        onPointerUp={up}
        onPointerCancel={() => {
          tap.current = null;
        }}
      />
      {count === 0 && (
        <p className="pointer-events-none absolute inset-0 grid place-items-center p-6 text-center text-sm text-muted-foreground">
          Frames appear here as the glasses see them
        </p>
      )}
      {shown &&
        createPortal(
          <div
            ref={card}
            aria-hidden="true"
            className="pointer-events-none fixed top-0 left-0 z-50 flex flex-col gap-1 rounded-lg border border-border bg-card p-1.5 shadow-lg"
          >
            {shown.imageUrl && (
              /* eslint-disable-next-line @next/next/no-img-element -- served by the API, any size */
              <img
                src={shown.imageUrl}
                alt=""
                onLoad={place}
                className="block max-h-[min(180px,35vh)] max-w-[min(180px,40vw)] self-center rounded object-contain"
              />
            )}
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground tabular-nums">
              {shown.label}
              {highlighted.has(shown.id) && (
                <>
                  <span
                    className="size-2 rounded-full border-2"
                    style={{ borderColor: FOUND }}
                  />
                  in the answer
                </>
              )}
            </span>
          </div>,
          document.body,
        )}
    </div>
  );
}
