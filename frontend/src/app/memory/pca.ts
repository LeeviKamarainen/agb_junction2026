// The projection behind the memory map: decoding the stored vectors and their principal
// components. Pure functions, so they can be tested without React.

export function decode(base64: string): Float32Array {
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
export function pca(
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
