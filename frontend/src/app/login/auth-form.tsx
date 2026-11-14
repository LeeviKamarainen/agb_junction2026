"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api/browser";
import { nextPath } from "@/lib/session";

type Mode = "login" | "register";

const copy = {
  login: {
    title: "Log in",
    submit: "Log in",
    switchText: "No account yet?",
    switchLink: "Register a guest account",
    switchHref: "/register",
  },
  register: {
    title: "Register",
    submit: "Create guest account",
    switchText: "Already have an account?",
    switchLink: "Log in",
    switchHref: "/login",
  },
} as const;

function problem(status: number, mode: Mode, detail: unknown): string {
  if (status === 401) return "Wrong username or password.";
  if (status === 409) return "That username is taken.";
  if (status === 422) {
    const message =
      typeof detail === "string"
        ? detail
        : "Use 3–32 letters, digits, _ . or -, and a password of at least 8 characters.";
    return message;
  }
  return mode === "login"
    ? `Couldn’t log in (HTTP ${status}).`
    : `Couldn’t register (HTTP ${status}).`;
}

/** Log in or register; on success a full page load to ?next= (or the memory lab). */
export function AuthForm({ mode }: { mode: Mode }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = { user: useId(), pass: useId(), error: useId() };
  const text = copy[mode];

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = { username: username.trim(), password };
    try {
      const { data, error, response } =
        mode === "login"
          ? await api.POST("/api/auth/login", { body })
          : await api.POST("/api/auth/register", { body });
      if (data) {
        // A full load, so every page and the proxy see the new session cookie.
        window.location.assign(nextPath());
        return;
      }
      const detail = (error as { detail?: unknown } | undefined)?.detail;
      setError(problem(response.status, mode, detail));
    } catch (caught) {
      setError(`Couldn’t reach the backend: ${caught}`);
    }
    setBusy(false);
  }

  return (
    <form
      onSubmit={submit}
      className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-border bg-card p-6"
      aria-describedby={error ? ids.error : undefined}
    >
      <h1 className="text-xl font-semibold tracking-tight">{text.title}</h1>
      {mode === "register" ? (
        <p className="text-sm text-muted-foreground">
          A guest account sees the demo pictures and the memories you add
          yourself. Nobody else’s, and Gemma can’t read theirs for you either.
        </p>
      ) : null}
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
          minLength={3}
          maxLength={32}
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
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          minLength={mode === "register" ? 8 : undefined}
          maxLength={200}
          required
        />
        {mode === "register" ? (
          <p className="text-xs text-muted-foreground">
            At least 8 characters.
          </p>
        ) : null}
      </div>
      {error ? (
        <p id={ids.error} className="text-sm text-destructive" role="alert">
          {error}
        </p>
      ) : null}
      <Button type="submit" size="lg" disabled={busy}>
        {busy ? (
          <LoaderCircle className="animate-spin" aria-hidden="true" />
        ) : null}
        {text.submit}
      </Button>
      <p className="text-sm text-muted-foreground">
        {text.switchText}{" "}
        <Link
          href={text.switchHref}
          className="text-primary underline-offset-4 hover:underline"
        >
          {text.switchLink}
        </Link>
      </p>
    </form>
  );
}
