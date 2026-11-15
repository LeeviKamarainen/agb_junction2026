// The memory trail's geometry: when the map is refitted and from what, that a refit does not
// mirror it, how it fills the square, what the playhead reveals, how points move, and what the
// pointer picks. `npm test` runs it with Node's own test runner.

import assert from "node:assert/strict";
import { test } from "node:test";

import { pca } from "../memory/pca.ts";
import {
  byTime,
  type Fit,
  fit,
  layout,
  mostlySame,
  nearest,
  orient,
  placeCard,
  retarget,
  revealed,
  sameIds,
  type Spot,
  spotAt,
} from "./trail.ts";

const range = (from: number, to: number) => Array.from({ length: to - from }, (_, i) => from + i);

test("the map is refitted when its ids change, from the last axes only when 90% of both are shared", () => {
  assert.equal(sameIds([1, 2, 3], [3, 1, 2]), true);
  assert.equal(sameIds([1, 2, 3], [1, 2, 4]), false); // as many, but not the same ones

  const last = new Set(range(0, 100));
  assert.equal(mostlySame(last, range(10, 110)), true); // 90 of 100 kept: the boundary
  assert.equal(mostlySame(last, range(11, 111)), false); // 89 kept
  assert.equal(mostlySame(last, range(0, 112)), false); // all 100 kept, but 100 < 0.9 * 112
  assert.equal(mostlySame(last, range(0, 50)), false); // all 50 kept, but half the old fit is gone
  assert.equal(mostlySame(new Set(range(0, 10)), range(0, 100)), false); // a fit of 10 is no start for 100
});

/** A vector `a` along the first axis and `b` along the second, so the principal axes are known. */
function along(id: number, a: number, b: number) {
  const vector = new Float32Array(16);
  vector[0] = a;
  vector[1] = b;
  return { id, vector };
}

/** `count` points at `a`, their `b` alternating +1 and -1 so that a and b stay uncorrelated. */
function group(firstId: number, count: number, a: number) {
  return range(0, count).map((i) => along(firstId + i, a, i % 2 ? -1 : 1));
}

function correlation(xs: number[], ys: number[]): number {
  const mean = (v: number[]) => v.reduce((s, x) => s + x, 0) / v.length;
  const [mx, my] = [mean(xs), mean(ys)];
  let [sxy, sxx, syy] = [0, 0, 0];
  xs.forEach((x, i) => {
    sxy += (x - mx) * (ys[i] - my);
    sxx += (x - mx) ** 2;
    syy += (ys[i] - my) ** 2;
  });
  return sxy / Math.sqrt(sxx * syy);
}

test("a refit whose first axis flips keeps the shared points where they were, not mirrored", () => {
  // 100 points with a long tail towards +a; 30 more far on the other side move the long tail to
  // -a, and pca.ts points each axis along its longer tail.
  const first = [...group(0, 90, -1), ...group(90, 10, 9)];
  const more = [...first, ...group(100, 30, -12)];
  const before = fit(first, null);
  const after = fit(more, before);

  const old = (axis: 0 | 1) => before.coords.map((c) => c[axis]);
  const raw = pca(
    more.map((p) => p.vector),
    [],
    2,
  ).coords.slice(0, 100);
  assert.ok(correlation(old(0), raw.map((c) => c[0])) < -0.999, "the raw refit is mirrored");
  for (const axis of [0, 1] as const) {
    const now = after.coords.slice(0, 100).map((c) => c[axis]);
    assert.ok(correlation(old(axis), now) > 0.999, `axis ${axis} kept its direction`);
  }
});

test("orient undoes a mirrored or a swapped refit, and moves the new points with the rest", () => {
  const last: Fit = {
    ids: [1, 2, 3, 4],
    coords: [
      [1, 2],
      [3, -1],
      [-2, 0.5],
      [-2, -1.5],
    ],
    axes: [],
  };
  const ids = [...last.ids, 9]; // 9 is new
  const now = [...last.coords, [5, 6] as Spot];

  const mirrored = now.map(([x, y]): Spot => [-x, y]);
  assert.deepEqual(orient(ids, mirrored, last), now);
  const swapped = now.map(([x, y]): Spot => [-y, x]); // a quarter turn: the axes traded places
  assert.deepEqual(orient(ids, swapped, last), now);
  // Nothing in common: nothing to keep, so the fit is drawn as it came.
  assert.deepEqual(orient([7, 8], mirrored.slice(0, 2), last), mirrored.slice(0, 2));
});

test("layout stretches each axis over all the points and puts an axis without spread in the middle", () => {
  assert.deepEqual(
    layout([
      [-2, 10],
      [4, 10],
      [1, 10],
    ]),
    [
      [0, 0.5],
      [1, 0.5],
      [0.5, 0.5],
    ],
  );
  assert.deepEqual(
    layout([
      [0, -1],
      [6, 5],
      [3, 0.5],
    ]),
    [
      [0, 0],
      [1, 1],
      [0.5, 0.25],
    ],
  );
  assert.deepEqual(layout([[3, 3]]), [[0.5, 0.5]]);
});

test("the playhead reveals the points at or before it, in time order whatever order they came in", () => {
  const order = byTime([
    { id: 3, at: 2 },
    { id: 1, at: 0.5 },
    { id: 2, at: 2 },
    { id: 4, at: 7 },
  ]);
  assert.deepEqual(
    order.map((p) => p.id),
    [1, 2, 3, 4],
  );
  assert.equal(revealed(order, undefined), 4); // no playhead: a camera's memories, all shown
  assert.equal(revealed(order, 0.4), 0);
  assert.equal(revealed(order, 0.5), 1); // a point exactly at the playhead is reached
  assert.equal(revealed(order, 2), 3); // both points at 2
  assert.equal(revealed(order, 6.9), 3);
  assert.equal(revealed(order, 100), 4);
});

test("a new layout mid-move starts each point where it is drawn, so nothing jumps", () => {
  const first = retarget(null, new Map([[1, [0, 0] as Spot]]), 0, 400);
  assert.deepEqual(spotAt(first, 1, 0), [0, 0]); // a first layout is drawn in place
  const moving = retarget(first, new Map([[1, [1, 1] as Spot]]), 0, 400);
  const midway = spotAt(moving, 1, 200);
  assert.ok(midway && midway[0] > 0 && midway[0] < 1, "under way at 200 ms");

  const turned = retarget(
    moving,
    new Map([
      [1, [0, 1] as Spot],
      [2, [0.5, 0.5] as Spot],
    ]),
    200,
    400,
  );
  assert.deepEqual(spotAt(turned, 1, 200), midway);
  assert.deepEqual(spotAt(turned, 2, 200), [0.5, 0.5]); // a new point starts in its place
  assert.deepEqual(spotAt(turned, 1, 600), [0, 1]);
  assert.equal(spotAt(turned, 3, 600), undefined);
});

test("the pointer picks the nearest point within reach, the top one of a tie, never one not yet reached", () => {
  const xs = [0, 10, 10, 50];
  const ys = [0, 0, 0, 50];
  assert.equal(nearest(xs, ys, 3, 9, 1, 24), 2); // 1 and 2 tie; 2 is drawn later, on top
  assert.equal(nearest(xs, ys, 3, 3, 0, 24), 0);
  assert.equal(nearest(xs, ys, 3, 40, 0, 24), -1); // 30 px from the nearest
  assert.equal(nearest(xs, ys, 3, 49, 49, 24), -1); // point 3 is not reached yet
  assert.equal(nearest(xs, ys, 4, 49, 49, 24), 3);
});

test("the card sits right of and below the pointer, flips at the viewport's edges, and stays inside", () => {
  const card: Spot = [200, 150];
  const screen: Spot = [1000, 800];
  assert.deepEqual(placeCard([100, 100], card, screen, 12, 8), [112, 112]);
  assert.deepEqual(placeCard([900, 100], card, screen, 12, 8), [688, 112]);
  assert.deepEqual(placeCard([100, 750], card, screen, 12, 8), [112, 588]);
  // Room on neither side: as far in as it goes.
  assert.deepEqual(placeCard([150, 100], card, [300, 200], 12, 8), [92, 42]);
});
