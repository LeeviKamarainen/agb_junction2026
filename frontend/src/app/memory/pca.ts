// The projection behind the memory map: decoding the stored vectors and their principal
// components. Pure functions, so they can be tested without React.

export function decode(base64: string): Float32Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Float32Array(bytes.buffer); // little-endian, as every browser's platform is
}

// Principal components by randomized block power iteration with a Rayleigh-Ritz step (Halko,
// Martinsson and Tropp, "Finding structure with randomness", 2011, algorithms 4.4 and 5.1): a few
// passes over the data refine k + OVERSAMPLE directions at once, then a small eigenproblem picks
// the k axes among them. The numbers below were measured against numpy's exact PCA on synthetic
// 768-wide unit vectors, 20 to 2000 of them, with spectra falling as i^-0.5 to i^-2.
const OVERSAMPLE = 5; // the low end of the 5 to 10 Halko et al. advise
// From a random start: the three axes then hold at least 97.7% of the variance the best three
// hold, 99.9% when the spectrum falls as 1/i or faster; about 100 ms for 2000 vectors.
const POWER_ITERATIONS = 3;
// From the previous fit's axes no power iteration is needed: a refit after 10 of 2000 vectors
// changed held 99.7% or more, in about 35 ms, and every refit refines the axes further.

export type Components = {
  coords: number[][]; // each row's coordinate on each axis
  explained: number[]; // the share of the variance along each axis
  axes: Float64Array[]; // unit vectors, to start the next fit from
};

/**
 * The rows' coordinates on their first `k` principal components. `start`, the axes of an
 * earlier fit of mostly the same rows, makes it cheaper. The same rows and start always give the
 * same result, and each axis points the way its coordinates have the longer tail.
 */
export function pca(rows: Float32Array[], start: Float64Array[] = [], k = 3): Components {
  const n = rows.length;
  const d = rows[0]?.length ?? 0;
  const width = Math.min(k + OVERSAMPLE, n, d);
  const mean = new Float64Array(d);
  for (const row of rows) for (let j = 0; j < d; j++) mean[j] += row[j] / n;
  const x = new Float64Array(n * d); // the centred rows, one after another
  let total = 0;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < d; j++) {
      const value = rows[i][j] - mean[j];
      x[i * d + j] = value;
      total += value * value;
    }
  }

  let seed = 7;
  const random = () => (seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5;
  const warm = start.length >= k && start.every((axis) => axis.length === d);
  const directions = Array.from({ length: width }, (_, c) =>
    warm && c < k ? Float64Array.from(start[c]) : new Float64Array(d).map(random),
  );
  let q = orthonormal(times(x, n, d, directions));
  for (let i = 0; i < (warm ? 0 : POWER_ITERATIONS); i++) {
    q = orthonormal(times(x, n, d, orthonormal(timesTransposed(x, n, d, q))));
  }
  // Rayleigh-Ritz: with X ~ Q B, the eigenvectors of B B^T turn B's rows onto the axes.
  const b = timesTransposed(x, n, d, q); // B's rows, each d long
  const gram = new Float64Array(width * width);
  for (let r = 0; r < width; r++) {
    for (let c = 0; c < width; c++) gram[r * width + c] = dot(b[r], b[c]);
  }
  const turn = eigenvectors(gram, width);
  const axes = Array.from({ length: Math.min(k, width) }, (_, c) => {
    const axis = new Float64Array(d);
    for (let r = 0; r < width; r++) axpy(turn[r * width + c], b[r], axis);
    const norm = Math.sqrt(dot(axis, axis));
    return norm > 0 ? axis.map((value) => value / norm) : axis;
  });

  const columns = times(x, n, d, axes);
  columns.forEach((column, c) => {
    let skew = 0;
    for (const value of column) skew += value * value * value;
    if (skew < 0) {
      column.forEach((value, i) => (column[i] = -value));
      axes[c] = axes[c].map((value) => -value);
    }
  });
  return {
    coords: Array.from({ length: n }, (_, i) => columns.map((column) => column[i])),
    explained: columns.map((column) => (total > 0 ? dot(column, column) / total : 0)),
    axes,
  };
}

/** a[offset ...] times b, with four running sums so each add need not wait for the last. */
function dot(a: Float64Array, b: Float64Array, offset = 0): number {
  let s0 = 0;
  let s1 = 0;
  let s2 = 0;
  let s3 = 0;
  const end = b.length - (b.length % 4);
  let j = 0;
  for (; j < end; j += 4) {
    s0 += a[offset + j] * b[j];
    s1 += a[offset + j + 1] * b[j + 1];
    s2 += a[offset + j + 2] * b[j + 2];
    s3 += a[offset + j + 3] * b[j + 3];
  }
  for (; j < b.length; j++) s0 += a[offset + j] * b[j];
  return s0 + s1 + s2 + s3;
}

/** to += factor * from */
function axpy(factor: number, from: Float64Array, to: Float64Array): void {
  for (let j = 0; j < from.length; j++) to[j] += factor * from[j];
}

/** X (n rows of d) times the columns m, each d long: columns n long. */
function times(x: Float64Array, n: number, d: number, m: Float64Array[]): Float64Array[] {
  const out = m.map(() => new Float64Array(n));
  for (let i = 0; i < n; i++) {
    for (let c = 0; c < m.length; c++) out[c][i] = dot(x, m[c], i * d);
  }
  return out;
}

/** X transposed times the columns q, each n long: columns d long. */
function timesTransposed(x: Float64Array, n: number, d: number, q: Float64Array[]) {
  const out = q.map(() => new Float64Array(d));
  for (let i = 0; i < n; i++) {
    const row = x.subarray(i * d, (i + 1) * d);
    for (let c = 0; c < q.length; c++) axpy(q[c][i], row, out[c]);
  }
  return out;
}

/** The columns made orthonormal in place: Gram-Schmidt, run twice for accuracy. */
function orthonormal(columns: Float64Array[]): Float64Array[] {
  for (let pass = 0; pass < 2; pass++) {
    columns.forEach((column, c) => {
      for (let p = 0; p < c; p++) axpy(-dot(column, columns[p]), columns[p], column);
      const norm = Math.sqrt(dot(column, column));
      for (let r = 0; r < column.length; r++) column[r] = norm > 1e-300 ? column[r] / norm : 0;
    });
  }
  return columns;
}

/** A symmetric matrix's eigenvectors as columns, largest eigenvalue first: cyclic Jacobi. */
function eigenvectors(matrix: Float64Array, size: number): Float64Array {
  const a = Float64Array.from(matrix);
  const v = new Float64Array(size * size);
  for (let i = 0; i < size; i++) v[i * size + i] = 1;
  const scale = dot(a, a);
  for (let sweep = 0; sweep < 50; sweep++) {
    let off = 0;
    for (let p = 0; p < size; p++) {
      for (let q = p + 1; q < size; q++) off += a[p * size + q] ** 2;
    }
    if (off <= 1e-30 * scale) break;
    for (let p = 0; p < size; p++) {
      for (let q = p + 1; q < size; q++) {
        if (a[p * size + q] === 0) continue;
        // The rotation that zeroes a[p][q] (Numerical Recipes, section 11.1).
        const theta = (a[q * size + q] - a[p * size + p]) / (2 * a[p * size + q]);
        const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.hypot(theta, 1));
        const c = 1 / Math.hypot(t, 1);
        const s = t * c;
        for (let r = 0; r < size; r++) rotate(a, r * size + p, r * size + q, c, s);
        for (let r = 0; r < size; r++) rotate(a, p * size + r, q * size + r, c, s);
        for (let r = 0; r < size; r++) rotate(v, r * size + p, r * size + q, c, s);
      }
    }
  }
  const order = Array.from({ length: size }, (_, i) => i).sort(
    (i, j) => a[j * size + j] - a[i * size + i],
  );
  const sorted = new Float64Array(size * size);
  order.forEach((from, to) => {
    for (let r = 0; r < size; r++) sorted[r * size + to] = v[r * size + from];
  });
  return sorted;
}

function rotate(m: Float64Array, i: number, j: number, c: number, s: number): void {
  const [mi, mj] = [m[i], m[j]];
  m[i] = c * mi - s * mj;
  m[j] = s * mi + c * mj;
}
