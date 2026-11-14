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

  return { user, refresh };
}

export async function logOut() {
  await api.POST("/api/auth/logout");
}

/** Where to go after logging in: the ?next= path when it is one of ours, else the memory lab. */
export function nextPath(): string {
  if (typeof window === "undefined") return "/memory";
  const next = new URLSearchParams(window.location.search).get("next");
  return next && next.startsWith("/") && !next.startsWith("//")
    ? next
    : "/memory";
}
