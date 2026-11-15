// The memory trail's geometry, apart from React and the canvas: the 2D map of the remembered
// frames and how it stays steady as frames arrive, which frames the playhead has reached, and
// which frame is under the pointer. Pure functions, so they can be tested without a browser.

import { pca } from "../memory/pca.ts";

/** A place on the map: on the two principal axes, or in the unit square once laid out. */
export type Spot = readonly [number, number];

// The share of points two fits must have in common for the second to start from the first's axes:
// the Memory lab map's rule (memory-map.tsx), measured there on 2000 synthetic embeddings, where
// refits with up to 10% of them replaced held 99.5% or more of the variance a fresh fit holds.
const MOSTLY_SAME = 0.9;

/** Whether two lists hold the same ids, in any order: if not, the map is fitted again. */
export function sameIds(a: readonly number[], b: readonly number[]): boolean {
  if (a.length !== b.length) return false;
  const known = new Set(a);
  return b.every((id) => known.has(id));
}

/** Whether a fit of `ids` may start from the axes of the last fit, which was made of `last`. */
export function mostlySame(last: ReadonlySet<number>, ids: readonly number[]): boolean {
  let kept = 0;
  for (const id of ids) if (last.has(id)) kept++;
  return kept >= MOSTLY_SAME * Math.max(ids.length, last.size);
}

export type Fit = {
  ids: number[];
  coords: Spot[]; // each point on the two principal axes, in the order of `ids`
  axes: Float64Array[]; // to start the next fit from
};

/**
 * The points on their first two principal components. With `last`, the fit before, it starts
 * from that fit's axes when most points are the same (cheaper, see pca.ts), and turns the map so
 * that the points both fits share stay where they were.
 */
export function fit(
  points: readonly { id: number; vector: Float32Array }[],
  last: Fit | null,
): Fit {
  const ids = points.map((p) => p.id);
  if (points.length === 0) return { ids, coords: [], axes: [] };
  const width = points[0].vector.length;
  const odd = points.find((p) => p.vector.length !== width);
  if (odd) {
    throw new Error(
      `Memory ${odd.id}'s embedding has ${odd.vector.length} numbers, the others ${width}`,
    );
  }
  const start = last && mostlySame(new Set(last.ids), ids) ? last.axes : [];
  const { coords, axes } = pca(
    points.map((p) => p.vector),
    start,
    2,
  );
  // One point, or a few identical ones, span no direction: pca then returns fewer axes.
  const spots = coords.map((c): Spot => [c[0] ?? 0, c[1] ?? 0]);
  return { ids, coords: last ? orient(ids, spots, last) : spots, axes };
}

/**
 * `coords` with their two axes swapped or reversed, whichever keeps the points they share with
 * `last` nearest where `last` had them. Which way a principal axis points is arbitrary (pca.ts
 * takes the longer tail, which a few new points can change), and two axes of nearly equal spread
 * can trade places; either would mirror or turn the whole map.
 */
export function orient(
  ids: readonly number[],
  coords: readonly Spot[],
  last: Fit,
): Spot[] {
  const before = new Map(last.ids.map((id, i) => [id, last.coords[i]]));
  // along[a][b]: how far the new axis a runs with the old axis b, over the shared points. The
  // signed swap of axes that maximises the matching sum minimises the squared distances moved.
  const along = [
    [0, 0],
    [0, 0],
  ];
  ids.forEach((id, i) => {
    const old = before.get(id);
    if (!old) return;
    for (const a of [0, 1]) for (const b of [0, 1]) along[a][b] += coords[i][a] * old[b];
  });
  const straight = Math.abs(along[0][0]) + Math.abs(along[1][1]);
  const crossed = Math.abs(along[1][0]) + Math.abs(along[0][1]);
  const [x, y] = crossed > straight ? [1, 0] : [0, 1]; // the new axes drawn across and up
  const sx = along[x][0] < 0 ? -1 : 1;
  const sy = along[y][1] < 0 ? -1 : 1;
  return coords.map((c): Spot => [sx * c[x], sy * c[y]]);
}

/**
 * Each point's place in the unit square, y up: each axis stretched over the extent of all the
 * points, reached or not, so revealing more never rescales the map. An axis without spread (one
 * point, or two, which span a line) puts every point in its middle.
 */
export function layout(coords: readonly Spot[]): Spot[] {
  const lo = [Infinity, Infinity];
  const hi = [-Infinity, -Infinity];
  for (const c of coords) {
    for (const a of [0, 1]) {
      lo[a] = Math.min(lo[a], c[a]);
      hi[a] = Math.max(hi[a], c[a]);
    }
  }
  const unit = (value: number, a: number) =>
    hi[a] > lo[a] ? (value - lo[a]) / (hi[a] - lo[a]) : 0.5;
  return coords.map((c): Spot => [unit(c[0], 0), unit(c[1], 1)]);
}

/** The points in time order, ties by id: the order they were seen, whatever order they came in. */
export function byTime<T extends { at: number; id: number }>(points: readonly T[]): T[] {
  return [...points].sort((a, b) => a.at - b.at || a.id - b.id);
}

// How far past the playhead a point still counts as reached. The backend stores a frame's time to
// the microsecond, and the browser computes an image's in floating point (0.3 * 3 is
// 0.8999999999999999): a millisecond covers both, and no two frames are that close (2 fps).
export const REVEAL_SLACK_S = 0.001;

/**
 * How many of the time-ordered points the playhead has reached: those at or before `until`, or
 * all of them when there is no playhead.
 */
export function revealed(ordered: readonly { at: number }[], until: number | undefined): number {
  if (until === undefined) return ordered.length;
  let lo = 0;
  let hi = ordered.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (ordered[mid].at <= until + REVEAL_SLACK_S) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Points moving from one layout to the next. */
export type Motion = {
  from: ReadonlyMap<number, Spot>; // where each point set off; one without is already in place
  to: ReadonlyMap<number, Spot>; // every point on the map
  start: number; // ms
  duration: number; // ms
};

/** Cubic ease in and out: a point leaves gently and settles gently. */
function ease(t: number): number {
  return t < 0.5 ? 4 * t ** 3 : 1 - (2 - 2 * t) ** 3 / 2;
}

/** Where point `id` is drawn at `now`, if it is on the map. */
export function spotAt(motion: Motion, id: number, now: number): Spot | undefined {
  const to = motion.to.get(id);
  const from = motion.from.get(id);
  const t = (now - motion.start) / motion.duration;
  if (!to || !from || !(t < 1)) return to;
  const e = ease(Math.max(0, t));
  return [from[0] + (to[0] - from[0]) * e, from[1] + (to[1] - from[1]) * e];
}

/**
 * A motion to the layout `to` that starts each point from where `motion` draws it at `now`, so a
 * new layout mid-move never makes a point jump. A point new to the map starts in its place.
 */
export function retarget(
  motion: Motion | null,
  to: ReadonlyMap<number, Spot>,
  now: number,
  duration: number,
): Motion {
  const from = new Map<number, Spot>();
  for (const id of to.keys()) {
    const here = motion && spotAt(motion, id, now);
    if (here) from.set(id, here);
  }
  return { from, to, start: now, duration };
}

/**
 * The index of the drawn point nearest (x, y) among the first `count`, or -1 when none is within
 * `reach`. Of two equally near, the later wins: it is drawn on top.
 */
export function nearest(
  xs: ArrayLike<number>,
  ys: ArrayLike<number>,
  count: number,
  x: number,
  y: number,
  reach: number,
): number {
  let best = -1;
  let bestDistance = reach;
  for (let i = 0; i < count; i++) {
    const distance = Math.hypot(xs[i] - x, ys[i] - y);
    if (distance <= bestDistance) {
      best = i;
      bestDistance = distance;
    }
  }
  return best;
}

/**
 * Where a card of `size` goes beside the pointer, in viewport pixels: `gap` right of and below
 * it, on the other side along an axis where that would come nearer the viewport's edge than
 * `margin`, and pushed inside where it fits on neither side.
 */
export function placeCard(
  pointer: Spot,
  size: Spot,
  viewport: Spot,
  gap: number,
  margin: number,
): Spot {
  const along = (a: 0 | 1) => {
    const after = pointer[a] + gap;
    if (after + size[a] <= viewport[a] - margin) return after;
    const before = pointer[a] - gap - size[a];
    if (before >= margin) return before;
    return Math.max(margin, Math.min(viewport[a] - margin - size[a], after));
  };
  return [along(0), along(1)];
}

// The Memory lab map's time ramp (memory-map.tsx) without its two darkest steps, which show at
// 1.8:1 and 2.7:1 against the card background, under the 3:1 WCAG asks of graphics; from #3987e5
// on, every step clears 4:1 (measured with the dataviz skill's validator on surface #242933).
const RAMP = ["#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"].map((hex) =>
  [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)),
);

/** The colour of a point `u` of the way from the first moment to the last: early dark, late light. */
export function shade(u: number): string {
  const at = Math.min(1, Math.max(0, u)) * (RAMP.length - 1);
  const i = Math.min(Math.floor(at), RAMP.length - 2);
  return `rgb(${RAMP[i].map((c, k) => Math.round(c + (at - i) * (RAMP[i + 1][k] - c)))})`;
}
