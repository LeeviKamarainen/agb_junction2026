"use client";

import { LogIn, LogOut, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { logOut, useSession } from "@/lib/session";

type Props = {
  /** On a page that needs a login: go to /login when nobody is logged in (e.g. it expired). */
  requireLogin?: boolean;
  className?: string;
};

/** Who is logged in, with Log out; or Log in and Register links. */
export function UserMenu({ requireLogin = false, className = "" }: Props) {
  const { user } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (requireLogin && user === null) {
      const next = window.location.pathname + window.location.search;
      window.location.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [requireLogin, user]);

  async function leave() {
    await logOut();
    router.push("/login");
  }

  if (user === undefined)
    return (
      <span className={`text-sm text-muted-foreground ${className}`}>…</span>
    );

  if (user === null)
    return (
      <span className={`inline-flex items-center gap-3 text-sm ${className}`}>
        <Link
          href="/login"
          className="inline-flex items-center gap-1.5 text-muted-foreground hover:text-foreground"
        >
          <LogIn size={15} aria-hidden="true" /> Log in
        </Link>
        <Link
          href="/register"
          className="text-muted-foreground hover:text-foreground"
        >
          Register
        </Link>
      </span>
    );

  const admin = user.role === "admin";
  return (
    <span className={`inline-flex items-center gap-2 text-sm ${className}`}>
      <span
        className="inline-flex items-center gap-1.5"
        title={
          admin
            ? "Admin: sees every user’s memories"
            : "Guest: sees the demo pictures and your own memories"
        }
      >
        {admin ? (
          <ShieldCheck size={15} className="text-primary" aria-hidden="true" />
        ) : (
          <UserRound size={15} aria-hidden="true" />
        )}
        <span className="font-medium">{user.username}</span>
        <span className="rounded-full border border-border px-1.5 text-xs text-muted-foreground">
          {admin ? "admin" : "guest"}
        </span>
      </span>
      <Button variant="ghost" size="sm" onClick={() => void leave()}>
        <LogOut aria-hidden="true" /> Log out
      </Button>
    </span>
  );
}
