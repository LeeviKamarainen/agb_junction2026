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
      <Link href="/">Ask your day</Link>
      <Link href="/demos">Demos</Link>
      {user ? (
        <>
          <Link href="/glasses">Open camera</Link>
          <Link href="/memory">Memory lab</Link>
        </>
      ) : null}
      {user?.role === "admin" ? <Link href="/admin">Manage demos</Link> : null}
      <UserMenu />
    </nav>
  );
}
