// The memory map's PCA, checked on data whose principal axes are known exactly. `npm test` runs
// it with Node's own test runner.

import assert from "node:assert/strict";
import { test } from "node:test";

import { decode, pca } from "./pca.ts";

function dot(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += a[i] * b[i];
  return sum;
}

/** `count` orthonormal vectors `length` long from a seeded generator; `centred` ones sum to 0. */
function orthonormal(count: number, length: number, seed: number, centred: boolean) {
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5;
  const vectors: Float64Array[] = [];
  while (vectors.length < count) {
    const v = new Float64Array(length).map(random);
    const mean = centred ? v.reduce((sum, x) => sum + x, 0) / length : 0;
    for (let i = 0; i < length; i++) v[i] -= mean;
    for (const u of vectors) {
      const s = dot(v, u);
      for (let i = 0; i < length; i++) v[i] -= s * u[i];
    }
    const norm = Math.sqrt(dot(v, v));
    vectors.push(v.map((x) => x / norm));
  }
  return vectors;
}

// 400 rows of 96 numbers along 40 known axes, the variance along axis m falling as m^-0.5: a
// flat spectrum like that of varied embeddings, on which converging one axis at a time from an
// unscaled start stopped after a single step.
const SIGMAS = Array.from({ length: 40 }, (_, m) => (m + 1) ** -0.25);
const AXES = orthonormal(SIGMAS.length, 96, 11, false);
const WEIGHTS = orthonormal(SIGMAS.length, 400, 23, true);
const ROWS = WEIGHTS[0].map((_, i) => i).reduce<Float32Array[]>((rows, i) => {
  const row = new Float32Array(96);
  SIGMAS.forEach((sigma, m) => {
    for (let j = 0; j < 96; j++) row[j] += sigma * WEIGHTS[m][i] * AXES[m][j];
  });
  return [...rows, row];
}, []);
const TOTAL = SIGMAS.reduce((sum, s) => sum + s * s, 0);
const BEST = (SIGMAS[0] ** 2 + SIGMAS[1] ** 2 + SIGMAS[2] ** 2) / TOTAL;

/** The share of the variance each returned axis really holds, from the coordinates alone. */
function held(coords: number[][]): number[] {
  return coords[0].map((_, c) => coords.reduce((sum, row) => sum + row[c] ** 2, 0) / TOTAL);
}

test("the axes hold nearly as much variance as the best three, and say how much", () => {
  const { coords, explained, axes } = pca(ROWS);
  const shares = held(coords);

  assert.ok(shares.reduce((a, b) => a + b) >= 0.97 * BEST);
  shares.forEach((share, c) => assert.ok(Math.abs(share - explained[c]) < 1e-6));
  axes.forEach((a, i) => axes.forEach((b, j) => assert.ok(Math.abs(dot(a, b) - +(i === j)) < 1e-9)));
  assert.deepEqual(pca(ROWS).coords, coords); // the same rows, the same map
});

test("a refit from the previous axes after a few rows changed holds as much", () => {
  const before = pca(ROWS.slice(0, -4));
  const { coords } = pca(ROWS, before.axes);

  assert.ok(held(coords).reduce((a, b) => a + b) >= 0.97 * BEST);
});

test("one or two memories give finite coordinates", () => {
  for (const rows of [ROWS.slice(0, 1), ROWS.slice(0, 2)]) {
    const { coords, explained } = pca(rows);
    assert.ok(coords.flat().every(Number.isFinite));
    assert.ok(explained.every((share) => share >= 0 && share <= 1 + 1e-9));
  }
});

test("decode reads the backend's base64 of little-endian float32", () => {
  const values = new Float32Array([1.5, -2, 3.25]);
  const base64 = Buffer.from(values.buffer).toString("base64");

  assert.deepEqual(decode(base64), values);
});
