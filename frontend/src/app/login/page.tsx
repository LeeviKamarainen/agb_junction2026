import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { AuthForm } from "./auth-form";

export const metadata: Metadata = {
  title: "Log in — GlassMemory",
  description:
    "Start a 60-minute guest session or return to your temporary account with its username.",
};

/** Guests only; the admin logs in on /admin. */
export default function LoginPage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-10">
      <AuthForm />
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Home
      </Link>
    </main>
  );
}
