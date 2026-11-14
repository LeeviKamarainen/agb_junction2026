"use client";

import Link from "next/link";

import { LoaderCircle, Settings2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";

import { GuestButton } from "@/components/guest-button";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/browser";
import { nextPath } from "@/lib/session";

/** Guests can start fresh or return by username on /login; with `admin`, the password form /admin shows. */
export function AuthForm({ admin = false }: { admin?: boolean }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { user: useId(), pass: useId(), error: useId() };

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const { data, response } = admin
        ? await api.POST("/api/auth/login", {
            body: { username: username.trim(), password },
          })
        : await api.POST("/api/auth/guest/login", {
            body: { username: username.trim() },
          });
      if (data) {
        // A full load, so every page and the proxy see the new session cookie.
        window.location.assign(nextPath());
        return;
      }
      setError(
        response.status === 401
          ? admin
            ? "Wrong username or password."
            : "Guest account not found or expired. Start a new session below."
          : `Couldn’t log in (HTTP ${response.status}).`,
      );
    } catch (caught) {
      setError(`Couldn’t reach the backend: ${caught}`);
    }
    setBusy(false);
  }

  return (
    <div className="flex w-full max-w-sm flex-col gap-5 rounded-xl border border-border bg-card p-6">
      <form
        onSubmit={submit}
        className="flex flex-col gap-3"
        aria-labelledby={`${ids.user}-title`}
        aria-describedby={error ? ids.error : undefined}
      >
        <h1
          id={`${ids.user}-title`}
          className="text-xl font-semibold tracking-tight"
        >
          {admin ? "Admin login" : "Welcome back"}
        </h1>
        {!admin ? (
          <p className="text-sm text-muted-foreground">
            Enter your guest username to return to the same memories on this or
            another device. Your session keeps its original expiry time.
          </p>
        ) : null}
        <div className="flex flex-col gap-1.5">
          <label htmlFor={ids.user} className="text-sm font-medium">
            {admin ? "Username" : "Guest username"}
          </label>
          <Input
            id={ids.user}
            name="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={64}
            placeholder={admin ? undefined : "guest-…"}
            disabled={busy}
            required
          />
        </div>
        {admin ? (
          <div className="flex flex-col gap-1.5">
            <label htmlFor={ids.pass} className="text-sm font-medium">
              Password
            </label>
            <Input
              id={ids.pass}
              name="password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              maxLength={200}
              disabled={busy}
              required
            />
          </div>
        ) : null}
        {error ? (
          <p id={ids.error} className="text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="outline" disabled={busy}>
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : null}
          {admin ? "Log in" : "Return to guest account"}
        </Button>
      </form>

      {!admin ? (
        <>
          <div
            className="flex items-center gap-3 text-xs text-muted-foreground"
            aria-hidden="true"
          >
            <span className="h-px flex-1 bg-border" /> or{" "}
            <span className="h-px flex-1 bg-border" />
          </div>
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold">First time here?</h2>
            <p className="text-sm text-muted-foreground">
              Start a guest account with no sign-up. You get 60 minutes to try
              the demo memories and add your own. Then the account and what you
              added are deleted.
            </p>
            <GuestButton />
            <p className="text-xs text-muted-foreground">
              Keep your guest username private. It lets you return without a
              password.
            </p>
          </section>
          <Link
            href="/admin"
            aria-label="Admin login"
            title="Admin login"
            className="self-end rounded-md p-2 text-muted-foreground/40 transition-colors hover:text-muted-foreground focus-visible:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          >
            <Settings2 size={14} aria-hidden="true" />
          </Link>
        </>
      ) : null}
    </div>
  );
}
