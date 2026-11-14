"use client";

import { FlaskConical } from "lucide-react";
import Link from "next/link";

import { UserMenu } from "@/components/user-menu";
import { useSession } from "@/lib/session";

/** The home page's navigation: the login menu for everyone, plus the Memory lab for the admin. */
export function SiteNav() {
  const { user } = useSession();
  return (
    <nav
      aria-label="Main navigation"
      className="flex flex-wrap items-center justify-end gap-x-5 gap-y-2"
    >
      {user?.role === "admin" && (
        <Link
          href="/memory"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          <FlaskConical size={15} aria-hidden="true" /> Memory lab
        </Link>
      )}
      <UserMenu />
    </nav>
  );
}
