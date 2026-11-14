"use client";

import { LoaderCircle, UserRoundPlus } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/browser";
import { nextPath } from "@/lib/session";

/**
 * Starts a temporary guest account: no name or password, 30 minutes, then it and everything it
 * added are deleted. Goes on to ?next= (or the memory lab).
 */
export function GuestButton({
  variant = "default",
}: {
  variant?: "default" | "outline";
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const { data, response } = await api.POST("/api/auth/guest");
      if (data) {
        // A full load, so every page and the proxy see the new session cookie.
        window.location.assign(nextPath());
        return;
      }
      setError(`Couldn’t start a guest session (HTTP ${response.status}).`);
    } catch (caught) {
      setError(`Couldn’t reach the backend: ${caught}`);
    }
    setBusy(false);
  }

  return (
    <div className="flex flex-col gap-2">
      <Button
        type="button"
        size="lg"
        variant={variant}
        disabled={busy}
        onClick={() => void start()}
      >
        {busy ? (
          <LoaderCircle className="animate-spin" aria-hidden="true" />
        ) : (
          <UserRoundPlus aria-hidden="true" />
        )}
        Start a 30-minute guest session
      </Button>
      {error ? (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
