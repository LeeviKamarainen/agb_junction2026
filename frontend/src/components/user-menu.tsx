"use client";

import { LogOut, ShieldCheck, UserRound } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { loggedOut, logOut, useSession } from "@/lib/session";

// Set while logging out: the page is about to go home, so a page that needs a login must not
// send its visitor to /login meanwhile.
let leaving = false;

type Props = {
  /** On a page that needs a login: go to /login when nobody is logged in (e.g. it expired). */
  requireLogin?: boolean;
  className?: string;
};

/** Who is logged in, with Log out. */
export function UserMenu({ requireLogin = false, className = "" }: Props) {
  const { user } = useSession();
  const router = useRouter();

  useEffect(() => {
    if (requireLogin && user === null && !leaving) {
      const next = window.location.pathname + window.location.search;
      window.location.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [requireLogin, user]);

  /** Logs out and goes home; every useSession hears of it, so nothing shows the old login. */
  async function leave() {
    leaving = true;
    await logOut();
    // First: the pages kept in memory, home among them, forget the login before one shows.
    loggedOut();
    router.push("/");
    // Once home, a later visit to a page that needs a login asks for one again.
    setTimeout(() => {
      leaving = false;
    }, 2000);
  }

  // Visitors need no login (/try starts a guest) and the admin logs in on /admin: nothing to offer.
  if (!user) return null;

  const admin = user.role === "admin";
  return (
    <span className={`inline-flex flex-wrap items-center gap-2 text-sm ${className}`}>
      <span
        className="inline-flex flex-wrap items-center gap-1.5"
        title={
          admin
            ? "Admin: sees every user’s memories"
            : "Guest: sees the demo pictures and your own memories"
        }
      >
        {admin ? (
          <ShieldCheck size={15} className="text-primary" aria-hidden="true" />
        ) : (
          <UserRound size={15} aria-hidden="true" className="max-sm:hidden" />
        )}
        <span
          className="select-all font-medium max-sm:hidden"
          title={admin ? undefined : "Save this username to return to your temporary account"}
        >
          {user.username}
        </span>
        <span className="rounded-full border border-border px-1.5 text-xs text-muted-foreground max-sm:hidden">
          {admin ? "admin" : "guest"}
        </span>
        {user.expires_at ? <TimeLeft until={user.expires_at} /> : null}
      </span>
      <Button variant="ghost" size="sm" onClick={() => void leave()}>
        <LogOut aria-hidden="true" /> Log out
      </Button>
    </span>
  );
}

/** A guest's minutes left; at zero the page goes to /login, since the account is gone. */
function TimeLeft({ until }: { until: string }) {
  const end = new Date(until).getTime();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, []);

  const minutes = Math.max(0, Math.ceil((end - now) / 60_000));
  useEffect(() => {
    if (minutes === 0) window.location.replace("/login");
  }, [minutes]);

  return (
    <span
      className="text-xs text-muted-foreground"
      title="Then this guest account and everything it added are deleted"
    >
      {minutes} min left
    </span>
  );
}
