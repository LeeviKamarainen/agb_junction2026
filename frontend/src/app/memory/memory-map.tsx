"use client";

import { LoaderCircle, Pause, Play, RefreshCw, X } from "lucide-react";
import {
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";

import { CollectionFilter } from "./collections";
import {
  type CollectionCount,
  errorText,
  formatTime,
  type MemoryVector,
} from "./types";

// The map of the stored embeddings: every memory's 768-number vector from the vector table,
// projected onto its first three principal components in the browser. Similar memories sit close
// together. The 2D view is the first two components; the 3D view adds the third and turns.

type ColorBy = "time" | "collection" | "kind";
type ViewMode = "2d" | "3d" | "both";
type Point = { memory: MemoryVector; pos: [number, number, number] };
type Projection = { points: Point[]; explained: number[] };

// One hue from dark to light: early memories are dark, late ones light (as in memory/'s map).
const RAMP = [
  "#184f95",
  "#256abf",
  "#3987e5",
  "#6da7ec",
  "#9ec5f4",
  "#cde2fb",
].map((hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)));
const PALETTE = [
  "#ff794f",
  "#5fb3f9",
  "#7fd18b",
  "#e7c55b",
  "#c39bf2",
  "#f27fb2",
  "#5fd4c8",
  "#b0b3a8",
];
const KIND_COLORS: Record<string, string> = {
  image: "#5fb3f9",
  text: "#e7c55b",
};
const SURFACE = "#111210"; // the canvas background, also the gap drawn around each point
const FOUND = "#ff794f";
const RADIUS = 5; // of a point, in CSS pixels
const REACH = 32; // how far from a point the pointer may be and still pick it
const TAP = 6; // pixels a pointer may move and still count as a tap, not a drag

function decode(base64: string): Float32Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Float32Array(bytes.buffer); // little-endian, as every browser's platform is
}

function dot(a: Float64Array, b: Float64Array): number {
  let sum = 0;
  for (let j = 0; j < a.length; j++) sum += a[j] * b[j];
  return sum;
}

/**
 * The rows' coordinates on their first `k` principal components, by power iteration on the
 * centred rows (never forming the 768 × 768 covariance), and the share of the variance each
 * component explains. Deterministic: the same vectors always give the same map.
 */
function pca(
  rows: Float32Array[],
  k = 3,
): { coords: number[][]; explained: number[] } {
  const n = rows.length;
  const d = rows[0]?.length ?? 0;
  const mean = new Float64Array(d);
  for (const row of rows) for (let j = 0; j < d; j++) mean[j] += row[j] / n;
  const centred = rows.map((row) => {
    const x = new Float64Array(d);
    for (let j = 0; j < d; j++) x[j] = row[j] - mean[j];
    return x;
  });
  const total = centred.reduce((sum, x) => sum + dot(x, x), 0);

  let seed = 7;
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5;
  const components: Float64Array[] = [];
  const explained: number[] = [];
  for (let c = 0; c < k; c++) {
    let v = new Float64Array(d).map(random);
    let eigenvalue = 0;
    for (let iteration = 0; iteration < 40; iteration++) {
      const w = new Float64Array(d);
      for (const x of centred) {
        const s = dot(x, v);
        for (let j = 0; j < d; j++) w[j] += s * x[j];
      }
      for (const u of components) {
        const s = dot(w, u);
        for (let j = 0; j < d; j++) w[j] -= s * u[j];
      }
      const norm = Math.sqrt(dot(w, w));
      if (norm === 0) break;
      for (let j = 0; j < d; j++) w[j] /= norm;
      const settled = 1 - Math.abs(dot(w, v)) < 1e-8;
      v = w;
      eigenvalue = norm;
      if (settled) break;
    }
    components.push(v);
    explained.push(total > 0 ? eigenvalue / total : 0);
  }
  return {
    coords: centred.map((x) => components.map((u) => dot(x, u))),
    explained,
  };
}

function project(memories: MemoryVector[]): Projection {
  if (memories.length === 0) return { points: [], explained: [] };
  const { coords, explained } = pca(memories.map((m) => decode(m.embedding)));
  // Scale into the unit ball, keeping the proportions between the axes.
  const radius = Math.max(1e-9, ...coords.map((c) => Math.hypot(...c)));
  return {
    points: memories.map((memory, i) => ({
      memory,
      pos: [0, 1, 2].map((a) => (coords[i][a] ?? 0) / radius) as Point["pos"],
    })),
    explained,
  };
}

function ramp(u: number): string {
  const at = u * (RAMP.length - 1);
  const i = Math.min(Math.floor(at), RAMP.length - 2);
  return `rgb(${RAMP[i].map((c, k) => Math.round(c + (at - i) * (RAMP[i + 1][k] - c)))})`;
}

function timeOf(point: Point): number {
  return new Date(point.memory.created_at).getTime();
}

function dotAt(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  fill: string,
  radius: number,
  ring: string,
  line: number,
) {
  context.beginPath();
  context.arc(x, y, radius, 0, 2 * Math.PI);
  context.fillStyle = fill;
  context.fill();
  context.lineWidth = line;
  context.strokeStyle = ring;
  context.stroke();
}

type Placed = { x: number; y: number; point: Point };

function MapCanvas({
  mode,
  points,
  colors,
  found,
  chosen,
  onPick,
}: {
  mode: "2d" | "3d";
  points: Point[];
  colors: Map<number, string>;
  found: Set<number>;
  chosen: number | null;
  onPick: (point: Point | null, pin: boolean) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const placed = useRef<Placed[]>([]);
  const camera = useRef({ yaw: 0.7, pitch: 0.35 });
  const drag = useRef<{
    x: number;
    y: number;
    yaw: number;
    pitch: number;
    moved: boolean;
  } | null>(null);
  const [spinning, setSpinning] = useState(mode === "3d");

  const draw = useCallback(() => {
    const element = canvas.current;
    if (!element) return;
    const scale = window.devicePixelRatio || 1;
    const width = element.clientWidth;
    const height = element.clientHeight;
    element.width = width * scale;
    element.height = height * scale;
    const context = element.getContext("2d");
    if (!context) return;
    context.scale(scale, scale);
    context.clearRect(0, 0, width, height);
    if (!width || points.length === 0) {
      placed.current = [];
      return;
    }

    let list: (Placed & { depth: number })[];
    if (mode === "2d") {
      // Each axis stretched to the canvas, as in memory/'s map.
      const pad = 3 * RADIUS;
      const extent = (a: number) => {
        const values = points.map((p) => p.pos[a]);
        return [Math.min(...values), Math.max(...values)];
      };
      const [x0, x1] = extent(0);
      const [y0, y1] = extent(1);
      const unit = (v: number, lo: number, hi: number) =>
        hi > lo ? (v - lo) / (hi - lo) : 0.5;
      list = points.map((point) => ({
        point,
        x: pad + unit(point.pos[0], x0, x1) * (width - 2 * pad),
        y: height - pad - unit(point.pos[1], y0, y1) * (height - 2 * pad),
        depth: 1,
      }));
    } else {
      const { yaw, pitch } = camera.current;
      const [cy, sy, cp, sp] = [
        Math.cos(yaw),
        Math.sin(yaw),
        Math.cos(pitch),
        Math.sin(pitch),
      ];
      const reach = Math.min(width, height) * 0.42;
      const distance = 3;
      const toScreen = ([x, y, z]: number[]) => {
        const x1 = x * cy + z * sy;
        const z1 = -x * sy + z * cy;
        const y2 = y * cp - z1 * sp;
        const z2 = y * sp + z1 * cp;
        const s = distance / (distance + z2);
        return {
          x: width / 2 + x1 * s * reach,
          y: height / 2 - y2 * s * reach,
          depth: s,
        };
      };
      // The three principal axes, faint, so the turning is readable.
      context.lineWidth = 1;
      context.font = "11px system-ui, sans-serif";
      ["PC1", "PC2", "PC3"].forEach((label, a) => {
        const end = [0, 0, 0];
        end[a] = 1;
        const start = end.map((v) => -v);
        const from = toScreen(start);
        const to = toScreen(end);
        context.strokeStyle = "rgba(242, 240, 233, 0.16)";
        context.beginPath();
        context.moveTo(from.x, from.y);
        context.lineTo(to.x, to.y);
        context.stroke();
        context.fillStyle = "rgba(242, 240, 233, 0.45)";
        context.fillText(label, to.x + 4, to.y - 4);
      });
      list = points.map((point) => ({ point, ...toScreen(point.pos) }));
      list.sort((a, b) => a.depth - b.depth); // far ones first, so near ones cover them
    }

    for (const { x, y, point, depth } of list) {
      context.globalAlpha =
        mode === "3d" ? Math.min(1, 0.35 + 0.65 * (depth - 0.6) * 2) : 1;
      dotAt(
        context,
        x,
        y,
        colors.get(point.memory.id) ?? "#888",
        RADIUS * depth,
        SURFACE,
        1.5,
      );
    }
    context.globalAlpha = 1;
    for (const { x, y, point, depth } of list) {
      const fill = colors.get(point.memory.id) ?? "#888";
      if (found.has(point.memory.id))
        dotAt(context, x, y, fill, (RADIUS + 3) * depth, FOUND, 3);
      if (point.memory.id === chosen)
        dotAt(context, x, y, fill, (RADIUS + 4) * depth, "#fff", 3);
    }
    placed.current = list;
  }, [mode, points, colors, found, chosen]);

  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const observer = new ResizeObserver(draw); // also the first draw
    observer.observe(element);
    return () => observer.disconnect();
  }, [draw]);

  useEffect(() => {
    if (mode !== "3d" || !spinning) return;
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      if (!drag.current) camera.current.yaw += (now - last) * 0.0003;
      last = now;
      draw();
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [mode, spinning, draw]);

  function nearest(event: ReactPointerEvent<HTMLCanvasElement>): Point | null {
    const box = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - box.left;
    const y = event.clientY - box.top;
    let best: Point | null = null;
    let bestDistance = REACH;
    // Last drawn is nearest the eye in 3D, so it wins a tie.
    for (let i = placed.current.length - 1; i >= 0; i--) {
      const at = placed.current[i];
      const distance = Math.hypot(at.x - x, at.y - y);
      if (distance < bestDistance) {
        best = at.point;
        bestDistance = distance;
      }
    }
    return best;
  }

  function down(event: ReactPointerEvent<HTMLCanvasElement>) {
    if (mode === "3d") {
      event.currentTarget.setPointerCapture(event.pointerId);
      drag.current = {
        x: event.clientX,
        y: event.clientY,
        ...camera.current,
        moved: false,
      };
    } else {
      onPick(nearest(event), event.pointerType !== "mouse");
    }
  }

  function move(event: ReactPointerEvent<HTMLCanvasElement>) {
    const start = drag.current;
    if (start) {
      const dx = event.clientX - start.x;
      const dy = event.clientY - start.y;
      start.moved ||= Math.hypot(dx, dy) > TAP;
      if (start.moved) {
        camera.current.yaw = start.yaw + dx * 0.01;
        camera.current.pitch = Math.max(
          -1.5,
          Math.min(1.5, start.pitch + dy * 0.01),
        );
        draw();
      }
      return;
    }
    if (event.pointerType === "mouse") onPick(nearest(event), false);
  }

  function up(event: ReactPointerEvent<HTMLCanvasElement>) {
    const start = drag.current;
    drag.current = null;
    if (mode === "3d" && start && !start.moved) onPick(nearest(event), true);
    if (mode === "2d" && event.pointerType === "mouse")
      onPick(nearest(event), true);
  }

  return (
    <figure className="relative m-0 flex min-w-0 flex-col gap-1.5">
      <canvas
        ref={canvas}
        className={`block h-[52vh] min-h-[280px] w-full rounded-lg bg-background ${
          mode === "3d"
            ? "cursor-grab touch-none active:cursor-grabbing"
            : "cursor-crosshair touch-pan-y"
        }`}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={() => {
          drag.current = null;
        }}
        onPointerLeave={(event) => {
          if (!drag.current && event.pointerType === "mouse")
            onPick(null, false);
        }}
        aria-label={
          mode === "3d"
            ? "3D map of the memory embeddings: drag to turn it, click a point to see the memory"
            : "2D map of the memory embeddings: point at or click a point to see the memory"
        }
      />
      <figcaption className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {mode === "3d"
            ? "3D · first three principal components · drag to turn"
            : "2D · first two principal components"}
        </span>
        {mode === "3d" && (
          <Button
            variant="ghost"
            size="xs"
            onClick={() => setSpinning((value) => !value)}
            aria-pressed={spinning}
          >
            {spinning ? (
              <Pause aria-hidden="true" />
            ) : (
              <Play aria-hidden="true" />
            )}
            {spinning ? "Stop turning" : "Turn"}
          </Button>
        )}
      </figcaption>
    </figure>
  );
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="flex items-center gap-1.5"
    >
      <span className="text-xs text-muted-foreground">{label}:</span>
      <div className="flex rounded-md border border-border p-0.5">
        {options.map(([option, text]) => (
          <button
            key={option}
            type="button"
            role="radio"
            aria-checked={value === option}
            onClick={() => onChange(option)}
            className={`rounded px-2 py-0.5 text-xs ${
              value === option
                ? "bg-secondary text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {text}
          </button>
        ))}
      </div>
    </div>
  );
}

export function MemoryMap({
  collections,
  version,
  found,
}: {
  collections: CollectionCount[];
  /** Changes when memories were added or deleted, so the map loads again. */
  version: number;
  /** Memories the latest question found, ringed on the map. */
  found: number[];
}) {
  const [shown, setShown] = useState<string[]>([]);
  const [memories, setMemories] = useState<MemoryVector[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const [mode, setMode] = useState<ViewMode>("both");
  const [colorBy, setColorBy] = useState<ColorBy>("collection");
  // Ids, not points: a point that is deleted or filtered out then simply disappears.
  const [hoveredId, setHoveredId] = useState<number | null>(null);
  const [pinnedId, setPinnedId] = useState<number | null>(null);

  const active = shown.filter((name) =>
    collections.some((c) => c.name === name),
  );
  const activeKey = active.join(",");

  useEffect(() => {
    let current = true;
    const collection = activeKey ? activeKey.split(",") : undefined;
    // Loading when the filter or the memories change is the point of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    api
      .GET("/api/memories/vectors", {
        params: { query: { collection } },
        cache: "no-store",
      })
      .then(({ data, error: problem }) => {
        if (!current) return;
        if (problem || !data)
          throw new Error(errorText(problem, "Could not load the vectors"));
        setMemories(data);
        setError(null);
      })
      .catch((problem: unknown) => {
        if (current) {
          setError(
            problem instanceof Error
              ? problem.message
              : "Could not load the vectors",
          );
        }
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [activeKey, version, reload]);

  const projection = useMemo(() => project(memories ?? []), [memories]);
  const { points, explained } = projection;

  // Each collection keeps its colour, whatever the counts or the filter: by name, alphabetically.
  const collectionColors = useMemo(() => {
    const names = collections.map((c) => c.name).sort();
    return new Map(names.map((name, i) => [name, PALETTE[i % PALETTE.length]]));
  }, [collections]);

  const colors = useMemo(() => {
    const result = new Map<number, string>();
    if (colorBy === "time") {
      const times = points.map(timeOf);
      const [t0, t1] = [Math.min(...times), Math.max(...times)];
      for (const point of points) {
        const u = t1 > t0 ? (timeOf(point) - t0) / (t1 - t0) : 0.5;
        result.set(point.memory.id, ramp(u));
      }
    } else if (colorBy === "kind") {
      for (const point of points) {
        result.set(
          point.memory.id,
          KIND_COLORS[point.memory.kind] ?? "#b0b3a8",
        );
      }
    } else {
      for (const point of points) {
        result.set(
          point.memory.id,
          collectionColors.get(point.memory.collection) ?? "#b0b3a8",
        );
      }
    }
    return result;
  }, [points, colorBy, collectionColors]);

  const foundSet = useMemo(() => new Set(found), [found]);
  const pinned = points.find((p) => p.memory.id === pinnedId) ?? null;
  const hovered = points.find((p) => p.memory.id === hoveredId) ?? null;
  const shownPoint = pinned ?? hovered;
  const pick = useCallback((point: Point | null, pin: boolean) => {
    const id = point?.memory.id ?? null;
    if (pin) setPinnedId(id);
    else setHoveredId(id);
  }, []);
  const missingFound = found.filter(
    (id) => !points.some((p) => p.memory.id === id),
  ).length;

  const legend = (() => {
    if (colorBy === "time") {
      const times = points.map(timeOf);
      if (!times.length) return null;
      return (
        <span className="flex items-center gap-2">
          {formatTime(new Date(Math.min(...times)).toISOString())}
          <span
            className="h-2 w-32 rounded"
            style={{
              background: `linear-gradient(to right, ${RAMP.map((c) => `rgb(${c})`).join(", ")})`,
            }}
          />
          {formatTime(new Date(Math.max(...times)).toISOString())}
        </span>
      );
    }
    const entries: [string, string][] =
      colorBy === "kind"
        ? Object.entries(KIND_COLORS)
        : [...collectionColors].filter(
            ([name]) => active.length === 0 || active.includes(name),
          );
    return entries.map(([name, color]) => (
      <span key={name} className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-full" style={{ background: color }} />
        {name}
      </span>
    ));
  })();

  return (
    <section
      aria-labelledby="map-title"
      className="flex flex-col gap-3 rounded-xl border border-border bg-card p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 id="map-title" className="font-semibold">
            Memory map{" "}
            <span className="text-sm font-normal text-muted-foreground">
              {memories ? `${points.length} vectors` : ""}
            </span>
          </h2>
          <p className="text-xs text-muted-foreground">
            Every stored embedding, projected onto its principal components:
            similar memories sit close together. Memories the latest question
            found are ringed in orange.
            {explained.length > 0 &&
              ` The axes explain ${explained
                .map((share) => `${Math.round(share * 100)}%`)
                .join(", ")} of the variance.`}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setReload((n) => n + 1)}
        >
          {loading ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <RefreshCw aria-hidden="true" />
          )}
          Reload
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <Segmented
          label="View"
          value={mode}
          options={[
            ["2d", "2D"],
            ["3d", "3D"],
            ["both", "Both"],
          ]}
          onChange={setMode}
        />
        <Segmented
          label="Colour"
          value={colorBy}
          options={[
            ["collection", "Collection"],
            ["time", "Time"],
            ["kind", "Kind"],
          ]}
          onChange={setColorBy}
        />
        <CollectionFilter
          label="Show"
          collections={collections}
          selected={active}
          onChange={setShown}
        />
      </div>

      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      {memories && points.length < 2 && !error && (
        <p className="text-sm text-muted-foreground">
          The map needs at least two memories with embeddings. Add some above.
        </p>
      )}

      {points.length >= 2 && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
          <div
            className={`grid min-w-0 gap-4 ${mode === "both" ? "md:grid-cols-2" : ""}`}
          >
            {mode !== "3d" && (
              <MapCanvas
                mode="2d"
                points={points}
                colors={colors}
                found={foundSet}
                chosen={shownPoint?.memory.id ?? null}
                onPick={pick}
              />
            )}
            {mode !== "2d" && (
              <MapCanvas
                mode="3d"
                points={points}
                colors={colors}
                found={foundSet}
                chosen={shownPoint?.memory.id ?? null}
                onPick={pick}
              />
            )}
          </div>
          <aside
            className="flex min-w-0 flex-col gap-2 text-sm"
            aria-live="polite"
          >
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
              {legend}
            </div>
            {missingFound > 0 && (
              <p className="text-xs text-muted-foreground">
                {missingFound} found{" "}
                {missingFound === 1 ? "memory is" : "memories are"} outside the
                collections shown.
              </p>
            )}
            {shownPoint ? (
              <div className="flex flex-col gap-2 rounded-lg border border-border bg-background/60 p-2">
                {shownPoint.memory.image_url ? (
                  /* eslint-disable-next-line @next/next/no-img-element -- served by the API */
                  <img
                    src={shownPoint.memory.image_url}
                    alt={
                      shownPoint.memory.text || `Memory ${shownPoint.memory.id}`
                    }
                    className="max-h-64 w-full rounded object-contain"
                  />
                ) : null}
                {shownPoint.memory.text && (
                  <p className="line-clamp-6 text-sm">
                    {shownPoint.memory.text}
                  </p>
                )}
                <div className="flex items-start justify-between gap-2 text-xs text-muted-foreground">
                  <span>
                    #{shownPoint.memory.id} · {shownPoint.memory.kind} ·{" "}
                    <span className="text-primary">
                      {shownPoint.memory.collection}
                    </span>
                    <br />
                    {formatTime(shownPoint.memory.created_at)}
                    {foundSet.has(shownPoint.memory.id) &&
                      " · found by the last question"}
                  </span>
                  {pinned && (
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      onClick={() => setPinnedId(null)}
                      aria-label="Unpin this memory"
                    >
                      <X aria-hidden="true" />
                    </Button>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Point at a memory to see it; click or tap one to keep it here.
              </p>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}
