"use client";

import Link from "next/link";
import { UserMenu } from "@/components/user-menu";
import { useSession } from "@/lib/session";

export function SiteNav() {
  const { user } = useSession();
  return (
    <nav
      aria-label="Main navigation"
      className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 text-sm"
    >
      <Link href="/">Live</Link>
      <Link href="/demos">Demos</Link>
      {/* Anyone logged in, a guest too: the demo memories and their own, from every client. */}
      {user ? <Link href="/memory">Memory lab</Link> : null}
      {/* The admin's views. */}
      {user?.role === "admin" ? (
        <>
          <Link href="/admin">Manage demos</Link>
          <Link href="/admin/streams">Streams</Link>
          <Link href="/glasses">Glasses harness</Link>
        </>
      ) : null}
      <UserMenu />
    </nav>
  );
}
