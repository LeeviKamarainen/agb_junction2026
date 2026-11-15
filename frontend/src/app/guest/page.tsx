import { ArrowLeft } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { GuestButton } from "@/components/guest-button";

export const metadata: Metadata = {
  title: "Try it as a guest — GlassMemory",
  description:
    "A 60-minute guest session: no sign-up, see the demo pictures and add your own.",
};

export default function GuestPage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-10">
      <section className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-border bg-card p-6">
        <h1 className="text-xl font-semibold tracking-tight">
          Try it as a guest
        </h1>
        <p className="text-sm text-muted-foreground">
          No name or password. You see the demo pictures and whatever you add
          yourself. Gemma searches those memories when you ask. After 60 minutes
          the guest account and everything you added are deleted.
        </p>
        <GuestButton />
        <p className="text-sm text-muted-foreground">
          Save your guest username to return on another device. Keep it private;
          it opens your temporary account without a password.
        </p>
        <p className="text-sm text-muted-foreground">
          Already have a guest account?{" "}
          <Link
            href="/login"
            className="text-primary underline-offset-4 hover:underline"
          >
            Return with your username
          </Link>
        </p>
      </section>
      <Link
        href="/"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft size={15} aria-hidden="true" /> Home
      </Link>
    </main>
  );
}
