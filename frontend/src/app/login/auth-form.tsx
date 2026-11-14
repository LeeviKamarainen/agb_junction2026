"use client";

import { LoaderCircle } from "lucide-react";
import { type FormEvent, useId, useState } from "react";

import { GuestButton } from "@/components/guest-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/browser";
import { nextPath } from "@/lib/session";

/**
 * The admin's login, with a guest session for everyone else (guests have no password). On
 * success a full page load to ?next= (or the memory lab).
 */
export function AuthForm() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { user: useId(), pass: useId(), error: useId() };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const { data, response } = await api.POST("/api/auth/login", {
        body: { username: username.trim(), password },
      });
      if (data) {
        // A full load, so every page and the proxy see the new session cookie.
        window.location.assign(nextPath());
        return;
      }
      setError(
        response.status === 401
          ? "Wrong username or password."
          : `Couldn’t log in (HTTP ${response.status}).`,
      );
    } catch (caught) {
      setError(`Couldn’t reach the backend: ${caught}`);
    }
    setBusy(false);
  }

  return (
    <div className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-border bg-card p-6">
      <section className="flex flex-col gap-3">
        <h1 className="text-xl font-semibold tracking-tight">
          Try it as a guest
        </h1>
        <p className="text-sm text-muted-foreground">
          No sign-up. See the demo pictures and add your own; after 30 minutes
          the guest account and what you added are deleted.
        </p>
        <GuestButton />
      </section>

      <div
        className="flex items-center gap-3 text-xs text-muted-foreground"
        aria-hidden="true"
      >
        <span className="h-px flex-1 bg-border" /> or{" "}
        <span className="h-px flex-1 bg-border" />
      </div>

      <form
        onSubmit={submit}
        className="flex flex-col gap-3"
        aria-labelledby={`${ids.user}-title`}
        aria-describedby={error ? ids.error : undefined}
      >
        <h2 id={`${ids.user}-title`} className="text-sm font-semibold">
          Admin login
        </h2>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={ids.user} className="text-sm font-medium">
            Username
          </label>
          <Input
            id={ids.user}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={64}
            required
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={ids.pass} className="text-sm font-medium">
            Password
          </label>
          <Input
            id={ids.pass}
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            maxLength={200}
            required
          />
        </div>
        {error ? (
          <p id={ids.error} className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="outline" disabled={busy}>
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : null}
          Log in
        </Button>
      </form>
    </div>
  );
}
