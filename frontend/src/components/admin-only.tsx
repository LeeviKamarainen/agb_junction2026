"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useSession } from "@/lib/session";

/**
 * The admin's developer views: anyone else is sent to the home page. The backend still decides
 * what each user may read; this only keeps the tools out of a visitor's way.
 */
export function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useSession();
  const router = useRouter();
  const admin = user?.role === "admin";
  useEffect(() => {
    if (user !== undefined && !admin) router.replace("/");
  }, [user, admin, router]);
  return admin ? children : null;
}
