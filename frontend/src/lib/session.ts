"use client";

import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api/browser";
import type { components } from "@/lib/api/schema";

export type SessionUser = components["schemas"]["UserPublic"];

/**
 * The logged-in user from GET /api/auth/session: undefined while loading, null when nobody is
 * logged in. The backend decides what each user may see; this is only for showing it.
 */
export function useSession() {
  const [user, setUser] = useState<SessionUser | null | undefined>(undefined);

  const refresh = useCallback(async () => {
    try {
      const { data } = await api.GET("/api/auth/session");
      setUser(data ?? null);
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .GET("/api/auth/session")
      .then(({ data }) => {
        if (!cancelled) setUser(data ?? null);
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const changed = () => { void refresh(); };
    window.addEventListener("session-changed", changed);
    return () => window.removeEventListener("session-changed", changed);
  }, [refresh]);

  return { user, refresh };
}

export async function logOut() {
  await api.POST("/api/auth/logout");
}

/** Where to go after logging in: the ?next= path when it is one of ours, else the demo page. */
export function nextPath(): string {
  if (typeof window === "undefined") return "/";
  const next = new URLSearchParams(window.location.search).get("next");
  return next && next.startsWith("/") && !next.startsWith("//")
    ? next
    : "/";
}

/**
 * Makes sure someone is logged in before a call that needs it: when nobody is, starts a temporary
 * guest (60 minutes, seeing the demo memories and its own) and tells the page. Throws with a
 * message to show when that fails.
 */
export async function ensureSession(): Promise<void> {
  const session = await api.GET("/api/auth/session");
  if (!session.response.ok)
    throw new Error("Could not check your login. Please try again.");
  if (session.data) return;
  const created = await api.POST("/api/auth/guest");
  if (!created.data)
    throw new Error("Could not start a guest session. Please try again.");
  window.dispatchEvent(new Event("session-changed"));
}
