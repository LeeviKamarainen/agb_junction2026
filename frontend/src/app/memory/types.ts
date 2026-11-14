import type { components } from "@/lib/api/schema";

export type Memory = components["schemas"]["MemoryPublic"];
export type SearchResult = components["schemas"]["SearchResult"];
export type Step = components["schemas"]["Step"];

/** FastAPI errors carry `detail`: a string, or a list of validation problems. */
export function errorText(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((problem) =>
          problem && typeof problem === "object" && "msg" in problem
            ? String((problem as { msg: unknown }).msg)
            : JSON.stringify(problem),
        )
        .join("; ");
    }
  }
  return fallback;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** A Date as the value of an <input type="datetime-local">, in local time. */
export function toLocalInput(date: Date): string {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return shifted.toISOString().slice(0, 16);
}
